"""Q5 row bounds, exact allocation accounting and cache lifecycle."""
from pathlib import Path
import os,shutil,struct,subprocess,sys,tempfile,unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
try:
    from test_story_soft_render import fixture
    from test_story_gfx import literal_lz
except ImportError:
    from nds.tests.test_story_soft_render import fixture
    from nds.tests.test_story_gfx import literal_lz
ROOT=Path(__file__).resolve().parents[2]

HARNESS=r'''
#include "product.c"
#include <stdio.h>
static void need(int yes,const char*why){if(!yes){fprintf(stderr,"FAIL %s (%s)\n",why,story_gfx_error());exit(2);}}
int main(int argc,char**argv){
 need(argc==4&&story_gfx_open(argv[1],argv[2]),"open");size_t base=resident;
 unsigned bank=frames[1].bank,raw=banks[bank].raw,map=frames[1].map_raw,span=frames[1].height*4u;
 if(!strcmp(argv[3],"lifecycle")){
  need(story_gfx_show(1,1,0,0,-1,0),"first soft acquire");
  need(resident==base+raw+map+span,"soft map must account for height*4 appended bounds");
  const uint16_t*b=fc[1].map+map/2;const unsigned expected[16]={0,0,0,0,5,6,2,14,0,16,0,16,0,0,15,16};
  for(unsigned i=0;i<16;i++)need(b[i]==expected[i],"bounds must use A8>=4 and retain sparse row holes");
  StoryGfxSnapshot point;need(story_gfx_snapshot(&point),"snapshot");
  need(story_gfx_restore_begin(),"begin restore");story_gfx_clear();need(resident==base+raw+map+span,"restore backup pins map and bounds together");
  need(story_gfx_show(2,2,0,0,-1,0),"new soft frame");need(resident==base+raw+2*(map+span),"distinct map bounds charged once each");
  story_gfx_restore_rollback();need(resident==base+raw+map+span&&fc[1].map&&!fc[2].map,"rollback releases new bounds and restores old refs");
  need(story_gfx_snapshot_valid(&point),"snapshot steady budget after rollback");
  story_gfx_begin_batch(0);need(story_gfx_show(2,1,0,0,-1,0)&&story_gfx_end_batch(),"replace image in batch");
  need(resident==base+raw+map+span&&!fc[1].map&&fc[2].map,"last old ref releases complete map allocation");
  story_gfx_clear();need(resident==base,"clear frees all map/span/bank allocations");
 }else if(!strcmp(argv[3],"budget")){
  resident=NORMAL_LIMIT-raw-map;size_t before=resident;
  StoryGfxSnapshot target={.count=1};target.layers[0]=(StoryGfxLayerSnapshot){.image=1,.tag=1};
  need(!story_gfx_snapshot_valid(&target),"snapshot target union must also charge appended bounds");
  need(!story_gfx_show(1,1,0,0,-1,0),"bounds must count against allocation budget");
  need(resident==before&&!fc[1].map&&!bc[bank].data,"failed map-plus-bounds acquire rolls bank back");resident=base;
 }else if(!strcmp(argv[3],"bad_map")){
  need(story_gfx_show(2,2,0,0,-1,0),"pin other frame in shared soft bank");size_t before=resident;
  need(!story_gfx_show(1,1,0,0,-1,0),"bad soft tile index must fail");
  need(resident==before&&!fc[1].map&&fc[2].map&&bc[bank].refs==1,"failed inflate/map validation must release whole allocation and preserve shared bank");
  story_gfx_clear();need(resident==base,"bad-map cleanup leaves no bounds allocation");
 }else need(false,"case");
 story_gfx_close();need(!resident,"close resets residency");puts("PASS soft spans");return 0;
}
'''


class StorySoftSpanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='zato_soft_spans_');cls.folder=Path(cls.temp.name)
        a,c,colors,alphas,_=fixture();a=bytearray(a)
        values=np.zeros((8,16),dtype='u1');values[1]=3;values[2,5]=4;values[3,2]=255;values[3,13]=8;values[4]=255;values[5,0]=values[5,15]=4;values[6]=3;values[7,15]=7
        yy,xx=np.indices((8,16));rgb=(0x8000|(xx*2)|((yy*4)<<5)|(((xx+yy)%32)<<10)).astype('<u2')
        raw=bytearray()
        for alpha in (values,values[:,::-1]):
            color=np.where(alpha,rgb,0).astype('<u2')
            for x in (0,8):raw.extend(color[:,x:x+8].tobytes());raw.extend(alpha[:,x:x+8].tobytes())
        at,n,size,stride=struct.unpack_from('<4I',a,56);packed=literal_lz(raw);assert len(packed)==n and len(raw)==size;a[at:at+n]=packed
        cls.assets=cls.folder/'assets.zat';cls.assets.write_bytes(a);cls.catalog=cls.folder/'gfx.bin';cls.catalog.write_bytes(c)
        # Corrupt only frame1's literal map entry; frame2 in its bank stays valid.
        map_at=struct.unpack_from('<I',a,40+32+196+4)[0];bad=bytearray(a);bad[map_at+5:map_at+7]=b'\xff\xff'
        cls.bad_assets=cls.folder/'bad_assets.zat';cls.bad_assets.write_bytes(bad)
        (cls.folder/'product.c').write_bytes((ROOT/'nds/source/story_gfx.c').read_bytes());(cls.folder/'probe.c').write_text(HARNESS,encoding='utf8')
        cls.exe=cls.folder/'probe.exe';cc=os.environ.get('CC') or shutil.which('gcc') or shutil.which('clang')
        if not cc:raise unittest.SkipTest('host C compiler unavailable')
        r=subprocess.run([cc,'-std=c11','-O2','-Wall','-Wextra','-Werror','-I'+str(ROOT/'nds/source'),str(cls.folder/'probe.c'),'-o',str(cls.exe)],capture_output=True,text=True)
        if r.returncode:raise AssertionError(r.stdout+r.stderr)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def check(self,case):
        r=subprocess.run([str(self.exe),str(self.bad_assets if case=='bad_map' else self.assets),str(self.catalog),case],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
    def test_q5_bounds_and_lastref_batch_restore_rollback_lifecycle(self):self.check('lifecycle')
    def test_allocation_budget_counts_appended_bounds_and_unwinds_failure(self):self.check('budget')
    def test_bad_map_releases_appended_allocation_without_evicting_shared_bank(self):self.check('bad_map')


if __name__=='__main__':unittest.main()
