"""Measure a prospective shared-atlas/per-Say LZ10 format without shipping it.

Only the JSON audit is written. The frozen scene pack and runtime are untouched.
Each candidate block contains72B metadata, u16 timeline,12B pauses,10B commands.
The exact measured bytes apply to this documented candidate record layout.
"""
from pathlib import Path
from collections import Counter
from copy import copy
import argparse
import hashlib
import json
import struct
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'nds/tools'))
from pack_scene_text import Compiler,COMMAND
from pack_assets import lz10,unlz10
from anim_story import DEFAULT_GAME,_characters,read_compiled
from anim_player import RichText
from anim_text import SceneText


def compact_commands(commands,index_start,default_color):
    assert all(c[4]==index_start+i for i,c in enumerate(commands)), 'nonsequential logical index needs an explicit index stream'
    data=bytearray(b''.join(struct.pack('<H',c[0]) for c in commands))
    for column in (1,2,5):data.extend(c[column] for c in commands)
    runs=[]
    for i,c in enumerate(commands):
        color=c[3]
        if color==default_color:continue
        if runs and runs[-1][0]+runs[-1][1]==i and runs[-1][2]==color:runs[-1][1]+=1
        else:runs.append([i,1,color])
    data.extend(struct.pack('<H',len(runs)))
    data.extend(b''.join(struct.pack('<3H',*r) for r in runs))
    return bytes(data)


