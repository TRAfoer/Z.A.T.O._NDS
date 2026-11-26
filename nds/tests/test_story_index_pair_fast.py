"""Two indexed layers versus independent sequential RGB15 compositing."""
from pathlib import Path
import os,shutil,struct,subprocess,tempfile,unittest
try:
    from test_story_ppulse_fast import HARNESS as BASE
except ImportError:
    from nds.tests.test_story_ppulse_fast import HARNESS as BASE
ROOT=Path(__file__).resolve().parents[2]
HARNESS=BASE.split('int main(')[0]+r'''
static uint8_t pair_tiles[128*40],dummy_catalog;
static PackBank pair_banks[2];static Program pair_programs[2];
static Image pair_images[2];static Geometry pair_geometry[2];
static unsigned pair_pattern;
static unsigned pair_index(unsigned frame,unsigned x,unsigned y){return frame?(x*5+y*7+3)&15u:palette_index(x,y);}
static bool pair_opaque(unsigned frame,unsigned x,unsigned y){
 if(pair_pattern==2)return frame!=0;
 if(pair_pattern==3)return frame==0;
 return !pair_pattern||(x*7+y*3+frame*2)%5!=0;
}
static void pair_setup(unsigned width,unsigned height,unsigned pattern){
 setup(width,height,0);pair_pattern=pattern;memset(pair_tiles,0,sizeof(pair_tiles));
 for(unsigned frame=0;frame<2;frame++){
  uint8_t*data=frame?pair_tiles:test_tiles;memset(data,0,sizeof(pair_tiles));
  for(unsigned y=0;y<height;y++)for(unsigned x=0;x<width;x++){
   unsigned id=(y/8)*test_frames[0].cols+x/8,pos=(y&7)*8+(x&7);uint8_t*t=data+id*40;
   t[pos/2]|=(uint8_t)(pair_index(frame,x,y)<<((pos&1)*4));
   if(pair_opaque(frame,x,y))t[32+y%8]|=(uint8_t)(1u<<(x&7));
  }
  test_frames[frame].bank=frame;test_frames[frame].x=5;test_frames[frame].y=45;
  pair_banks[frame].tile_bytes=40;bc[frame].data=data;
  pair_images[frame]=(Image){.kind=0,.data=(int)frame,.count=1};
  pair_geometry[frame]=(Geometry){.source_width=(int)width*2,.fw=(int)width*Q,.fh=(int)height*Q,.sh=192*Q};
 }
 pair_programs[0]=(Program){.alpha=4096,.blend=0,.z0=Q,.z1=Q,.flip=1};
 pair_programs[1]=(Program){.alpha=819,.blend=2,.z0=Q,.z1=Q,.flip=1};
 banks=pair_banks;programs=pair_programs;images=pair_images;geometries=pair_geometry;catalog=&dummy_catalog;
 count=2;layers[0]=(Layer){.image=0,.tag=0,.program=0,.loaded=true};layers[1]=(Layer){.image=1,.tag=1,.program=1,.loaded=true};
 moving=batch_valid=replay=false;state_version++;
}
static uint16_t layer_reference(uint16_t d,uint16_t c,const Program*p){
 if(p->blend==2)return reference(d,c,(unsigned)p->alpha);
 if(!p->alpha)return d;
 uint16_t value=0x8000;
 for(unsigned s=0;s<15;s+=5){unsigned dc=(d>>s)&31,cc=(c>>s)&31,out;
  if(p->blend==1){out=dc+(cc*(unsigned)p->alpha+2048)/4096;if(out>31)out=31;}
  else out=(dc*(4096-(unsigned)p->alpha)+cc*(unsigned)p->alpha+2048)/4096;
  value|=(uint16_t)(out<<s);
 }
 return value;
}
static void pair_pixels(void){
 for(unsigned i=0;i<256*192;i++)expected[i]=0x8000;
 for(unsigned n=0;n<count;n++){
  const Layer*l=&layers[n];unsigned frame=l->image;const PackFrame*f=&test_frames[frame];const Program*p=&programs[l->program];
  int z=p->z0,ox=f->x*Q+(int)((int64_t)f->width*(Q-z)/2),oy=f->y*Q+(int)((int64_t)f->height*(Q-z)/2);
  for(unsigned y=0;y<192;y++)for(unsigned x=0;x<256;x++){
   int sx=floor_div((int64_t)x*Q+Q/2-ox,z),sy=floor_div((int64_t)y*Q+Q/2-oy,z);
   if(p->flip<0)sx=f->width-1-sx;
   if(sx<0||sy<0||sx>=f->width||sy>=f->height||!pair_opaque(frame,(unsigned)sx,(unsigned)sy))continue;
   uint16_t c=f->palette[pair_index(frame,(unsigned)sx,(unsigned)sy)]|0x8000;
   expected[y*256+x]=layer_reference(expected[y*256+x],c,p);
  }
 }
 need(story_gfx_draw(0,screen),"pair draw failed");
 for(unsigned i=0;i<256*192;i++)if(screen[i]!=expected[i]){fprintf(stderr,"pair pixel%u,%u got%04x expected%04x\n",i%256,i/256,screen[i],expected[i]);exit(2);}
}
int main(int argc,char**argv){
 need(argc==3,"args");uint16_t palettes[16][16];FILE*file=fopen(argv[1],"rb");need(file&&fread(palettes,32,16,file)==16,"palettes");fclose(file);
 if(!strcmp(argv[2],"palettes")){
  pair_setup(33,17,0);
  for(unsigned b=0;b<11;b++)for(unsigned t=11;t<16;t++)for(unsigned a=0;a<10;a++){
   memcpy(test_frames[0].palette,palettes[b],32);memcpy(test_frames[1].palette,palettes[t],32);pair_programs[1].alpha=(int)alphas[a];pair_pixels();
  }
 }else if(!strcmp(argv[2],"clip")){
  const int xy[][2]={{5,45},{-5,-2},{251,187},{256,45},{5,192}};
  for(unsigned pattern=0;pattern<4;pattern++)for(unsigned pos=0;pos<5;pos++){
   pair_setup(23,17,pattern);memcpy(test_frames[0].palette,palettes[0],32);memcpy(test_frames[1].palette,palettes[11],32);
   test_frames[0].x=test_frames[1].x=(int16_t)xy[pos][0];test_frames[0].y=test_frames[1].y=(int16_t)xy[pos][1];pair_pixels();
  }
 }else if(!strcmp(argv[2],"cache_gate")){
  pair_setup(23,17,1);memcpy(test_frames[0].palette,palettes[0],32);memcpy(test_frames[1].palette,palettes[11],32);pair_pixels();
  test_frames[0].palette[7]^=0x0421;pair_pixels();test_frames[1].palette[3]^=0x0821;pair_pixels();pair_programs[1].alpha=3686;pair_pixels();
  /*PAIR->COMPONENT->PAIR with identical top palette/alpha must rebuild the
   * union representation, including restoration of the original pair key. */
  layers[2]=layers[1];layers[1]=layers[0];layers[1].tag=2;count=3;pair_pixels();
  layers[1]=layers[2];count=2;pair_pixels();
  for(unsigned gate=0;gate<7;gate++){
   Program saved=pair_programs[gate==5||gate==6?0:1];PackFrame frame=test_frames[1];
   if(gate==0)test_frames[1].x++;
   if(gate==1)test_frames[1].width--;
   if(gate==2)pair_programs[1].flip=-1;
   if(gate==3)pair_programs[1].z0=pair_programs[1].z1=2*Q;
   if(gate==4)pair_programs[1].blend=0;
   if(gate==5)pair_programs[0].alpha=2048;
   if(gate==6)pair_programs[0].blend=1;
   pair_pixels();pair_programs[gate==5||gate==6?0:1]=saved;test_frames[1]=frame;
  }
 }else need(false,"case");
 puts("PASS indexed pair");return 0;
}
'''


class StoryIndexPairFastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='zato_index_pair_');cls.folder=Path(cls.temp.name)
        (cls.folder/'instrumented.c').write_bytes((ROOT/'nds/source/story_gfx.c').read_bytes());(cls.folder/'probe.c').write_text(HARNESS,encoding='utf8')
        cc=os.environ.get('CC') or shutil.which('gcc') or shutil.which('clang')
        if not cc:raise unittest.SkipTest('host C compiler unavailable')
        cls.exe=cls.folder/'probe.exe';r=subprocess.run([cc,'-std=c11','-O2','-Wall','-Wextra','-Werror','-Wno-unused-function','-I'+str(ROOT/'nds/source'),str(cls.folder/'probe.c'),'-o',str(cls.exe)],capture_output=True,text=True)
        if r.returncode:raise AssertionError(r.stdout+r.stderr)
        data=(ROOT/'nds/nitrofs/story/assets.zat').read_bytes();h=struct.unpack_from('<8s8I',data);at=40+h[3]*16
        cls.palettes=cls.folder/'palettes.bin';cls.palettes.write_bytes(b''.join(data[at+i*196+36:at+i*196+68] for i in list(range(193,204))+list(range(207,212))))
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def check(self,case):
        r=subprocess.run([str(self.exe),str(self.palettes),case],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
    def test_actual_palettes_nine_alpha_levels_and_zero(self):self.check('palettes')
    def test_zero_partial_masks_and_clipping(self):self.check('clip')
    def test_cache_modes_palette_alpha_invalidation_and_strict_gates(self):self.check('cache_gate')


if __name__=='__main__':unittest.main()
