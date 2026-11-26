"""Independent current-player NDS proof for the restored OP candidate.

Does not modify the production filesystem, original movie, root ROMs or generic
probe defaults. The isolated test ROM returns to Bmp16 color panels, proving
that both movie displays can be retired by the normal caller API.
"""
from __future__ import annotations
import argparse
import ctypes as C
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import time

import numpy as np
from PIL import Image,ImageDraw

from experiments.video_lab import run_player_probe as player
from experiments.video_lab import restore_op_text as restore
from experiments.video_lab import convert_palette as pal

OUT=restore.OUT
HANDOFF = '''done:
 for(int hold=0;hold<8;hold++){audio_demo_service();swiWaitForVBlank();}
 story_video_close();
 videoSetMode(MODE_5_2D);videoSetModeSub(MODE_5_2D);
 vramSetBankA(VRAM_A_MAIN_BG);vramSetBankC(VRAM_C_SUB_BG);
 int normal_main=bgInit(3,BgType_Bmp16,BgSize_B16_256x256,0,0);
 int normal_sub=bgInitSub(3,BgType_Bmp16,BgSize_B16_256x256,0,0);
 bgSetScale(normal_main,256,256);bgSetScale(normal_sub,256,256);
 bgSetScroll(normal_main,0,0);bgSetScroll(normal_sub,0,0);bgUpdate();
 dmaFillHalfWords(0x8000|RGB15(4,8,12),bgGetGfxPtr(normal_main),256*192*2);
 dmaFillHalfWords(0x8000|RGB15(12,8,4),bgGetGfxPtr(normal_sub),256*192*2);
 g_player_probe.ready+=10;
 while(1){audio_demo_service();swiWaitForVBlank();}
}'''


