"""Committed actual menu_final1 UI in original no-FAT and isolated FAT cores.

All native actions are physical keys; main RAM is read-only. This supplements,
but does not replace, the root's independent cold-start/full-story save proof.
"""
from pathlib import Path
import argparse,hashlib,json,os,struct,subprocess,sys,time,zlib,zipfile
import numpy as np
from PIL import Image
from verify_story_rom import ManualController,atomic_json,NONE
from verify_story_menu_rom import Probe as InitialProbe,SHELL,rgb15,title_expected,title_lower_expected
ROOT=Path(__file__).resolve().parents[1]
SPIKE=ROOT/'artifacts/save_spike'
sys.path.insert(0,str(ROOT/'tools'))
from fat16 import create,files

PRODUCT=['story_menu.c','story_menu.h','story_shell.c','story_shell.h','story_runtime.c','story_runtime.h','story_save.c','story_save.h','story_checkpoint.c','story_checkpoint.h']
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def product_hashes():return {n:digest(ROOT/'source'/n) for n in PRODUCT}
def configure(args):
    sys.path.insert(0,str(args.emulator_tools));import nds_emulator
    base=nds_emulator.Emulator
    class Patched(base):
        BUTTONS={**base.BUTTONS,'Y':1}
        def __init__(self,core,rom):super().__init__(args.core if args.case=='fat' else core,rom)
    nds_emulator.Emulator=Patched
    if args.case=='fat':os.environ['ZATO_MPCF_IMAGE']=str(args.image.resolve())
    else:os.environ.pop('ZATO_MPCF_IMAGE',None)

