"""Compile the audited ep2d3 slice into immutable layouts and 1bpp glyphs.

No game code is executed. anim_story's inert reader supplies events; the existing
preview player is replayed sequentially so EXTEND/NVL retain their authored state.
The ARM9 renderer never shapes text or loads individual glyphs from NitroFS.
"""
from __future__ import annotations
import argparse
from copy import copy, deepcopy
import hashlib
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zlib
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'experiments/asset_lab'))
from anim_story import build_story, DEFAULT_GAME
from anim_player import ScenePlayer, RichText
from anim_text import SceneText, FOOTER

DEST = ROOT / 'nds/nitrofs/scene/text.bin'
ART = ROOT / 'nds/artifacts'
MANIFEST = ART / 'scene_text_manifest.json'
HEADER = struct.Struct('<8s24I')
REC = struct.Struct('<18I')
COMMAND = struct.Struct('<HBBHHBB')


def tick(seconds):
    return max(0, math.ceil(seconds * 60 - 1e-8))


def rgb15(rgb):
    values = np.asarray(rgb, dtype=np.uint16) >> 3
    return (values[..., 0] | values[..., 1] << 5 | values[..., 2] << 10 | 0x8000).astype('<u2')


class Compiler:
    def __init__(self, renderer):
        self.renderer = renderer
        self.atlas = bytearray()
        self.glyphs = []
        self.glyph_ids = {}
        self.commands = []
        self.command_blocks = {}
        self.timelines = []
        self.pauses = []

    def glyph(self, g, font=12):
        mask, advance = self.renderer.glyph(g['char'], font, round(g.get('scale', 1.), 3), g.get('bold', False), g.get('italic', False))
        bitmap = np.asarray(mask) != 0
        bits = np.packbits(bitmap.ravel()).tobytes()
        key = (mask.width, mask.height, bits)
        if key not in self.glyph_ids:
            self.glyph_ids[key] = len(self.glyphs)
            self.glyphs.append((len(self.atlas), mask.width, mask.height))
            self.atlas.extend(bits)
        return self.glyph_ids[key], advance

    def command(self, char, x, y, color=(255,255,255), font=12, index=0, page=0, advance=False):
        gid, adv = self.glyph(dict(char=char), font)
        return (gid, x, y, int(rgb15(color)), index, page, adv if advance else 0)

    def block(self, commands):
        block = b''.join(COMMAND.pack(*c) for c in commands)
        if block not in self.command_blocks:
            self.command_blocks[block] = len(self.commands)
            self.commands.extend(commands)
        return self.command_blocks[block], len(commands)

    def layout(self, plan, portrait=False, upper=False):
        pages = self.renderer.layout(plan, portrait=portrait, **(dict(x=8,y=24,width=240,bottom=168) if upper else {}))
        commands = []
        for page, entries in enumerate(pages):
            offset = 0
            shifts = {}
            if upper:
                maxbottom = max((e['y']+e['height'] for e in entries), default=24)
                offset = max(0, (144-(maxbottom-24))//2)
                rows = {}
                for item in entries:
                    rows.setdefault(item['y'], []).append(item)
                for y,row in rows.items():
                    rowwidth = max((e['x']+e['width'] for e in row), default=8)-8
                    shifts[y] = (256-rowwidth)//2-8
            for item in entries:
                g = item['glyph']
                gid = 65535 if g['char']=='\n' else self.glyph(g,item['font'])[0]
                x,y = item['x']+shifts.get(item['y'],0),item['y']+offset
                if gid != 65535:
                    _,w,h = self.glyphs[gid]
                    if not (0<=x and x+w<=256 and 0<=y and y+h<=192):
                        raise ValueError(f'out of screen glyph {g!r} at {x},{y}')
                    if portrait and y<60 and x+w>196:
                        raise ValueError('glyph intrudes into Asya portrait')
                commands.append((gid,x,y,int(rgb15(g['color'])),item['index'],page,0))
        return commands,len(pages)

    def static(self):
        def header(asya=False, portrait=False):
            text = '------'+('[阿莎雅]' if asya else '')
            x = 8
            commands = []
            right = 190 if portrait else 248
            for c in text:
                command = self.command(c,x,4)
                commands.append(command)
                x += self.renderer.glyph(c)[1]
            while x+self.renderer.glyph('-')[1]<=right:
                commands.append(self.command('-',x,4))
                x += self.renderer.glyph('-')[1]
            return self.block(commands)
        normal = header()
        asya = header(True,True)
        width = sum(self.renderer.fonts[8][1].get(c,8) for label in FOOTER for c in label)+4*(len(FOOTER)-1)
        x = (256-width)//2
        boxes, commands = [],[]
        for label,action in zip(FOOTER,('return','log','skip','auto','bilingual','save','quick_save','quick_load','settings')):
            start = x
            for c in label:
                commands.append(self.command(c,x,181,color=(150,150,150),font=8))
                x += self.renderer.glyph(c,8)[1]
            boxes.append(dict(label=label,action=action,x0=start,y0=180,x1=x,y1=191))
            x += 4
        # These directories are contiguous and intentionally never block-deduped.
        footer = (len(self.commands),len(commands))
        self.commands.extend(commands)
        self.commands.extend(self.command(c,0,0,(130,130,130),8,advance=True) for c in '0123456789/')
        ascii_dir = (len(self.commands),95)
        self.commands.extend(self.command(chr(c),0,0,font=8,advance=True) for c in range(32,127))
        asya_plain = header(True,False)
        return normal,asya,footer,asya_plain,ascii_dir,boxes


def displays(story):
    player = ScenePlayer(story)
    player.reset()
    result = []
    for event in story['events']:
        previous = player.display
        previous_nvl = player.nvl_raw
        player._apply(event,replay=True)
        if event['kind']=='say':
            display = deepcopy(player.display)
            display['active'] = True
            prior = previous['raw'] if event['who']=='extend' and previous else previous_nvl+'\n' if event['who']=='n' and previous_nvl else ''
            result.append((event,display,len(RichText(prior).glyphs),False))
        elif event.get('literal'):
            literal = str(event['literal'])
            result.append((event,dict(text=RichText(literal,base_cps=0),who='centered',raw=literal,original='',source=event,attributes=[],revealed=True,active=True),0,True))
    return result


def verify_native(report, rows, renderer, records, native):
    exe = ART / 'scene_text_validate.exe'
    gcc = shutil.which('gcc')
    if not gcc:
        raise RuntimeError('host GCC required for scene text verification')
    subprocess.run([gcc,'-std=c11','-O2','-Wall','-Wextra','-Werror','-Inds/source','nds/source/scene_text.c','nds/tests/validate_scene_text.c','-o',str(exe)],cwd=ROOT,check=True,capture_output=True,text=True)
    cases=[]
    golden=[]
    names={608:'asya_no_attribute',574:'centered_begin',601:'centered_extend',690:'literal',777:'after'}
    names.update({event['line']:'asya_red' for event,display,_,literal in rows if display['who']=='a' and display['attributes']==['red']})
    def add(id,visible,page,bi,window=True,quick=True,upper=False,ticks=0,name=None):
        event,display,_,literal=rows[id]
        display=deepcopy(display)
        display['revealed']=visible>=len(display['text'].glyphs)
        # Exact visible-count paint, independent of native timeline/command data.
        display['text'].visible=lambda elapsed: visible
        if upper:
            base = ((np.arange(256*192,dtype=np.uint32)*37)&32767)|0x8000
            v=base.reshape(192,256)
            rgb=np.stack(((v&31),((v>>5)&31),((v>>10)&31)),axis=2).astype(np.uint8)
            rgb=(rgb<<3)|(rgb>>2)
            image=renderer.upper_overlay(Image.fromarray(rgb),display if not literal else None,literal=display['raw'] if literal else '')
            plan=display['text']
            # Preview upper_overlay chooses the visible page automatically.
            page=native_page(records[id],visible,False,native)
        else:
            image,meta=renderer.lower(display,elapsed=0,page=page,bilingual=bi,window_visible=window,quick_menu=quick)
            if display['text'].automatic:
                page=meta['shown_page']
        expected=zlib.crc32(rgb15(np.asarray(image)).tobytes())
        case=(id,visible,page,int(bi),(1 if window else 0)|(2 if quick else 0)|(ticks<<8),int(upper))
        cases.append(case)
        oracle_plan=display['text']
        if bi and not upper and display['who']!='centered':
            oracle_plan=copy(oracle_plan)
            oracle_plan.glyphs=list(oracle_plan.glyphs)+[dict(g,font=8,color=(150,150,150),scale=1.) for g in RichText('\n'+display['original'],base_cps=0).glyphs]
            oracle_plan.raw+='\0'+display['original']
        oracle_layout=renderer.layout(oracle_plan,portrait=renderer.portrait(display) is not None,**(dict(x=8,y=24,width=240,bottom=168) if upper or display['who']=='centered' else {}))
        oracle_visible=len(oracle_plan.glyphs) if bi and visible>=len(display['text'].glyphs) else visible
        expected_page=next((i for i,p in reversed(list(enumerate(oracle_layout))) if any(v['index']<oracle_visible for v in p)),0)
        # Use the original RichText clock rather than compiler tick values.
        expected_visible=rows[id][1]['text'].visible(ticks/60)
        golden.append((expected,expected_page,expected_visible))
        if name:
            image.save(ART/f'scene_text_golden_{name}.png')
    for id,(event,display,_,literal) in enumerate(rows):
        info=records[id]
        count=info[1]
        if literal or display['who']=='centered':
            for n in sorted({count} if literal else {1,count//2,count}):
                add(id,n,0,False,upper=True,name=names.get(event['line']) if n==count else None)
            add(id,count,0,False)
        else:
            for bi in (False,True):
                for page in range(info[5 if bi else 4]):
                    add(id,count,page,bi,name=names.get(event['line'])+('_bi' if bi else '') if event['line'] in names and page==0 else None)
            add(id,min(count,1),0,False)
            if info[3]:
                for pause in native['pauses'][id]:
                    add(id,pause['glyphs'],0,False,ticks=tick(pause['start']))
    # Window/menu toggles and blank IDs must not retain dialogue pixels.
    for window,quick in ((False,False),(False,True),(True,False)):
        add(0,records[0][1],0,False,window=window,quick=quick)
    for window,quick in ((False,False),(False,True),(True,False),(True,True)):
        image,_=renderer.lower(None,window_visible=window,quick_menu=quick)
        cases.append((0xffffffff,0,0,0,int(window)|(int(quick)<<1),0))
        golden.append((zlib.crc32(rgb15(np.asarray(image)).tobytes()),0,0))
    base=((np.arange(256*192,dtype=np.uint32)*37)&32767)|0x8000
    v=base.reshape(192,256)
    rgb=np.stack(((v&31),((v>>5)&31),((v>>10)&31)),axis=2).astype(np.uint8)
    rgb=(rgb<<3)|(rgb>>2)
    image=Image.fromarray(rgb)
    renderer.emit(image,'ASCII 0z!',-2,188,font=8)
    cases.append((0xffffffff,0,0,0,0,2))
    golden.append((zlib.crc32(rgb15(np.asarray(image)).tobytes()),0,0))
    pixel_frames=len(cases)
    for id,(_,display,_,_) in enumerate(rows):
        plan=display['text']
        layout=renderer.layout(plan,portrait=renderer.portrait(display) is not None,**(dict(x=8,y=24,width=240,bottom=168) if display['who']=='centered' else {}))
        probes={0,tick(plan.duration),tick(plan.duration)+1}
        for stamp in plan.times:
            at=tick(stamp)
            probes.add(at)
            if at:probes.add(at-1)
        for at in sorted(probes):
            visible=plan.visible(at/60)
            page=next((i for i,p in reversed(list(enumerate(layout))) if any(v['index']<visible for v in p)),0)
            cases.append((id,visible,0,0,at<<8,3))
            golden.append((0,page,visible))
    timing_probes=len(cases)-pixel_frames
    for id,(_,display,_,_) in enumerate(rows):
        source=display['text']
        count=len(source.glyphs)
        for bi in (False,True):
            plan=source
            upper=display['who']=='centered'
            if bi and not upper:
                plan=copy(source)
                plan.glyphs=list(source.glyphs)+[dict(g,font=8,color=(150,150,150),scale=1.) for g in RichText('\n'+display['original'],base_cps=0).glyphs]
                plan.raw+='\0'+display['original']
            layout=renderer.layout(plan,portrait=renderer.portrait(display) is not None,**(dict(x=8,y=24,width=240,bottom=168) if upper else {}))
            for page in [*range(len(layout)+1),0xffffffff]:
                cap=next((item['index'] for next_page,entries in enumerate(layout) if next_page>page for item in entries if item['index']<count),count)
                visible=len(plan.glyphs) if bi and cap>=count else cap
                automatic_page=next((i for i,p in reversed(list(enumerate(layout))) if any(v['index']<visible for v in p)),0)
                cases.append((id,cap,page,int(bi),0,4))
                golden.append((cap,automatic_page,source.visible(0)))
    page_probes=len(cases)-pixel_frames-timing_probes
    cases_path=ART/'scene_text_cases.bin'
    result_path=ART/'scene_text_native_results.bin'
    cases_path.write_bytes(b''.join(struct.pack('<6I',*c) for c in cases))
    output=subprocess.check_output([str(exe),str(DEST),str(cases_path),str(result_path)],text=True)
    results=list(struct.iter_unpack('<3I',result_path.read_bytes()))
    if len(results)!=len(golden):
        raise AssertionError('native frame count')
    for index,(actual,expected) in enumerate(zip(results,golden)):
        if actual!=expected:
            raise AssertionError(f'native preview mismatch case {index}, request {cases[index]}, actual {actual}, expected {expected}, line {rows[cases[index][0]][0]["line"]}')
    # Recompute valid CRCs after targeted table corruption: bounds checks, not
    # only the checksum, must reject structurally invalid content.
    data=bytearray(DEST.read_bytes())
    bad=ART/'scene_text_invalid.bin'
    rejects=[]
    variants=[('truncated',bytes(data[:-1])),('CRC',bytes(data[:120]+bytes([data[120]^1])+data[121:]))]
    broken=bytearray(data);struct.pack_into('<I',broken,8+5*4,0xfffffff0);struct.pack_into('<I',broken,16,0);struct.pack_into('<I',broken,16,zlib.crc32(broken));variants.append(('offset',broken))
    for name,at in [('glyph bitmap offset',struct.unpack_from('<I',data,8+6*4)[0]),('record character count',HEADER.size+4)]:
        broken=bytearray(data);struct.pack_into('<I',broken,at,0xffffffff);struct.pack_into('<I',broken,16,0);struct.pack_into('<I',broken,16,zlib.crc32(broken));variants.append((name,broken))
    for name,contents in variants:
        bad.write_bytes(contents)
        result=subprocess.run([str(exe),str(bad)],capture_output=True,text=True)
        if result.returncode!=3:
            raise AssertionError(f'bad pack was accepted: {name}')
        rejects.append(name)
    bad.unlink()
    return dict(result='PASS',frames=pixel_frames,timing_probes=timing_probes,page_probes=page_probes,pixel_oracle='existing SceneText lower/upper_overlay; RGB15 quantization',timeline_oracle='original RichText.visible at every authored reveal tick and immediately before it',page_oracle='existing SceneText.layout: next page first logical Chinese index; final/English-only pages cap at Chinese count',native_output=output.strip(),malformed_rejected=rejects,case_sha256=hashlib.sha256(cases_path.read_bytes()).hexdigest())


def native_page(info,visible,bi,native):
    page=0
    for c in native['cn'][info[17]]:
        if c[4]<visible:page=c[5]
    if bi and visible>=info[1] and native['en'][info[17]]:
        page=native['en'][info[17]][-1][5]
    return page


def build(game=DEFAULT_GAME, verify=True):
    ART.mkdir(parents=True,exist_ok=True)
    DEST.parent.mkdir(parents=True,exist_ok=True)
    story=build_story(game)
    if any(e['kind']=='say' and e['text'] is None for e in story['events']):
        raise ValueError('cannot silently omit an untranslated source Say')
    renderer=SceneText(game,story['characters'])
    compiler=Compiler(renderer)
    normal,asya,footer,asya_plain,ascii_dir,footer_boxes=compiler.static()
    rows=displays(story)
    records=[]
    text_records=[]
    native=dict(cn=[],en=[],times=[],pauses=[])
    line_to_id={}
    literal_line_to_id={}
    for id,(event,display,new_start,literal) in enumerate(rows):
        plan=display['text']
        portrait=renderer.portrait(display) is not None
        # This slice has only the original red-expression side image. The state
        # comes from sequential replay; source608 has no previous a attributes.
        if portrait and display['attributes']!=['red']:
            raise ValueError('unexpected side-image expression in ep2d3 slice')
        upper=literal or display['who']=='centered'
        cn,pages_cn=compiler.layout(plan,portrait=portrait,upper=upper)
        en=[]
        pages_bi=pages_cn
        if not upper:
            bilingual=copy(plan)
            bilingual.glyphs=list(plan.glyphs)
            english=RichText('\n'+display['original'],base_cps=0)
            bilingual.glyphs.extend(dict(g,font=8,color=(150,150,150),scale=1.) for g in english.glyphs)
            bilingual.raw+='\0'+display['original']
            combined,pages_bi=compiler.layout(bilingual,portrait=portrait)
            en=[c for c in combined if c[4]>=len(plan.glyphs)]
            if combined[:len(cn)]!=cn:
                raise AssertionError('bilingual relaid Chinese glyphs')
        cn_at,cn_count=compiler.block(cn)
        en_at,en_count=compiler.block(en)
        times=[tick(t) for t in plan.times]
        if times and max(times)>65535:
            raise ValueError('text timeline exceeds uint16 ticks')
        time_at=len(compiler.timelines)
        compiler.timelines.extend(times)
        pause_at=len(compiler.pauses)
        compiler.pauses.extend((tick(p['start']),tick(p['end']),p['glyphs']) for p in plan.pauses)
        mode={'a':1,'n':2,'centered':3}.get(display['who'],0)
        values=(event['line'],len(plan.glyphs),tick(plan.duration),len(plan.pauses),pages_cn,pages_bi,mode,int(new_start>0),new_start,time_at,pause_at,cn_at,cn_count,en_at,en_count,int(portrait),int(upper),id)
        records.append(values)
        native['cn'].append(cn);native['en'].append(en);native['times'].append(times);native['pauses'].append(plan.pauses)
        (literal_line_to_id if literal else line_to_id)[str(event['line'])]=id
        text_records.append(dict(id=id,source_line=event['line'],kind='literal' if literal else 'say',who=display['who'],attributes=display['attributes'],portrait=portrait,char_count=len(plan.glyphs),duration_ticks=tick(plan.duration),pauses=[dict(start_tick=tick(p['start']),end_tick=tick(p['end']),glyphs=p['glyphs']) for p in plan.pauses],pages_cn=pages_cn,pages_bi=pages_bi,mode=mode,continuation=int(new_start>0),new_char_start=new_start,automatic=plan.automatic,raw=display['raw'],chinese=plan.text,original=display['original'],translation_id=event.get('translation_id')))
    if len(line_to_id)!=278:
        raise AssertionError('278 independent source Say IDs required')
    if renderer.diagnostics:
        raise ValueError(sorted(renderer.diagnostics))
    rec_off=HEADER.size
    glyph_off=rec_off+len(records)*REC.size
    time_off=glyph_off+len(compiler.glyphs)*8
    time_bytes=struct.pack('<'+'H'*len(compiler.timelines),*compiler.timelines)
    if len(time_bytes)%4:time_bytes+=b'\0\0'
    pause_off=time_off+len(time_bytes)
    pause_bytes=b''.join(struct.pack('<3I',*p) for p in compiler.pauses)
    cmd_off=pause_off+len(pause_bytes)
    cmd_bytes=b''.join(COMMAND.pack(*c) for c in compiler.commands)
    atlas_off=cmd_off+len(cmd_bytes)
    portrait_off=atlas_off+len(compiler.atlas)
    portrait_path=Path(game)/story['characters']['a']['attributes']['red']
    portrait=renderer._portrait(str(portrait_path))
    portrait_bytes=rgb15(np.asarray(portrait)).tobytes()
    total=portrait_off+len(portrait_bytes)
    header=(1,total,0,len(records),len(compiler.glyphs),rec_off,glyph_off,time_off,pause_off,cmd_off,atlas_off,len(compiler.atlas),*normal,*asya,*footer,portrait_off,len(portrait_bytes),*asya_plain,*ascii_dir)
    data=bytearray(HEADER.pack(b'ZATSTX01',*header))
    data.extend(b''.join(REC.pack(*r) for r in records))
    data.extend(b''.join(struct.pack('<IHH',*g) for g in compiler.glyphs))
    data.extend(time_bytes);data.extend(pause_bytes);data.extend(cmd_bytes);data.extend(compiler.atlas);data.extend(portrait_bytes)
    assert len(data)==total
    struct.pack_into('<I',data,16,zlib.crc32(data))
    DEST.write_bytes(data)
    report=dict(version=1,path='nds/nitrofs/scene/text.bin',bytes=len(data),ram_bytes=len(data)+24*4,caller_screen_bytes=256*192*2,glyph_count=len(compiler.glyphs),glyph_bitmap_bytes=len(compiler.atlas),command_count=len(compiler.commands),command_bytes=len(cmd_bytes),timeline_bytes=len(time_bytes),say_count=len(line_to_id),literal_count=len(literal_line_to_id),line_to_id=line_to_id,literal_line_to_id=literal_line_to_id,text_records=text_records,footer_boxes=footer_boxes,sha256=hashlib.sha256(data).hexdigest(),crc32=f'{struct.unpack_from("<I",data,16)[0]:08x}',source_sha256=story['source_sha256'],portrait_source=str(portrait_path),portrait_source_sha256=hashlib.sha256(portrait_path.read_bytes()).hexdigest(),notes=['60Hz ceil timestamps; first glyph at tick0; newlines keep logical indices without consuming time','CN and BI share Chinese timeline; English appears in full when Chinese completes','CPU-only fixed glyph commands; RGB15; 1bpp deduplicated styled atlas; no runtime layout','EXTEND/NVL and side-image attribute state compiled by sequential ScenePlayer replay','scene_text_draw_ascii includes all 95 printable ASCII glyphs at8px; diagnostics are opt-in'])
    if verify:
        report['native_verification']=verify_native(report,rows,renderer,records,native)
    MANIFEST.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--game',type=Path,default=DEFAULT_GAME)
    parser.add_argument('--no-verify',action='store_true')
    args=parser.parse_args()
    report=build(args.game,verify=not args.no_verify)
    print(json.dumps({k:report[k] for k in ('bytes','ram_bytes','say_count','literal_count','glyph_count','sha256','native_verification') if k in report},ensure_ascii=False,indent=2))
