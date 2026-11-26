"""Whole-story shared-atlas text pack with explicit per-Say streamed caches.

This writes only nitrofs/story/text.bin and story_text* audit artifacts. The
already delivered scene pack, font subsets, game sources and ROM stay untouched.
"""
from pathlib import Path
from copy import copy
from collections import Counter
import argparse
import ast
import hashlib
import importlib.util
import json
import struct
import sys
import zipfile
import zlib
import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'nds/tools'))
sys.path.insert(0,str(ROOT/'experiments/asset_lab'))
sys.path.insert(0,str(ROOT/'experiments/story_lab'))
from pack_scene_text import Compiler,COMMAND,rgb15
from pack_assets import lz10,unlz10
from measure_text_pack import compact_commands
from anim_story import DEFAULT_GAME,_characters,read_compiled
from anim_player import RichText
from anim_text import SceneText
from font_core import parse_bdf
from paired_layout import layout_cn,layout_paired
from poem_layout import poem_pages,POEM_POLICY
from story_typography import StoryTypographyRenderer,StoryTypographyCompiler,INDEX_MASK,TYPOGRAPHY_POLICY,font_archive

OUT=ROOT/'nds/artifacts'
DEST=ROOT/'nds/nitrofs/story/text.bin'
MANIFEST=OUT/'story_text_manifest.json'
HEADER=struct.Struct('<8s32I')
INDEX=struct.Struct('<IHHI')
BODY_LAYOUT_POLICY=dict(version=1,x=20,y=30,width=216,bottom=173,hard_right=248,shared_header_word=31,shared_header_value=1)


class StoryRenderer(StoryTypographyRenderer):
    def portrait(self,display):
        if display and display['who']=='a' and ' '.join(display.get('attributes',[])) in getattr(self,'transparent_attributes',set()):return None
        return super().portrait(display)


def string_pairs(path):
    result={};old=None
    for raw in path.read_text(encoding='utf-8-sig').splitlines():
        line=raw.strip()
        for prefix in ('old ','new '):
            if line.startswith(prefix):
                value=ast.literal_eval(line[len(prefix):])
                if not isinstance(value,str):raise ValueError(f'nonliteral string translation {path}')
                if prefix=='old ':old=value
                elif old is not None:result[old]=value;old=None
    return result


