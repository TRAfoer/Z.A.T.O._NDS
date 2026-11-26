"""Build an independent real ARM9 movie player ROM, then record full AV evidence."""
from pathlib import Path
import argparse
import ctypes as C
import json
import re
import shutil
import struct
import subprocess
import sys
import wave
import numpy as np
from experiments.video_lab import convert_op as op

ROOT=op.ROOT
OUT=op.DEFAULT_OUTPUT/'player_probe'
MAIN=r'''
#include <nds.h>
#include <filesystem.h>
#include <stdint.h>
#include <maxmod9.h>
#include "audio_demo.h"
#include "story_video.h"
volatile struct {uint32_t magic,ready,frames,errors,vblanks,start_ticks,end_ticks,max_service_ticks,service_calls,hardware_position;} g_player_probe;
static void vblank(void){g_player_probe.vblanks++;g_player_probe.hardware_position=mmStreamGetPosition();}
static uint32_t ticks(void){uint16_t high=TIMER_DATA(3),low=TIMER_DATA(2);if(high!=TIMER_DATA(3)){high=TIMER_DATA(3);low=TIMER_DATA(2);}return (uint32_t)high<<16|low;}
int main(void){
 irqSet(IRQ_VBLANK,vblank);irqEnable(IRQ_VBLANK);
 videoSetMode(MODE_5_2D);videoSetModeSub(MODE_5_2D);vramSetBankC(VRAM_C_SUB_BG);
 int sub=bgInitSub(3,BgType_Bmp16,BgSize_B16_256x256,0,0);dmaFillHalfWords(0x8000,bgGetGfxPtr(sub),256*256*2);
 g_player_probe.magic=0x5250565a;
 if(!nitroFSInit(NULL)||!audio_demo_init()||!story_video_open("movie/op.zpv","movie/op.wav",1283072,2048)){g_player_probe.errors++;goto done;}
 TIMER_DATA(2)=TIMER_DATA(3)=0;TIMER_CR(3)=TIMER_ENABLE|TIMER_CASCADE;TIMER_CR(2)=TIMER_ENABLE|TIMER_DIV_1024;
 while(1){
   scanKeys();unsigned keys=keysDown();
   if(keys&(KEY_A|KEY_START)){story_video_close();g_player_probe.ready=2;break;}
   if(keys&KEY_L)story_video_pause(!g_story_video_status.paused);
   if(keys&KEY_RIGHT)story_video_seek(g_story_video_status.audio_frame+10*22050);
   if(keys&KEY_LEFT)story_video_seek(g_story_video_status.audio_frame>10*22050?g_story_video_status.audio_frame-10*22050:0);
   uint32_t start=ticks();story_video_service();uint32_t elapsed=ticks()-start;
   if(elapsed>g_player_probe.max_service_ticks)g_player_probe.max_service_ticks=elapsed;
   g_player_probe.service_calls++;
   swiWaitForVBlank();story_video_commit();g_player_probe.frames++;
   if(!g_player_probe.start_ticks&&g_story_video_status.audio_frame)g_player_probe.start_ticks=ticks();
   if(story_video_finished()){g_player_probe.end_ticks=ticks();g_player_probe.ready=1;break;}
   if(g_story_video_status.errors){g_player_probe.errors++;break;}
 }
done:
 while(1){audio_demo_service();swiWaitForVBlank();}
}
'''


