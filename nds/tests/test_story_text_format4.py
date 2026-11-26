"""Small independent wire fixture for paired rows and decorated 1bpp glyphs."""
from pathlib import Path
import shutil,struct,subprocess,tempfile,unittest,zlib

ROOT=Path(__file__).resolve().parents[2]

def commands(gids,xs,ys,pages):
    n=len(gids)
    return struct.pack('<'+'H'*n,*gids)+bytes(xs)+bytes(ys)+bytes(pages)+b'\0\0'

def fixture(change=None):
    cn=commands([0x8000,0],[30,42],[35,35],[0,0])
    en=commands([0x4000]*3,[30,34,30],[49]*3,[0,0,1])
    paired=commands([0x8000,0],[30,30],[35,35],[0,1])
    timeline=struct.pack('<5H',2,1,0,1,1)
    f=[1,2,1,0,1,2,0,0,0,48,58,58,2,58+len(cn),3,0,0,0,
       58+len(cn)+len(en),2,58+len(cn)+len(en)+len(paired),3,
       58+len(cn)+len(en)+len(paired)+6,0]
    raw=bytearray(struct.pack('<24H',*f)+timeline+cn+en+paired+struct.pack('<3H',1,1,2)+struct.pack('<2H',1,2))
    if change:change(raw,f)
    glyph_at=148;atlas_at=156;header_at=160;commands_at=168;portrait_at=1228
    total=portrait_at+len(raw)
    h=[2,total,0,1,1,1,1,0,136,glyph_at,atlas_at,1,header_at,commands_at,106,
       0,0,11,95,0,portrait_at,0,portrait_at,len(raw),180,len(raw),0,portrait_at-136,0,4,12,0]
    shared=bytearray(struct.pack('<8s32I',b'ZATSTX02',*h))
    shared.extend(struct.pack('<IHHI',portrait_at,len(raw)|0x8000,len(raw),zlib.crc32(raw)))
    shared.extend(struct.pack('<IHH',0,1,0x0101));shared.extend(b'\x80');shared.extend(bytes(header_at-len(shared)))
    shared.extend(struct.pack('<II',0,0))
    shared.extend(struct.pack('<HBBHHBB',0,0,0,0xffff,0,0,1)*106)
    shared.extend(bytes(portrait_at-len(shared)))
    struct.pack_into('<I',shared,16,zlib.crc32(shared))
    return shared+raw

HARNESS=r'''
#include "story_text.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static void need(int n,const char*s){if(!n){fprintf(stderr,"FAIL %s (%s)\n",s,story_text_error());exit(2);}}
int main(int argc,char**argv){
 need(argc==3,"arguments");
 if(!strcmp(argv[2],"reject_open")){need(!story_text_open(argv[1]),"reject invalid shared segment");return 0;}
 need(story_text_open(argv[1]),"open format4");
 if(!strcmp(argv[2],"reject")){need(!story_text_select(0,0),"reject invalid record");story_text_close();return 0;}
 need(story_text_select(0,0),"select paired record");
 if(!strcmp(argv[2],"header")){
  uint16_t pixels[256*192];story_text_draw_lower(0,1,0,1,1,0,pixels);
  need(pixels[10*256+8]==0xffff&&pixels[10*256+40]==0xffff&&pixels[10*256+54]==0xffff,"continuous name line");
  need(pixels[10*256+44]==0x8000&&pixels[10*256+50]==0x8000,"name interrupts line without strike-through");
  need(pixels[4*256+44]==0xffff&&pixels[4*256+50]==0xffff,"name remains readable");
  story_text_close();return 0;
 }
 if(!strcmp(argv[2],"advance")){
  uint16_t pixels[256*192];story_text_draw_lower(0,2,1,1,1,0,pixels);
  need(pixels[50*256+30]==0xca52&&pixels[50*256+33]==0xca52,"underline covers its advance");
  need(pixels[50*256+34]==0x8000,"underline stops before the following nonbold character");
  story_text_close();return 0;
 }
 need(story_text_page_limit(0,0,0)==2,"normal CN cap");
 need(story_text_page_limit(0,0,1)==1&&story_text_page_limit(0,1,1)==2,"paired caps");
 need(story_text_page(0,1,1)==0&&story_text_page(0,2,1)==1,"paired CN page anchor");
 uint16_t screen[256*192];unsigned before=story_text_read_calls();
 story_text_draw_lower(0,1,0,1,1,0,screen);
 need(screen[10*256+8]==0xffff&&screen[10*256+247]==0xffff,"empty header keeps a continuous line");
 need(screen[35*256+30]==0xffff,"unbold foreground");
 need(screen[35*256+31]==0x8000,"no horizontal bold dilation");
 need(screen[36*256+31]==0xb18c,"one-pixel gray shadow");
 need(screen[49*256+30]==0xca52&&screen[49*256+34]==0xca52,"EN appears with first CN glyph");
 need(screen[50*256+30]==0xca52,"EN underline");
 story_text_draw_lower(0,1,1,1,1,0,screen);need(screen[49*256+30]==0x8000,"future English remains hidden");
 story_text_draw_lower(0,2,1,1,1,0,screen);need(screen[49*256+30]==0xca52,"second paired page English");
 story_text_draw_lower(0,1,0,0,1,0,screen);need(screen[49*256+30]==0x8000,"CN-only hides English");
 need(story_text_read_calls()==before,"draw/getters read no files");story_text_close();return 0;
}
'''