def prepare_records(ir,game=DEFAULT_GAME):
    game=Path(game);name_pairs=string_pairs(game/'tl/chinese/name.rpy');extra=string_pairs(game/'tl/chinese/extra.rpy');dates=string_pairs(game/'tl/chinese/date.rpy')
    names={None:'','centered':'','n':''}
    for d in ir['speaker_definitions']:
        raw=d.get('static_name','');names[d['variable']]=RichText(name_pairs.get(raw,raw),base_cps=0).text
    records=[];literal_map={};location_map={};date_map={};display_segments=[];nvl_segments=[]
    for label in ir['labels']:
        for event in label['events']:
            if event['kind']=='command' and event.get('value')=='nvl clear':display_segments=[];nvl_segments=[]
            if event['kind']!='say':continue
            if event['global_text_id']!=len(records):raise AssertionError('stable global text id')
            d=event['display'];plan=RichText(d['raw']);assert plan.text==d['plain']
            for g in plan.glyphs:g['language']='cn'
            english=RichText(d['original'],base_cps=0)
            for g in english.glyphs:g.update(language='en',font=12 if 0x400<=ord(g['char'])<=0x4ff else 8,color=(150,150,150))
            prior=display_segments if event['who']=='extend' else nvl_segments if event['who']=='n' and d['continuation'] else []
            segments=[dict(segment) for segment in prior]
            en_start=segments[-1]['en_end'] if segments else 0
            if segments and event['who']=='n':
                # The authored NVL context inserts one hard newline in each
                # cumulative language. Keep it with the preceding source block.
                segments[-1]['cn_end']=d['new_char_start'];en_start+=1;segments[-1]['en_end']=en_start
            assert (segments[-1]['cn_end'] if segments else 0)==d['new_char_start']
            segments.append(dict(source_event_id=event['source_event_id'],cn_start=d['new_char_start'],cn_end=len(plan.glyphs),en_start=en_start,en_end=len(english.glyphs)))
            display_segments=segments
            if d['who']=='n':nvl_segments=segments
            records.append(dict(id=len(records),kind='say',event=event,plan=plan,english_plan=english,segments=segments,original=d['original'],who=d['who'],attributes=d['attributes'],timeline=d['timeline_ticks'],duration=d['duration_ticks'],pauses=d['pauses'],continuation=d['continuation'],new_char_start=d['new_char_start'],automatic=d['automatic'],header_name=names.get(d['who'],'')))
    for label in ir['labels']:
        for event in label['events']:
            if event['kind'] not in ('show','scene') or event.get('image')!='text':continue
            original=event['literal'];chinese=extra.get(original,original)
            record=dict(id=len(records),kind='literal',event=event,plan=literal_plan(chinese,event),original=original,who='centered',attributes=[],duration=0,pauses=[],continuation=False,new_char_start=0,automatic=False,header_name='')
            record['timeline']=[0]*len(record['plan'].glyphs)
            if chinese!=original:
                record['original_plan']=literal_plan(original,event)
                for g in record['original_plan'].glyphs:g['language']='en'
            literal_map[str(event['source_event_id'])]=record['id'];location_map[f'{event["chapter"]}:{event["line"]}']=record['id'];records.append(record)
    prepare_poems(records)
    for definition in ir['promoted_definitions']:
        screen=definition.get('data',{}).get('screen',{}).get('state',{})
        name=screen.get('name','')
        if not name.startswith('date'):continue
        child=screen['children'][0]['state'];original=ast.literal_eval(child['positional'][0]);translated=dates[original]
        record=dict(id=len(records),kind='date',event=dict(source_event_id=definition['parent_source_event_id'],source_file=definition['source_file'],line=definition['line'],chapter=definition['chapter']),plan=RichText(translated,base_cps=0),original=original,who='centered',attributes=[],duration=0,pauses=[],continuation=False,new_char_start=0,automatic=False,header_name='',date_name=name,date_source=screen,native_position=[31,36])
        for g in record['plan'].glyphs:g['font']=8
        record['timeline']=[0]*len(record['plan'].glyphs);date_map[name]=record['id'];records.append(record)
    return records,names,literal_map,location_map,date_map


def prepare_poems(records):
    """Associate the four reviewed source literals by exact source location."""
    by_source={(r['event']['source_file'],r['event']['chapter'],r['event']['line']):r for r in records if r['kind']=='literal'}
    for filename,chapter,ru_line,en_line,ids in (
        ('game/ep1.rpy','ep1',2864,2871,[11952,11953]),
        ('game/ep2d7.rpy','ep2d7',2123,2143,[11960,11961]),
    ):
        russian=by_source[(filename,chapter,ru_line)];english=by_source[(filename,chapter,en_line)]
        if [russian['id'],english['id']]!=ids:raise ValueError('Poem source record identity changed')
        def plan(raw,font,language):
            result=RichText(raw,base_cps=0)
            for glyph in result.glyphs:glyph.update(font=font,language=language,scale=1.)
            return result
        ru=plan(russian['original'],12,'en');en=plan(english['original'],8,'en');cn=plan(english['plan'].raw,12,'cn')
        merged=RichText(ru.raw+en.raw,base_cps=0)
        merged.glyphs=[dict(g) for g in ru.glyphs+en.glyphs]
        merged.text=ru.text+en.text;merged.times=[0]*len(merged.glyphs)
        for record,stage in ((russian,'russian'),(english,'english')):
            record['poem']=dict(russian_plan=ru,english_plan=en,chinese_plan=cn,source_record_ids=ids,stage=stage)
            record['plan']=cn;record['original_plan']=merged;record['timeline']=[0]*len(cn.glyphs)


def literal_plan(raw,event):
    plan=RichText(raw,base_cps=0)
    credits=event['chapter']=='ep3'
    small=credits or raw.count('\n')>=4 or len(plan.glyphs)>100
    for g in plan.glyphs:
        g['font']=12 if credits and g['scale']>1 else 8 if small else 12
        if 0x400<=ord(g['char'])<=0x4ff:g['font']=12
        if credits:g['scale']=1.
    plan.compact_literal=small
    return plan


