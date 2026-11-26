"""Read-only libretro menu navigation/pixels; deliberately not a FAT save proof."""
from pathlib import Path
import argparse,hashlib,json,struct,sys,time,zipfile
import numpy as np
from PIL import Image
from verify_story_rom import NativeSession,atomic_json,NONE
ROOT=Path(__file__).resolve().parents[1]
SHELL='magic active mode page selection available save_ops load_ops save_errors last_slot io_epoch autosaves global_writes'.split()

def rgb15(image,alpha=False):
    a=np.asarray(image.convert('RGBA'),dtype=np.uint16);q=(a[:,:,:3]*31+127)//255;w=(0x8000|q[:,:,0]|q[:,:,1]<<5|q[:,:,2]<<10).astype(np.uint16)
    return np.where(a[:,:,3]>=128,w,0).astype(np.uint16) if alpha else w

def title_expected(game):
    bg=Image.open(game/'gui/main_menu.png').convert('RGBA').crop((280,0,1280,720)).resize((256,184),Image.Resampling.LANCZOS)
    logo=Image.open(game/'gui/overlay/main_menu.png').convert('RGBA').crop((280,0,1280,720)).resize((256,184),Image.Resampling.LANCZOS)
    a=rgb15(bg);b=rgb15(logo,True);out=np.full((192,256),0x8000,dtype=np.uint16);out[4:188]=np.where(b,b,a);return out

def title_lower_expected(game,signature=True):
    sys.path.insert(0,str(ROOT.parent/'experiments/font_lab'))
    from font_core import parse_bdf
    info=json.loads((ROOT.parent/'experiments/font_lab/out/analysis.json').read_text(encoding='utf8'))
    with zipfile.ZipFile(info['font_archive']) as z:
        font=parse_bdf(z.read('fusion-pixel-12px-monospaced-zh_hans.bdf'));small=parse_bdf(z.read('fusion-pixel-8px-monospaced-zh_hans.bdf'))
    image=Image.open(game/'gui/overlay/main_menu.png').convert('RGBA').crop((0,0,280,720)).resize((75,192),Image.Resampling.LANCZOS)
    out=np.full((192,256),0x8000,dtype=np.uint16);out[:,:75]=rgb15(image)
    for i,value in enumerate(['开始','读档','图鉴','设置','致谢','控制','退出']):
        x=24;y=20+i*22
        for char in value:
            mask=font.bitmap(ord(char)).astype(bool);out[y:y+12,x:x+12][mask]=0xffff if i==0 else 0xc631;x+=font.glyphs[ord(char)]['advance']
    if signature:
        x=84
        for char in 'Ferry // Nopanamaman.  2025':
            mask=small.bitmap(ord(char)).astype(bool);out[180:188,x:x+8][mask]=0xc631;x+=small.glyphs[ord(char)]['advance']
    return out

