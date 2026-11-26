"""Separate reflow-cold experiment; the existing exact-cold verifier is unchanged.

Physical keys, read-only RAM and fresh owned FAT images. Seed .sav bytes come
from a real prior ROM save. Reflow first ACK is derived independently from the
new wire timeline/caps and packed-mask viewport oracle. Resave is performed by
the NDS, followed by a separate process's exact same-key load.
"""
from pathlib import Path
import argparse,copy,hashlib,importlib.util,json,os,shutil,struct,traceback
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
cold=module('font_reflow_cold',ROOT/'nds/tools/verify_story_scroll_cold.py')
seed=module('font_reflow_seed',ROOT/'nds/tools/verify_story_audio_scroll.py')
oracle=module('font_reflow_pixels',ROOT/'nds/tools/story_scroll_pixel_audit.py')
def digest(data):return hashlib.sha256(data).hexdigest()

def expected(pack,cp,reflow):
    flow=cp['flow'];id=flow['text_id'];bi=bool(flow['flags']&(1<<15));f,cn,en,threshold,rows=pack.rows(id,bi)
    raw,_=pack.raw(id);visible=flow['visible'];page=flow['page'];row=cp['scroll'];manual=cp['scroll_manual'];covered=cp['scroll_covered']
    if reflow:
        anchor=covered+1 if covered<visible else max(visible,1)
        page=next((c['page']for c in reversed(cn)if c['index']<anchor),0)
        cursor=f[9];runs=struct.unpack_from('<H',raw,cursor)[0];clock=0;timeline=[]
        for i in range(runs):
            n,delta=struct.unpack_from('<HH',raw,cursor+2+i*4)
            for _ in range(n):clock+=delta;timeline.append(clock)
        assert len(timeline)==f[1]
        authored=f[1]if flow['flags']&(1<<11)else sum(t<=flow['local']for t in timeline)
        if not flow['flags']&(1<<11)and flow['pause_index']<f[3]:
            pause=struct.unpack_from('<3I',raw,f[10]+flow['pause_index']*12);authored=min(authored,pause[2])
        cap=struct.unpack_from('<H',raw,f[22]+page*2)[0]if bi and f[19]else min((c['index']for c in cn if c['page']>page),default=f[1])
        visible=min(authored,cap);covered=min(covered,visible);manual=0
        # Earliest independently rendered row origin which contains the logical
        # source anchor. No native for_visible/follow implementation is called.
        row=0
        if visible:
            target=anchor-1
            for origin in range(len(rows)):
                _,_,_,seen,_=pack.expected(f,rows,threshold,visible,origin)
                if target in seen:row=origin;break
            else:raise AssertionError(('anchor not drawable',anchor,visible))
    pixels,bounds,placed,_,_=pack.expected(f,rows,threshold,visible,row)
    if bounds[2]<=covered<bounds[3]:covered=bounds[3]
    return dict(ack=dict(commands=flow['commands'],text_id=id,page=page,visible=visible,local=flow['local'],ticks=flow['now']),
        view=dict(scroll=row,scroll_manual=manual,scroll_covered=covered),
        ranges=dict(zip(('cn_first','cn_end','en_first','en_end'),bounds[2:])),pixels=pixels,
        anchor=anchor if reflow else None,placed=placed)