def extend_fonts(renderer,records,names):
    needed={8:set(chr(c) for c in range(32,127)),10:set(),12:set('------[]'+''.join(names.values()))}
    for record in records:
        for plan in [record['plan']]+[record[key] for key in ('original_plan','english_plan') if key in record]:
            for g in plan.glyphs:
                if g['char']!='\n':
                    size,_=renderer.resolve_font(g['char'],g.get('font',12),g.get('scale',1.))
                    needed[size].add(g['char'])
    additions={size:sorted(needed[size]-renderer.fonts[size][2].keys()) for size in (8,10,12)}
    analysis=json.loads((ROOT/'experiments/font_lab/out/analysis.json').read_text(encoding='utf-8'))
    archive=Path(analysis['font_archive']);missing=[]
    with zipfile.ZipFile(archive) as z:
        for size in (8,10,12):
            if not additions[size]:continue
            font=parse_bdf(z.read(f'fusion-pixel-{size}px-monospaced-zh_hans.bdf'))
            bitmaps,advances,ids=renderer.fonts[size]
            for char in additions[size]:
                cp=ord(char)
                if cp not in font.glyphs:missing.append(dict(font=size,char=char,codepoint=f'U+{cp:04X}'));continue
                ids[char]=len(bitmaps);bitmaps.append(font.bitmap(cp));advances[char]=font.glyphs[cp]['advance']
    if missing:raise ValueError(f'missing literal/header font characters: {missing}')
    return dict(archive=str(archive),archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),additions={str(size):chars for size,chars in additions.items()},missing=[],
        required_native_sizes={str(size):len(chars) for size,chars in needed.items()},size_fallbacks=list(renderer.size_fallbacks.values()))


