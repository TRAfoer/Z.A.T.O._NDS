"""Independent new menu pack, real C navigation and source-pixel checks."""
from pathlib import Path
import hashlib,json,os,shutil,struct,subprocess,sys,tempfile,unittest,zlib
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parents[2]

def rgb15(image,alpha=False):
    a=np.asarray(image.convert('RGBA'),dtype=np.uint16);q=(a[:,:,:3]*31+127)//255
    words=(0x8000|q[:,:,0]|q[:,:,1]<<5|q[:,:,2]<<10).astype('<u2')
    return np.where(a[:,:,3]>=128,words,0).astype('<u2') if alpha else words

class StoryMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.folder=Path(cls.tmp.name);cls.pack=cls.folder/'menu.bin';cls.manifest=cls.folder/'menu.json';cls.exe=cls.folder/'menu.exe'
        packer=ROOT/'nds/tools/pack_story_menu.py';assert packer.exists(),'independent menu packer is missing'
        p=subprocess.run([sys.executable,str(packer),'--output',str(cls.pack),'--manifest',str(cls.manifest)],capture_output=True,text=True)
        assert p.returncode==0,p.stdout+p.stderr
        cls.m=json.loads(cls.manifest.read_text(encoding='utf8'))
        p=subprocess.run([shutil.which('gcc'),'-std=c11','-O2','-Wall','-Wextra','-Werror','-I'+str(ROOT/'nds/source'),str(ROOT/'nds/source/story_menu.c'),str(ROOT/'nds/tests/validate_story_menu.c'),'-o',str(cls.exe)],capture_output=True,text=True)
        assert p.returncode==0,p.stderr
        cls.update_exe=cls.folder/'menu_update.exe'
        p=subprocess.run([shutil.which('gcc'),'-std=c11','-O2','-Wall','-Wextra','-Werror','-I'+str(ROOT/'nds/source'),str(ROOT/'nds/source/story_menu.c'),str(ROOT/'nds/tests/validate_story_menu_update.c'),'-o',str(cls.update_exe)],capture_output=True,text=True)
        assert p.returncode==0,p.stderr
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def native(self,path=None,mode='valid'):
        p=subprocess.run([str(self.exe),str(path or self.pack),mode,str(self.folder)],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr);return p.stdout
    def test_real_c_navigation_confirm_pages_settings_touch_and_zero_io(self):self.native()
    def test_quick_bar12_dim_background_continuous_scroll_and_credits(self):
        p=subprocess.run([str(self.update_exe),str(self.pack),str(self.folder)],capture_output=True,text=True);self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        import zipfile
        sys.path.insert(0,str(ROOT/'experiments/font_lab'));from font_core import parse_bdf
        info=json.loads((ROOT/'experiments/font_lab/out/analysis.json').read_text(encoding='utf8'))
        with zipfile.ZipFile(info['font_archive']) as z:font=parse_bdf(z.read('fusion-pixel-12px-monospaced-zh_hans.bdf'))
        bar=np.fromfile(self.folder/'quick_bar_12.rgb15',dtype='<u2').reshape(192,256);expected=np.full((12,24),0x8000,dtype=np.uint16);x=0
        for c in '返回':
            mask=font.bitmap(ord(c)).astype(bool);expected[:,x:x+12][mask]=0xffff;x+=font.glyphs[ord(c)]['advance']
        np.testing.assert_array_equal(bar[159:171,23:47],expected)
        self.assertTrue(np.all(bar[157:159,23:47]==0x8000));self.assertTrue(np.all(bar[171:174,23:47]==0x8000))
        counter=np.full((12,30),0x8000,dtype=np.uint16);x=0
        for c in '1/3':
            mask=font.bitmap(ord(c)).astype(bool);counter[:,x:x+12][mask]=0xc631;x+=font.glyphs[ord(c)]['advance']
        np.testing.assert_array_equal(bar[177:189,119:149],counter)
        collapsed=np.fromfile(self.folder/'collapsed_bar_12.rgb15',dtype='<u2').reshape(192,256);expected=np.full((12,24),0x8000,dtype=np.uint16);x=0
        for c in '菜单':
            mask=font.bitmap(ord(c)).astype(bool);expected[:,x:x+12][mask]=0xffff;x+=font.glyphs[ord(c)]['advance']
        np.testing.assert_array_equal(collapsed[177:189,10:34],expected)
        dim=np.fromfile(self.folder/'game_dim_lower.rgb15',dtype='<u2').reshape(192,256);self.assertTrue(np.all(dim[:,75:]==(0x8000|(6<<10)|(4<<5)|2)))
        for line in (self.folder/'text_pages.txt').read_text().splitlines():
            mode,title,game=map(int,line.split());value=self.m['credits_string'] if mode==6 else self.m['controls_string'];rows=1;x=0
            for c in value:
                if c=='\n':rows+=1;x=0;continue
                advance=font.glyphs[ord(c)]['advance']
                if x and x+advance>232:rows+=1;x=0
                x+=advance
            self.assertEqual((title,game),((rows+19)//20,(rows+9)//10))
            for context,total in [('title',title),('game',game)]:
                counter=np.full((12,60),0x8000,dtype=np.uint16);x=0
                for c in f'{total} / {total}':
                    mask=font.bitmap(ord(c)).astype(bool);counter[:,x:x+12][mask]=0xc631;x+=font.glyphs[ord(c)]['advance']
                screen=np.fromfile(self.folder/f'text_counter_{mode}_{context}.rgb15',dtype='<u2').reshape(192,256)
                np.testing.assert_array_equal(screen[177:189,108:168],counter,err_msg=f'{context} mode{mode} final overlapping viewport must reach total page')
        for value in ['中文汉化(ParaTranz)：https://paratranz.cn/projects/17445','翻译校对：bilibili@100ol  、bilibili@枫封峰','移植制作：bilibili@神币の商家   GPT-6.1 Sol']:self.assertIn(value,self.m['credits_string'])
    def test_title_pixels_are_exact_original_right_crop_and_logo(self):
        self.native();game=Path(self.m['source_game']);base=Image.open(game/'gui/main_menu.png').convert('RGBA').crop((280,0,1280,720)).resize((256,184),Image.Resampling.LANCZOS)
        overlay=Image.open(game/'gui/overlay/main_menu.png').convert('RGBA').crop((280,0,1280,720)).resize((256,184),Image.Resampling.LANCZOS)
        expected=np.full((192,256),0x8000,dtype='<u2');bg=rgb15(base);logo=rgb15(overlay,True);expected[4:188]=np.where(logo,logo,bg)
        actual=np.fromfile(self.folder/'title_upper.rgb15',dtype='<u2').reshape(192,256);np.testing.assert_array_equal(actual,expected)
    def test_lower_menu_pixels_use_original_eyes_and_unscaled_unicode_font(self):
        sys.path.insert(0,str(ROOT/'nds/tools'));from verify_story_menu_rom import title_lower_expected
        self.native();actual=np.fromfile(self.folder/'title_lower.rgb15',dtype='<u2').reshape(192,256)
        np.testing.assert_array_equal(actual,title_lower_expected(Path(self.m['source_game'])))
    def test_rgb332_thumbnails_preserve_full_four_three_image(self):
        self.native();screen=np.fromfile(self.folder/'save_upper.rgb15',dtype='<u2').reshape(192,256)
        y,x=np.indices((48,64));source=((x*4)&0xe0)|((y*4)&0x1c)|((x+y)&3)
        r=(source>>5)&7;g=(source>>2)&7;b=source&3
        expected=(0x8000|((r*31+3)//7)|((g*31+3)//7)<<5|((b*31+1)//3)<<10).astype('<u2')
        np.testing.assert_array_equal(screen[10:154,32:224],expected.repeat(3,0).repeat(3,1))
    def test_empty_slot_uses_the_original_no_signal_image(self):
        self.native();image=Image.open(Path(self.m['source_game'])/'gui/button/slot_idle_background.png').convert('RGBA').crop((10,10,266,154)).resize((64,36),Image.Resampling.LANCZOS)
        actual=np.fromfile(self.folder/'empty_lower.rgb15',dtype='<u2').reshape(192,256)
        np.testing.assert_array_equal(actual[42:78,13:77],rgb15(image))
    def test_quick_page_card_labels_use_Q_instead_of_internal_page17(self):
        import zipfile
        sys.path.insert(0,str(ROOT/'experiments/font_lab'));from font_core import parse_bdf
        info=json.loads((ROOT/'experiments/font_lab/out/analysis.json').read_text(encoding='utf8'))
        with zipfile.ZipFile(info['font_archive']) as z:font=parse_bdf(z.read('fusion-pixel-8px-monospaced-zh_hans.bdf'))
        expected=np.full((8,16),0x8000,dtype=np.uint16);x=0
        for c in 'Q-1':
            mask=font.bitmap(ord(c)).astype(bool);expected[:,x:x+8][mask]=0xc631;x+=font.glyphs[ord(c)]['advance']
        self.native();actual=np.fromfile(self.folder/'quick_lower.rgb15',dtype='<u2').reshape(192,256)
        np.testing.assert_array_equal(actual[90:98,12:28],expected)
    def test_identity_and_gallery_match_current_source_indices(self):
        self.assertEqual(self.m['story_identity_sha256'],hashlib.sha256((ROOT/'nds/nitrofs/story/script.bin').read_bytes()).hexdigest())
        script=json.loads((ROOT/'nds/artifacts/story_script_manifest.json').read_text(encoding='utf8'));gfx=json.loads((ROOT/'nds/artifacts/story_gfx_manifest.json').read_text(encoding='utf8'))
        for row in self.m['gallery']:
            self.assertLess(row['bit'],36);self.assertEqual(script['unlocks'][row['bit']],row['unlock']);self.assertEqual(gfx['name_to_image_id'][row['name']],row['frame'])
        self.assertEqual(sum(x['unlock']=='game_completed' for x in self.m['gallery']),8)
        self.assertEqual(self.m['missing_glyphs'],[])
    def test_malformed_pack_lengths_crc_offsets_and_font_bounds_rejected(self):
        data=self.pack.read_bytes();bad=[data[:n] for n in (0,8,103,len(data)-1)]
        for word,value in [(0,2),(3,0xffffffff),(4,0),(15,200),(18,4096),(23,1)]:
            b=bytearray(data);struct.pack_into('<I',b,8+word*4,value);struct.pack_into('<I',b,16,0);struct.pack_into('<I',b,16,zlib.crc32(b));bad.append(b)
        b=bytearray(data);b[-1]^=1;bad.append(b)
        for i,b in enumerate(bad):
            path=self.folder/f'bad{i}.bin';path.write_bytes(b);self.native(path,'reject')

if __name__=='__main__':unittest.main()