def measure(ir,game=DEFAULT_GAME):
    game=Path(game)
    characters=_characters(game,read_compiled(game/'script.rpyc'))
    renderer=SceneText(game,characters)
    compiler=Compiler(renderer)
    compiler.static()
    portrait_paths=characters['a']['attributes']
    portrait_ids={name:index+1 for index,name in enumerate(sorted(portrait_paths))}
    records=[];codecs=Counter();pages_cn=Counter();pages_bi=Counter();missing_portraits=Counter()
    raw_total=packed_total=commands_total=time_total=pause_total=0
    compact_raw_total=compact_packed_total=0;compact_max_raw=compact_max_packed=0
    all_raw=hashlib.sha256();all_packed=hashlib.sha256();english_count=0
    for label in ir['labels']:
        for event in label['events']:
            if event['kind']!='say':continue
            display=event['display'];plan=RichText(display['raw'])
            assert plan.text==display['plain']
            attrs=' '.join(display['attributes'])
            portrait_id=portrait_ids.get(attrs,0) if display['who']=='a' else 0
            if display['who']=='a' and attrs and not portrait_id:missing_portraits[attrs]+=1
            upper=display['who']=='centered'
            cn,p_cn=compiler.layout(plan,portrait=bool(portrait_id),upper=upper)
            en=[];p_bi=p_cn
            if not upper:
                bilingual=copy(plan);bilingual.glyphs=list(plan.glyphs)
                english=RichText('\n'+display['original'],base_cps=0)
                english_count+=len(english.glyphs)
                bilingual.glyphs.extend(dict(g,font=8,color=(150,150,150),scale=1.) for g in english.glyphs)
                bilingual.raw+='\0'+display['original']
                combined,p_bi=compiler.layout(bilingual,portrait=bool(portrait_id))
                assert combined[:len(cn)]==cn
                en=[c for c in combined if c[4]>=display['char_count']]
            times=display['timeline_ticks']
            if max(times,default=0)>65535:raise ValueError('u16 timeline overflow')
            time_bytes=struct.pack('<'+'H'*len(times),*times)
            if len(time_bytes)%4:time_bytes+=b'\0\0'
            pauses=b''.join(struct.pack('<3I',p['start_tick'],p['end_tick'],p['glyphs']) for p in display['pauses'])
            cn_bytes=b''.join(COMMAND.pack(*c) for c in cn)
            en_bytes=b''.join(COMMAND.pack(*c) for c in en)
            p_at=72+len(time_bytes);cn_at=p_at+len(pauses);en_at=cn_at+len(cn_bytes)
            mode={'a':1,'n':2,'centered':3}.get(display['who'],0)
            header=struct.pack('<18I',event['line'],display['char_count'],display['duration_ticks'],len(display['pauses']),p_cn,p_bi,mode,int(display['continuation']),display['new_char_start'],72,p_at,cn_at,len(cn),en_at,len(en),portrait_id,int(upper),event['global_text_id'])
            raw=header+time_bytes+pauses+cn_bytes+en_bytes
            compressed=lz10(raw)
            assert unlz10(compressed)==raw
            codec=1 if len(compressed)<len(raw) else 0
            packed=compressed if codec else raw
            compact_cn=compact_commands(cn,0,0xffff)
            compact_en=compact_commands(en,len(cn),0xca52)
            compact_header=struct.pack('<18I',event['line'],display['char_count'],display['duration_ticks'],len(display['pauses']),p_cn,p_bi,mode,int(display['continuation']),display['new_char_start'],72,p_at,cn_at,len(cn),cn_at+len(compact_cn),len(en),portrait_id,int(upper),event['global_text_id'])
            compact=compact_header+time_bytes+pauses+compact_cn+compact_en
            compact_lz=lz10(compact);assert unlz10(compact_lz)==compact
            compact_packed=compact_lz if len(compact_lz)<len(compact) else compact
            compact_raw_total+=len(compact);compact_packed_total+=len(compact_packed)
            compact_max_raw=max(compact_max_raw,len(compact));compact_max_packed=max(compact_max_packed,len(compact_packed))
            raw_total+=len(raw);packed_total+=len(packed);commands_total+=len(cn_bytes)+len(en_bytes);time_total+=len(time_bytes);pause_total+=len(pauses)
            codecs[codec]+=1;pages_cn[p_cn]+=1;pages_bi[p_bi]+=1
            all_raw.update(raw);all_packed.update(packed)
            records.append(dict(global_text_id=event['global_text_id'],source_event_id=event['source_event_id'],source_file=event['source_file'],source_line=event['line'],raw_bytes=len(raw),packed_bytes=len(packed),codec=codec,pages_cn=p_cn,pages_bi=p_bi,portrait_id=portrait_id))
        print(f'measured {label["label"]}: {len(records)}/11951 records, payload {packed_total}B',flush=True)
    if renderer.diagnostics:raise ValueError(sorted(renderer.diagnostics))
    atlas_bytes=len(compiler.atlas)+8*len(compiler.glyphs)
    index_bytes=len(records)*16
    portrait_bytes=len(portrait_paths)*60*60*2
    static_bytes=len(compiler.commands)*COMMAND.size
    return dict(kind='candidate_streamed_text_size_audit',shipping_package=False,record_layout='18 little-endian u32 fields/72B; relative byte offsets; u16 timeline padded4; pause3u32; CN/EN10B commands; LZ10 iff smaller',
        records=len(records),record_header_bytes=72*len(records),record_payload_raw_bytes=raw_total,record_payload_packed_bytes=packed_total,commands_raw_bytes=commands_total,timeline_bytes=time_total,pause_bytes=pause_total,
        directory_stride=16,directory_bytes=index_bytes,glyph_count=len(compiler.glyphs),glyph_bitmap_bytes=len(compiler.atlas),glyph_descriptors_bytes=8*len(compiler.glyphs),glyph_atlas_resident_bytes=atlas_bytes,
        glyph_dimensions={f'{w}x{h}':count for (w,h),count in Counter((w,h) for _,w,h in compiler.glyphs).items()},portrait_count=len(portrait_paths),portrait_raw_bytes=portrait_bytes,missing_portrait_attributes=dict(missing_portraits),
        static_ui_bytes=static_bytes,candidate_total_bytes=104+index_bytes+packed_total+atlas_bytes+portrait_bytes+static_bytes,max_active_record_raw_bytes=max(r['raw_bytes'] for r in records),max_active_record_packed_bytes=max(r['packed_bytes'] for r in records),
        compact_soa5_color_run_candidate=dict(raw_bytes=compact_raw_total,packed_bytes=compact_packed_total,total_bytes=104+index_bytes+compact_packed_total+atlas_bytes+portrait_bytes+static_bytes,max_stored_raw_bytes=compact_max_raw,max_stored_packed_bytes=compact_max_packed,max_expanded_active_bytes=max(r['raw_bytes'] for r in records),implicit_index_all_records_verified=True),
        chinese_glyphs=ir['summary']['cumulative_chinese_glyphs'],bilingual_added_english_glyphs=english_count,codecs=dict(codecs),cn_page_distribution=dict(pages_cn),bi_page_distribution=dict(pages_bi),
        all_record_raw_sha256=all_raw.hexdigest(),all_record_packed_sha256=all_packed.hexdigest(),records_detail=records,
        notes=['Exact byte counts for this candidate format, not a deployed package. No ROM/pack/runtime was changed.','Layout uses the confirmed12px/8px preview geometry and any exact matching original Asya side-image attribute; whoa/toubled stay unresolved.','72B block reserves final field for stable global_text_id; future speaker/header directory is not yet serialized and will add a small amount.','All35 original Asya PNG portraits budgeted as60x60 RGB15, including unused happy; no actual portrait outputs written.'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ir',type=Path,default=ROOT/'nds/artifacts/full_story_ir.json')
    parser.add_argument('--output',type=Path,default=ROOT/'nds/artifacts/full_story_text_sizes.json')
    args=parser.parse_args()
    report=measure(json.loads(args.ir.read_text(encoding='utf-8')))
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='records_detail'},ensure_ascii=False,indent=2))
