"""Real credits renderer clips the native badge through both scroll layouts."""
from pathlib import Path
import importlib.util,json,shutil,subprocess,sys,tempfile,unittest,zipfile
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'nds/tools'))
import pack_story_menu as packer
HARNESS=r'''
#include "story_menu.h"
#include <stdio.h>
#include <stdlib.h>
static uint16_t upper[256*192],lower[256*192],background[256*192];
int main(int argc,char**argv){
 if(argc!=4||!story_menu_open(argv[1]))return 2;
 unsigned row=(unsigned)strtoul(argv[3],NULL,10);const int offsets[]={0,8,9,-1,-3,2};
 for(unsigned game=0;game<2;game++)for(unsigned k=0;k<6;k++){
  StoryMenuState s;story_menu_init(&s,STORY_MENU_CREDITS,game!=0);s.scroll=row-offsets[k];
  if(game)s.game_preview=background;
  if(!story_menu_draw(&s,0,upper,lower))return 3;
  for(unsigned screen=0;screen<2;screen++){
   char path[1024];snprintf(path,sizeof(path),"%s/%u_%u_%u.rgb15",argv[2],game,k,screen);
   FILE*f=fopen(path,"wb");if(!f)return 4;
   if(fwrite(screen?lower:upper,2,256*192,f)!=256*192)return 5;fclose(f);
  }
 }
 story_menu_close();return 0;
}
'''


class CreditsBadgeRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='zato_badge_render_');cls.folder=Path(cls.temp.name)
        cls.pack=cls.folder/'menu.bin';cls.manifest=cls.folder/'menu.json';cls.m=packer.build(packer.source.DEFAULT_GAME,cls.pack,cls.manifest)
        info=json.loads((ROOT/'experiments/font_lab/out/analysis.json').read_text(encoding='utf8'))
        sys.path.insert(0,str(ROOT/'experiments/font_lab'));from font_core import parse_bdf
        with zipfile.ZipFile(info['font_archive']) as z:font=parse_bdf(z.read('fusion-pixel-12px-monospaced-zh_hans.bdf'))
        prefix=cls.m['credits_string'].split('NDS移植\n',1)[0]+'NDS移植'
        row=0;x=0
        for c in prefix:
            if c=='\n':row+=1;x=0;continue
            advance=font.glyphs[ord(c)]['advance']
            if x and x+advance>232:row+=1;x=0
            x+=advance
        cls.row=row+1
        cls.mask=np.concatenate([np.array(Image.open(ROOT/'nds/icon/credits/Trader-pixel-96x43-white-native.png').convert('RGBA'))[:,:,3]>0,np.zeros((43,8),dtype=bool),
            np.array(Image.open(ROOT/'nds/icon/credits/ChatGPT-pixel-43x43-white.png').convert('RGBA'))[:,:,3]>0],axis=1)
        src=cls.folder/'render.c';src.write_text(HARNESS,encoding='utf8');exe=cls.folder/'render.exe'
        r=subprocess.run([shutil.which('gcc'),'-std=c11','-O2','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-I'+str(ROOT/'nds/source'),str(src),str(ROOT/'nds/source/story_menu.c'),'-o',str(exe)],capture_output=True,text=True)
        if r.returncode:raise AssertionError(r.stderr)
        subprocess.run([str(exe),str(cls.pack),str(cls.folder),str(cls.row)],check=True)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def test_native_badge_is_exact_in_title_and_in_game_scrolling(self):
        self.assertEqual(self.mask.shape,(43,147))
        for game in (0,1):
            screen=1 if game else 0
            image=np.fromfile(self.folder/f'{game}_0_{screen}.rgb15',dtype='<u2').reshape(192,256)
            np.testing.assert_array_equal(image[34:77,54:201],np.where(self.mask,0xffff,0x8000),err_msg=f'context{game} native badge differs from supplied PNGs')
    def test_badge_crosses_dual_screen_seam_and_is_clipped_above_footer(self):
        for game in (0,1):
            for k,offset in enumerate((0,8,9,-1,-3,2)):
                start=self.row-offset
                for screen in (0,1):
                    if game and screen==0:continue
                    skip=start+(10 if not game and screen==1 else 0)
                    top=30+(self.row-skip)*14+4;lo=max(30,top);hi=min(170,top+43)
                    if lo>=hi:continue
                    image=np.fromfile(self.folder/f'{game}_{k}_{screen}.rgb15',dtype='<u2').reshape(192,256)
                    np.testing.assert_array_equal(image[lo:hi,54:201],np.where(self.mask[lo-top:hi-top],0xffff,0x8000),err_msg=f'{game}/{offset}/{screen} clipped image')
                    self.assertTrue(np.all(image[170:177,54:201]==0x8000),'badge covered footer gap')


if __name__=='__main__':unittest.main()
