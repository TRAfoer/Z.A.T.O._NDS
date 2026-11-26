"""Continuous lower-story rows and reveal gates; actual format4 reader in C."""
from pathlib import Path
import json,shutil,subprocess,tempfile,unittest

ROOT=Path(__file__).resolve().parents[2]
FLOW=r'''
#include "story_flow.h"
#include <stdio.h>
#include <stdlib.h>
static SceneTextInfo t={.char_count=6,.duration_ticks=5,.pages_cn=2,.pages_bi=2};
static SceneTextPause p={1,4,2};
static bool select_text(void*c,unsigned id,unsigned slot){(void)c;(void)id;(void)slot;return true;}
static const SceneTextInfo*info(void*c,unsigned id){(void)c;(void)id;return &t;}
static const SceneTextPause*pause_at(void*c,unsigned id,unsigned i){(void)c;(void)id;return t.pause_count&&!i?&p:NULL;}
static unsigned visible(void*c,unsigned id,uint32_t n){(void)c;(void)id;return n<5?n+1:6;}
static unsigned page(void*c,unsigned id,unsigned n,int bi){(void)c;(void)id;(void)bi;return n>3;}
static unsigned limit(void*c,unsigned id,unsigned page,int bi){(void)c;(void)id;(void)bi;return page?6:3;}
static const StoryFlowOps ops={.select_text=select_text,.info=info,.pause=pause_at,.visible=visible,.page=page,.limit=limit};
static void need(int ok,const char*why){if(!ok){fprintf(stderr,"FAIL %s\n",why);exit(2);}}
int main(int argc,char**argv){
 need(argc==2,"case");StoryEvent events[2]={{.op=ST_TEXT,.d=5},{.op=ST_END}};StoryChapter chapter={.end_pc=2};
 StoryScript script={.count=2,.chapter_count=1,.text_count=1,.events=events,.chapters=&chapter};StoryFlow f;
 need(story_flow_init(&f,&script,&ops,NULL),"init");f.scrolling=true;
 if(argv[1][0]=='c'){
  story_flow_tick(&f,3);story_flow_mark_presented(&f);f.scroll_ready=false;story_flow_tick(&f,1);
  need(f.page==0&&f.presented,"real ACK retained while coverage incomplete");story_flow_advance(&f);need(f.page==0,"A cannot skip unseen scroll tail");
  f.scroll_ready=true;story_flow_tick(&f,1);need(f.page==1&&!f.presented&&!f.scroll_ready,"ordinary chunk advances without A only after coverage");
 }else if(argv[1][0]=='p'){
  t.pause_count=1;f.local=4;story_flow_refresh(&f);story_flow_mark_presented(&f);f.scroll_ready=false;story_flow_tick(&f,0);need(f.pause_index==0,"w waits for visible coverage");
  f.scroll_ready=true;story_flow_tick(&f,0);need(f.pause_index==1&&!f.scroll_ready,"w proceeds after coverage");
 }else if(argv[1][0]=='e'){
  f.page=1;f.local=5;f.source_auto=true;story_flow_refresh(&f);story_flow_mark_presented(&f);f.scroll_ready=false;story_flow_tick(&f,1);need(!f.done,"NW waits for coverage");
  f.scroll_ready=true;story_flow_tick(&f,1);need(f.done,"NW ends after coverage");
 }else if(argv[1][0]=='u'){
  t.mode=3;f.local=3;story_flow_refresh(&f);story_flow_mark_presented(&f);f.scroll_ready=true;story_flow_tick(&f,1);need(f.page==0,"centered still requires its original manual page input");
 }else return 9;
 need(sizeof(StoryFlow)==sizeof(void*)*3+104,"flow ABI unchanged");return 0;
}
'''