def compile_probe(out, movie_path=None):
    probe=out/'probe';source=probe/'source';movie=probe/'nitrofs/movie';audio=probe/'nitrofs/audio'
    for folder in (source,movie,audio):folder.mkdir(parents=True,exist_ok=True)
    for name in ('audio_demo.c','audio_demo.h','ima_adpcm.c','ima_adpcm.h','story_video.c','story_video.h','asset_pack.c','asset_pack.h'):
        shutil.copyfile(restore.ROOT/'nds/source'/name,source/name)
    # Current audio snapshots include shared story types. Keep their actual
    # header dependency closure instead of substituting old probe headers.
    for header in (restore.ROOT/'nds/source').glob('*.h'):
        shutil.copyfile(header,source/header.name)
    main=player.MAIN.replace('done:\n while(1){audio_demo_service();swiWaitForVBlank();}\n}',HANDOFF)
    assert main!=player.MAIN
    (source/'main.c').write_text(main,encoding='utf-8')
    shutil.copyfile(movie_path or OUT/'op_text_restored.zpv',movie/'op.zpv')
    shutil.copyfile(pal.op.DEFAULT_OUTPUT/'op_soundtrack_ima.wav',movie/'op.wav')
    shutil.copyfile(restore.ROOT/'nds/nitrofs/audio/index.bin',audio/'index.bin')
    (probe/'Makefile').write_text('''BLOCKSDS ?= /opt/wonderful/thirdparty/blocksds/core
NAME := zato_op_text_optimized_test
GAME_TITLE := ZATO OP Clear Text
GAME_SUBTITLE := Independent Video Test
GAME_AUTHOR := ZATO NDS
SOURCEDIRS := source
INCLUDEDIRS := source
NITROFSDIR := nitrofs
DEFINES := -DZATO_NITROFS=1
LIBS := -lnds9 -lmm9
LIBDIRS := $(BLOCKSDS)/libs/maxmod
CFLAGS := -std=gnu11 -Wextra
include $(BLOCKSDS)/sys/default_makefiles/rom_arm9/Makefile
NDSTOOL_ARGS += -h 0x200
''',encoding='utf-8')
    result=subprocess.run(['C:/msys64/opt/wonderful/wonderful_shell.cmd','-defterm','-no-start','-here','-c','make -j4'],cwd=probe,capture_output=True,text=True)
    (probe/'build.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    if result.returncode:raise RuntimeError((result.stdout+result.stderr)[-5000:])
    rom=probe/'zato_op_text_optimized_test.nds'
    if movie_path is None:
        shutil.copyfile(rom,OUT/'ZATO_OP_Text_Optimized_Test.nds')
    return rom


def run(out,rom,scenario,baseline=False):
    tools=Path(r'C:/DeskBox/Source/Nds_UI_Test/tools');sys.path.insert(0,str(tools))
    from nds_emulator import Emulator,BATCH
    mapping=(rom.parent/'build/zato_op_text_optimized_test.map').read_text()
    def offset(name):
        match=re.search(r'0x([0-9a-fA-F]+)\s+'+name+r'\s',mapping)
        if not match:raise ValueError(name)
        return int(match[1],16)-0x02000000
    vo,ao,po=map(offset,('g_story_video_status','g_audio_status','g_player_probe'))
    vf='magic active ready frame decoded displayed total_frames fps colors audio_frame paused seeking finished errors late_frames max_late_samples bytes reads commits source_hash requested_seek'.split()
    af='magic initialized rate music_count sfx_count music_index sfx_index music_playing sfx_playing music_starts sfx_starts callbacks music_underruns sfx_underruns errors music_frames sfx_frames music_loops sfx_finishes ui_version nonzero_samples output_peak'.split()
    pf='magic ready frames errors vblanks start_ticks end_ticks max_service_ticks service_calls hardware_position'.split()
    report=json.loads((OUT/'report.json').read_text(encoding='utf8'));decoded=np.load(OUT/'decoded_rgb.npy',mmap_mode='r')
    wanted={min(c['end']-1,c['start']+max(5,(c['end']-c['start'])//2)):c['id'] for c in report['cues']}
    wanted[1745]='last_frame'
    out.mkdir(parents=True,exist_ok=True)
    emu=Emulator(tools/'emulator/desmume_libretro.dll',rom)
    seen=set();captures=[];capture_numbers={};last=None;trace=[];late_events=[];last_late=0;audio_stats=[0,0,0]
    def capture_audio(data,n):
        samples=np.frombuffer(C.string_at(data,n*4),dtype='<i2').reshape(-1,2)
        audio_stats[0]+=n;audio_stats[1]+=int(np.count_nonzero(samples[:,0]!=samples[:,1]));audio_stats[2]=max(audio_stats[2],int(np.max(np.abs(samples.astype(np.int32)))))
        return n
    callback=BATCH(capture_audio);emu.core.retro_set_audio_sample_batch(callback)
    def state():
        data=emu.memory()
        return dict(video=dict(zip(vf,struct.unpack_from('<21I',data,vo))),
                    audio=dict(zip(af,struct.unpack_from('<22I',data,ao))),
                    probe=dict(zip(pf,struct.unpack_from('<10I',data,po))))
    skipped=False;started=time.monotonic()
    try:
        for n in range(4600):
            emu.run(1);s=state();v=s['video'];a=s['audio']
            assert not v['errors'] and not a['errors'] and not s['probe']['errors'],s
            if v['displayed'] and v['active']:
                assert not np.asarray(emu.frame)[192:].any(),'OP lower display must remain black'
            if v['displayed']:seen.add(v['frame'])
            number=v['frame']
            if v['late_frames']>last_late:
                late_events.append(dict(emulator_frame=emu.frame_count,**v));last_late=v['late_frames']
            name=wanted.get(number)
            if name is None:
                # A fast cut may occupy only one observed VBlank at its exact
                # midpoint. Capture any held, visibly established cue picture.
                name=next((c['id'] for c in report['cues'] if c['start']+min(5,(c['end']-c['start'])//3)<=number<c['end'] and c['id'] not in captures),None)
            if name is not None and number==last and v['active'] and name not in captures:
                actual=np.asarray(emu.frame)
                expected=restore.dual(decoded[number])
                if not baseline:
                    np.testing.assert_array_equal(actual>>3,expected>>3,err_msg=f'Native exact dual RGB555 frame{number}')
                assert not actual[192:].any(),'OP lower display must remain black'
                emu.frame.save(out/f'{name}_native.png');captures.append(name);capture_numbers[name]=number
            last=number
            if scenario=='skip' and not skipped and v['audio_frame']>22050:
                emu.press('A',held=1,settle=0);skipped=True
            if n%120==0:trace.append(dict(emulator_frame=emu.frame_count,**s))
            if n and n%600==0:print(f'{scenario}: {number}/1746; errors 0; underruns {a["music_underruns"]}',flush=True)
            if s['probe']['ready']>=10:break
        (out/'raw_observation.json').write_text(json.dumps(dict(final=s,seen_frame_count=len(seen),captures=capture_numbers,late_events=late_events),indent=2)+'\n',encoding='utf8')
        assert s['probe']['ready']==(12 if scenario=='skip' else 11),s
        assert not a['music_underruns'] and not a['sfx_underruns'],s
        assert not v['active'] and not a['music_playing'],s
        emu.run(3);actual=np.asarray(emu.frame)>>3
        assert np.all(actual[:192]==(4,8,12)) and np.all(actual[192:]==(12,8,4)),'Bmp16 display restoration'
        emu.frame.save(out/'bmp16_handoff.png')
        if scenario=='full':
            assert seen==set(range(1746)),f'Missing movie pictures: {set(range(1746))-seen}'
            assert v['displayed']==1746 and v['decoded']==1746,s
            assert a['music_frames']==1283072,s
            assert set(captures)==set(wanted.values()),(captures,wanted)
        result=dict(result='PASS' if not v['late_frames'] else 'LATE_FRAMES',scenario=scenario,rom=str(rom),rom_sha256=pal.op.sha256(rom),rom_bytes=rom.stat().st_size,
                    final=s,seen_frame_count=len(seen),native_pixel_captures=captures,native_capture_frames=capture_numbers,both_screens_restored_to_bmp16=True,
                    lower_screen_black_throughout=True,
                    max_service_ms=s['probe']['max_service_ticks']*1024*1000/33513982,
                    audio_output_frames=audio_stats[0],stereo_difference_frames=audio_stats[1],audio_peak=audio_stats[2],
                    wall_seconds=time.monotonic()-started,trace=trace,late_events=late_events)
        (out/'verification.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
        print(json.dumps({k:v for k,v in result.items() if k not in ('trace','late_events')},indent=2),flush=True)
        return result
    finally:emu.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--baseline',action='store_true')
    parser.add_argument('--scenario',choices=('full','skip'));args=parser.parse_args()
    out=OUT/('native_baseline' if args.baseline else 'native_player')
    if args.scenario:
        run(out/args.scenario,out/'probe/zato_op_text_optimized_test.nds',args.scenario,baseline=args.baseline)
        return
    rom=compile_probe(out,restore.ROOT/'nds/storyfs/movie/op.zpv' if args.baseline else None)
    command=[sys.executable,'-m','experiments.video_lab.verify_restored_op']
    # The libretro core cannot be reinitialized safely inside one interpreter.
    subprocess.run(command+['--scenario','full']+(['--baseline'] if args.baseline else []),cwd=restore.ROOT,check=True)
    if args.baseline:return
    subprocess.run(command+['--scenario','skip'],cwd=restore.ROOT,check=True)
    full=json.loads((out/'full/verification.json').read_text(encoding='utf8'))
    skip=json.loads((out/'skip/verification.json').read_text(encoding='utf8'))
    report=json.loads((OUT/'report.json').read_text(encoding='utf8'))
    report.update(status='VERIFIED' if full['result']=='PASS' else 'LATE_FRAMES',native_full=str(out/'full/verification.json'),native_skip=str(out/'skip/verification.json'),
                  test_rom=str(OUT/'ZATO_OP_Text_Optimized_Test.nds'),test_rom_bytes=rom.stat().st_size,
                  native_frame_count=full['seen_frame_count'],late_frames=full['final']['video']['late_frames'])
    (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


if __name__=='__main__':main()