class StoryTextFormat4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.folder=Path(cls.temp.name)
        src=cls.folder/'fixture.c';src.write_text(HARNESS);cls.exe=cls.folder/'fixture.exe'
        run=subprocess.run([shutil.which('gcc'),'-std=c11','-O2','-Wall','-Wextra','-Werror','-I'+str(ROOT/'nds/source'),str(src),str(ROOT/'nds/source/story_text.c'),str(ROOT/'nds/source/asset_pack.c'),'-o',str(cls.exe)],capture_output=True,text=True)
        if run.returncode:raise AssertionError(run.stderr)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def run_fixture(self,data,mode='valid'):
        pack=self.folder/'fixture.bin';pack.write_bytes(data)
        run=subprocess.run([str(self.exe),str(pack),mode],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)
    def test_paired_partial_reveal_styles_pages_and_zero_io(self):self.run_fixture(fixture())
    def test_reject_mismatched_english_threshold_count(self):
        self.run_fixture(fixture(lambda raw,f:struct.pack_into('<H',raw,42,2)),'reject')
    def test_reject_decreasing_thresholds(self):
        self.run_fixture(fixture(lambda raw,f:struct.pack_into('<H',raw,f[20]+2,0)),'reject')
    def test_reject_shadow_beyond_physical_screen(self):
        self.run_fixture(fixture(lambda raw,f:raw.__setitem__(f[11]+4,255)),'reject')
    def test_halfwidth_underline_uses_advance_instead_of_bitmap_canvas(self):
        data=fixture();struct.pack_into('<HH',data,152,8,0x0401)
        struct.pack_into('<I',data,16,0);struct.pack_into('<I',data,16,zlib.crc32(data[:1228]))
        self.run_fixture(data,'advance')
    def test_continuous_line_preserves_a_black_gap_for_the_name(self):
        data=fixture();struct.pack_into('<II',data,160,0,2)
        for at,x in [(168,44),(178,50)]:struct.pack_into('<HBBHHBB',data,at,0,x,4,0xffff,0,0,1)
        struct.pack_into('<I',data,16,0);struct.pack_into('<I',data,16,zlib.crc32(data[:1228]))
        self.run_fixture(data,'header')
    def test_portrait_segment_cannot_wrap_around_shared_bounds(self):
        data=fixture();struct.pack_into('<I',data,8+7*4,1)
        struct.pack_into('<I',data,8+20*4,(1228-7200)&0xffffffff)
        struct.pack_into('<I',data,8+21*4,7200)
        struct.pack_into('<H',data,1228+15*2,1)
        struct.pack_into('<I',data,144,zlib.crc32(data[1228:]))
        struct.pack_into('<I',data,16,0);struct.pack_into('<I',data,16,zlib.crc32(data[:1228]))
        self.run_fixture(data,'reject_open')

if __name__=='__main__':unittest.main()
