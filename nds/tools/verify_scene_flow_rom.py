"""Walk all278 source Say interactions in the shipping ROM, without skipping NW.

The only utility reused from Nds_UI_Test is its generic libretro frontend.
Ordinary dialogue advances only after its current page endpoint was committed
for two frames. Authored NW, soft waits, hard waits and transitions run naturally.
"""
from pathlib import Path
from copy import deepcopy
import argparse
import ctypes as C
import hashlib
import json
import struct
import sys
import time
import numpy as np
from PIL import Image,ImageDraw

ROOT=Path(__file__).resolve().parents[1]
from verify_scene_audio_rom import scene_fields,symbol_offset,find_map,AUDIO_FIELDS


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom',type=Path,default=ROOT/'zato_ep2d3_scene_nitrofs.nds')
    parser.add_argument('--map',type=Path)
    parser.add_argument('--tools',type=Path,default=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools'))
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/scene_flow_rom')
    parser.add_argument('--max-frames',type=int,default=120000)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    text=json.loads((ROOT/'artifacts/scene_text_manifest.json').read_text(encoding='utf-8'))
    script=json.loads((ROOT/'artifacts/scene_script_manifest.json').read_text(encoding='utf-8'))
    records={r['id']:r for r in text['text_records']}
    expected=[e['a'] for e in script['events'] if e['name']=='TEXT']
    assert len(expected)==278 and len(set(expected))==278
    sys.path.insert(0,str(ROOT/'tools'))
    from pack_scene_text import displays
    from anim_story import build_story,DEFAULT_GAME
    from anim_text import SceneText
    story=build_story()
    rows=displays(story)
    renderer=SceneText(DEFAULT_GAME,story['characters'])
    layouts={id:renderer.layout(display['text'],portrait=renderer.portrait(display) is not None) for id,(_,display,_,_) in enumerate(rows)}
    caps={}
    for id,layout in layouts.items():
        caps[id]=[layout[p+1][0]['index'] if p+1<len(layout) else records[id]['char_count'] for p in range(len(layout))]
    mapping_path=args.map or find_map(args.rom)
    mapping=mapping_path.read_text(encoding='utf-8')
    offset=symbol_offset(mapping,'g_scene_status')
    audio_offset=symbol_offset(mapping,'g_audio_status')
    fields=scene_fields((ROOT/'source/scene_runtime.h').read_text(encoding='utf-8'))
    request_offset=offset+fields.index('requested_checkpoint')*4
    sys.path.insert(0,str(args.tools))
    from nds_emulator import Emulator
    rom_data=args.rom.read_bytes()
    assert 0<len(rom_data)<64_000_000
    report=dict(result='RUNNING',rom=str(args.rom),rom_bytes=len(rom_data),rom_sha256=hashlib.sha256(rom_data).hexdigest(),scene_fields=fields,dialogue=[],screenshots=[],pixel_checks=0,upper_pixel_checks=0,groups=[],issues=[],limitation='Actual ROM/emulated hardware verification; physical NDS frame rate/card latency require the user test.')
    output=args.output/'verification.json'
    emu=Emulator(args.tools/'emulator/desmume_libretro.dll',args.rom)
    memory=emu.core.retro_get_memory_data(2)
    assert memory and emu.core.retro_get_memory_size(2)>=4*1024*1024
    def state(check=True):
        scene=dict(zip(fields,struct.unpack('<'+'I'*len(fields),C.string_at(memory+offset,4*len(fields)))))
        audio=dict(zip(AUDIO_FIELDS,struct.unpack('<22I',C.string_at(memory+audio_offset,88))))
        if check:
            assert scene['magic']==0x4353545a and scene['ready'] and not scene['errors'],scene
            assert audio['magic']==0x4455415a and audio['initialized'],audio
            assert not (audio['errors'] or audio['music_underruns'] or audio['sfx_underruns']),audio
        return scene,audio
    def persist():output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    def window_at(pc):
        window=True
        for event in script['events'][:pc]:
            if event['name']=='WINDOW':window=bool(event['a'])
            elif event['name']=='TEXT' and records[event['a']]['mode']!=3:window=True
        return window
    def check_lower(s):
        id=s['text_id']
        display=deepcopy(rows[id][1])
        display['revealed']=s['visible']>=records[id]['char_count']
        display['text'].visible=lambda elapsed:s['visible']
        image,_=renderer.lower(display,page=s['page'],bilingual=bool(s['bilingual']),window_visible=window_at(s['pc']))
        actual=np.asarray(emu.frame)[192:384,:256,:3]>>3
        expected_pixels=np.asarray(image)>>3
        mismatch=int(np.count_nonzero(np.any(actual!=expected_pixels,axis=2)))
        assert not mismatch,('lower pixels',mismatch,s)
        report['pixel_checks']+=1
    def snap(name,s):
        if name in captured:return
        path=args.output/(name+'.png')
        emu.frame.save(path)
        report['screenshots'].append(dict(name=name,path=str(path),state=dict(s)))
        captured.add(name)
    def check_upper_text(s,literal=False):
        if literal:
            id=text['literal_line_to_id']['690']
            image=renderer.upper_overlay(Image.new('RGB',(256,192)),literal=rows[id][1]['raw'])
        else:
            display=deepcopy(rows[s['text_id']][1])
            display['revealed']=s['visible']>=records[s['text_id']]['char_count']
            display['text'].visible=lambda elapsed:s['visible']
            image=renderer.upper_overlay(Image.new('RGB',(256,192)),display)
        actual=np.asarray(emu.frame)[:192,:256,:3]>>3
        expected_pixels=np.asarray(image)>>3
        mismatch=int(np.count_nonzero(np.any(actual!=expected_pixels,axis=2)))
        assert not mismatch,('upper text pixels',mismatch,s)
        report['upper_pixel_checks']+=1
    captured=set()
    seen=[]
    seen_info={}
    groups=set()
    previous_status=None
    stable=0
    last_scene_frame=None
    last_group=None
    group_age=0
    held=0
    release=0
    start=time.perf_counter()
    try:
        for _ in range(2400):
            emu.run(1)
            s,a=state(False)
            if s['ready']:break
        else:raise AssertionError(('boot timeout',s,a))
        state()
        C.c_uint32.from_address(memory+request_offset).value=0
        for _ in range(2400):
            emu.run(1);s,a=state()
            if s['checkpoint']==0 and s['source_line']>=240:break
        else:raise AssertionError('checkpoint0 request timeout')
        first_reads=s['gfx_reads']
        report['start']=dict(scene=s,audio=a)
        for iteration in range(args.max_frames):
            keys=('A',) if held else ()
            emu.run(1,keys)
            if held:held-=1
            elif release:release-=1
            s,a=state()
            assert s['gfx_reads']==first_reads,('unexpected graphics I/O',s)
            if s['frames']==last_scene_frame:continue
            last_scene_frame=s['frames']
            groups.add(s['group'])
            group_age=group_age+1 if last_group==s['group'] else 1
            last_group=s['group']
            identity=(s['text_id'],s['visible'],s['page'],s['pc'])
            stable=stable+1 if previous_status==identity else 1
            previous_status=identity
            id=s['text_id']
            if id in records and records[id]['kind']=='say':
                if id not in seen_info:
                    seen.append(id)
                    row=dict(id=id,source_line=records[id]['source_line'],first=dict(s),max_visible=s['visible'],pages_seen=[],page_endpoints=[],full_seen=False)
                    seen_info[id]=row;report['dialogue'].append(row)
                    assert seen==expected[:len(seen)],('source order',seen,expected[:len(seen)])
                    if len(seen)%20==0:
                        print(f'flow: {len(seen)}/278 text IDs, source {records[id]["source_line"]}, emu {emu.frame_count}, elapsed {time.perf_counter()-start:.1f}s',flush=True)
                        persist()
                row=seen_info[id]
                row['max_visible']=max(row['max_visible'],s['visible'])
                if s['page'] not in row['pages_seen']:row['pages_seen'].append(s['page'])
                if s['visible']==records[id]['char_count']:
                    if not row['full_seen']:row['full_state']=dict(s)
                    row['full_seen']=True
            active=script['events'][s['pc']-1] if 0<s['pc']<=len(script['events']) else {}
            if active.get('name')=='TEXT' and active['a']==id:
                r=records[id]
                page=min(s['page'],len(caps[id])-1)
                endpoint=s['visible']>=caps[id][page]
                if stable>=2 and endpoint:
                    if r['mode']!=3:
                        marker=(s['page'],s['visible'])
                        if marker not in checked.setdefault(id,set()):
                            check_lower(s);checked[id].add(marker)
                            seen_info[id]['page_endpoints'].append(dict(page=s['page'],visible=s['visible'],state_frame=s['frames']))
                    if r['source_line']==608:snap('asya_without_attribute',s)
                    if r['source_line']==564:snap('source564',s)
                    if r['source_line']==627:snap('asya_red_portrait',s)
                    if r['source_line']==709 and s['page']>0:snap('nvl_second_page',s)
                    if not active['flags'] and not held and not release:
                        held=2;release=2
                if r['mode']==3 and s['visible']==r['char_count'] and stable>=2:
                    if r['source_line']==574 and 'centered_keyboard_begin' not in captured:
                        check_upper_text(s);snap('centered_keyboard_begin',s)
                if r['mode']==3 and r['source_line']==601 and s['visible']>=r['new_char_start']+1 and stable>=2 and 'centered_extended_text' not in captured:
                    check_upper_text(s)
                    snap('centered_extended_text',s)
            if s['group']==script['gfx_ids']['mirror'] and group_age>=6:snap('mirror_scene',s)
            if s['group']==script['gfx_ids']['iss'] and s['group_frame']>=4 and group_age>=6:snap('iss_effect',s)
            if s['group']==script['gfx_ids']['window'] and s['group_frame']>=2 and group_age>=6:snap('window_animation',s)
            if active.get('name')=='WAIT' and active.get('line') in (691,692) and stable>=3 and 'literal_jammed_signal' not in captured:
                check_upper_text(s,literal=True);snap('literal_jammed_signal',s)
            if s['done']:
                report['final']=dict(scene=s,audio=a)
                break
        else:raise AssertionError(('full slice flow timeout',s,len(seen)))
        assert seen==expected,('missing Say IDs',[i for i in expected if i not in seen_info])
        incomplete=[dict(id=id,line=records[id]['source_line'],got=row['max_visible'],expected=records[id]['char_count']) for id,row in seen_info.items() if row['max_visible']!=records[id]['char_count']]
        assert not incomplete,('incomplete logical character counts',incomplete)
        assert groups==set(script['gfx_ids'].values()),('missing graphics group',groups,script['gfx_ids'])
        assert len(captured)>=8,('key screenshots missing',captured)
        contact_names=('source564','iss_effect','asya_red_portrait','mirror_scene','literal_jammed_signal','window_animation')
        assert set(contact_names)<=captured,('contact sheet views missing',captured)
        sheet=Image.new('RGB',(768,816),(20,20,20));draw=ImageDraw.Draw(sheet)
        for index,name in enumerate(contact_names):
            x=(index%3)*256;y=(index//3)*408
            draw.text((x+6,y+7),name,fill='white')
            with Image.open(args.output/(name+'.png')) as frame:sheet.paste(frame,(x,y+24))
        sheet.save(args.output/'contact_sheet.png')
        report['contact_sheet']=str(args.output/'contact_sheet.png')
        expected_sfx=sum(e['name']=='SFX' for e in script['events'])
        expected_music=sum(e['name']=='MUSIC' for e in script['events'])
        assert a['sfx_starts']-report['start']['audio']['sfx_starts']==expected_sfx,('SFX start count',a,expected_sfx)
        assert a['music_starts']-report['start']['audio']['music_starts']==expected_music,('music start count',a,expected_music)
        report.update(result='PASS',say_count=len(seen),full_counts=len(seen_info),groups=sorted(groups),emulator_frames=emu.frame_count,wall_seconds=time.perf_counter()-start,authored_sfx_starts=expected_sfx,authored_music_starts=expected_music,missed_vblanks=s['missed_vblanks']-report['start']['scene']['missed_vblanks'])
        print(f'PASS actual ROM flow: {len(seen)} Say IDs in order, all logical characters, {len(groups)} graphics groups, END, {report["pixel_checks"]} native lower pixel checks, {len(captured)} screenshots',flush=True)
    except Exception as error:
        report.update(result='FAIL',error=f'{type(error).__name__}: {error}',groups=sorted(groups),emulator_frames=emu.frame_count,wall_seconds=time.perf_counter()-start)
        try:
            s,a=state(False);report['failure_state']=dict(scene=s,audio=a);snap('failure',s)
        except Exception:pass
        raise
    finally:
        persist();emu.close()


checked={}
if __name__=='__main__':main()