class Probe(cold.Probe):
    def __init__(self,a):
        super().__init__(a);self.lower_address=self.n.symbols.section('lower','story_runtime')[0];self.first_lower=None
    def observe(self):
        had=self.first is not None;super().observe()
        if not had and self.first is not None:self.first_lower=self.n.read(self.lower_address,98304)
    def load_checked(self,pack,reflow,source):
        image_before=digest(self.args.image.read_bytes());saved,sav_before=cold.saved_slot(self.args.image,self.args.slot);cp=saved['checkpoint'];flow=cp['flow']
        assert sav_before==self.args.seed_sav.read_bytes(),'boot changed seeded save'
        if self.args.phase in ('reflow','unaffected'):
            assert cp['version']==2 and source['details']['saved']['checkpoint']['sha256']==cp['sha256']
            if reflow:assert source['identity']['rom_sha256']=='6aa5c9fff3a2e6b2a70f2c2b18bfbc0f4d8db71db019a52832289bba79f9b391'
        else:
            assert cp['version']==3 and cp['layout_key']==pack.h[2]
            assert source['details']['resaved']['saved']['checkpoint']['sha256']==cp['sha256']
            assert source['identity']['rom_sha256']==self.args.expected_sha
        want=expected(pack,cp,reflow);self.expected=saved
        self.edge('DOWN',lambda s:s['shell']['selection']==1);self.edge('A',lambda s:s['shell']['mode']==3)
        for selection in range(1,self.args.slot+1):self.edge('RIGHT',lambda s,n=selection:s['shell']['selection']==n)
        head=self.n.presentation_head;self.edge('A',lambda s:s['shell']['load_ops']or s['shell']['save_errors']);self.wait(lambda s:self.first is not None)
        assert self.state['shell']['load_ops']==1 and not self.state['shell']['save_errors'],self.state
        ack=self.first['ack'];assert ack['serial']>head and ack['flags']&1 and ack['flags']&16 and ack['flags']&32,ack
        assert bool(ack['flags']&8)==bool(flow['flags']&(1<<15))
        for k,v in want['ack'].items():assert ack[k]==v,('first source ACK',k,v,ack)
        for k,v in want['view'].items():assert ack[k]==v,('first reflow viewport',k,v,ack)
        for k,v in want['ranges'].items():assert ack[k]==v,('first reflow ranges',k,v,ack)
        if not reflow:
            source_ack=source['details']['source_ack']if self.args.phase=='unaffected'else source['details']['resaved']['source_ack']
            for k in ('commands','text_id','page','visible','local','ticks','scroll','scroll_manual','scroll_covered','cn_first','cn_end','en_first','en_end'):
                assert ack[k]==source_ack[k],('CP3 exact source presentation',k,ack,source_ack)
        status=self.first['state']['s']
        for k,field in [('pc','pc'),('chapter','chapter'),('source_line','line'),('pause_index','pause_index')]:assert status[k]==flow[field],(k,status,flow)
        layers=[{k:l[k]for k in ('image','tag','program','placement','start')}for l in self.first['layers']];assert layers==cp['layers']
        sound=cold.audio_matches(self.first['voices'],cp)
        actual=np.frombuffer(self.first_lower,dtype='<u2').reshape(192,256)
        # The bottom quick-menu bar is composed separately by runtime at y174.
        mismatch=int(np.count_nonzero(actual[:174]!=want['pixels'][:174]));assert mismatch==0,('new packed-mask first body pixels',mismatch)
        self.screenshot('first_loaded_after_two_commits')
        after,sav_after=cold.saved_slot(self.args.image,self.args.slot);assert sav_after==sav_before and after['checkpoint']['sha256']==cp['sha256']
        assert digest(self.args.image.read_bytes())==image_before,'load changed owned FAT'
        (self.args.output/'extracted_loaded.sav').write_bytes(sav_after)
        want.pop('pixels')
        return dict(saved=saved,expected=want,first_ack=self.first,pre_ack_observations=self.pre_ack,audio_checks=sound,
            first_body_pixel_mismatches=mismatch,first_body_sha256=digest(actual[:174].tobytes()),
            sav_unchanged_on_load=True,image_unchanged_on_load=True,
            audio_barrier_proof='First presentation flags32; directly sampled pre-ACK voices paused. Later sample counters are not claimed as exact audible cursors.')
    def resave(self):
        before=self.state;self.edge('START',lambda s:s['shell']['mode']==1 and s['shell']['active'])
        for selection in (1,2):self.edge('DOWN',lambda s,n=selection:s['shell']['selection']==n)
        self.edge('A',lambda s:s['shell']['mode']==2)
        for selection in range(1,self.args.resave_slot+1):self.edge('RIGHT',lambda s,n=selection:s['shell']['selection']==n)
        sav=cold.save_file(self.args.image)[1];at=24576+self.args.resave_slot*4096;assert not any(sav[at:at+4096]),'resave slot must be empty'
        writes=self.state['shell']['save_ops'];errors=self.state['shell']['save_errors'];self.edge('A',lambda s:s['shell']['save_ops']>writes or s['shell']['save_errors']>errors)
        assert self.state['shell']['save_errors']==errors;self.wait(lambda s:s['shell']['mode']==9)
        saved,sav=cold.saved_slot(self.args.image,self.args.resave_slot);cp=saved['checkpoint'];flow=cp['flow'];assert cp['version']==3 and cp['layout_key']==self.pack.h[2]
        keys=dict(commands=flow['commands'],text_id=flow['text_id'],page=flow['page'],visible=flow['visible'],local=flow['local'],ticks=flow['now'])
        source_ack=next((e for e in reversed(self.entries)if e['flags']&1 and all(e[k]==v for k,v in keys.items())),None);assert source_ack
        for k in ('scroll','scroll_manual','scroll_covered'):assert source_ack[k]==cp[k]
        assert flow['text_id']==before['s']['text_id'];self.screenshot('resaved_cp3_message');(self.args.output/'extracted_resaved.sav').write_bytes(sav)
        return dict(saved=saved,source_ack=source_ack,final=self.state)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('rom','map','image','output','seed-sav','source-report'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--expected-sha',required=True);p.add_argument('--phase',choices=('reflow','exact','unaffected'),required=True);p.add_argument('--slot',type=int,default=1);p.add_argument('--resave-slot',type=int,default=2)
    p.add_argument('--text-pack',type=Path,default=ROOT/'nds/nitrofs/story/text.bin');p.add_argument('--text-manifest',type=Path,default=ROOT/'nds/artifacts/story_text_manifest.json')
    a=p.parse_args();assert Path(os.environ['ZATO_MPCF_IMAGE']).resolve()==a.image.resolve();assert not a.image.exists()and not a.output.exists()
    assert digest(a.rom.read_bytes())==a.expected_sha;a.output.mkdir(parents=True);seed.seed_image(a.image,a.seed_sav)
    supplied_rom=a.rom;copy_rom=a.output.parent/'candidate_rom/ZATO_Story_Chain.nds';copy_rom.parent.mkdir(parents=True,exist_ok=True)
    if not copy_rom.exists():shutil.copyfile(a.rom,copy_rom)
    assert digest(copy_rom.read_bytes())==a.expected_sha;a.rom=copy_rom
    a.core=ROOT/'nds/artifacts/save_spike/desmume_fat_test.dll';a.emulator_tools=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools');a.cpu_mode='interpreter'
    pack=oracle.Pack.__new__(oracle.Pack);pack.data=a.text_pack.read_bytes();pack.manifest=json.loads(a.text_manifest.read_text(encoding='utf8'));pack.h=struct.unpack_from('<32I',pack.data,8);pack.glyphs={};pack.records=pack.manifest['records']
    from verify_scene_package import files
    assert files(a.rom)['story/text.bin']==pack.data,'oracle input differs from ROM text'
    source=json.loads(a.source_report.read_text(encoding='utf8'));assert source['result']=='PASS'
    identity=dict(rom=str(supplied_rom),rom_sha256=a.expected_sha,map_sha256=digest(a.map.read_bytes()),text_sha256=digest(pack.data),seed_sav_sha256=digest(a.seed_sav.read_bytes()),
        source_report_sha256=digest(a.source_report.read_bytes()),core_sha256=digest(a.core.read_bytes()),tool_sha256=digest(Path(__file__).read_bytes()),phase=a.phase)
    probe=None
    try:
        probe=Probe(a);probe.pack=pack;probe.boot_title();details=dict(load=probe.load_checked(pack,a.phase=='reflow',source))
        if a.phase=='reflow':details['resaved']=probe.resave()
        cold.native.atomic_json(a.output/'report.json',dict(result='PASS',identity=identity,details=details,image_sha256=digest(a.image.read_bytes())))
        print(json.dumps(dict(result='PASS',identity=identity,first_ack=probe.first['ack']),indent=2))
    except BaseException as error:
        cold.native.atomic_json(a.output/'report.json',dict(result='FAIL',identity=identity,error=str(error),traceback=traceback.format_exc(),state=probe.state if probe else None,first_ack=probe.first if probe else None));raise
    finally:
        if probe:probe.n.close()
if __name__=='__main__':main()