def build(out,colors):
    probe=out/'probe';source=probe/'source';movie=probe/'nitrofs/movie';audio=probe/'nitrofs/audio'
    for folder in (source,movie,audio):folder.mkdir(parents=True,exist_ok=True)
    for name in ('audio_demo.c','audio_demo.h','ima_adpcm.c','ima_adpcm.h','story_video.c','story_video.h','asset_pack.c','asset_pack.h'):
        shutil.copyfile(ROOT/'nds/source'/name,source/name)
    (source/'main.c').write_text(MAIN,encoding='utf-8')
    candidate=op.DEFAULT_OUTPUT/('palette/refinement/op_palette64_f30.zpv' if colors==64 else 'palette/fallback48/op_palette48_f30.zpv')
    shutil.copyfile(candidate,movie/'op.zpv');shutil.copyfile(op.DEFAULT_OUTPUT/'op_soundtrack_ima.wav',movie/'op.wav')
    shutil.copyfile(ROOT/'nds/nitrofs/audio/index.bin',audio/'index.bin')
    (probe/'Makefile').write_text('''BLOCKSDS ?= /opt/wonderful/thirdparty/blocksds/core
NAME := zato_video_player_probe
GAME_TITLE := ZATO Video Player
GAME_SUBTITLE := Full native AV probe
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
    return probe/'zato_video_player_probe.nds'


def run(out,rom,scenario='full'):
    tools=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools');sys.path.insert(0,str(tools))
    from nds_emulator import Emulator,BATCH
    mapping=(rom.parent/'build/zato_video_player_probe.map').read_text()
    def offset(name):
        match=re.search(r'0x([0-9a-fA-F]+)\s+'+name+r'\s',mapping)
        if not match:raise ValueError(name)
        return int(match[1],16)-0x02000000
    vo=offset('g_story_video_status');ao=offset('g_audio_status');po=offset('g_player_probe')
    vf='magic active ready frame decoded displayed total_frames fps colors audio_frame paused seeking finished errors late_frames max_late_samples bytes reads commits source_hash requested_seek'.split()
    af='magic initialized rate music_count sfx_count music_index sfx_index music_playing sfx_playing music_starts sfx_starts callbacks music_underruns sfx_underruns errors music_frames sfx_frames music_loops sfx_finishes ui_version nonzero_samples output_peak'.split()
    pf='magic ready frames errors vblanks start_ticks end_ticks max_service_ticks service_calls hardware_position'.split()
    emu=Emulator(tools/'emulator/desmume_libretro.dll',rom);pcm=[]
    capture_frames=[0]
    def capture(data,n):pcm.append(C.string_at(data,n*4));capture_frames[0]+=n;return n
    callback=BATCH(capture);emu.core.retro_set_audio_sample_batch(callback)
    def state():
        data=emu.memory();return dict(video=dict(zip(vf,struct.unpack_from('<21I',data,vo))),audio=dict(zip(af,struct.unpack_from('<22I',data,ao))),probe=dict(zip(pf,struct.unpack_from('<10I',data,po))))
    trace=[];screens=[];actions=[];previous_display=None
    try:
        for frame in range(4300):
            emu.run(1);s=state()
            if frame%30==0:trace.append(dict(emulator_frame=emu.frame_count,capture_frames=capture_frames[0],**s))
            # libretro's video callback can precede the ARM9 commit in the same
            # retro_run. Capture on the held VBlank after publication, not the
            # first RAM snapshot naming a newly committed frame.
            if s['video']['frame'] in (0,436,873,1309,1745) and s['video']['frame']==previous_display and s['video']['frame'] not in screens and s['video']['active']:
                number=s['video']['frame'];screens.append(number);emu.frame.save(out/f'frame_{number:05}.png')
            previous_display=s['video']['frame']
            if scenario=='pause_seek' and not actions and s['video']['audio_frame']>2*22050:
                emu.press('L',held=5,settle=15);first=state();image=np.asarray(emu.frame).copy();pause_start=capture_frames[0]
                emu.run(60);last=state()
                assert first['video']['audio_frame']==last['video']['audio_frame'] and first['video']['frame']==last['video']['frame'],(first,last)
                assert np.array_equal(image,np.asarray(emu.frame))
                actions.append(dict(action='pause_60_vblanks',first=first,last=last,all_pixels_frozen=True,capture_start=pause_start,capture_end=capture_frames[0]))
                emu.press('L',held=5,settle=15);resumed=state();assert resumed['video']['audio_frame']>last['video']['audio_frame']
                before=resumed['video']['audio_frame'];emu.press('RIGHT',held=5,settle=1)
                for _ in range(160):
                    emu.run(1);seek=state()
                    if not seek['video']['seeking'] and seek['video']['audio_frame']>before+9*22050:break
                assert not seek['video']['seeking'] and seek['video']['audio_frame']>before+9*22050,seek
                actions.append(dict(action='seek_forward_10_seconds',before=before,after=seek))
            if scenario=='skip' and s['video']['audio_frame']>22050:
                emu.press('A',held=5,settle=5);s=state();assert not s['video']['active'] and s['probe']['ready']==2
                actions.append(dict(action='A_skip',after=s));break
            if s['probe']['ready'] or s['probe']['errors'] or s['video']['errors']:break
        if s['probe']['ready']==1:
            emu.run(2);emu.frame.save(out/f"frame_{s['video']['frame']:05}.png")
        report=dict(result='PASS' if s['probe']['ready']==(2 if scenario=='skip' else 1) and not s['video']['errors'] and not s['audio']['errors'] and not s['audio']['music_underruns'] else 'FAIL',
            rom=str(rom),rom_bytes=rom.stat().st_size,rom_sha256=op.sha256(rom),final=s,emulator_frames=emu.frame_count,
            max_service_ms=s['probe']['max_service_ticks']*1024*1000/33513982,trace=trace,scenario=scenario,actions=actions)
        raw=b''.join(pcm)
        with wave.open(str(out/'native_output.wav'),'wb') as wav:wav.setnchannels(2);wav.setsampwidth(2);wav.setframerate(44100);wav.writeframes(raw)
        values=np.frombuffer(raw,dtype='<i2').reshape(-1,2)
        for action in actions:
            if action['action']=='pause_60_vblanks':
                section=values[action['capture_start']:action['capture_end']]
                action['channel_peaks']=np.abs(section.astype(np.int32)).max(axis=0).tolist()
                assert not section.any(),'Paused output was not silent'
        report['audio_capture']=dict(frames=len(values),channel_peaks=np.abs(values.astype(np.int32)).max(axis=0).tolist(),different_channel_frames=int(np.count_nonzero(values[:,0]!=values[:,1])))
        (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps({k:v for k,v in report.items() if k!='trace'},indent=2))
        return report
    finally:emu.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=OUT);parser.add_argument('--colors',type=int,choices=(48,64),default=64);parser.add_argument('--run-only',action='store_true');parser.add_argument('--rom',type=Path);parser.add_argument('--scenario',choices=('full','pause_seek','skip'),default='full');args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    rom=args.rom or (args.output/'probe/zato_video_player_probe.nds' if args.run_only else build(args.output,args.colors))
    run(args.output,rom,args.scenario)


if __name__=='__main__':main()
