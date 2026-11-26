"""Identity indexed multiply against an independent RGB5 pixel oracle.

Lossless pixels pass before and after optimization. The real ARM performance
RED is recorded separately by check_story_pulse_budget.py.
"""
from pathlib import Path
import os,shutil,struct,subprocess,tempfile,unittest

ROOT=Path(__file__).resolve().parents[2]

HARNESS=r'''
#include "instrumented.c"
#include <stdio.h>
static unsigned services;
void audio_demo_service(void){services++;}
static PackFrame test_frames[2];
static PackBank test_banks[1];
static Program test_programs[1];
static uint16_t test_map[128],screen[256*192],expected[256*192];
static uint8_t test_tiles[128*40];
static const unsigned alphas[]={0,819,1229,1638,2048,2458,2867,3277,3686,4096};
static void need(int yes,const char*why){if(!yes){fprintf(stderr,"FAIL %s\n",why);exit(2);}}
static unsigned palette_index(unsigned x,unsigned y){return (x+3*y)&15u;}
static bool opaque(unsigned x,unsigned y,unsigned pattern){return !pattern||((x*7+y*3)%5!=0);}
static uint16_t dest_color(unsigned x,unsigned y){
 unsigned n=(x/16+y*8)&31u;
 return (uint16_t)(0x8000u|n|(((n*7)&31u)<<5)|(((n*13)&31u)<<10));
}
static uint16_t reference(uint16_t dest,uint16_t color,unsigned alpha){
 uint16_t result=0x8000;
 for(unsigned channel=0;channel<3;channel++){
  unsigned shift=channel*5,src=(color>>shift)&31u,d=(dest>>shift)&31u;
  /* Quantize the original continuous multiply factor, then the destination.
   * Keep the two rounding stages separate, using a wide independent form. */
  uint64_t numerator=31ull*4096ull-(uint64_t)alpha*(31u-src);
  unsigned factor=(unsigned)((numerator+15ull)/31ull);
  result|=(uint16_t)(((d*factor+2048u)/4096u)<<shift);
 }
 return result;
}
static void setup(unsigned width,unsigned height,unsigned pattern){
 memset(test_frames,0,sizeof(test_frames));memset(test_tiles,0,sizeof(test_tiles));
 PackFrame*f=&test_frames[0];f->width=width;f->height=height;f->cols=(width+7)/8;f->rows=(height+7)/8;f->format=1;
 for(unsigned ty=0;ty<f->rows;ty++)for(unsigned tx=0;tx<f->cols;tx++){
  unsigned id=ty*f->cols+tx;test_map[id]=(uint16_t)id;uint8_t*tile=test_tiles+id*40;
  for(unsigned py=0;py<8;py++)for(unsigned px=0;px<8;px++){
   unsigned x=tx*8+px,y=ty*8+py,pos=py*8+px;
   tile[pos/2]|=(uint8_t)(palette_index(x,y)<<((pos&1)*4));
   if(x<width&&y<height&&opaque(x,y,pattern))tile[32+py]|=(uint8_t)(1u<<px);
  }
 }
 test_frames[1]=*f;test_banks[0].tile_bytes=40;
 frames=test_frames;banks=test_banks;programs=test_programs;
 fc[0].map=fc[1].map=test_map;bc[0].data=test_tiles;
 test_programs[0]=(Program){.alpha=4096,.blend=2,.z0=Q,.z1=Q,.flip=1};
}
static void pixels(const uint16_t*palette,unsigned alpha,int ox,int oy,int top,int bottom,unsigned pattern){
 memcpy(test_frames[0].palette,palette,32);test_programs[0].alpha=(int32_t)alpha;
 for(unsigned y=0;y<192;y++)for(unsigned x=0;x<256;x++)screen[y*256+x]=expected[y*256+x]=dest_color(x,y);
 PackFrame*f=&test_frames[0];
 for(unsigned y=0;y<192;y++)for(unsigned x=0;x<256;x++){
  int sx=(int)x-ox,sy=(int)y-oy;
  if((int)y>=top&&(int)y<bottom&&sx>=0&&sy>=0&&sx<f->width&&sy<f->height&&opaque((unsigned)sx,(unsigned)sy,pattern))
   expected[y*256+x]=reference(expected[y*256+x],palette[palette_index((unsigned)sx,(unsigned)sy)],alpha);
 }
 unsigned prior=services;
 draw_layer(&(Layer){.program=0},(Sample){0,NONE,4096},(Transform){ox*Q,oy*Q,Q},screen,top,bottom);
 for(unsigned i=0;i<256*192;i++)if(screen[i]!=expected[i]){
  fprintf(stderr,"pixel %u,%u alpha%u got%04x expected%04x\n",i%256,i/256,alpha,screen[i],expected[i]);exit(2);
 }
 if(alpha&&oy==32&&f->height>=17&&ox<256&&ox+(int)f->width>0&&top<=32&&bottom>=49)need(services>prior,"large cached multiply must service audio");
}
int main(int argc,char**argv){
 need(argc==3,"args");FILE*file=fopen(argv[1],"rb");need(file!=NULL,"palettes");uint16_t palettes[5][16];need(fread(palettes,32,5,file)==5,"read palettes");fclose(file);
 if(!strcmp(argv[2],"exhaustive")){
  /*16 colors x32 destination values in every channel, all9 source alpha
   * levels and alpha0, for each of the five real ppulse palettes. */
  setup(128,4,0);
  for(unsigned p=0;p<5;p++)for(unsigned a=0;a<10;a++)pixels(palettes[p],alphas[a],5,80,0,192,0);
 }else if(!strcmp(argv[2],"dispatch")){
  setup(128,17,0);pixels(palettes[0],819,5,32,0,192,0);pixels(palettes[0],4096,5,32,0,192,0);
 }else if(!strcmp(argv[2],"clip_cache")){
  setup(23,17,1);
  const int positions[][4]={{5,32,0,192},{-5,-2,0,192},{251,187,0,192},{-3,20,24,34},{256,32,0,192},{5,192,0,192}};
  for(unsigned i=0;i<6;i++)for(unsigned a=0;a<10;a++)pixels(palettes[i%5],alphas[a],positions[i][0],positions[i][1],positions[i][2],positions[i][3],1);
  /* Same backing palette pointer with changed bytes, changed alpha, then
   * restoration of an earlier key. None may reuse a stale component LUT. */
  palettes[0][3]^=0x0421;pixels(palettes[0],819,5,32,0,192,1);
  pixels(palettes[0],3686,5,32,0,192,1);
  palettes[0][3]^=0x0421;pixels(palettes[0],819,5,32,0,192,1);
  pixels(palettes[1],0,5,32,0,192,1);
  for(unsigned i=0;i<256*192;i++)screen[i]=expected[i]=(uint16_t)(i*37u);
  test_programs[0].alpha=0;
  draw_layer(&(Layer){.program=0},(Sample){0,NONE,4096},(Transform){5*Q,32*Q,Q},screen,0,192);
  need(!memcmp(screen,expected,sizeof(screen)),"alpha0 must preserve every destination bit");
 }else if(!strcmp(argv[2],"fallback")){
  setup(23,17,1);memcpy(test_frames[0].palette,palettes[0],32);memcpy(test_frames[1].palette,palettes[1],32);
  Layer layer={.program=0};
  Transform transforms[]={{5*Q,32*Q,Q},{5*Q,32*Q,2*Q},{5*Q+Q/2,32*Q,Q}};
  for(unsigned i=0;i<5;i++){
   test_programs[0].flip=i==0?-1:1;Transform t=transforms[i>=3?0:i];
   unsigned mix=i>=3?2048:4096;bool old=i==3;
   for(unsigned y=0;y<192;y++)for(unsigned x=0;x<256;x++){
    uint16_t dest=dest_color(x,y);screen[y*256+x]=expected[y*256+x]=dest;
    int sx=floor_div((int64_t)x*Q+Q/2-t.x,t.z),sy=floor_div((int64_t)y*Q+Q/2-t.y,t.z);
    if(i==0)sx=22-sx;
    if(sx<0||sy<0||sx>=23||sy>=17||!opaque((unsigned)sx,(unsigned)sy,1))continue;
    unsigned index=palette_index((unsigned)sx,(unsigned)sy);
    unsigned coverage=mix+(old?4096-mix:0),global=(unsigned)test_programs[0].alpha;
    unsigned alpha=(coverage*global+2048)/4096;uint16_t value=0x8000;
    for(unsigned c=0;c<3;c++){
     unsigned shift=c*5,cur=(palettes[0][index]>>shift)&31,prev=old?(palettes[1][index]>>shift)&31:0,d=(dest>>shift)&31;
     unsigned prem=(unsigned)(((uint64_t)global*(cur*mix+prev*(4096-mix)))/4096);
     unsigned factor=(31*(4096-alpha)+prem+15)/31;
     value|=(uint16_t)(((d*factor+2048)/4096)<<shift);
    }
    expected[y*256+x]=value;
   }
   draw_layer(&layer,(Sample){0,old?1:NONE,mix},t,screen,0,192);
   need(!memcmp(screen,expected,sizeof(screen)),"flip, zoom, fractional position, crossfade and partial mix pixels changed");
  }
 }else need(false,"case");
 printf("PASS palette multiply %s\n",argv[2]);return 0;
}
'''


class StoryPpulseFastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='zato_ppulse_fast_');cls.folder=Path(cls.temp.name)
        source=(ROOT/'nds/source/story_gfx.c').read_text(encoding='utf8')
        (cls.folder/'instrumented.c').write_text(source,encoding='utf8');(cls.folder/'probe.c').write_text(HARNESS,encoding='utf8')
        cc=os.environ.get('CC') or shutil.which('gcc') or shutil.which('clang')
        if not cc:raise unittest.SkipTest('host C compiler unavailable')
        cls.exe=cls.folder/'probe.exe'
        result=subprocess.run([cc,'-std=c11','-O2','-Wall','-Wextra','-Werror','-I'+str(ROOT/'nds/source'),str(cls.folder/'probe.c'),'-o',str(cls.exe)],capture_output=True,text=True)
        if result.returncode:raise AssertionError(result.stdout+result.stderr)
        data=(ROOT/'nds/nitrofs/story/assets.zat').read_bytes();h=struct.unpack_from('<8s8I',data);frame_at=40+h[3]*16
        cls.palettes=cls.folder/'palettes.bin';cls.palettes.write_bytes(b''.join(data[frame_at+i*196+36:frame_at+i*196+68] for i in range(207,212)))
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def check(self,case):
        result=subprocess.run([str(self.exe),str(self.palettes),case],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_all_rgb5_destination_values_real_palettes_and_nine_source_alpha_levels(self):self.check('exhaustive')
    def test_identity_multiply_full_pixels_and_audio_service(self):self.check('dispatch')
    def test_mask_odd_origins_clipping_alpha_zero_and_cache_invalidation(self):self.check('clip_cache')
    def test_flip_zoom_fractional_origin_and_crossfade_keep_generic_fallback(self):self.check('fallback')


if __name__=='__main__':unittest.main()
