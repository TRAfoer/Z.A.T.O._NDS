"""Read-only actual-ROM font samples, physical inputs and host-C pixel proof.

Uses a new writable FAT image; never writes game RAM or uses savestates.
The host reader has already been checked against the independent BDF oracle.
"""
from pathlib import Path
import argparse, hashlib, json, os, shutil, struct, subprocess, sys, traceback
import numpy as np
import verify_story_scroll_cold as cold

ROOT = Path(__file__).resolve().parents[2]
RENDER = r'''
#include "story_text.h"
#include <stdio.h>
#include <stdlib.h>
void audio_demo_service(void){}
int main(int argc,char**argv){
 if(argc!=7||!story_text_open(argv[1]))return 1;
 unsigned id=strtoul(argv[2],0,10),visible=strtoul(argv[3],0,10),row=strtoul(argv[4],0,10),bi=strtoul(argv[5],0,10);
 if(!story_text_select(id,0))return 2;
 uint16_t pixels[49152];StoryTextViewport view;
 story_text_draw_lower_scroll(id,visible,row,bi,1,1,pixels,&view);
 FILE*f=fopen(argv[6],"wb");if(!f)return 3;
 int ok=fwrite(pixels,2,49152,f)==49152;fclose(f);story_text_close();return ok?0:4;
}
'''

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('rom','map','image','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--expected-sha',required=True)
    p.add_argument('--case',choices=('early','small'),required=True)
    p.add_argument('--core',type=Path,default=ROOT/'nds/artifacts/save_spike/desmume_fat_test.dll')
    p.add_argument('--emulator-tools',type=Path,default=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools'))
    p.add_argument('--text-pack',type=Path,default=ROOT/'nds/nitrofs/story/text.bin')
    p.add_argument('--text-manifest',type=Path,default=ROOT/'nds/artifacts/story_text_manifest.json')
    a=p.parse_args();a.cpu_mode='interpreter';a.language=1
    for name in ('rom','map','image','output','core','emulator_tools','text_pack','text_manifest'):setattr(a,name,getattr(a,name).resolve())
    assert Path(os.environ['ZATO_MPCF_IMAGE']).resolve()==a.image
    assert not a.output.exists() and not a.image.exists()
    assert hashlib.sha256(a.rom.read_bytes()).hexdigest()==a.expected_sha
    a.output.mkdir(parents=True);cold.create_image(a.image)
    source=a.output/'render.c';source.write_text(RENDER)
    exe=a.output/'render.exe'
    subprocess.run([shutil.which('gcc') or 'gcc','-std=c11','-O2','-Wall','-Wextra','-Werror','-I'+str(ROOT/'nds/source'),str(source),str(ROOT/'nds/source/story_text.c'),str(ROOT/'nds/source/asset_pack.c'),'-o',str(exe)],check=True,capture_output=True)
    probe=None;report=dict(result='RUNNING',rom_sha256=a.expected_sha,text_sha256=hashlib.sha256(a.text_pack.read_bytes()).hexdigest(),method='physical inputs, read-only RAM, isolated actual FAT; actual video compared with independently verified host-C renderer',checks=[])
    try:
        probe=cold.Probe(a);probe.boot_title();probe.configure_start()
        # The first public chapter store may be sampled while seek is still
        # clearing buffered input. Wait for actual post-seek source time before
        # issuing another chapter edge, so this probe cannot lose it in that
        # deliberate input clear.
        start_tick=probe.state['s']['ticks']
        probe.wait(lambda s:s['s']['ticks']>=start_tick+90)
        chapter=0 if a.case=='early' else 2
        key='LEFT' if chapter==0 else 'RIGHT'
        probe.edge(key,lambda s:s['s']['chapter']==chapter)
        probe.wait(lambda s:s['s']['chapter']==chapter and s['s']['blocked'] and s['s']['text_id']!=cold.NONE)
        for target in ((94,198) if a.case=='early' else (3048,)):
            probe.drive_to(target);probe.screenshot('text_'+str(target))
            state=probe.state;s=state['s'];assert s['text_id']==target and s['visible']==s['char_count']
            out=a.output/('text_'+str(target)+'.rgb15')
            subprocess.run([str(exe),str(a.text_pack),str(target),str(s['visible']),str(s['scroll']),str(s['bilingual']),str(out)],check=True,capture_output=True)
            words=np.frombuffer(out.read_bytes(),dtype='<u2').reshape(192,256)
            expected=np.stack((words&31,(words>>5)&31,(words>>10)&31),axis=2)
            actual=np.asarray(probe.n.emu.frame)[192:]>>3
            # Runtime composes the 12px quick-menu bar separately at y174.
            # The separate actual-ROM menu verifier checks that whole bar.
            np.testing.assert_array_equal(actual[:174],expected[:174],err_msg='actual lower story pixels differ at text '+str(target))
            record=probe.wire.get(target);heights=set()
            for command in record['commands_bi_cn']+record['commands_en']:
                gid=command['gid']
                if gid==65535:continue
                offset=probe.wire.h[9]+(gid&0x3fff)*8
                heights.add(struct.unpack_from('<H',probe.wire.data,offset+6)[0]&255)
            assert ({24,16} if a.case=='early' else {10,8})<=heights,(target,heights)
            report['checks'].append(dict(text_id=target,glyph_heights=sorted(heights),lower_body_pixels=44544,state=state,presentation=probe.entries[-1]))
        report.update(result='PASS',core_frames=probe.n.emu.frame_count)
    except BaseException as error:
        report.update(result='FAIL',error=str(error),traceback=traceback.format_exc(),state=probe.state if probe else None)
        raise
    finally:
        cold.native.atomic_json(a.output/'report.json',report)
        if probe:probe.n.close()
    print(json.dumps(dict(result=report['result'],samples=[r['text_id'] for r in report['checks']]),ensure_ascii=False))

if __name__=='__main__':main()
