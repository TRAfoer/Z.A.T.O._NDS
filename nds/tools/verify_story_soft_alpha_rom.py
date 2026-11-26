"""Natural CN OP-to-END run, with physical-L soft-alpha native pixel probes.

Imports the shared strict verifier unchanged. No RAM writes, synthetic clock,
source seeks, package mutation, or NDS rebuilds are performed.
"""
from pathlib import Path
import argparse,hashlib,json,sys,time
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'nds/tools'));sys.path.insert(0,str(ROOT/'nds/tests'))
import verify_story_rom as verifier
from story_gfx_oracle import Oracle

class SoftVerifier(verifier.FullVerifier):
    def __init__(self,*args):
        super().__init__(*args);self.oracle=Oracle();self.soft_checks=[];self.first_blur_age=None
        self.report['soft_alpha_pixel_checks']=self.soft_checks
        self.report['unused_soft_asset']=dict(frame_id=330,name='fbodgray',reason='No ordinary source SHOW or sequence dependency; covered by host source/C proof, not forced into native RAM.')
    def pause(self,state,enabled):
        assert bool(state['s']['paused'])!=enabled
        # A single real key-down edge is held through IRQ sampling until the
        # committed pause bit acknowledges it; release only after this ACK.
        for _ in range(180):
            state=self.session.step(('L',));self.observe(state)
            if bool(state['s']['paused'])==enabled:break
        else:raise AssertionError(('physical L was not acknowledged',enabled,state))
        start=state['input_vblanks']
        for _ in range(180):
            state=self.session.step();self.observe(state)
            if state['input_vblanks']-start>=3:return state
        raise AssertionError(('pause release IRQ timeout',state))
    def captures(self,state):
        s=state['s'];f=state['f']
        if s['special_group']!=verifier.NONE or f['literal_id']!=verifier.NONE or f['date_id']!=verifier.NONE or s['paused']:return state
        if s['text_id']==verifier.NONE or not f['blocked'] or self.text['records'][s['text_id']]['mode']==3:return state
        tr=state['transition']
        if tr['duration'] and s['ticks']-tr['start']<tr['duration']:return state
        if state['private_flow_pc']!=s['pc'] or state['private_flow_commands']!=s['commands']:return state
        layers=self.session.active_layers();selected={row['image'] for row in layers};label=None
        for id,name in ((150,'cinemablur'),(375,'gubw'),(407,'iugraygrad'),(459,'mubw')):
            key='soft_'+name
            if id in selected and key not in self.capture_keys:label=key;break
        blur=next((row for row in layers if row['image']==521),None)
        if blur:
            age=s['ticks']-blur['start']
            if 'soft_mublur_first' not in self.capture_keys:label='soft_mublur_first'
            elif self.first_blur_age is not None and age>=self.first_blur_age+72 and 'soft_mublur_loop' not in self.capture_keys:label='soft_mublur_loop'
        if label is None:return state
        state=self.pause(state,True)
        path=self.args.screens/(label+'.png');state=self.session.capture(path,self.observe)
        layers=self.session.active_layers();native=[(r['image'],r['placement'],r['program'],r['start']) for r in layers]
        expected=self.oracle.draw(native,state['s']['ticks'])
        expected_rgb=np.stack((expected&31,(expected>>5)&31,(expected>>10)&31),axis=2).astype('u1')
        actual=(np.asarray(self.session.emu.frame)[:192]>>3).astype('u1')
        if not np.array_equal(actual,expected_rgb):
            y,x=np.argwhere(np.any(actual!=expected_rgb,axis=2))[0]
            raise AssertionError(('actual soft upper pixels',label,int(x),int(y),actual[y,x].tolist(),expected_rgb[y,x].tolist(),state))
        self.capture_keys.add(label)
        row=dict(label=label,path=str(path),ticks=state['s']['ticks'],source_line=state['s']['source_line'],chapter=state['s']['chapter'],text_id=state['s']['text_id'],layers=layers,
            rgb15_pixel_comparison='PASS_49152_EXACT',rgb5_sha256=hashlib.sha256(actual.tobytes()).hexdigest(),paused=True,audio=state['a'])
        if blur:
            current=next(r for r in layers if r['image']==521);row['mublur_age_ticks']=state['s']['ticks']-current['start']
            if label=='soft_mublur_first':self.first_blur_age=row['mublur_age_ticks']
        self.soft_checks.append(row);self.report['screens'].append(row)
        print('SOFT_PIXEL_PASS '+label+' tick'+str(state['s']['ticks']),flush=True)
        state=self.pause(state,False);self.progress(state,True);return state
    def run(self):
        result=super().run()
        required={'soft_cinemablur','soft_gubw','soft_iugraygrad','soft_mubw','soft_mublur_first','soft_mublur_loop'}
        assert required.issubset(self.capture_keys),('unobserved source-reachable soft captures',sorted(required-self.capture_keys))
        result['soft_alpha_capture_result']='PASS'
        result['soft_alpha_capture_rule']='Actual L key pause ACK +3 real VBlank IRQs, stable same-frame screenshot, all49152 upper RGB15 pixels exactly equal original-based independent oracle; resume by L.'
        result['tools']={str(Path(__file__).relative_to(ROOT)):verifier.sha(Path(__file__)),'nds/tools/verify_story_rom.py':verifier.sha(ROOT/'nds/tools/verify_story_rom.py'),'nds/tests/story_gfx_oracle.py':verifier.sha(ROOT/'nds/tests/story_gfx_oracle.py')}
        verifier.atomic_json(self.args.output,result);return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom',type=Path,default=ROOT/'nds/zato_story_chain_menu_audio_soft_v1_nitrofs.nds')
    p.add_argument('--map',type=Path)
    p.add_argument('--core',type=Path)
    p.add_argument('--output',type=Path,default=ROOT/'nds/artifacts/story_soft_alpha_rom_verification.json')
    p.add_argument('--screens',type=Path,default=ROOT/'nds/artifacts/story_soft_alpha_rom_screens')
    p.add_argument('--emulator-tools',type=Path,default=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools'))
    args=p.parse_args()
    if args.map is None:args.map=ROOT/'nds/build'/(args.rom.stem+'.map')
    args.cpu_mode='interpreter';args.skip_preflight=True;args.require_presentations=True;args.bilingual=False;args.max_say=None;args.max_frames=1200000
    args.screens.mkdir(parents=True,exist_ok=True)
    script,text,gfx,walk,rom=verifier.load_inputs(args)
    assert rom['sha256']=='049ee586e2fe54b1f5869e67b9aef60a1308f65da68373457705be0ea88942f8',rom
    SoftVerifier(args,script,text,gfx,walk,rom,{'result':'SKIPPED','reason':'Dedicated natural CN alpha increment; existing full suite/preflight separate.'}).run()
if __name__=='__main__':main()
