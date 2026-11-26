"""Actual-ROM dual-actor positions versus independent source-PNG pixels."""
from pathlib import Path
import argparse,ctypes,hashlib,json,struct,sys
import numpy as np
from verify_story_rom import NativeSession,ManualController,STATUS,NONE,atomic_json

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from story_gfx_oracle import Oracle


def run(args):
    digest=hashlib.sha256(args.rom.read_bytes()).hexdigest()
    assert digest==args.expected_sha,'immutable candidate ROM identity'
    oracle=Oracle();manifest=json.loads((ROOT/'artifacts/story_text_manifest.json').read_text(encoding='utf8'))
    data=(ROOT/'nitrofs/story/text.bin').read_bytes();assert hashlib.sha256(data).hexdigest()==manifest['sha256']
    rom=args.rom.read_bytes();fat,length=struct.unpack_from('<2I',rom,0x48)
    assert any(rom[a:b]==data for a,b in (struct.unpack_from('<2I',rom,at) for at in range(fat,fat+length,8)) if b-a==len(data)),'ROM text/host metadata mismatch'
    session=NativeSession(args);controller=ManualController()
    try:
        skip=False
        for _ in range(10000):
            state=session.step(('A',) if skip else ())
            if state['s']['ready']:break
            video=state.get('video');skip=skip or bool(video and video['ready'])
        else:raise AssertionError('boot timeout')
        before=state['input_vblanks']
        while state['input_vblanks']==before:state=session.step()
        address=session.status+STATUS.index('requested_chapter')*4
        ctypes.memmove(session.base+address-0x02000000,struct.pack('<I',1),4)
        for _ in range(15000):
            state=session.step();session.healthy(state)
            if state['s']['chapter']==1 and state['s']['requested_chapter']==NONE:break
        else:raise AssertionError('chapter request timeout')
        for _ in range(20000):
            session.healthy(state);session.cache_records(manifest);s=state['s'];tr=state['transition']
            if s['text_id']==577 and s['visible']==manifest['records'][577]['char_count'] and (not tr['duration'] or s['ticks']-tr['start']>=tr['duration']):break
            state=session.step(controller.keys(session,state))
        else:raise AssertionError(('source scene timeout',state))
        state=session.capture(args.image);session.healthy(state);layers=session.active_layers()
        expected=oracle.draw([(x['image'],x['placement'],x['program'],x['start']) for x in layers],state['s']['ticks'])
        rgb=np.stack((expected&31,(expected>>5)&31,(expected>>10)&31),axis=2)
        np.testing.assert_array_equal(np.asarray(session.emu.frame)[:192]>>3,rgb,err_msg='all 49152 native upper pixels versus independent source PNG oracle')
        actors=[]
        for layer in layers:
            cur,old,mix=oracle.sample(layer['image'],state['s']['ticks']-layer['start'])
            f=oracle.frames[cur]
            if f['category']!=2:continue
            x,y,z=oracle.position(cur,layer['placement'],oracle.programs[layer['program']],state['s']['ticks']-layer['start'])
            assert 48<=x+f['width']/2<=208,'actor canvas center outside approved interval'
            actors.append(dict(name=f['name'],placement=layer['placement'],x=x,width=f['width'],canvas_center=x+f['width']/2))
        assert {x['placement'] for x in actors}=={1,2},actors
        assert hashlib.sha256(args.rom.read_bytes()).hexdigest()==digest,'frozen ROM changed'
        report=dict(result='PASS',rom_sha256=digest,text_sha256=manifest['sha256'],source='ep1:48',say=577,actors=actors,layers=layers,
            placement_policy=oracle.manifest['placement_policy'],pixels=49152,image=str(args.image.resolve()),audio=state['a'],input_policy='Held A skips OP; only official requested_chapter hook plus ordinary complete-page A input. No flow/graphics state writes.')
        atomic_json(args.output,report);print(json.dumps(dict(result='PASS',actors=actors,pixels=49152),ensure_ascii=False),flush=True)
        return report
    finally:session.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--rom',type=Path,required=True);p.add_argument('--map',type=Path,required=True);p.add_argument('--expected-sha',required=True)
    p.add_argument('--output',type=Path,default=ROOT/'artifacts/story_actor_native_verification.json');p.add_argument('--image',type=Path,default=ROOT/'artifacts/story_actor_native.png')
    p.add_argument('--emulator-tools',type=Path,default=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools'));p.add_argument('--cpu-mode',default='interpreter',choices=['interpreter'])
    run(p.parse_args())

if __name__=='__main__':main()
