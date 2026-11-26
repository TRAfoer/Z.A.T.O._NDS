"""Physical-input probes of the actual scroll/menu ROM and writable FAT file.

RAM is read only. Run in a fresh process with ZATO_MPCF_IMAGE set by the parent.
The emulator frontend adds only the standard libretro touchscreen input ABI.
"""
from pathlib import Path
import argparse, hashlib, json, os, struct, sys, time
import numpy as np
from verify_story_menu_rom import Probe as BaseProbe,title_lower_expected
from verify_story_rom import atomic_json, NONE

ROOT=Path(__file__).resolve().parents[1]
SPIKE=ROOT/'artifacts/save_spike'
sys.path.insert(0,str(ROOT/'tools'))
from fat16 import files

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def configure(a):
    sys.path.insert(0,str(a.emulator_tools))
    import nds_emulator
    base=nds_emulator.Emulator
    class TouchEmulator(base):
        pointer=None
        BUTTONS={**base.BUTTONS,'Y':1}
        def environment(self,command,data):
            result=super().environment(command,data)
            if command==16:
                self.variables[b'desmume_pointer_mouse']=b'enabled'
                self.variables[b'desmume_pointer_type']=b'touch'
            return result
        def input(self,port,device,index,button):
            if port==0 and device==6:
                if self.pointer is None:return 0
                x,y=self.pointer
                return [int((x+.5)*65536/256)-32768,
                        int((192+y+.5)*65536/384)-32768,1][button] if button<3 else 0
            return super().input(port,device,index,button)
    nds_emulator.Emulator=TouchEmulator

