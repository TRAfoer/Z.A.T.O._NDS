from pathlib import Path
import shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[2]
HARNESS=r'''
#include "story_flow.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static SceneTextInfo infos[3]={{.char_count=3,.duration_ticks=2,.pages_cn=1,.pages_bi=1},
 {.char_count=6,.duration_ticks=5,.pages_cn=2,.pages_bi=2},
 {.char_count=2,.duration_ticks=4,.pause_count=1,.pages_cn=1,.pages_bi=1,.mode=3}};
static SceneTextPause pause={1,4,2};static unsigned events_seen,sfx,selected;
static bool event(void*c,const StoryEvent*e,uint32_t n,bool q){(void)c;(void)n;if(!q){events_seen++;if(e->op==ST_SFX)sfx++;}return true;}
static bool select_text(void*c,unsigned id,unsigned slot){(void)c;(void)slot;selected=id;return id<3;}
static const SceneTextInfo*info(void*c,unsigned id){(void)c;return id<3?&infos[id]:NULL;}
static const SceneTextPause*getpause(void*c,unsigned id,unsigned i){(void)c;return id==2&&!i?&pause:NULL;}
static unsigned visible(void*c,unsigned id,uint32_t ticks){(void)c;unsigned v=ticks+1;return v<infos[id].char_count?v:infos[id].char_count;}
static unsigned page(void*c,unsigned id,unsigned count,int bi){(void)c;return id==1&&count>(bi?1u:3u)?1:0;}
static unsigned limit(void*c,unsigned id,unsigned p,int bi){(void)c;return id==1&&!p?(bi?1u:3u):infos[id].char_count;}
static const StoryFlowOps ops={.event=event,.select_text=select_text,.info=info,.pause=getpause,.visible=visible,.page=page,.limit=limit};
static void need(int ok,const char*why){if(!ok){fprintf(stderr,"FAIL %s\n",why);exit(2);}}
static StoryFlow f;static StoryScript script;static StoryEvent ev[8];static StoryChapter chapters[2];
static void begin(unsigned count){script=(StoryScript){.events=ev,.count=count,.chapters=chapters,.chapter_count=1,.text_count=3};chapters[0]=(StoryChapter){.first_pc=0,.end_pc=count};need(story_flow_init(&f,&script,&ops,NULL),"init");}
static void frame(unsigned delta){story_flow_refresh(&f);story_flow_mark_presented(&f);story_flow_tick(&f,delta);}
int main(int argc,char**argv){need(argc==2,"case");
 if(!strcmp(argv[1],"nw")){
  ev[0]=(StoryEvent){.op=ST_TEXT,.flags=1,.a=0,.d=2};ev[1]=(StoryEvent){.op=ST_SFX};ev[2]=(StoryEvent){.op=ST_END};begin(3);
  need(f.visible==1&&!sfx,"first glyph");frame(2);need(f.visible==3&&!sfx,"final glyph not yet committed");
  frame(1);need(sfx==1&&f.done,"audio follows final rendered frame");
 }else if(!strcmp(argv[1],"pages")){
  ev[0]=(StoryEvent){.op=ST_TEXT,.a=1,.d=5};ev[1]=(StoryEvent){.op=ST_END};begin(2);frame(20);
  need(f.visible==3&&f.page_hold&&f.local==2,"page clock cap");frame(100);need(f.local==2,"read dwell excluded");
  story_flow_advance(&f);need(f.page==1&&!f.page_hold,"page advance");frame(1);need(f.visible==4,"resume next glyph");
 }else if(!strcmp(argv[1],"wait")){
  ev[0]=(StoryEvent){.op=ST_WAIT,.a=5,.b=1};ev[1]=(StoryEvent){.op=ST_WAIT,.a=-1};ev[2]=(StoryEvent){.op=ST_END};begin(3);
  story_flow_advance(&f);need(f.pc==1&&f.waiting,"hard ignores click");frame(5);need(f.forever&&!f.hard&&f.pc==2,"indefinite wait");
  frame(1000);need(!f.done,"no invented indefinite timeout");story_flow_advance(&f);need(f.done,"soft accepts click");
 }else if(!strcmp(argv[1],"internal")){
  ev[0]=(StoryEvent){.op=ST_TEXT,.flags=7,.a=2,.d=4};ev[1]=(StoryEvent){.op=ST_SFX};ev[2]=(StoryEvent){.op=ST_END};begin(3);
  frame(4);need(!sfx&&f.pause_index==0,"wait endpoint first");frame(1);need(!sfx&&f.pause_index==1,"w and nw separate");
  frame(1);need(sfx==1&&!f.text_active,"centered no ghost");
 }else if(!strcmp(argv[1],"bi_continuation")){
  infos[1].continuation=1;infos[1].new_char_start=1;
  ev[0]=(StoryEvent){.op=ST_TEXT,.a=1,.d=5};ev[1]=(StoryEvent){.op=ST_END};begin(2);
  f.bilingual=true;need(story_flow_seek(&f,0),"paired continuation seek");
  need(f.page==1,"continuation must select its current-language page");
 }else if(!strcmp(argv[1],"bi_toggle")){
  ev[0]=(StoryEvent){.op=ST_TEXT,.a=1,.d=5};ev[1]=(StoryEvent){.op=ST_END};begin(2);frame(20);
  need(f.visible==3,"normal page endpoint");story_flow_toggle_language(&f);
  need(f.bilingual&&f.page==1&&f.visible==3,"language toggle preserves logical reveal anchor");
 }else if(!strcmp(argv[1],"seek")){
  ev[0]=(StoryEvent){.op=ST_TEXT,.a=0,.d=2};ev[1]=(StoryEvent){.op=ST_WAIT,.a=60,.b=1};
  ev[2]=(StoryEvent){.op=ST_TEXT,.flags=1,.a=0,.d=2};ev[3]=(StoryEvent){.op=ST_JUMP,.a=4,.b=1};
  ev[4]=(StoryEvent){.op=ST_TEXT,.a=1,.d=5};ev[5]=(StoryEvent){.op=ST_END};begin(6);
  script.chapter_count=2;chapters[0].end_pc=4;chapters[1]=(StoryChapter){.first_pc=4,.end_pc=6,.first_text=1,.text_count=1};
  need(story_flow_seek(&f,1),"seek");need(f.now==62&&f.chapter==1&&f.text_id==1&&selected==1,"minimum authored time and chapter state");
 }else return 9;
 return 0;
}
'''
class StoryFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();folder=Path(cls.temp.name);src=folder/'flow.c';src.write_text(HARNESS);cls.exe=folder/'flow.exe'
        p=subprocess.run([shutil.which('gcc'),'-std=c11','-O2','-Wall','-Wextra','-Werror','-I'+str(ROOT/'nds/source'),str(ROOT/'nds/source/story_flow.c'),str(src),'-o',str(cls.exe)],capture_output=True,text=True)
        if p.returncode:raise AssertionError(p.stderr)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def check(self,case):
        p=subprocess.run([str(self.exe),case],capture_output=True,text=True);self.assertEqual(p.returncode,0,p.stderr)
    def test_nw_requires_final_render_before_following_sound(self):self.check('nw')
    def test_manual_page_clock_freezes_during_reading(self):self.check('pages')
    def test_hard_and_indefinite_wait_semantics(self):self.check('wait')
    def test_internal_wait_and_nw_have_separate_boundaries(self):self.check('internal')
    def test_chapter_seek_uses_authored_minimum_time(self):self.check('seek')
    def test_bilingual_continuation_selects_its_paired_page(self):self.check('bi_continuation')
    def test_language_toggle_remaps_current_logical_reveal(self):self.check('bi_toggle')
if __name__=='__main__':unittest.main()
