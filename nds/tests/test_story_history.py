"""Pure history metadata: real timestamps, source fallback and scene recipes."""
from pathlib import Path
import os,shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[2]
HARNESS=r'''
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
static unsigned allocation_call,fail_allocation,live_allocations;
static void*history_calloc(size_t count,size_t size);
static void history_free(void*data);
#define calloc history_calloc
#define free history_free
#include "story_history.c"
#undef calloc
#undef free
static void*history_calloc(size_t count,size_t size){allocation_call++;if(allocation_call==fail_allocation)return NULL;void*p=calloc(count,size);if(p)live_allocations++;return p;}
static void history_free(void*p){if(p)live_allocations--;free(p);}
static void need(int yes,const char*why){if(!yes){fprintf(stderr,"FAIL %s\n",why);exit(2);}}
static StoryEvent events[64];static StoryScript script;static StoryHistory h;
static StoryEvent show(unsigned image,unsigned tag,int place,unsigned program,int behind){return (StoryEvent){.op=ST_SHOW,.a=(int)image,.b=(int)tag,.c=(int32_t)((uint16_t)place|((uint32_t)(uint16_t)behind<<16)),.d=(int)program};}
static void open_history(unsigned count){script=(StoryScript){.count=count,.text_count=12,.events=events};need(story_history_open(&h,&script),"open history");}
static StoryHistoryView view(unsigned pc){StoryHistoryView v;memset(&v,0xa5,sizeof(v));need(story_history_build(&h,pc,&v),"build history");return v;}
int main(int argc,char**argv){
 (void)history_calloc;(void)history_free;
 need(argc==2||argc==3,"args");
 if(!strcmp(argv[1],"allocation")){
  events[0]=(StoryEvent){.op=ST_TEXT,.a=0};
  for(unsigned fail=1;fail<=2;fail++){
   allocation_call=0;fail_allocation=fail;script=(StoryScript){.count=1,.text_count=1,.events=events};
   need(!story_history_open(&h,&script),"allocation failure returns false");need(!h.script&&!h.starts&&!h.completed&&!live_allocations,"partial open allocation unwinds");story_history_close(&h);
  }
  fail_allocation=0;open_history(1);need(live_allocations==2&&h.starts[0]==UINT32_MAX&&h.completed[0]==UINT32_MAX,"tables start unknown");
  story_history_note_event(&h,0,17,false);story_history_note_complete(&h,0,19);story_history_clear(&h);need(h.starts[0]==UINT32_MAX&&h.completed[0]==UINT32_MAX,"clear resets both tables");
 }else if(!strcmp(argv[1],"fast_first_complete")){
  events[0]=show(50,4,1,7,-1);events[1]=(StoryEvent){.op=ST_TEXT,.a=2,.d=120};open_history(2);
  story_history_note_event(&h,0,100,false);story_history_note_event(&h,1,110,false);story_history_note_complete(&h,1,113);
  story_history_note_complete(&h,1,900);story_history_note_event(&h,0,999,false);
  StoryHistoryView v=view(1);need(v.now==113&&v.gfx.count==1&&v.gfx.layers[0].start==100,"first complete and sequence origin survive later read dwell");need(v.gfx.layers[0].program==7,"program ID retained");
 }else if(!strcmp(argv[1],"behind_inherit")){
  events[0]=show(10,1,1,0,-1);events[1]=show(20,2,2,0,-1);events[2]=show(30,3,0,0,2);
  events[3]=show(21,2,-1,6,1);events[4]=(StoryEvent){.op=ST_HIDE,.a=3};events[5]=(StoryEvent){.op=ST_TEXT,.a=0,.d=4};open_history(6);
  for(unsigned i=0;i<6;i++)story_history_note_event(&h,i,i*3,false);
  StoryHistoryView v=view(5);need(v.gfx.count==2&&v.gfx.layers[0].tag==2&&v.gfx.layers[1].tag==1,"behind and same-tag ordering follows compositor");
  need(v.gfx.layers[0].image==21&&v.gfx.layers[0].placement==2&&v.gfx.layers[0].start==9,"same-tag replacement inherits placement but restarts animation");
 }else if(!strcmp(argv[1],"special_literal_date")){
  events[0]=show(10,1,0,0,-1);events[1]=(StoryEvent){.op=ST_SPECIAL,.a=3,.b=0,.c=1};events[2]=(StoryEvent){.op=ST_WAIT,.a=27};
  events[3]=(StoryEvent){.op=ST_SPECIAL,.a=2,.b=0,.c=0};events[4]=(StoryEvent){.op=ST_LITERAL,.a=8};events[5]=(StoryEvent){.op=ST_WAIT,.a=12};
  events[6]=(StoryEvent){.op=ST_TRANSITION,.a=TR_DISSOLVE,.b=6};events[7]=(StoryEvent){.op=ST_DATE,.a=9};events[8]=(StoryEvent){.op=ST_TEXT,.a=1,.d=3};
  events[9]=(StoryEvent){.op=ST_HIDE_LITERAL};events[10]=(StoryEvent){.op=ST_DATE,.a=-1};events[11]=(StoryEvent){.op=ST_SPECIAL,.a=-1};events[12]=(StoryEvent){.op=ST_SCENE};events[13]=show(70,7,-1,0,-1);events[14]=(StoryEvent){.op=ST_TEXT,.a=3,.d=2};open_history(15);
  StoryHistoryView v=view(8);need(v.now==48&&v.backend.special&&v.backend.group==2&&v.backend.phase==0&&v.backend.origin[0]==0&&!v.gfx.count,"special entry clears ordinary layers and preserves hands/red origin");
  need(v.literal_id==8&&v.literal_start==27&&v.literal_duration==18&&v.date_id==9,"literal start and source wait/transition duration");need(v.backend.transition==TR_DISSOLVE&&v.backend.transition_start==39&&v.backend.transition_duration==6,"explicit transition metadata");
  v=view(14);need(!v.backend.special&&v.literal_id==UINT32_MAX&&v.date_id==UINT32_MAX&&v.gfx.count==1&&v.gfx.layers[0].placement==0,"hide/date/special exit/scene clear recipe");
 }else if(!strcmp(argv[1],"cold_fallback")){
  events[0]=show(10,1,0,0,-1);events[1]=(StoryEvent){.op=ST_WAIT,.a=10};events[2]=(StoryEvent){.op=ST_TEXT,.a=0,.d=6,.b=TR_VPUNCH,.c=17};
  events[3]=(StoryEvent){.op=ST_WAIT,.a=2};events[4]=(StoryEvent){.op=ST_TEXT,.a=1,.d=4};open_history(5);
  StoryHistoryView v=view(2);need(v.now==16&&v.gfx.layers[0].start==0&&v.backend.transition_start==10&&v.backend.transition_duration==17,"cold fallback and concurrent attached transition");
  v=view(4);need(v.now==22,"text fallback uses authored typing time; attached transition adds no extra wait");
 }else if(!strcmp(argv[1],"quiet_noeffects")){
  events[0]=show(10,1,0,0,-1);events[1]=(StoryEvent){.op=ST_MUSIC,.a=77};events[2]=(StoryEvent){.op=ST_SFX,.a=88};events[3]=(StoryEvent){.op=ST_UNLOCK,.a=4,.b=1};events[4]=(StoryEvent){.op=ST_TEXT,.a=0,.d=8};open_history(5);
  story_history_note_event(&h,0,20,false);story_history_note_event(&h,0,400,true);story_history_note_event(&h,4,21,true);story_history_note_complete(&h,0,99);
  StoryHistoryView v=view(4);need(h.starts[0]==20&&h.starts[4]==UINT32_MAX&&h.completed[0]==UINT32_MAX,"quiet and nonTEXT completion do not change actual tables");
  need(v.now==28&&v.gfx.layers[0].start==20&&!v.backend.have_music&&!v.backend.music_tick&&!v.backend.music.op,"audio and unlock events have no history side effects");
 }else if(!strcmp(argv[1],"mixed_anchor")){
  events[0]=show(10,1,0,0,-1);events[1]=(StoryEvent){.op=ST_WAIT,.a=30};events[2]=show(20,2,0,0,-1);events[3]=(StoryEvent){.op=ST_LITERAL,.a=8};events[4]=(StoryEvent){.op=ST_SPECIAL,.a=4,.b=2,.c=1};events[5]=(StoryEvent){.op=ST_TRANSITION,.a=TR_HPUNCH,.b=10};events[6]=(StoryEvent){.op=ST_TEXT,.a=0,.d=20};open_history(7);
  story_history_note_event(&h,6,5,false);story_history_note_complete(&h,6,7);StoryHistoryView v=view(6);
  need(v.now==7&&v.literal_start<=7&&v.backend.origin[2]<=7&&v.backend.transition_start<=7,"unknown source prefix is anchored backwards without future timestamps");
  story_history_clear(&h);story_history_note_event(&h,3,2,false);story_history_note_event(&h,6,5,false);v=view(6);need(v.literal_start==2,"actual literal origin never shifts with unknown prefix");
 }else if(!strcmp(argv[1],"validation")){
  events[0]=(StoryEvent){.op=ST_WAIT,.a=3};events[1]=(StoryEvent){.op=ST_TEXT,.a=0,.d=1};open_history(2);
  StoryHistoryView v,before;memset(&v,0x5a,sizeof(v));before=v;
  need(!story_history_build(&h,0,&v)&&!memcmp(&v,&before,sizeof(v)),"nonTEXT target rejects atomically");need(!story_history_build(&h,2,&v),"out-of-range target");
  story_history_note_event(&h,100,1,false);story_history_note_complete(&h,100,1);
 }else if(!strcmp(argv[1],"overflow")){
  for(unsigned i=0;i<17;i++)events[i]=show(i,i,0,0,-1);
  events[17]=(StoryEvent){.op=ST_TEXT,.a=0,.d=1};open_history(18);StoryHistoryView v,before;memset(&v,0x5a,sizeof(v));before=v;
  need(!story_history_build(&h,17,&v)&&!memcmp(&v,&before,sizeof(v)),"layer limit fails atomically");
 }else if(!strcmp(argv[1],"actual")){
  need(argc==3,"actual script path");FILE*f=fopen(argv[2],"rb");need(f!=NULL,"open actual script fixture");uint32_t words[16];need(fread(words,4,16,f)==16,"script header");
  unsigned n=words[3],texts=words[10],at=words[6];StoryEvent*actual=calloc(n,sizeof(*actual));need(actual!=NULL,"script fixture buffer");
  need(!fseek(f,(long)at,SEEK_SET)&&fread(actual,sizeof(*actual),n,f)==n,"source events");fclose(f);
  script=(StoryScript){.count=n,.text_count=texts,.events=actual};need(story_history_open(&h,&script),"open full source metadata");unsigned built=0;
  for(unsigned pc=0;pc<n;pc++)if(actual[pc].op==ST_TEXT){
   StoryHistoryView v;if(!story_history_build(&h,pc,&v)){fprintf(stderr,"actual pc%u source line%u failed\n",pc,actual[pc].line);exit(2);}built++;
   for(unsigned i=0;i<v.gfx.count;i++)need(v.gfx.layers[i].start<=v.now,"cold layer timestamp is not in future");
   if(v.literal_id!=UINT32_MAX)need(v.literal_start<=v.now,"cold literal timestamp is not in future");
  }
  need(built==11951,"every actual Say has a source scene recipe");free(actual);
 }else need(0,"case");
 story_history_close(&h);need(!h.script&&!h.starts&&!h.completed&&!live_allocations,"close frees both tables");puts("PASS history metadata");return 0;
}
'''


class StoryHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='zato_history_');cls.folder=Path(cls.temp.name);(cls.folder/'probe.c').write_text(HARNESS,encoding='utf8')
        cc=os.environ.get('CC') or shutil.which('gcc') or shutil.which('clang')
        if not cc:raise unittest.SkipTest('host C compiler unavailable')
        cls.exe=cls.folder/'probe.exe';r=subprocess.run([cc,'-std=c11','-O2','-Wall','-Wextra','-Werror','-I'+str(ROOT/'nds/source'),str(cls.folder/'probe.c'),'-o',str(cls.exe)],capture_output=True,text=True)
        if r.returncode:raise AssertionError(r.stdout+r.stderr)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def check(self,case):
        r=subprocess.run([str(self.exe),case],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
    def test_allocation_failure_open_clear_and_close(self):self.check('allocation')
    def test_fast_a_first_complete_does_not_drift_during_reading(self):self.check('fast_first_complete')
    def test_same_tag_behind_placement_inheritance_and_sequence_origin(self):self.check('behind_inherit')
    def test_special_origin_literal_duration_date_and_hide(self):self.check('special_literal_date')
    def test_cold_source_fallback_and_attached_transition(self):self.check('cold_fallback')
    def test_quiet_does_not_overwrite_and_audio_has_no_side_effects(self):self.check('quiet_noeffects')
    def test_unknown_prefix_anchor_never_shifts_actual_origin(self):self.check('mixed_anchor')
    def test_target_validation_keeps_output_untouched(self):self.check('validation')
    def test_layer_limit_failure_keeps_output_untouched(self):self.check('overflow')
    def test_all_11951_actual_source_targets_build_without_assets_or_audio(self):
        r=subprocess.run([str(self.exe),'actual',str(ROOT/'nds/nitrofs/story/script.bin')],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stdout+r.stderr)


if __name__=='__main__':unittest.main()