class Probe(BaseProbe):
    def __init__(self,a):
        configure(a);super().__init__(a)
        self.report.update(scope='Actual ROM physical buttons/touch, committed screens and persistent FAT file',checks=[])
    def wait(self,predicate,limit=16000):
        for _ in range(limit):
            if predicate():return self.session.last
            self.step()
        raise AssertionError(('wait timeout',self.shell(),self.session.last))
    def press(self,key,predicate=None):
        before=self.session.last['input_vblanks']
        for _ in range(4000):
            sh=self.step((key,))
            if predicate(sh) if predicate else self.session.last['input_vblanks']>=before+3:
                self.report['actions'].append(dict(key=key,shell=sh));return self.release()
        raise AssertionError(('key timeout',key,self.shell(),self.session.last))
    def touch(self,x,y,predicate):
        self.session.emu.pointer=(x,y)
        try:
            for _ in range(4000):
                sh=self.step()
                if predicate(sh):break
            else:raise AssertionError(('touch timeout',x,y,self.shell(),self.session.last))
        finally:self.session.emu.pointer=None
        self.report['actions'].append(dict(touch=[x,y],shell=self.shell()))
        return self.release()
    def capture(self,label):
        self.wait(lambda:self.session.last['s']['ready'])
        start=self.session.last['s']['frames']
        self.wait(lambda:self.session.last['s']['frames']>=start+2)
        result=super().capture(label).copy()
        self.report['captures'][-1]['commits_before']=start
        self.report['captures'][-1]['commits_after']=self.session.last['s']['frames']
        return result
    def check(self,name,**details):
        self.report['checks'].append(dict(name=name,result='PASS',**details))
        print('PASS '+name,flush=True)
    def frozen_rgb(self,lower=False):
        name='game_lower_pixels' if lower else 'gallery_pixels'
        address,size=self.session.symbols.section(name,'story_shell')
        assert size==98304
        words=np.frombuffer(self.session.read(address,size),dtype='<u2').reshape(192,256)
        return np.stack((words&31,(words>>5)&31,(words>>10)&31),axis=2).astype(np.uint8)
    def boot_title(self):
        for _ in range(16000):
            state=self.session.last
            v=state.get('video') if state else None
            self.step(('A',) if v and v.get('ready') else ())
            if self.shell()['magic']==0x4c48535a and self.shell()['active'] and self.shell()['mode']==0 and self.session.last['s']['ready']:break
        else:raise AssertionError('Title boot timeout')
        self.release();image=self.capture('title')
        menu=json.loads((ROOT/'artifacts/story_menu_manifest.json').read_text(encoding='utf8'))
        words=title_lower_expected(Path(menu['source_game']))
        expected=np.stack((words&31,(words>>5)&31,(words>>10)&31),axis=2)
        np.testing.assert_array_equal(image[192:]>>3,expected)
        self.check('original title lower and author signature exact pixels',pixels=49152)
    def sav(self):
        payloads=files(self.args.image)
        values=[v for k,v in payloads.items() if k.lower().endswith('.sav')]
        assert len(values)==1 and len(values[0])==524288
        return values[0]
    def menu_cases(self):
        assert self.shell()['available']==1,'Use isolated writable-FAT core/image'
        self.select(3);self.press('A',lambda sh:sh['mode']==5)
        self.press('B',lambda sh:sh['mode']==0);assert self.shell()['selection']==3
        self.select(4);self.press('A',lambda sh:sh['mode']==6)
        top=self.capture('credits_start')
        for _ in range(12):self.press('R')
        end=self.capture('credits_end_tools')
        assert np.any((top>>3)!=(end>>3))
        self.press('B',lambda sh:sh['mode']==0);assert self.shell()['selection']==4
        self.select(5);self.press('A',lambda sh:sh['mode']==7);self.capture('controls')
        self.press('B',lambda sh:sh['mode']==0);assert self.shell()['selection']==5
        self.check('Title child menus return to their original selection; credits scroll')
        self.select(0);self.press('A',lambda sh:not sh['active'])
        self.wait(lambda:self.session.last['s']['blocked'] and self.session.last['s']['text_id']!=NONE and
                  self.session.last['s']['visible']>=self.session.last['s']['char_count'] and self.shell()['autosaves']>=1 and
                  self.session.last['s']['ticks']-self.session.last['s']['transition_start']>=self.session.last['s']['transition_duration'])
        source=self.capture('story_source');before=self.session.last['s'].copy()
        self.press('RIGHT');self.press('LEFT')
        assert self.session.last['s']['chapter']==before['chapter']
        self.check('chapter keys disabled by default')
        self.press('START',lambda sh:sh['active'] and sh['mode']==1)
        game=self.capture('start_menu_dimmed')
        # The opening star field moves between key events. Compare the exact
        # frozen original buffers, not a screenshot from several ticks earlier.
        np.testing.assert_array_equal(game[:192]>>3,self.frozen_rgb())
        np.testing.assert_array_equal(game[192+35:192+160,90:244]>>3,self.frozen_rgb(True)[35:160,90:244]//4)
        self.check('START right side preserves dimmed lower story pixels',pixels=125*154)
        self.select(2);self.press('A',lambda sh:sh['mode']==2)
        self.press('A',lambda sh:sh['mode']==9 and sh['save_ops']==1)
        assert not self.shell()['save_errors'];self.capture('saved_after_autosave')
        self.check('manual save succeeds after chapter autosave')
        self.press('B',lambda sh:sh['mode']==2);self.press('B',lambda sh:sh['mode']==1)
        assert self.shell()['selection']==2
        self.press('B',lambda sh:not sh['active'])
        before=self.capture('before_quick');old=self.session.last['s'].copy()
        self.press('SELECT',lambda sh:sh['active'] and sh['mode']==10)
        quick=self.capture('quick_page1')
        np.testing.assert_array_equal(quick[:192]>>3,self.frozen_rgb())
        np.testing.assert_array_equal(quick[192:192+156]>>3,self.frozen_rgb(True)[:156])
        for key,indices in [('RIGHT',list(range(1,10))+[0]),('LEFT',list(range(9,-1,-1)))]:
            for index in indices:
                page,selection=index//4+1,index%4
                self.press(key,lambda sh,page=page,selection=selection:sh['mode']==10 and sh['page']==page and sh['selection']==selection)
        self.capture('quick_lr_crossed_all_pages')
        self.check('LEFT/RIGHT cross all quick-menu page edges and wrap first/last item',items=10,directions=2)
        tick=self.session.last['s']['ticks'];local=self.session.last['s']['local']
        for _ in range(24):self.step()
        assert self.session.last['s']['ticks']==tick and self.session.last['s']['local']==local
        self.press('X',lambda sh:not sh['active'])
        assert self.session.last['s']['bilingual']==old['bilingual'] and self.session.last['s']['pc']==old['pc']
        self.check('four-item quick overlay freezes scene and consumes other operation')
        self.touch(100,185,lambda sh:sh['active'] and sh['mode']==10)
        self.touch(100,70,lambda sh:not sh['active'])
        assert self.session.last['s']['pc']==old['pc']
        self.check('touch opens bar; outside touch only dismisses')
        self.press('SELECT',lambda sh:sh['active'] and sh['mode']==10)
        self.press('R',lambda sh:sh['page']==2);self.capture('quick_page2')
        self.press('RIGHT',lambda sh:sh['selection']==1);self.press('RIGHT',lambda sh:sh['selection']==2)
        count=self.shell()['save_ops'];self.press('A',lambda sh:not sh['active'] and sh['save_ops']==count+1)
        assert not self.shell()['save_errors'];self.check('quick save uses captured scene point')
        self.press('SELECT',lambda sh:sh['active'] and sh['mode']==10)
        self.press('R',lambda sh:sh['page']==2)
        for selection in (1,2,3):self.press('RIGHT',lambda sh,selection=selection:sh['selection']==selection)
        self.press('A',lambda sh:sh['mode']==8);assert self.shell()['selection']==1
        self.press('LEFT',lambda sh:sh['selection']==0)
        self.press('A',lambda sh:not sh['active'] and sh['load_ops']==1)
        self.capture('quick_loaded_actual_scene')
        assert not self.shell()['save_errors'] and self.session.last['s']['text_id']==old['text_id']
        self.check('actual quick-load confirmation restores the saved scene after dual-screen OP')
        self.press('SELECT',lambda sh:sh['active'] and sh['mode']==10)
        self.press('R',lambda sh:sh['page']==2);self.press('R',lambda sh:sh['page']==3)
        self.capture('quick_page3_debug_last')
        self.press('A',lambda sh:sh['mode']==5);self.press('B',lambda sh:sh['mode']==10)
        assert self.shell()['page']==3 and self.shell()['selection']==0
        self.check('Quick Settings returns to the same page and selected item')
        self.press('RIGHT',lambda sh:sh['selection']==1)
        self.press('A',lambda sh:not sh['active']);self.wait(lambda:self.session.last['s']['paused'])
        self.capture('debug_panel');pc=self.session.last['s']['pc'];language=self.session.last['s']['bilingual']
        self.press('X');self.wait(lambda:not self.session.last['s']['paused'])
        assert self.session.last['s']['pc']==pc and self.session.last['s']['bilingual']==language
        self.check('last quick item opens debug; arbitrary exit key is consumed')
        self.press('START',lambda sh:sh['active'] and sh['mode']==1);self.select(4)
        self.press('A',lambda sh:sh['mode']==5);self.select(3);self.press('RIGHT')
        self.capture('chapter_keys_enabled');saved=self.sav()
        generations=[struct.unpack_from('<I',saved,p+48)[0] for p in (0,8192)]
        bank=8192 if generations[1]>generations[0] else 0
        assert struct.unpack_from('<I',saved,bank+1572)[0]==1
        self.press('B',lambda sh:sh['mode']==1);assert self.shell()['selection']==4
        self.press('B',lambda sh:not sh['active'])
        self.press('RIGHT',lambda sh:self.session.last['s']['chapter']==1)
        self.check('chapter-key switch writes existing save file and enables navigation')
    def run(self):
        try:
            self.boot_title();self.menu_cases()
            assert not self.session.last['s']['errors'] and not self.session.last['a']['errors']
            assert digest(self.args.rom)==self.sha
            self.report.update(result='PASS',rom_bytes=self.args.rom.stat().st_size,save_sha256=hashlib.sha256(self.sav()).hexdigest(),wall_seconds=time.monotonic()-self.start)
            return self.report
        except BaseException as e:
            self.report.update(result='FAIL',error=str(e),shell=self.shell(),state=self.session.last)
            try:self.capture('failure')
            except Exception:pass
            raise
        finally:atomic_json(self.args.output,self.report);self.session.close()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for flag in ('rom','map','image','output','screens'):p.add_argument('--'+flag,type=Path,required=True)
    p.add_argument('--expected-sha',required=True)
    p.add_argument('--core',type=Path,default=SPIKE/'desmume_fat_test.dll')
    p.add_argument('--emulator-tools',type=Path,default=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools'))
    p.add_argument('--cpu-mode',default='interpreter')
    a=p.parse_args();assert Path(os.environ['ZATO_MPCF_IMAGE']).resolve()==a.image.resolve()
    print(Probe(a).run()['result'],flush=True)
if __name__=='__main__':main()
