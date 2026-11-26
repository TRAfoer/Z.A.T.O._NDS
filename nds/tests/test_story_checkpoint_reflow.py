"""Independent wire4 geometry fixture + real Flow/checkpoint/text C reflow."""
from pathlib import Path
import shutil,struct,subprocess,tempfile,unittest,zlib
ROOT=Path(__file__).resolve().parents[2]

def compact(gids,xs,ys,pages):
    n=len(gids);return struct.pack('<'+'H'*n,*gids)+bytes(xs)+bytes(ys)+bytes(pages)+b'\0\0'

def fixture(scaled):
    count=211;records=[]
    for id in range(3):
        ticks=[0]*count if id==0 else list(range(count))
        runs=[(count,0)]if id==0 else [(1,0),(count-1,1)]
        timeline=struct.pack('<H',len(runs))+b''.join(struct.pack('<HH',n,t)for n,t in runs)
        pauses=struct.pack('<3I',20,50,21)if id==2 else b''
        pages=[];xs=[];cnys=[];enys=[]
        for i in range(count):
            page=0 if i<50 else 1 if i<100 else 2;j=i-(0,50,100)[page]
            pages.append(page);xs.append(31+(j%40)*4);cnys.append(35+(j//40)*45);enys.append(cnys[-1]+(25 if scaled else 14))
        cn=compact([0]*count,xs,cnys,pages);en=compact([1]*count,xs,enys,pages)
        pause_at=48+len(timeline);cn_at=pause_at+len(pauses);en_at=cn_at+len(cn);bi_at=en_at+len(en);threshold_at=bi_at+len(cn);limits_at=threshold_at+count*2
        f=[10+id,count,0 if id==0 else 210,int(id==2),3,3,0,0,0,48,pause_at,cn_at,count,en_at,count,0,0,0,bi_at,count,threshold_at,count,limits_at,0]
        records.append(struct.pack('<24H',*f)+timeline+pauses+cn+en+cn+struct.pack('<'+'H'*count,*range(1,count+1))+struct.pack('<3H',50,100,211))
    glyph_at=136+len(records)*12;atlas_at=glyph_at+16;cnheight=24 if scaled else 12
    atlas=bytes([255])*((4*cnheight+7)//8)+bytes([255])*2
    header_at=(atlas_at+len(atlas)+3)&~3;commands_at=header_at+8;shared_end=commands_at+106*10
    maxraw=max(map(len,records));expanded=96+count*4+12+count*30+count*2+6
    h=[2,shared_end+sum(map(len,records)),0,len(records),len(records),2,1,0,136,glyph_at,atlas_at,len(atlas),header_at,commands_at,106,0,0,11,95,0,shared_end,0,shared_end,maxraw,expanded,maxraw,0,shared_end-136,0,4,12,0]
    shared=bytearray(struct.pack('<8s32I',b'ZATSTX02',*h));at=shared_end
    for raw in records:shared.extend(struct.pack('<IHHI',at,len(raw)|32768,len(raw),zlib.crc32(raw)));at+=len(raw)
    shared.extend(struct.pack('<IHH',0,4,(4<<8)|cnheight)+struct.pack('<IHH',(4*cnheight+7)//8,2,(4<<8)|8));shared.extend(atlas);shared.extend(bytes(header_at-len(shared)));shared.extend(bytes(8))
    shared.extend(struct.pack('<HBBHHBB',1,0,0,0xffff,0,0,1)*106);assert len(shared)==shared_end
    struct.pack_into('<I',shared,16,zlib.crc32(shared));return shared+b''.join(records)

HARNESS=r'''
#include "story_checkpoint.h"
#include "story_text.h"
#include "story_gfx.h"
#include "audio_demo.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static void need(int ok,const char*s){if(!ok){fprintf(stderr,"FAIL %s (%s)\n",s,story_text_error());exit(2);}}
void audio_demo_service(void){}
static AudioSceneSnapshot sound={.version=1,.paused=1,.master_music=4096,.master_sfx=4096};
bool audio_scene_snapshot(AudioSceneSnapshot*out){*out=sound;return true;}
bool audio_scene_snapshot_valid(const AudioSceneSnapshot*s){return s&&s->version==1;}
bool audio_scene_restore(const AudioSceneSnapshot*s){sound=*s;sound.paused=1;return true;}
static bool pending;
bool story_gfx_snapshot(StoryGfxSnapshot*out){memset(out,0,sizeof(*out));return true;}
bool story_gfx_snapshot_valid(const StoryGfxSnapshot*s){return s&&s->count==0;}
bool story_gfx_restore(const StoryGfxSnapshot*s){return story_gfx_snapshot_valid(s);}
bool story_gfx_restore_pending(void){return pending;}
bool story_gfx_restore_begin(void){pending=true;return true;}
void story_gfx_restore_commit(void){pending=false;}
void story_gfx_restore_rollback(void){pending=false;}
static bool select_text(void*c,unsigned id,unsigned slot){(void)c;return story_text_select(id,slot)!=0;}
static const SceneTextInfo*info(void*c,unsigned id){(void)c;return story_text_info(id);}
static const SceneTextPause*pause_at(void*c,unsigned id,unsigned i){(void)c;return story_text_pause(id,i);}
static unsigned visible_at(void*c,unsigned id,uint32_t tick){(void)c;return story_text_visible(id,tick);}
static unsigned page_at(void*c,unsigned id,unsigned n,int bi){(void)c;return story_text_page(id,n,bi);}
static unsigned limit_at(void*c,unsigned id,unsigned p,int bi){(void)c;return story_text_page_limit(id,p,bi);}
static StoryEvent events[2];static StoryChapter chapter={.first_pc=0,.end_pc=2,.text_count=3};
static StoryScript script={.count=2,.chapter_count=1,.text_count=3,.events=events,.chapters=&chapter};
static StoryFlowOps ops={.select_text=select_text,.info=info,.pause=pause_at,.visible=visible_at,.page=page_at,.limit=limit_at};
static StoryFlow flow;
static void begin(unsigned id){
 need(story_text_select(id,0),"select source");const SceneTextInfo*t=story_text_info(id);
 events[0]=(StoryEvent){.op=ST_TEXT,.line=t->source_line,.a=(int)id,.d=(int)t->duration_ticks};events[1]=(StoryEvent){.op=ST_END};
 need(story_flow_init(&flow,&script,&ops,NULL),"flow init");flow.scrolling=true;flow.bilingual=true;flow.now=100;story_flow_mark_presented(&flow);
}
static StoryCheckpointDecoded oldcap(void){
 begin(0);StoryFlowSnapshot s;need(story_flow_snapshot(&flow,&s),"capture current source");
 s.value[STORY_FLOW_PAGE]=0;s.value[STORY_FLOW_VISIBLE]=s.value[STORY_FLOW_PRESENTED_VISIBLE]=85;
 StoryCheckpointDecoded d={.flow=s,.audio=sound,.scroll=5,.scroll_manual=1,.scroll_covered=40};return d;
}
#ifndef STORY_CHECKPOINT_REFLOW_VERSION
static bool prepare(const StoryCheckpointDecoded*d,StoryCheckpointDecoded*out){(void)d;(void)out;return false;}
static uint32_t key(void){return 0;}
static void setkey(StoryCheckpointDecoded*d,uint32_t k){(void)d;(void)k;}
static uint32_t getkey(const StoryCheckpointDecoded*d){(void)d;return 0;}
#else
static bool prepare(const StoryCheckpointDecoded*d,StoryCheckpointDecoded*out){return story_checkpoint_prepare(&flow,d,out);}
static uint32_t key(void){return story_text_layout_key();}
static void setkey(StoryCheckpointDecoded*d,uint32_t k){d->layout_key=k;}
static uint32_t getkey(const StoryCheckpointDecoded*d){return d->layout_key;}
#endif
static void word(uint8_t*p,uint32_t n){p[0]=n;p[1]=n>>8;p[2]=n>>16;p[3]=n>>24;}
int main(int argc,char**argv){
 need(argc==3&&story_text_open(argv[1]),"open independent wire4");const char*mode=argv[2];
 if(!strcmp(mode,"legacy")){
  StoryCheckpointDecoded d=oldcap(),out;StoryFlow before=flow;need(!story_flow_snapshot_valid(&flow,&d.flow),"new layout has no old cap85");
  need(story_checkpoint_valid(&flow,&d),"source-valid legacy cap85 must be recoverable");
  need(prepare(&d,&out),"prepare legacy reflow");need(!memcmp(&flow,&before,sizeof(flow))&&d.flow.value[STORY_FLOW_VISIBLE]==85,"validation is not live mutation");
  need(out.flow.value[STORY_FLOW_PAGE]==0&&out.flow.value[STORY_FLOW_VISIBLE]==50,"anchor41 selects cap50 without jumping source time");
  need(out.scroll_covered==40&&!out.scroll_manual&&out.scroll<=story_text_scroll_max(0,50,1),"coverage retained and view rebuilt");
  need(out.flow.value[STORY_FLOW_PC]==d.flow.value[STORY_FLOW_PC]&&out.flow.value[STORY_FLOW_NOW]==100&&out.flow.value[STORY_FLOW_LOCAL]==0,"source scalar clocks preserved");
  need(!memcmp(&out.audio,&d.audio,sizeof(d.audio))&&!memcmp(&out.gfx,&d.gfx,sizeof(d.gfx)),"audio graphics untouched");
  need(story_checkpoint_restore(&flow,&d)&&flow.visible==50&&!flow.presented&&!flow.scroll_ready&&sound.paused,"restore needs first real ACK and held audio");
  d=oldcap();d.scroll_covered=85;need(prepare(&d,&out)&&out.flow.value[STORY_FLOW_PAGE]==1&&out.flow.value[STORY_FLOW_VISIBLE]==100&&out.scroll_covered==85,"fully covered oldcap85 anchors new page100 without inventing coverage");
  begin(1);flow.local=40;story_flow_refresh(&flow);story_flow_mark_presented(&flow);need(story_flow_snapshot(&flow,&d.flow),"progressive capture for reflow");d.audio=sound;d.scroll_covered=30;d.flow.value[STORY_FLOW_VISIBLE]=d.flow.value[STORY_FLOW_PRESENTED_VISIBLE]=37;setkey(&d,0);
  need(prepare(&d,&out)&&out.flow.value[STORY_FLOW_VISIBLE]==41&&out.flow.value[STORY_FLOW_LOCAL]==40,"new cap50 never reveals beyond source tick41");
 }else if(!strcmp(mode,"exact")){
  begin(0);StoryFlowSnapshot s;need(story_flow_snapshot(&flow,&s),"strict capture");StoryCheckpointDecoded d={.flow=s,.audio=sound,.scroll=1,.scroll_manual=1,.scroll_covered=40},out;setkey(&d,key());
  need(key()!=0&&prepare(&d,&out),"same key exact preparation");need(!memcmp(&d,&out,sizeof(d)),"same key retains every viewport and semantic byte");
  d.flow.value[STORY_FLOW_VISIBLE]=d.flow.value[STORY_FLOW_PRESENTED_VISIBLE]=85;need(!story_checkpoint_valid(&flow,&d),"same key cannot use legacy fallback for bad cap");
 }else if(!strcmp(mode,"unaffected")){
  begin(0);StoryFlowSnapshot s;need(story_flow_snapshot(&flow,&s),"native8/12 capture");StoryCheckpointDecoded d={.flow=s,.audio=sound,.scroll=1,.scroll_manual=1,.scroll_covered=40},out;
  need(prepare(&d,&out)&&out.scroll==1&&out.scroll_manual==1&&out.scroll_covered==40&&!memcmp(&s,&out.flow,sizeof(s)),"legacy unaffected manual view exact");
 }else if(!strcmp(mode,"futurekey")){
  StoryCheckpointDecoded d=oldcap(),out;setkey(&d,key()^0x13579bdu);need(prepare(&d,&out)&&out.flow.value[STORY_FLOW_VISIBLE]==50,"different nonzero key reflows even native8/12");
 }else if(!strcmp(mode,"invalid")){
  StoryCheckpointDecoded d=oldcap(),bad,out;bad=d;bad.flow.value[STORY_FLOW_PRESENTED_VISIBLE]++;need(!prepare(&bad,&out),"bad ACK rejected before rewrite");
  bad=d;bad.flow.flags&=~STORY_FLOW_F_PRESENTED;need(!prepare(&bad,&out),"missing ACK rejected");
  bad=d;bad.flow.value[STORY_FLOW_PAGE_READY]=101;need(!prepare(&bad,&out),"future dwell timestamp rejected");
  bad=d;bad.flow.value[STORY_FLOW_PC]++;need(!prepare(&bad,&out),"wrong pc rejected");
  bad=d;bad.flow.value[STORY_FLOW_PAUSE_INDEX]=1;need(!prepare(&bad,&out),"nonexistent pause rejected");
  bad=d;bad.flow.value[STORY_FLOW_PAGE]=65535;need(!prepare(&bad,&out),"old page cannot exceed any supported wire page range");
  begin(1);flow.local=40;story_flow_refresh(&flow);story_flow_mark_presented(&flow);need(story_flow_snapshot(&flow,&d.flow),"progressive source capture");d.audio=sound;d.scroll_covered=0;
  bad=d;bad.flow.value[STORY_FLOW_VISIBLE]=bad.flow.value[STORY_FLOW_PRESENTED_VISIBLE]=42;need(!prepare(&bad,&out),"future visible beyond source tick rejected");
  begin(2);flow.local=30;story_flow_refresh(&flow);story_flow_mark_presented(&flow);need(story_flow_snapshot(&flow,&d.flow),"pending source pause");d.scroll_covered=0;
  bad=d;bad.flow.value[STORY_FLOW_PAUSE_INDEX]=1;need(!prepare(&bad,&out),"future source pause cannot be marked consumed");
  d.flow.value[STORY_FLOW_LOCAL]=d.flow.value[STORY_FLOW_PRESENTED_LOCAL]=50;d.flow.value[STORY_FLOW_PAUSE_INDEX]=1;d.flow.value[STORY_FLOW_VISIBLE]=d.flow.value[STORY_FLOW_PRESENTED_VISIBLE]=10;
  need(!prepare(&d,&out),"consumed source pause requires its glyphs to have been acknowledged");
 }else if(!strcmp(mode,"wire3")){
  begin(0);StoryBackendSnapshot backend={0};uint8_t v1[768],v2[768],v3[768],bad[768];StoryCheckpointDecoded d;
  need(story_checkpoint_capture(&flow,&backend,v1)&&v1[8]==1,"v1 capture preserved");
  need(story_checkpoint_capture_view(&flow,&backend,1,true,40,v3),"new capture");
  need(v3[8]==3&&v3[12]==0x68&&v3[13]==2,"v3 payload616");need(!memcmp(v1+16,v3+16,600),"old600 bytes untouched");
  need(story_checkpoint_decode(v3,&d)&&getkey(&d)==key()&&key()!=0,"v3 layout key");
  memcpy(bad,v3,768);word(bad+628,0);need(!story_checkpoint_decode(bad,&d),"v3 cannot masquerade as legacy zero key");
  for(unsigned i=632;i<768;i++){memcpy(bad,v3,768);bad[i]=1;need(!story_checkpoint_decode(bad,&d),"v3 reserved strict");}
  memcpy(v2,v3,768);word(v2+8,2);word(v2+12,612);memset(v2+628,0,140);need(story_checkpoint_decode(v2,&d)&&getkey(&d)==0&&d.scroll_manual==1,"v2 legacy decode");
  need(story_checkpoint_decode(v1,&d)&&getkey(&d)==0&&d.scroll_covered==flow.visible,"v1 legacy decode");
 }else return 9;
 story_text_close();return 0;
}
'''

class CheckpointReflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='zato_reflow_');cls.path=Path(cls.temp.name);src=cls.path/'test.c';src.write_text(HARNESS);cls.exe=cls.path/'test.exe'
        sources=['story_checkpoint.c','story_flow.c','story_text.c','asset_pack.c']
        run=subprocess.run([shutil.which('gcc')or'gcc','-std=c11','-O2','-Wall','-Wextra','-Werror','-I'+str(ROOT/'nds/source'),str(src)]+[str(ROOT/'nds/source'/s)for s in sources]+['-o',str(cls.exe)],capture_output=True,text=True)
        if run.returncode:raise AssertionError(run.stderr)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def check(self,mode,scaled=True):
        pack=self.path/'fixture.bin';pack.write_bytes(fixture(scaled));run=subprocess.run([str(self.exe),str(pack),mode],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
    def test_old_cap85_partial_coverage_reflows_from_source_anchor(self):self.check('legacy')
    def test_same_layout_key_requires_exact_old_view(self):self.check('exact')
    def test_unaffected_legacy_manual_view_is_not_reset(self):self.check('unaffected',False)
    def test_different_nonzero_key_reflows_even_native_fonts(self):self.check('futurekey',False)
    def test_bad_source_ack_time_pause_and_future_visible_never_migrate(self):self.check('invalid')
    def test_v3_key_wire_reserved_and_v1_v2_compatibility(self):self.check('wire3')
if __name__=='__main__':unittest.main()