class Probe:
    def __init__(self,args):
        self.args=args;self.sha=hashlib.sha256(args.rom.read_bytes()).hexdigest();self.start=time.monotonic()
        assert self.sha==args.expected_sha
        self.session=NativeSession(args);self.shell_address=self.session.symbols.public('g_story_shell_status')
        self.report=dict(result='RUNNING',rom_sha256=self.sha,scope='Actual interpreter UI navigation and selected pixels. Libretro has no writable FAT; no cold-save claim.',captures=[],actions=[],pixel_checks=[])
    def shell(self):return dict(zip(SHELL,struct.unpack('<13I',self.session.read(self.shell_address,52))))
    def step(self,keys=()):self.session.step(keys);return self.shell()
    def release(self):
        before=self.session.last['input_vblanks']
        for _ in range(120):
            s=self.step()
            if self.session.last['input_vblanks']>=before+2:return s
        raise AssertionError('release IRQ timeout')
    def press(self,key,predicate=None):
        before=self.session.last['input_vblanks']
        for _ in range(120):
            s=self.step((key,))
            if predicate(s) if predicate else self.session.last['input_vblanks']>=before+3:
                self.report['actions'].append(dict(key=key,shell=s));return self.release()
        raise AssertionError(('input timeout',key,s))
    def select(self,index):
        for _ in range(20):
            old=self.shell()
            if old['selection']==index:return old
            self.press('DOWN',lambda s:s['selection']!=old['selection'])
        raise AssertionError(('selection timeout',index,self.shell()))
    def capture(self,label):
        path=self.args.screens/(label+'.png');self.session.capture(path);shell=self.shell()
        self.report['captures'].append(dict(name=label,path=str(path.resolve()),shell=shell,state=self.session.last['s'],audio=self.session.last['a']))
        return np.asarray(self.session.emu.frame)
    def open_title_item(self,index,mode):
        assert self.shell()['mode']==0,self.shell();self.select(index);self.press('A',lambda s:s['mode'] in mode if isinstance(mode,tuple) else s['mode']==mode)
    def close_to_title(self):
        for _ in range(5):
            if self.shell()['mode']==0:return
            old=self.shell()['mode'];self.press('B',lambda s:s['mode']!=old)
        raise AssertionError(('cannot return title',self.shell()))
    def run(self):
        try:
            skip=False
            for _ in range(10000):
                s=self.step(('A',) if skip else ())
                v=self.session.last.get('video');skip=skip or bool(v and v['ready'])
                if s['magic']==0x4c48535a and s['active'] and s['mode']==0:break
            else:raise AssertionError(('OP to title timeout',s))
            self.release();frame=self.capture('title');menu=json.loads((ROOT/'artifacts/story_menu_manifest.json').read_text(encoding='utf8'))
            actual=frame[:192]>>3;words=title_expected(Path(menu['source_game']));expected=np.stack((words&31,words>>5&31,words>>10&31),axis=2)
            np.testing.assert_array_equal(actual,expected,err_msg='first title upper versus original PNG crop/logo')
            self.report['pixel_checks'].append(dict(name='original title right crop and logo',pixels=49152,result='PASS'))
            words=title_lower_expected(Path(menu['source_game']));expected=np.stack((words&31,words>>5&31,words>>10&31),axis=2)
            np.testing.assert_array_equal(frame[192:]>>3,expected,err_msg='all lower pixels versus original eyes and Fusion12 BDF glyphs')
            self.report['pixel_checks'].append(dict(name='original eyes and independent Fusion12 Unicode masks',pixels=49152,result='PASS'))
            self.report['writable_FAT_available']=bool(s['available'])
            self.open_title_item(1,(3,9));self.capture('title_load_storage_unavailable' if self.shell()['mode']==9 else 'title_load')
            if self.shell()['mode']==9:self.press('B',lambda x:x['mode']==3)
            if not s['available']:self.capture('load_grid_no_FAT')
            self.close_to_title()
            self.open_title_item(3,5);self.press('RIGHT');self.press('DOWN');self.press('LEFT');self.capture('settings');self.close_to_title()
            self.open_title_item(4,6);self.capture('credits');self.press('R');self.capture('credits_scrolled');self.close_to_title()
            self.open_title_item(5,7);self.capture('controls');self.close_to_title()
            self.select(0);self.press('A',lambda x:not x['active'])
            for _ in range(4000):
                state=self.session.last
                if state['s']['errors'] or state['f']['failed']:raise AssertionError(('story start failed',state))
                if state['s']['text_id']!=NONE and state['s'].get('blocked') and state['s'].get('window'):break
                self.step()
            else:raise AssertionError(('no ordinary Say after Start',self.session.last))
            self.capture('first_say');self.press('START',lambda x:x['active'] and x['mode']==1);self.capture('game_menu')
            frozen=self.session.last['s']['ticks'];self.select(2);self.press('A',lambda x:x['mode'] in (2,9));self.capture('game_save_unavailable' if self.shell()['mode']==9 else 'game_save')
            if self.shell()['mode']==9:self.press('B',lambda x:x['mode']==2)
            if not self.shell()['available']:self.capture('save_grid_no_FAT')
            self.press('B',lambda x:x['mode']==1);assert self.session.last['s']['ticks']==frozen,'story time advanced while menus were active'
            self.select(3);self.press('A',lambda x:x['mode'] in (3,9));self.capture('game_load_unavailable' if self.shell()['mode']==9 else 'game_load')
            if self.shell()['mode']==9:self.press('B',lambda x:x['mode']==3)
            self.press('B',lambda x:x['mode']==1)
            self.select(5);self.press('A',lambda x:x['mode']==8);self.capture('return_title_confirm_no');self.press('B',lambda x:x['mode']==1)
            self.select(5);self.press('A',lambda x:x['mode']==8);self.press('LEFT',lambda x:x['selection']==0);self.press('A',lambda x:x['mode']==0);self.capture('returned_title')
            self.select(6);self.press('A',lambda x:x['mode']==8);self.press('LEFT',lambda x:x['selection']==0);self.press('A',lambda x:x['mode']==9);self.capture('quit_shutdown_message')
            assert hashlib.sha256(self.args.rom.read_bytes()).hexdigest()==self.sha
            self.report.update(result='PASS',wall_seconds=time.monotonic()-self.start,core_frames=self.session.emu.frame_count,final_shell=self.shell(),audio=self.session.last['a'])
            return self.report
        except BaseException as e:
            self.report.update(result='FAIL',error=str(e),shell=self.shell(),state=self.session.last)
            raise
        finally:atomic_json(self.args.output,self.report);self.session.close()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--rom',type=Path,required=True);p.add_argument('--map',type=Path,required=True);p.add_argument('--expected-sha',required=True)
    p.add_argument('--output',type=Path,default=ROOT/'artifacts/story_menu_rom_probe0.json');p.add_argument('--screens',type=Path,default=ROOT/'artifacts/story_menu_rom_probe0_screens')
    p.add_argument('--emulator-tools',type=Path,default=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools'));p.add_argument('--cpu-mode',choices=['interpreter'],default='interpreter')
    r=Probe(p.parse_args()).run();print(r['result'],r['scope'],flush=True)
if __name__=='__main__':main()