def literal_entries(renderer,plan):
    # Other authored literals retain their source glyphs and hard line breaks.
    base=8 if getattr(plan,'compact_literal',False) else 12;gap=2 if base==8 else 3
    pages=[[]];x=8;y=24;row_height=base
    def next_row():
        nonlocal x,y,row_height
        x=8;y+=row_height+gap;row_height=base
    for index,g in enumerate(plan.glyphs):
        font=g.get('font',12);ink=renderer.measure(g,font);advance=ink.advance
        if g['char']!='\n' and x>8 and x+ink.ink_width>248:next_row()
        if y+ink.ink_height>168:pages.append([]);x=8;y=24;row_height=base
        pages[-1].append(dict(index=index,glyph=g,font=font,x=x,y=y,width=0 if g['char']=='\n' else advance,height=0 if g['char']=='\n' else ink.ink_height,ink_width=0 if g['char']=='\n' else ink.ink_width))
        if g['char']=='\n':next_row()
        else:x+=advance;row_height=max(row_height,ink.ink_height)
    result=[]
    for page,entries in enumerate(pages):
        maxbottom=max((e['y']+e['height'] for e in entries),default=24);offset=max(0,(144-(maxbottom-24))//2)
        rows={}
        for item in entries:rows.setdefault(item['y'],[]).append(item)
        for row in rows.values():
            width=max((e['x']+e['ink_width'] for e in row),default=8)-8;shift=(256-width)//2-8
            for item in row:result.append(dict(item,x=item['x']+shift,y=item['y']+offset,page=page))
    assert sorted(e['index'] for e in result)==list(range(len(plan.glyphs)))
    return result,len(pages)


def literal_commands(compiler,plan,index_start=0):
    entries,pages=literal_entries(compiler.renderer,plan);commands=[]
    for item in entries:
        g=item['glyph'];gid=65535 if g['char']=='\n' else compiler.glyph(g,item['font'])[0]
        x,y=item['x'],item['y']
        if gid!=65535:
            ink=compiler.renderer.measure(g,item['font']);w,h=ink.ink_width,ink.ink_height
            assert 0<=x and x+w<=256 and 0<=y and y+h<=192
        commands.append((gid,x,y,int(rgb15(g['color'])),index_start+item['index'],item['page'],0))
    return commands,pages


def entry_commands(compiler,pages,index_start=0,*,source_order=False):
    commands=[]
    for page,entries in enumerate(pages):
        for item in entries:
            g=item['glyph'];gid=65535 if g['char']=='\n' else compiler.glyph(g,item['font'])[0]
            if gid!=65535:
                ink=compiler.renderer.measure(g,item['font'])
                if not(0<=item['x'] and item['x']+ink.ink_width<=256 and 0<=item['y'] and item['y']+ink.ink_height<=192):
                    raise ValueError(f'Entry ink exceeds screen: {item!r}')
            commands.append((gid,item['x'],item['y'],int(rgb15(g['color'])),index_start+item['index'],page,0))
    if source_order:commands.sort(key=lambda c:c[4])
    assert [c[4] for c in commands]==list(range(index_start,index_start+len(commands)))
    return commands


def timeline_rle(times):
    runs=[];previous=0
    for value in times:
        delta=value-previous
        assert 0<=delta<=65535 and 0<=value<=65535
        if runs and runs[-1][1]==delta:runs[-1][0]+=1
        else:runs.append([1,delta])
        previous=value
    encoded=struct.pack('<H',len(runs))+b''.join(struct.pack('<HH',*r) for r in runs)
    decoded=[];clock=0
    for count,delta in runs:
        for _ in range(count):clock+=delta;decoded.append(clock)
    assert decoded==times
    return encoded


def compile_records(ir,game=DEFAULT_GAME):
    records,names,literal_map,location_map,date_map=prepare_records(ir,game)
    characters=_characters(Path(game),read_compiled(Path(game)/'script.rpyc'));renderer=StoryRenderer(game,characters)
    transparent=set()
    for name,path in characters['a']['attributes'].items():
        with Image.open(Path(game)/path) as image:
            if image.convert('RGBA').getbbox() is None:transparent.add(name)
    renderer.transparent_attributes=transparent
    font_report=extend_fonts(renderer,records,names);compiler=StoryTypographyCompiler(renderer)
    normal,asya,footer,asya_plain,ascii_dir,boxes=compiler.static()
    # Format4's native border is a continuous line; directories contain only
    # the authored bracketed name. The frozen scene compiler remains unchanged.
    header_dirs=[(0,0)];header_lookup={('',False):0};header_names=[dict(id=0,name='',portrait=False)]
    def header(name,portrait):
        if not name:return 0
        key=(name,bool(portrait))
        if key in header_lookup:return header_lookup[key]
        commands=[];x=44
        for c in name:
            commands.append(compiler.command(c,x,4));x+=renderer.glyph(c)[1]
        header_lookup[key]=len(header_dirs);header_names.append(dict(id=len(header_dirs),name=name,portrait=bool(portrait)));header_dirs.append(compiler.block(commands));return header_lookup[key]
    portrait_paths=characters['a']['attributes'];portrait_ids={name:i+1 for i,name in enumerate(sorted(portrait_paths))}
    payload=[];details=[];max_raw=max_expanded=max_packed=0;missing_portraits=Counter()
    for record in records:
        plan=record['plan'];kind=record['kind'];attrs=' '.join(record['attributes']);portrait_id=portrait_ids.get(attrs,0) if record['who']=='a' and attrs not in transparent else 0
        if record['who']=='a' and attrs and not portrait_id and attrs not in transparent:missing_portraits[attrs]+=1
        header_id=header(record['header_name'],bool(portrait_id));en=[];bi_cn=[];paired=None
        if record.get('poem'):
            poem=record['poem']
            grouped=poem_pages(poem['russian_plan'].glyphs,poem['english_plan'].glyphs,poem['chinese_plan'].glyphs,renderer.metrics)
            cn=entry_commands(compiler,grouped['lower_pages'],source_order=True)
            en=entry_commands(compiler,grouped['upper_pages'],len(cn),source_order=True)
            p_cn=p_bi=len(grouped['lower_pages']);upper_kind=4
        elif kind=='literal':
            cn,p_cn=literal_commands(compiler,plan);p_bi=p_cn;upper_kind=2
            if 'original_plan' in record:en,p_bi=literal_commands(compiler,record['original_plan'],len(cn))
        elif kind=='date':
            cn=[];x,y=record['native_position']
            for i,g in enumerate(plan.glyphs):
                gid,advance=compiler.glyph(g,8);cn.append((gid,x,y,int(rgb15(g['color'])),i,0,0));x+=advance
            p_cn=p_bi=1;upper_kind=3
        else:
            upper_kind=1 if record['who']=='centered' else 0
            if upper_kind:cn,p_cn=compiler.layout(plan,upper=True)
            else:
                normal=layout_cn(plan.glyphs,renderer.metrics,portrait=bool(portrait_id))
                cn=entry_commands(compiler,normal);p_cn=len(normal)
            p_bi=p_cn
            if not upper_kind:
                paired=layout_paired(plan.glyphs,record['english_plan'].glyphs,renderer.metrics,record['segments'],portrait=bool(portrait_id))
                bi_cn=entry_commands(compiler,paired['cn_pages']);en=entry_commands(compiler,paired['en_pages'],len(cn));p_bi=len(paired['cn_pages'])
                assert len(bi_cn)==len(cn) and len(en)==len(record['english_plan'].glyphs)
        assert len(cn)==len(plan.glyphs)
        times=timeline_rle(record['timeline']);pauses=b''.join(struct.pack('<3I',p['start_tick'],p['end_tick'],p['glyphs']) for p in record['pauses'])
        cn_bytes=compact_commands(cn,0,0xffff);en_bytes=compact_commands(en,len(cn),0xffff if kind=='literal' else 0xca52)
        pause_at=48+len(times);cn_at=pause_at+len(pauses);en_at=cn_at+len(cn_bytes)
        extension=(0,0,0,0,0,0);tail=b''
        if paired is not None:
            bi_bytes=compact_commands(bi_cn,0,0xffff)
            bi_at=en_at+len(en_bytes);reveal_at=bi_at+len(bi_bytes);limits_at=reveal_at+len(en)*2
            extension=(bi_at,len(bi_cn),reveal_at,len(en),limits_at,0)
            tail=bi_bytes+struct.pack('<'+'H'*len(en),*paired['en_reveal'])+struct.pack('<'+'H'*p_bi,*paired['bi_limits'])
        mode={'a':1,'n':2,'centered':3}.get(record['who'],0)
        values=(record['event']['line'],len(cn),record['duration'],len(record['pauses']),p_cn,p_bi,mode,int(record['continuation']),record['new_char_start'],48,pause_at,cn_at,len(cn),en_at,len(en),portrait_id,upper_kind,header_id,*extension)
        assert all(0<=v<=65535 for v in values)
        raw=struct.pack('<24H',*values)+times+pauses+cn_bytes+en_bytes+tail
        packed=lz10(raw);assert unlz10(packed)==raw
        if len(packed)>=len(raw):packed=raw;flag=0x8000
        else:flag=0
        assert len(raw)<=65535 and len(packed)<32768
        expanded=96+((len(cn)*2+3)&~3)+len(pauses)+(len(cn)+len(en)+len(bi_cn))*10+(len(en)*2+p_bi*2 if paired is not None else 0)
        max_raw=max(max_raw,len(raw));max_expanded=max(max_expanded,expanded);max_packed=max(max_packed,len(packed))
        payload.append((packed,flag,len(raw),zlib.crc32(raw)))
        event=record['event']
        detail=dict(id=record['id'],kind=kind,source_event_id=event['source_event_id'],chapter=event['chapter'],source_file=event['source_file'],source_line=event['line'],char_count=len(cn),duration_ticks=record['duration'],pause_count=len(record['pauses']),pages_cn=p_cn,pages_bi=p_bi,mode=mode,continuation=int(record['continuation']),new_char_start=record['new_char_start'],automatic=record['automatic'],header_id=header_id,portrait_id=portrait_id,raw_bytes=len(raw),expanded_bytes=expanded,packed_bytes=len(packed))
        detail.update(paired=paired is not None,bi_cn_count=len(bi_cn),en_count=len(en),new_en_start=record['segments'][-1]['en_start'] if kind=='say' else 0,upper_kind=upper_kind)
        if record.get('poem'):
            detail['poem']=dict(russian=poem['russian_plan'].raw,english=poem['english_plan'].raw,chinese=poem['chinese_plan'].raw,
                                source_record_ids=poem['source_record_ids'],stage=poem['stage'],groups=grouped['groups'])
        if paired is not None:detail.update(segments=record['segments'],pairs=paired['pairs'],en_reveal=paired['en_reveal'],bi_limits=paired['bi_limits'])
        if kind!='say':detail.update(chinese=plan.raw,original=record['original'],fonts=sorted(set(g.get('font',12) for g in plan.glyphs)),authored_lines=plan.text.count('\n')+1)
        if kind=='date':detail.update(date_name=record['date_name'],date_source=record['date_source'],native_position=record['native_position'])
        details.append(detail)
        if (record['id']+1)%2000==0:print(f'packed text {record["id"]+1}/{len(records)}',flush=True)
    if renderer.diagnostics:raise ValueError(sorted(renderer.diagnostics))
    portrait_data=[];portrait_report=[]
    for name in sorted(portrait_paths):
        path=Path(game)/portrait_paths[name]
        if name in transparent:portrait_data.append(bytes(7200))
        else:portrait_data.append(rgb15(np.asarray(renderer._portrait(str(path)))).tobytes())
        portrait_report.append(dict(id=portrait_ids[name],attribute=name,transparent=name in transparent,source=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    return dict(records=records,renderer=renderer,compiler=compiler,payload=payload,details=details,max_raw=max_raw,max_expanded=max_expanded,max_packed=max_packed,header_dirs=header_dirs,header_names=header_names,footer=footer,ascii_dir=ascii_dir,boxes=boxes,portrait_data=b''.join(portrait_data),portraits=portrait_report,font_report=font_report,literal_map=literal_map,location_map=location_map,date_map=date_map,missing_portraits=dict(missing_portraits),names=names)


def build(verify=True,force=False,game=DEFAULT_GAME):
    OUT.mkdir(parents=True,exist_ok=True);DEST.parent.mkdir(parents=True,exist_ok=True)
    ir_path=ROOT/'nds/artifacts/full_story_ir.json';ir_bytes=ir_path.read_bytes();ir=json.loads(ir_bytes)
    inputs={'ir':hashlib.sha256(ir_bytes).hexdigest(),'packer':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    for module in (ROOT/'experiments/story_lab/paired_layout.py',ROOT/'nds/tools/story_typography.py',ROOT/'nds/tools/poem_layout.py'):inputs[module.stem]=hashlib.sha256(module.read_bytes()).hexdigest()
    inputs['typography_policy']=hashlib.sha256(json.dumps(TYPOGRAPHY_POLICY,sort_keys=True).encode()).hexdigest()
    inputs['body_layout_policy']=hashlib.sha256(json.dumps(BODY_LAYOUT_POLICY,sort_keys=True).encode()).hexdigest()
    inputs['poem_policy']=hashlib.sha256(json.dumps(POEM_POLICY,sort_keys=True).encode()).hexdigest()
    inputs['font_archive']=hashlib.sha256(font_archive().read_bytes()).hexdigest()
    for size in (8,10,12):inputs[f'font_{size}px']=hashlib.sha256((ROOT/f'experiments/font_lab/out/font_{size}px.zfn').read_bytes()).hexdigest()
    for name in ('name.rpy','extra.rpy','date.rpy'):inputs[name]=hashlib.sha256((Path(game)/'tl/chinese'/name).read_bytes()).hexdigest()
    cached=json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.is_file() else None
    if not force and cached and cached.get('inputs_sha256')==inputs and DEST.is_file() and hashlib.sha256(DEST.read_bytes()).hexdigest()==cached['sha256']:
        report=cached;context=None
    else:
        context=compile_records(ir,game);c=context['compiler'];n=len(context['records']);glyphs=len(c.glyphs)
        index_at=HEADER.size;glyph_at=index_at+n*12;atlas_at=glyph_at+glyphs*8;header_at=(atlas_at+len(c.atlas)+3)&~3
        commands_at=header_at+len(context['header_dirs'])*8;portrait_at=(commands_at+len(c.commands)*10+3)&~3;records_at=portrait_at+len(context['portrait_data'])
        at=records_at;index=bytearray();body=bytearray()
        for packed,flag,raw,crc in context['payload']:index.extend(INDEX.pack(at,len(packed)|flag,raw,crc));body.extend(packed);at+=len(packed)
        h=[2,at,0,n,11951,glyphs,len(context['header_dirs']),len(context['portraits']),index_at,glyph_at,atlas_at,len(c.atlas),header_at,commands_at,len(c.commands),*context['footer'],*context['ascii_dir'],context['footer'][0]+context['footer'][1],portrait_at,len(context['portrait_data']),records_at,context['max_raw'],context['max_expanded'],context['max_packed'],0,records_at-HEADER.size,0,4,12,1]
        assert len(h)==32
        data=bytearray(HEADER.pack(b'ZATSTX02',*h));data.extend(index);data.extend(b''.join(struct.pack('<IHH',offset,w,(c.glyph_advances[i]<<8)|height) for i,(offset,w,height) in enumerate(c.glyphs)));data.extend(c.atlas);data.extend(bytes(header_at-len(data)));data.extend(b''.join(struct.pack('<II',*d) for d in context['header_dirs']));data.extend(b''.join(COMMAND.pack(*cmd) for cmd in c.commands));data.extend(bytes(portrait_at-len(data)));data.extend(context['portrait_data']);assert len(data)==records_at
        struct.pack_into('<I',data,16,zlib.crc32(data));data.extend(body);assert len(data)==at
        DEST.write_bytes(data)
        report=dict(version=2,record_format=4,wire_record_header_bytes=48,cache_record_header_bytes=96,directory_stride=12,path=str(DEST),bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),inputs_sha256=inputs,say_count=11951,literal_count=len(context['literal_map']),date_count=len(context['date_map']),record_count=n,shared_bytes=records_at,index_bytes=n*12,glyph_count=glyphs,glyph_atlas_bytes=len(c.atlas)+glyphs*8,portrait_bytes=len(context['portrait_data']),header_count=len(context['header_dirs']),static_command_bytes=len(c.commands)*10,max_stored_record_bytes=context['max_raw'],max_expanded_record_bytes=context['max_expanded'],max_packed_record_bytes=context['max_packed'],managed_resident_bytes=records_at+context['max_raw']+context['max_packed']+2*context['max_expanded'],records=context['details'],header_names=context['header_names'],speaker_names={str(k):v for k,v in context['names'].items()},portraits=context['portraits'],missing_portrait_attributes=context['missing_portraits'],font_extension=context['font_report'],missing_glyphs=[],literal_source_event_id_to_id=context['literal_map'],literal_chapter_line_to_id=context['location_map'],date_name_to_id=context['date_map'],footer_boxes=context['boxes'],source_sha256=ir['source_sha256'],frozen_ep2d3=ir['frozen_ep2d3'],notes=['Explicit2-slot streamed reader; no getter/draw I/O. Shared atlas,headers,portraits,index resident.','Lossless source timeline/pauses and5-column normalCN/EN/pairedCN streams; paired EN reveal thresholds and BI page caps. Source glyph indexes and all words preserved. Each record LZ10 roundtrip checked.','OfficialChinese name/date/extra string translations applied. Non-poem translated literals retain X original-language switching; the four reviewed poem records always show Chinese below and interleaved Russian/English above.','All authored literal glyphs/line breaks retained; multi-page counts reported for source-Pause scheduling. Credits headings12px/body8px; poem groups use Chinese12px below and Russian12px/English8px above, balanced into2/3 shared pages.','whoa/toubled have no exact SideImage candidate: no inferred spelling alias. RenPy get_side_image/choose_image returnsNone when attributes do not match. The original fully transparent blank side image means no portrait and no text avoidance.','Original game,font ZIP,existing font subsets,frozen scene package and delivered ROM are read-only.'])
    report['typography_policy']=TYPOGRAPHY_POLICY
    report['body_layout_policy']=BODY_LAYOUT_POLICY
    report['poem_policy']=POEM_POLICY
    if verify:
        sys.path.insert(0,str(ROOT/'nds/tests'))
        from story_text_oracle import verify_pack
        report['verification']=verify_pack(report,ir,context,game)
    MANIFEST.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--force',action='store_true');parser.add_argument('--no-verify',action='store_true');args=parser.parse_args()
    report=build(verify=not args.no_verify,force=args.force)
    print(json.dumps({k:report[k] for k in ('bytes','say_count','literal_count','date_count','glyph_count','managed_resident_bytes','max_expanded_record_bytes','sha256','verification') if k in report},ensure_ascii=False,indent=2))