TEXT=r'''
#include "story_text.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
void audio_demo_service(void){}
static void need(int ok,const char*why){if(!ok){fprintf(stderr,"FAIL %s\n",why);exit(2);}}
int main(int argc,char**argv){
 need(argc==5,"pack/id/bi/en_count");need(story_text_open(argv[1]),"open");unsigned id=strtoul(argv[2],NULL,10),bi=strtoul(argv[3],NULL,10),en=strtoul(argv[4],NULL,10);
 need(story_text_select(id,0),"select");const SceneTextInfo*t=story_text_info(id);unsigned reads=story_text_read_calls(),max=story_text_scroll_max(id,t->char_count,bi);
 uint16_t screen[49152],frame[49152],footer[49152];unsigned char cn_seen[65536]={0},en_seen[65536]={0};story_text_draw_lower(id,0,0,bi,1,1,frame);story_text_draw_lower(UINT32_MAX,0,0,bi,1,1,footer);
 for(unsigned row=0;row<=max;row++){
  StoryTextViewport v;story_text_draw_lower_scroll(id,t->char_count,row,bi,1,1,screen,&v);
  need(v.scroll==row&&v.max_scroll==max,"row clamp and maximum");need(v.cn_first<=v.cn_end&&v.cn_end<=t->char_count,"CN range");
  need(v.en_first<=v.en_end&&(!v.en_end||(v.en_first>=t->char_count&&v.en_end<=t->char_count+en)),"EN global range");
  for(unsigned n=v.cn_first;n<v.cn_end;n++)cn_seen[n]=1;
  for(unsigned n=v.en_first;n<v.en_end;n++)en_seen[n]=1;
  for(unsigned y=0;y<30;y++)need(!memcmp(screen+y*256,frame+y*256,512),"fixed header above the expanded body");
  for(unsigned y=174;y<192;y++)need(!memcmp(screen+y*256,footer+y*256,512),"body never writes into footer at174");
  if(t->mode==1)for(unsigned y=0;y<60;y++)need(!memcmp(screen+y*256+196,frame+y*256+196,120),"portrait never covered by scrolling glyphs");
 }
 for(unsigned n=0;n<t->char_count;n++)need(cn_seen[n],"every logical CN command remains reachable");
 if(bi)for(unsigned n=t->char_count;n<t->char_count+en;n++)need(en_seen[n],"every EN command remains reachable");
 unsigned covered=0,row=0;for(unsigned step=0;covered<t->char_count&&step<512;step++){
  row=story_text_scroll_follow(id,t->char_count,bi,row,covered);StoryTextViewport v;story_text_draw_lower_scroll(id,t->char_count,row,bi,1,1,screen,&v);
  need(v.cn_first<=covered&&v.cn_end>covered,"follow never jumps over unseen batch glyphs");covered=v.cn_end;
 }need(covered==t->char_count,"automatic follow covers complete fast batch");
 need(story_text_scroll_for_visible(id,t->char_count,bi)<=max,"visible anchor");need(story_text_read_calls()==reads,"scroll layout/draw/follow perform no IO");
 unsigned other=id?0:197;need(story_text_select(other,1),"alternate record slot1");const SceneTextInfo*o=story_text_info(other);StoryTextViewport a,b;
 story_text_draw_lower_scroll(other,o->char_count,0,bi,1,1,frame,&a);need(story_text_select(other,0),"copy record to live slot");
 story_text_draw_lower_scroll(other,o->char_count,0,bi,1,1,screen,&b);need(!memcmp(&a,&b,sizeof(a))&&!memcmp(frame,screen,sizeof(frame)),"slot layout invalidation and cached copy");
 story_text_close();need(!story_text_resident_bytes()&&!story_text_scroll_max(id,1,bi),"close releases row layout cache");return 0;
}
'''

class StoryScrollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.folder=Path(cls.temp.name);cls.exes={}
        for name,code,sources in [('flow',FLOW,['story_flow.c']),('text',TEXT,['story_text.c','asset_pack.c'])]:
            source=cls.folder/(name+'.c');source.write_text(code);exe=cls.folder/(name+'.exe')
            command=[shutil.which('gcc') or 'gcc','-std=c11','-O2','-Wall','-Wextra','-Werror','-I'+str(ROOT/'nds/source')]+[str(ROOT/'nds/source'/p) for p in sources]+[str(source),'-o',str(exe)]
            result=subprocess.run(command,capture_output=True,text=True)
            if result.returncode:raise AssertionError(result.stderr)
            cls.exes[name]=exe
        cls.records=json.loads((ROOT/'nds/artifacts/story_text_manifest.json').read_text(encoding='utf8'))['records']
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def run_case(self,name,*args):
        result=subprocess.run([str(self.exes[name]),*map(str,args)],capture_output=True,text=True);self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_ordinary_chunks_continue_only_after_coverage(self):self.run_case('flow','chunks')
    def test_internal_pause_waits_for_coverage(self):self.run_case('flow','pause')
    def test_author_nw_waits_for_coverage(self):self.run_case('flow','end')
    def test_centered_retains_manual_paging(self):self.run_case('flow','upper')
    def test_actual_format4_rows_preserve_all_commands_avatar_and_fixed_ui(self):
        for id in (0,197,338,974,989,9158,11231,11902):
            for bi in (0,1):
                with self.subTest(text_id=id,bilingual=bi):self.run_case('text',ROOT/'nds/nitrofs/story/text.bin',id,bi,self.records[id]['en_count'])

if __name__=='__main__':unittest.main()
