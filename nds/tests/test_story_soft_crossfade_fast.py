"""Lossless soft-mask crossfade pixels before/after the performance fix.

The performance RED is the separately recorded248ms native upper render and
eight-glyph reveal batches. This oracle retains the original Q12 arithmetic.
"""
from pathlib import Path
import os,shutil,subprocess,tempfile,unittest

ROOT=Path(__file__).resolve().parents[2]

HARNESS=r'''
#include "product.c"
#include <stdio.h>
static PackFrame test_frames[2];
static PackBank test_banks[2];
static Program test_programs[1];
static uint16_t maps[2][512],storage[256*192+2],expected[256*192];
static uint8_t tiles[2][512*192];
static unsigned services,pattern;static bool raw_low_words;
void audio_demo_service(void){services++;}
static void need(int yes,const char*why){if(!yes){fprintf(stderr,"FAIL %s\n",why);exit(2);}}
static uint32_t random_word(unsigned v){v^=v>>16;v*=0x7feb352du;v^=v>>15;v*=0x846ca68bu;return v^(v>>16);}
static unsigned opacity(unsigned frame,unsigned x,unsigned y){
 if(pattern==1)return (x*3+y*7+frame)%4?255:0;
 if(pattern==2)return 0;
 unsigned a=frame?(y/2)%33:(x/4)%33;
 if(a==32)return 255;
 if(!a)return (x+y+frame)%4; /*Include A8=1..3 whose Q5 opacity is zero. */
 return a*8;
}
static uint16_t source_color(unsigned frame,unsigned x,unsigned y){return opacity(frame,x,y)?(uint16_t)((raw_low_words?0:0x8000u)|(random_word(frame*733u+x*43u+y*113u)&32767u)):0;}
static uint16_t destination(unsigned x,unsigned y){return (uint16_t)random_word(x*23u+y*251u+9u);}
static void setup(unsigned width,unsigned height,unsigned old_width,unsigned old_height,unsigned kind){
 pattern=kind;memset(test_frames,0,sizeof(test_frames));memset(tiles,0,sizeof(tiles));
 for(unsigned frame=0;frame<2;frame++){
  PackFrame*f=&test_frames[frame];f->bank=frame;f->width=frame?old_width:width;f->height=frame?old_height:height;
  f->cols=(f->width+7)/8;f->rows=(f->height+7)/8;f->format=2;
  f->map_raw=(uint32_t)f->cols*f->rows*2;
  for(unsigned ty=0;ty<f->rows;ty++)for(unsigned tx=0;tx<f->cols;tx++){
   unsigned id=ty*f->cols+tx;maps[frame][id]=(uint16_t)id;uint8_t*tile=tiles[frame]+id*192;
   for(unsigned py=0;py<8;py++)for(unsigned px=0;px<8;px++){
    unsigned x=tx*8+px,y=ty*8+py,pos=py*8+px;
    uint16_t color=source_color(frame,x,y);memcpy(tile+pos*2,&color,2);tile[128+pos]=(uint8_t)opacity(frame,x,y);
   }
  }
  uint16_t*bounds=maps[frame]+f->cols*f->rows;
  for(unsigned y=0;y<f->height;y++){
   unsigned left=f->width,right=0;
   for(unsigned x=0;x<f->width;x++)if(opacity(frame,x,y)>=4){if(left==f->width)left=x;right=x+1;}
   bounds[y*2]=(uint16_t)(right?left:0);bounds[y*2+1]=(uint16_t)right;
  }
  test_banks[frame].tile_bytes=192;fc[frame].map=maps[frame];bc[frame].data=tiles[frame];
 }
 frames=test_frames;banks=test_banks;programs=test_programs;
 test_programs[0]=(Program){.alpha=4096,.blend=0,.z0=Q,.z1=Q,.flip=1};
}
static uint16_t reference(uint16_t d,uint16_t c,uint16_t o,unsigned ca8,unsigned oa8,unsigned mix,const Program*p){
 uint64_t ca=((((uint64_t)ca8+4)/8)*(unsigned)p->alpha+16)/32;
 uint64_t oa=((((uint64_t)oa8+4)/8)*(unsigned)p->alpha+16)/32;
 uint64_t alpha=(ca*mix+oa*(4096-mix)+2048)/4096;
 if(!alpha)return d;
 uint16_t value=0x8000;
 for(unsigned channel=0;channel<3;channel++){
  unsigned shift=channel*5,dc=(d>>shift)&31,cc=(c>>shift)&31,oc=(o>>shift)&31;
  uint64_t prem=(ca*cc*mix+oa*oc*(4096-mix)+2048)/4096,out;
  if(p->blend==1){out=dc+(prem+2048)/4096;if(out>31)out=31;}
  else if(p->blend==2){uint64_t factor=(31*(4096-alpha)+prem+15)/31;out=(dc*factor+2048)/4096;}
  else out=(dc*(4096-alpha)+prem+2048)/4096;
  value|=(uint16_t)(out<<shift);
 }
 return value;
}
static int ref_floor(int64_t value,int divisor){if(value>=0)return (int)(value/divisor);return -(int)((-value+divisor-1)/divisor);}
static void pixels(unsigned mix,Transform t,int top,int bottom){
 uint16_t*screen=storage+1;storage[0]=0x3579;storage[256*192+1]=0x2468;
 for(unsigned y=0;y<192;y++)for(unsigned x=0;x<256;x++){
  uint16_t d=destination(x,y);screen[y*256+x]=expected[y*256+x]=d;
  int sx=ref_floor((int64_t)x*Q+Q/2-t.x,t.z),sy=ref_floor((int64_t)y*Q+Q/2-t.y,t.z);
  if(test_programs[0].flip<0)sx=test_frames[0].width-1-sx;
  if((int)y<top||(int)y>=bottom||sx<0||sy<0||sx>=test_frames[0].width||sy>=test_frames[0].height)continue;
  unsigned ca=opacity(0,(unsigned)sx,(unsigned)sy),oa=0;uint16_t c=source_color(0,(unsigned)sx,(unsigned)sy),o=0;
  if(sx<test_frames[1].width&&sy<test_frames[1].height){oa=opacity(1,(unsigned)sx,(unsigned)sy);o=source_color(1,(unsigned)sx,(unsigned)sy);}
  expected[y*256+x]=reference(d,c,o,ca,oa,mix,&test_programs[0]);
 }
 draw_layer(&(Layer){.program=0},(Sample){0,1,mix},t,screen,top,bottom);
 for(unsigned i=0;i<256*192;i++)if(screen[i]!=expected[i]){
  fprintf(stderr,"pixel%u,%u mix%u got%04x expected%04x\n",i%256,i/256,mix,screen[i],expected[i]);exit(2);
 }
 need(storage[0]==0x3579&&storage[256*192+1]==0x2468,"screen canary overwritten");
}
int main(int argc,char**argv){
 need(argc==2,"args");
 if(!strcmp(argv[1],"alphas")){
  setup(132,66,132,66,0);unsigned before=services;
  /*Every33x33 source-alpha pair,8 independent RGB triples each; all actual
   * sequence k0..5 and every other valid integer Q4 mix including endpoints. */
  for(unsigned k=0;k<=16;k++)pixels(k*256,(Transform){5*Q,32*Q,Q},0,192);
  need(services>before,"soft crossfade must service audio");
 }else if(!strcmp(argv[1],"clip")){
  const int positions[][4]={{5,32,0,192},{-5,-2,0,192},{251,187,0,192},{-3,20,24,34},{256,32,0,192},{5,192,0,192}};
  for(unsigned pattern=0;pattern<3;pattern++){
   setup(23,17,23,17,pattern);
   for(unsigned i=0;i<6;i++)for(unsigned k=0;k<=16;k++)pixels(k*256,(Transform){positions[i][0]*Q,positions[i][1]*Q,Q},positions[i][2],positions[i][3]);
  }
 }else if(!strcmp(argv[1],"raw_low_words")){
  raw_low_words=true;setup(132,66,132,66,0);
  pixels(0,(Transform){5*Q,32*Q,Q},0,192);
  pixels(4096,(Transform){5*Q,32*Q,Q},0,192);
  pixels(1280,(Transform){5*Q,32*Q,Q},0,192);
 }else if(!strcmp(argv[1],"fallback")){
  for(unsigned kind=0;kind<7;kind++){
   setup(43,37,kind==1?37:43,kind==1?33:37,0);
   Transform t={5*Q,32*Q,Q};unsigned mix=1024;
   if(kind==0)mix=300;
   if(kind==2)test_programs[0].flip=-1;
   if(kind==3)t.z=Q*3/2;
   if(kind==4)t.x+=Q/2;
   if(kind==5)test_programs[0].alpha=2048;
   if(kind==6)test_programs[0].blend=2;
   pixels(mix,t,0,192);
  }
 }else need(false,"case");
 printf("PASS soft crossfade %s\n",argv[1]);return 0;
}
'''


class StorySoftCrossfadeFastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='zato_soft_crossfade_');cls.folder=Path(cls.temp.name)
        (cls.folder/'product.c').write_bytes((ROOT/'nds/source/story_gfx.c').read_bytes())
        (cls.folder/'probe.c').write_text(HARNESS,encoding='utf8')
        cc=os.environ.get('CC') or shutil.which('gcc') or shutil.which('clang')
        if not cc:raise unittest.SkipTest('host C compiler unavailable')
        cls.exe=cls.folder/'probe.exe'
        result=subprocess.run([cc,'-std=c11','-O2','-Wall','-Wextra','-Werror','-I'+str(ROOT/'nds/source'),str(cls.folder/'probe.c'),'-o',str(cls.exe)],capture_output=True,text=True)
        if result.returncode:raise AssertionError(result.stdout+result.stderr)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def check(self,case):
        result=subprocess.run([str(self.exe),case],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_all_source_q5_alpha_pairs_all_q4_mixes_and_random_rgb15(self):self.check('alphas')
    def test_zero_full_masks_odd_origins_unaligned_output_and_clipping(self):self.check('clip')
    def test_raw_rgb15_without_high_bit_matches_generic_endpoints(self):self.check('raw_low_words')
    def test_partial_mix_unequal_dimensions_zoom_flip_fractional_alpha_and_blend_fallback(self):self.check('fallback')


if __name__=='__main__':unittest.main()