class MenuProbe(InitialProbe):
    def __init__(self,args):
        configure(args);super().__init__(args);self.products=product_hashes();self.source_menu=json.loads((ROOT/'artifacts/story_menu_manifest.json').read_text(encoding='utf8'))
        self.text=json.loads((ROOT/'artifacts/story_text_manifest.json').read_text(encoding='utf8'))
        self.ui,self.ui_size=self.session.symbols.section('menu','story_shell');assert self.ui_size==332,('native UI ABI',self.ui_size)
        self.upper,_=self.session.symbols.section('upper','story_runtime');self.lower,_=self.session.symbols.section('lower','story_runtime')
        self.dirty=[self.session.symbols.section(n,'story_runtime')[0] for n in ['upper_dirty','lower_dirty']]
        self.menu_valid=self.session.symbols.section('menu_valid','story_runtime')[0];self.menu_version=self.session.symbols.section('menu_version','story_runtime')[0]
        self.report.update(case=args.case,source_sha256=self.products,checks=[],static_cache=[],fat_snapshots=[],physical_buttons='Y mapped to libretro button1; held until IRQ/committed UI acknowledgement, released for2 IRQs')
        self.report.update(core_path=self.session.emu.core_path.decode(),core_sha256=digest(Path(self.session.emu.core_path.decode())),fat_environment=os.environ.get('ZATO_MPCF_IMAGE'))
    def shell(self):return dict(zip(SHELL+['title_visits'],struct.unpack('<14I',self.session.read(self.shell_address,56))))
    def wait(self,predicate,limit=12000):
        for _ in range(limit):
            if predicate():return
            self.step()
        raise AssertionError(('native wait timeout',self.shell(),self.session.last))
    def press(self,key,predicate=None):
        before=self.session.last['input_vblanks']
        for _ in range(4000):
            sh=self.step((key,))
            accepted=predicate(sh) if predicate else self.session.last['input_vblanks']>=before+3
            if accepted:
                self.report['actions'].append(dict(key=key,shell=sh));return self.release()
        raise AssertionError(('held input not acknowledged',key,self.shell(),self.session.last))
    def committed(self):
        self.wait(lambda:self.session.last['s']['ready']==1)
        start=self.session.last['s']['frames'];self.wait(lambda:self.session.last['s']['frames']>=start+2)
        return start
    def capture(self,label):
        start=self.committed();path=self.args.screens/(label+'.png');self.session.capture(path)
        end=self.session.last['s']['frames'];assert end>=start+2,(label,start,end)
        self.report['captures'].append(dict(name=label,path=str(path.resolve()),sha256=digest(path),shell=self.shell(),commits_before=start,commits_after=end,state=self.session.last['s'],audio=self.session.last['a']))
        return np.asarray(self.session.emu.frame).copy()
    def check(self,name,**values):self.report['checks'].append(dict(name=name,result='PASS',**values));print(f'{self.args.case}: PASS {name}',flush=True)
    def pixels(self,frame,upper=True):
        a=(frame[:192] if upper else frame[192:])>>3
        return (0x8000|a[:,:,0].astype(np.uint16)|a[:,:,1].astype(np.uint16)<<5|a[:,:,2].astype(np.uint16)<<10)
    def prefs(self):
        raw=self.session.read(self.ui,40);sh=self.shell()
        assert raw[0]==sh['mode'] and struct.unpack_from('<I',raw,4)[0]==sh['selection'] and struct.unpack_from('<I',raw,8)[0]==sh['page'],'bound private UI prefix'
        return dict(bilingual=bool(raw[21]),music=struct.unpack_from('<I',raw,24)[0],sfx=struct.unpack_from('<I',raw,28)[0])
    def font(self,size):
        if not hasattr(self,'fonts'):self.fonts={}
        if size not in self.fonts:
            sys.path.insert(0,str(ROOT.parent/'experiments/font_lab'));from font_core import parse_bdf
            meta=json.loads((ROOT.parent/'experiments/font_lab/out/analysis.json').read_text(encoding='utf8'))
            with zipfile.ZipFile(meta['font_archive']) as z:self.fonts[size]=parse_bdf(z.read(f'fusion-pixel-{size}px-monospaced-zh_hans.bdf'))
        return self.fonts[size]
    def message_expected(self,prompt,detail=''):
        out=np.full((192,256),0x8000,dtype=np.uint16);out[48,12:244]=out[155,12:244]=0xffff;out[48:156,12]=out[48:156,243]=0xffff
        for value,font,y,color in [(prompt,12,66,0xffff),(detail,8,108,0xc631)]:
            f=self.font(font);x=24
            for c in value:
                if c=='\n':x=24;y+=14;continue
                advance=f.glyphs[ord(c)]['advance']
                if x>24 and x+advance>232:x=24;y+=14
                mask=f.bitmap(ord(c)).astype(bool);out[y:y+font,x:x+font][mask]=color;x+=advance
        return out
    def static_cache(self,label):
        self.committed();start=self.session.last['s']['frames'];samples=[]
        for _ in range(14):
            frame=self.session.last['s']['frames'];self.wait(lambda:self.session.last['s']['frames']>frame)
            row=dict(frame=self.session.last['s']['frames'],version=self.session.last['s']['gfx_version'],dirty=[self.session.read(a,1)[0] for a in self.dirty],valid=self.session.read(self.menu_valid,1)[0],
                upper=digest_bytes(self.session.read(self.upper,98304)),lower=digest_bytes(self.session.read(self.lower,98304)))
            samples.append(row)
        assert all(not any(x['dirty']) and x['valid'] for x in samples),(label,samples)
        assert len({(x['version'],x['upper'],x['lower']) for x in samples})==1,(label,samples)
        self.report['static_cache'].append(dict(name=label,frames_start=start,frames_end=samples[-1]['frame'],samples=samples,interpretation='14 distinct real commits with dirty=false, valid cache and identical framebuffer hashes; frozen source gates drawing/DMA on these flags.'))
        self.check(label+' static cache',commits=len(samples))
    def new_game(self):
        self.close_to_title();self.select(0);self.press('A',lambda sh:not sh['active'])
        self.wait(lambda:self.session.last['s']['blocked'] and self.session.last['s']['text_id']!=NONE and self.session.last['s']['visible']>=self.session.last['s']['char_count'] and self.session.last['s']['char_count']>0 and self.session.last['s']['special_group']==NONE and self.session.last['s']['ticks']-self.session.last['s']['transition_start']>=self.session.last['s']['transition_duration'])
        assert not self.session.last['s']['errors']
        self.capture('first_say_complete');self.press('START',lambda sh:sh['active'] and sh['mode']==1)
        return self.capture('game_frozen_upper')
    def settings_checks(self,game=None):
        if game is None:self.open_title_item(3,5)
        else:self.select(4);self.press('A',lambda sh:sh['mode']==5)
        before=self.prefs();assert before==dict(bilingual=False,music=100,sfx=100),before
        self.capture('settings_default');self.check('default in-RAM CN/music100/sfx100',**before)
        self.press('RIGHT',lambda sh:self.prefs()['bilingual']);assert self.prefs()['bilingual']
        self.press('DOWN',lambda sh:sh['selection']==1);self.press('LEFT',lambda sh:self.prefs()['music']==90)
        self.press('DOWN',lambda sh:sh['selection']==2);self.press('LEFT',lambda sh:self.prefs()['sfx']==90)
        image=self.capture('settings_BI_volume90')
        if game is not None:
            np.testing.assert_array_equal(image[:192],game[:192],err_msg='settings full frozen game upper')
            self.check('Settings full frozen game upper',pixels=49152)
        if game is not None:self.static_cache('game settings')
        self.check('CN/BI and both volume controls',**self.prefs())
        self.press('RIGHT',lambda sh:self.prefs()['sfx']==100);self.press('UP',lambda sh:sh['selection']==1);self.press('RIGHT',lambda sh:self.prefs()['music']==100)
        self.press('UP',lambda sh:sh['selection']==0);self.press('LEFT',lambda sh:not self.prefs()['bilingual'])
        self.press('B',lambda sh:sh['mode']==(1 if game is not None else 0))
    def sav(self,label):
        values=files(self.args.image);candidates=[(k,v) for k,v in values.items() if k.lower().endswith('.sav')]
        assert len(candidates)==1 and len(candidates[0][1])==524288,(label,{k:len(v) for k,v in values.items()})
        name,sav=candidates[0]
        for at in [0,8192]:assert sav[at:at+8]==b'ZATSAV01' and zlib.crc32(sav[at:at+8188])==struct.unpack_from('<I',sav,at+8188)[0]
        self.report['fat_snapshots'].append(dict(name=label,file=name,bytes=len(sav),sha256=digest_bytes(sav)))
        return sav
    def slot(self,label):
        sav=self.sav(label);raw=bytearray(sav[24576:28672]);saved=struct.unpack_from('<I',raw,252)[0];raw[252:256]=bytes(4)
        assert raw[:8]==b'ZATSLOT1' and zlib.crc32(raw)==saved
        row=dict(seq=struct.unpack_from('<I',raw,48)[0],valid=bool(struct.unpack_from('<I',raw,52)[0]),text_id=struct.unpack_from('<I',raw,68)[0],page=struct.unpack_from('<I',raw,72)[0],thumb=bytes(raw[1024:]),sav=sav)
        assert bytes(raw[12:44]).hex()==self.source_menu['story_identity_sha256']
        return row
    def saved_picture(self,label):
        image=self.capture(label);np.testing.assert_array_equal(self.pixels(image,False),self.message_expected('存档完成。'),err_msg='committed Saved modal from original BDF masks')
        slot=self.slot(label);assert slot['valid'];thumb=np.frombuffer(slot['thumb'],dtype=np.uint8).reshape(48,64).astype(np.uint16)
        expected=0x8000|(((thumb>>5)*31+3)//7)|((((thumb>>2)&7)*31+3)//7)<<5|(((thumb&3)*31+1)//3)<<10
        np.testing.assert_array_equal(self.pixels(image)[10:154,32:224],expected.repeat(3,0).repeat(3,1),err_msg='Saved modal selected full4:3 actual on-disk RGB332 preview')
        self.check(label+' actual Saved/preview pixels',lower_pixels=49152,preview_pixels=27648,slot_seq=slot['seq'])
        return slot
    def readonly_case(self):
        self.open_title_item(1,9);image=self.capture('no_FAT_load_error')
        np.testing.assert_array_equal(self.pixels(image,False),self.message_expected('操作失败。','Writable storage is unavailable'))
        self.check('no-FAT real error dialog',pixels=49152);self.press('B',lambda sh:sh['mode']==3);self.press('B',lambda sh:sh['mode']==0)
        self.settings_checks();self.open_title_item(4,6);self.capture('credits_title');self.static_cache('credits');self.close_to_title()
        self.open_title_item(5,7);self.capture('controls_title');self.static_cache('controls');self.close_to_title()
        game=self.new_game();self.settings_checks(game);assert self.session.last['s']['errors']==0
        self.select(2);self.press('A',lambda sh:sh['mode']==9);image=self.capture('no_FAT_save_error')
        np.testing.assert_array_equal(self.pixels(image,False),self.message_expected('操作失败。','Writable storage is unavailable'))
        assert self.shell()['save_ops']==0 and self.shell()['load_ops']==0;self.check('no-FAT refuses save with actual error dialog',pixels=49152)
    def minimal_readonly(self):
        self.open_title_item(1,9);image=self.capture('no_FAT_load_error')
        np.testing.assert_array_equal(self.pixels(image,False),self.message_expected('操作失败。','Writable storage is unavailable'))
        assert self.shell()['save_ops']==0 and self.shell()['load_ops']==0;self.check('no-FAT actual refusal dialog',pixels=49152)
        self.press('B',lambda sh:sh['mode']==3);self.press('B',lambda sh:sh['mode']==0);self.open_title_item(3,5)
        assert self.prefs()==dict(bilingual=False,music=100,sfx=100);self.capture('no_FAT_default_CN_100');self.check('default no-FAT preferences usable in RAM',**self.prefs())
    def minimal_fat(self):
        self.new_game();self.press('B',lambda sh:not sh['active'])
        self.press('RIGHT',lambda sh:self.session.last['s']['chapter']==1)
        controller=ManualController()
        for _ in range(12000):
            state=self.session.last;self.session.cache_records(self.text)
            if state['s']['text_id']==577 and state['s']['visible']>=state['s']['char_count'] and state['s']['blocked'] and state['s']['ticks']-state['s']['transition_start']>=state['s']['transition_duration']:break
            self.step(controller.keys(self.session,state))
        else:raise AssertionError(('actual dual-actor source577 timeout',self.session.last))
        source=self.capture('dual_actor_source577');assert np.count_nonzero(source[:192])>10000
        gfx=json.loads((ROOT/'artifacts/story_gfx_manifest.json').read_text(encoding='utf8'));ids={gfx['name_to_image_id'][n] for n in ['gupanic','iuglare']}
        actors=[x for x in self.session.active_layers() if x['image'] in ids];assert len(actors)==2 and {x['placement'] for x in actors}=={1,2},actors
        self.check('actual source577 dual actors',actors=actors,input='Physical RIGHT diagnostic chapter key before saving, then natural source A navigation. Not used to reconstruct a load.')
        self.press('START',lambda sh:sh['active'] and sh['mode']==1);game=self.capture('dual_actor_game_frozen')
        np.testing.assert_array_equal(game[:192],source[:192],err_msg='whole actual scene577 retained upon game menu entry')
        frozen_tick=self.session.last['s']['ticks'];self.settings_checks(game);assert self.session.last['s']['ticks']==frozen_tick
        self.select(2);self.press('A',lambda sh:sh['mode']==2);self.capture('dual_actor_save_empty');self.static_cache('dual actor save grid')
        self.press('A',lambda sh:sh['mode']==9 and sh['save_ops']==1);saved=self.saved_picture('dual_actor_saved_real')
        assert saved['text_id']==577
        original=((game[:192]>>3).astype(np.uint16).reshape(48,4,64,4,3).sum(axis=(1,3))+8)//16
        expected=((original[:,:,0]>>2)<<5)|((original[:,:,1]>>2)<<2)|(original[:,:,2]>>3)
        actual=np.frombuffer(saved['thumb'],dtype=np.uint8).reshape(48,64);np.testing.assert_array_equal(actual,expected)
        assert np.count_nonzero(actual)>500
        self.check('nonblack dual actors on-disk full4:3 BOX thumbnail',pixels=3072,nonzero=int(np.count_nonzero(actual)),saved_text_id=saved['text_id'])
    def fat_case(self):
        game=self.new_game();frozen_tick=self.session.last['s']['ticks'];self.settings_checks(game);assert self.session.last['s']['ticks']==frozen_tick
        self.select(2);self.press('A',lambda sh:sh['mode']==2);self.capture('manual_save_empty');self.static_cache('save_grid')
        self.press('A',lambda sh:sh['mode']==9 and sh['save_ops']==1);first=self.saved_picture('saved_first_real')
        original=((game[:192]>>3).astype(np.uint16).reshape(48,4,64,4,3).sum(axis=(1,3))+8)//16
        box=((original[:,:,0]>>2)<<5)|((original[:,:,1]>>2)<<2)|(original[:,:,2]>>3)
        np.testing.assert_array_equal(np.frombuffer(first['thumb'],dtype=np.uint8).reshape(48,64),box)
        self.check('on-disk thumbnail BOX includes entire game screen',pixels=3072)
        self.press('B',lambda sh:sh['mode']==2);self.press('A',lambda sh:sh['mode']==8);assert self.shell()['selection']==1
        self.capture('overwrite_default_no');ops=self.shell()['save_ops'];self.press('A',lambda sh:sh['mode']==2)
        assert self.shell()['save_ops']==ops and self.slot('overwrite_no')['sav']==first['sav'];self.check('overwrite default No leaves save unchanged')
        self.press('A',lambda sh:sh['mode']==8);self.press('LEFT',lambda sh:sh['selection']==0);self.press('A',lambda sh:sh['mode']==9 and sh['save_ops']==2)
        second=self.saved_picture('saved_overwrite_yes');assert second['seq']>first['seq'];self.press('B',lambda sh:sh['mode']==2)
        self.press('L',lambda sh:sh['page']==17);self.capture('quick_page_latest6')
        self.press('R',lambda sh:sh['page']==1)
        for n in range(2,17):self.press('R',lambda sh,n=n:sh['page']==n)
        self.capture('manual_page16');self.press('R');assert self.shell()['page']==16;self.check('Q and manual page16/end boundary navigation')
        for n in range(15,0,-1):self.press('L',lambda sh,n=n:sh['page']==n)
        self.press('B',lambda sh:sh['mode']==1);self.press('B',lambda sh:not sh['active'])
        controller=ManualController()
        for _ in range(18000):
            state=self.session.last;self.session.cache_records(self.text)
            if state['s']['text_id']==84 and state['s']['visible']>=state['s']['char_count'] and state['s']['blocked']:break
            self.step(controller.keys(self.session,state))
        else:raise AssertionError(('cross-scene source progress timeout',self.session.last))
        other=self.capture('later_scene84');assert not np.array_equal(other[:192],game[:192]);self.press('START',lambda sh:sh['active'] and sh['mode']==1)
        self.select(3);self.press('A',lambda sh:sh['mode']==3);self.press('A',lambda sh:sh['mode']==8);assert self.shell()['selection']==1
        self.capture('load_default_no_from_other_scene');self.press('A',lambda sh:sh['mode']==3);assert self.shell()['load_ops']==0;self.check('load default No preserves later scene')
        self.press('A',lambda sh:sh['mode']==8);self.press('LEFT',lambda sh:sh['selection']==0);self.press('A',lambda sh:not sh['active'] and sh['load_ops']==1)
        self.wait(lambda:self.session.last['s']['blocked'] and self.session.last['s']['text_id']==second['text_id']);self.capture('loaded_previous_scene0');self.check('actual confirmed load returns to saved earlier scene',from_text=84,to_text=second['text_id'])
        self.press('START',lambda sh:sh['active'] and sh['mode']==1);self.select(2);self.press('A',lambda sh:sh['mode']==2)
        self.press('Y',lambda sh:sh['mode']==8);assert self.shell()['selection']==1;self.capture('delete_default_no');self.press('A',lambda sh:sh['mode']==2)
        assert self.slot('delete_no')['valid'];self.check('Y delete default No preserves slot')
        self.press('Y',lambda sh:sh['mode']==8);self.press('LEFT',lambda sh:sh['selection']==0);self.press('A',lambda sh:sh['mode']==2)
        self.capture('deleted_slot0_no_signal');assert not self.slot('delete_yes')['valid'];self.check('confirmed Y deletion clears actual slot')
        self.press('B',lambda sh:sh['mode']==1);self.select(5);self.press('A',lambda sh:sh['mode']==8);assert self.shell()['selection']==1
        self.capture('return_title_default_no');self.press('A',lambda sh:sh['mode']==1);assert self.shell()['title_visits']==1
        self.select(5);self.press('A',lambda sh:sh['mode']==8);self.press('LEFT',lambda sh:sh['selection']==0);self.press('A',lambda sh:sh['mode']==0)
        self.capture('return_title_yes');assert self.shell()['title_visits']==2;self.check('return Title default No/confirmed Yes')
    def run(self):
        try:
            skip=False
            for _ in range(12000):
                sh=self.step(('A',) if skip else ());v=self.session.last.get('video');skip=skip or bool(v and v['ready'])
                if sh['magic']==0x4c48535a and sh['active'] and sh['mode']==0:break
            else:raise AssertionError('Title boot timeout')
            self.release();image=self.capture('title_committed')
            assert bool(self.shell()['available'])==(self.args.case=='fat'),self.shell()
            actual=self.pixels(image,False);np.testing.assert_array_equal(actual,title_lower_expected(Path(self.source_menu['source_game'])))
            self.check('Title lower original eyes/Unicode source pixels',pixels=49152)
            self.report['writable_FAT_available']=bool(self.shell()['available'])
            if self.args.minimal:
                if self.args.case=='fat':self.minimal_fat()
                else:self.minimal_readonly()
            elif self.args.case=='fat':self.fat_case()
            else:self.readonly_case()
            assert product_hashes()==self.products and digest(self.args.rom)==self.sha,'Frozen product changed during probe'
            self.report.update(result='PASS',minimal_incremental=self.args.minimal,core_frames=self.session.emu.frame_count,wall_seconds=time.monotonic()-self.start,audio=self.session.last['a'],final_shell=self.shell(),rom_bytes=self.args.rom.stat().st_size,
                limitations=['Representative UI pixels/navigation only, not every original screen/source state.','FAT case uses a private file-backed emulator image; root provides independent cold-process/full-story proofs.'])
            return self.report
        except BaseException as e:
            self.report.update(result='FAIL',error=str(e),shell=self.shell(),state=self.session.last)
            raise
        finally:atomic_json(self.args.output,self.report);self.session.close()

def digest_bytes(raw):return hashlib.sha256(raw).hexdigest()
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--case',choices=['all','readonly','fat'],default='all');p.add_argument('--minimal',action='store_true',help='Incremental successor candidate checks; full navigation is separately preserved.')
    p.add_argument('--rom',type=Path,default=ROOT/'zato_story_chain_menu_final1_nitrofs.nds');p.add_argument('--map',type=Path,default=ROOT/'build/zato_story_chain_menu_final1_nitrofs.map')
    p.add_argument('--expected-sha',default='5656c3b9c39d020d570003da98eea68551a9c8c9592330f78460c58da97b5cc4')
    p.add_argument('--core',type=Path,default=SPIKE/'desmume_fat_test.dll');p.add_argument('--image',type=Path,default=ROOT/'artifacts/story_menu_final1_ui_fat16.img')
    p.add_argument('--output',type=Path,default=ROOT/'artifacts/story_menu_final1_rom_verification.json');p.add_argument('--screens',type=Path,default=ROOT/'artifacts/story_menu_final1_screens')
    p.add_argument('--emulator-tools',type=Path,default=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools'));p.add_argument('--cpu-mode',choices=['interpreter'],default='interpreter')
    args=p.parse_args()
    if args.case!='all':
        if args.case=='fat' and not args.image.exists():create(args.image)
        report=MenuProbe(args).run();print('PASS',args.case,len(report['checks']),'checks',flush=True);return
    assert not args.image.exists(),'Choose a new exclusive FAT image; existing images are never overwritten.'
    reports=[]
    for case in ['readonly','fat']:
        out=args.output.with_name(args.output.stem+'_'+case+'.json')
        cmd=[sys.executable,str(Path(__file__).resolve()),'--case',case,'--rom',str(args.rom),'--map',str(args.map),'--expected-sha',args.expected_sha,'--core',str(args.core),'--image',str(args.image),'--output',str(out),'--screens',str(args.screens/case)]
        if args.minimal:cmd.append('--minimal')
        env=dict(os.environ)
        if case=='fat':env['ZATO_MPCF_IMAGE']=str(args.image.resolve())
        else:env.pop('ZATO_MPCF_IMAGE',None)
        subprocess.run(cmd,check=True,env=env);reports.append(json.loads(out.read_text(encoding='utf8')))
    result=dict(result='PASS',rom_sha256=args.expected_sha,rom_bytes=args.rom.stat().st_size,minimal_incremental=args.minimal,scope='Committed representative original-core/no-FAT and isolated writable-FAT menu UI checks.',cases=reports,
        image=str(args.image.resolve()),image_sha256=digest(args.image),files={k:len(v) for k,v in files(args.image).items()},source_sha256=product_hashes())
    atomic_json(args.output,result);print('PASS both UI environments',flush=True)
if __name__=='__main__':main()
