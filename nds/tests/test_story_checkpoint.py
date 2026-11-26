"""Real C checkpoint codecs and live source records; no ROM build/repacking."""
from pathlib import Path
import json,shutil,struct,subprocess,tempfile,unittest
from test_story_gfx import fixture_assets,fixture_catalog,literal_lz

ROOT=Path(__file__).resolve().parents[2]
HARNESS=r'''
#include "story_checkpoint.h"
#include "story_text.h"
#include "story_gfx.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
_Static_assert(sizeof(StoryFlow)==sizeof(void*)*3+104,"unchanged flow ABI");
static unsigned dispatched,audio_restores,audio_fail_once;
static AudioSceneSnapshot sound={.version=1,.paused=1,.master_music=4096,.master_sfx=3000,
 .music={.enabled=1,.loop=1,.offset_frames=12345,.gain_q12=2048,.fade_target=4096,.fade_total=10,.fade_left=5,.fade_step=409,.fade_remainder=6}};
void audio_demo_service(void){}
bool audio_scene_snapshot(AudioSceneSnapshot*out){if(!out)return false;*out=sound;return true;}
bool audio_scene_snapshot_valid(const AudioSceneSnapshot*s){return s&&s->version==1&&s->paused<=1&&s->master_music<=4096&&s->master_sfx<=4096&&!(s->music.flags&~3u)&&!(s->sfx.flags&~3u);}
bool audio_scene_restore(const AudioSceneSnapshot*s){if(!audio_scene_snapshot_valid(s))return false;if(audio_fail_once){audio_fail_once=0;return false;}sound=*s;audio_restores++;return true;}
static bool event(void*c,const StoryEvent*e,uint32_t now,bool quiet){(void)c;(void)e;(void)now;(void)quiet;dispatched++;return true;}
static bool select_text(void*c,unsigned id,unsigned slot){(void)c;return story_text_select(id,slot)!=0;}
static const SceneTextInfo*info(void*c,unsigned id){(void)c;return story_text_info(id);}
static const SceneTextPause*pause_at(void*c,unsigned id,unsigned n){(void)c;return story_text_pause(id,n);}
static unsigned visible(void*c,unsigned id,uint32_t ticks){(void)c;return story_text_visible(id,ticks);}
static unsigned page(void*c,unsigned id,unsigned n,int bi){(void)c;return story_text_page(id,n,bi);}
static unsigned limit(void*c,unsigned id,unsigned p,int bi){(void)c;return story_text_page_limit(id,p,bi);}
static const StoryFlowOps ops={.event=event,.select_text=select_text,.info=info,.pause=pause_at,.visible=visible,.page=page,.limit=limit};
static StoryScript script;static StoryFlow flow;
static void need(int ok,const char*why){if(!ok){fprintf(stderr,"FAIL %s\n",why);exit(2);}}
static void putword(uint8_t*p,uint32_t n){p[0]=(uint8_t)n;p[1]=(uint8_t)(n>>8);p[2]=(uint8_t)(n>>16);p[3]=(uint8_t)(n>>24);}
static void begin(unsigned id,int bi){
 unsigned at=0;while(at<script.count&&(script.events[at].op!=ST_TEXT||(unsigned)script.events[at].a!=id))at++;
 need(at<script.count,"real text event");const StoryEvent*e=&script.events[at];
 memset(&flow,0,sizeof(flow));flow.script=&script;flow.ops=&ops;flow.pc=at+1;flow.commands=at+1;flow.now=543210;flow.line=e->line;
 while(flow.chapter+1<script.chapter_count&&at>=script.chapters[flow.chapter].end_pc)flow.chapter++;
 flow.text_id=id;flow.literal_id=flow.date_id=flow.page_ready=STORY_NONE;flow.window=flow.quick_menu=flow.text_active=flow.blocked=true;flow.bilingual=bi;flow.source_auto=(e->flags&1)!=0;
 need(story_text_select(id,0),"load real text");const SceneTextInfo*t=story_text_info(id);
 flow.page=t->continuation?story_text_page(id,t->new_char_start+1,bi):0;story_flow_refresh(&flow);story_flow_mark_presented(&flow);
}
static void capture(StoryFlowSnapshot*s){need(story_flow_snapshot(&flow,s),"flow capture");need(story_flow_snapshot_valid(&flow,s),"flow valid");}
static void roundtrip(unsigned id,int bi,unsigned local){
 begin(id,bi);flow.local=local;flow.paused=true;story_flow_refresh(&flow);story_flow_mark_presented(&flow);StoryFlowSnapshot saved;capture(&saved);
 uint32_t pc=flow.pc,p=flow.page,v=flow.visible,n=flow.now;flow.local=0;flow.visible=0;flow.page=0;flow.paused=false;
 need(story_flow_restore(&flow,&saved),"flow restore");need(flow.pc==pc&&flow.page==p&&flow.visible==v&&flow.now==n&&flow.local==local,"semantic values");
 need(flow.paused&&!flow.presented,"original pause retained; new ACK required");need(!dispatched,"no replay or source dispatch");
 need(!story_flow_snapshot(&flow,&saved),"uncommitted restored UI cannot resave");
 story_flow_mark_presented(&flow);capture(&saved);
}
int main(int argc,char**argv){
 need(argc==8,"arguments");need(story_script_open(&script,argv[2]),"script open");need(story_text_open(argv[3]),"text open");
 need(story_gfx_open(argv[4],argv[5]),"gfx open");const char*test=argv[1];
 if(!strcmp(test,"partial")){begin(0,0);flow.local=12;story_flow_refresh(&flow);need(flow.visible&&flow.visible<story_text_info(0)->char_count,"real partial CN");roundtrip(0,0,12);}
 else if(!strcmp(test,"bipage")){roundtrip(974,1,4);need(flow.page==1,"real BI second page preserved");}
 else if(!strcmp(test,"extend")){roundtrip(197,0,4);roundtrip(974,1,4);}
 else if(!strcmp(test,"internal")){
  begin((unsigned)strtoul(argv[6],NULL,10),0);const SceneTextPause*p=story_text_pause(flow.text_id,0);need(p!=NULL,"real internal w");flow.local=p->start_tick;story_flow_refresh(&flow);story_flow_mark_presented(&flow);StoryFlowSnapshot s;capture(&s);unsigned cap=flow.visible;
  need(story_flow_restore(&flow,&s)&&flow.pause_index==0&&flow.visible==cap,"pause index and cap retained");
  StoryFlowSnapshot b=s;b.value[11]=1;b.value[8]=b.value[15]=0;b.value[9]=b.value[16]=story_text_visible(flow.text_id,0);
  need(!story_flow_snapshot_valid(&flow,&b),"cannot skip a future source pause");
 }
 else if(!strcmp(test,"invalid")){
  begin(0,0);StoryFlowSnapshot s,b;capture(&s);StoryFlow before=flow;
  b=s;b.value[0]=script.count+1;need(!story_flow_restore(&flow,&b),"bad pc");need(!memcmp(&before,&flow,sizeof(flow)),"invalid restore retains live flow");
  b=s;b.value[4]++;need(!story_flow_snapshot_valid(&flow,&b),"bad commands");b=s;b.value[10]=99;need(!story_flow_snapshot_valid(&flow,&b),"bad page");
  b=s;b.value[11]=99;need(!story_flow_snapshot_valid(&flow,&b),"bad pause index");b=s;b.value[9]++;need(!story_flow_snapshot_valid(&flow,&b),"bad visible");
  b=s;b.value[5]=974;need(!story_flow_snapshot_valid(&flow,&b),"wrong text for pc");b=s;b.value[2]++;need(!story_flow_snapshot_valid(&flow,&b),"wrong chapter");
  b=s;b.flags|=STORY_FLOW_SNAPSHOT_FLAGS+1u;need(!story_flow_snapshot_valid(&flow,&b),"unknown flag");b=s;b.value[16]++;need(!story_flow_snapshot_valid(&flow,&b),"unrendered glyph cap");
 }
 else if(!strcmp(test,"ack")){
  begin(0,0);StoryFlowSnapshot s;flow.presented=false;need(!story_flow_snapshot(&flow,&s),"no initial ACK");
  story_flow_mark_presented(&flow);flow.local=12;story_flow_refresh(&flow);need(!story_flow_snapshot(&flow,&s),"old ACK cannot cover new text");
  story_flow_mark_presented(&flow);capture(&s);flow.window=false;need(!story_flow_snapshot(&flow,&s),"hidden body cannot claim complete UI ACK");flow.window=true;flow.waiting=true;need(!story_flow_snapshot(&flow,&s),"WAIT unsafe");
 }
 else if(!strcmp(test,"pagehold")){
  begin(9158,0);flow.local=story_text_info(9158)->duration_ticks;flow.page_hold=true;story_flow_refresh(&flow);story_flow_mark_presented(&flow);StoryFlowSnapshot s;
  need(!story_flow_snapshot(&flow,&s),"page hold needs ready timestamp");flow.page_ready=flow.now-17;capture(&s);unsigned page=flow.page,ready=flow.page_ready;
  need(story_flow_restore(&flow,&s)&&flow.page_hold&&flow.page_ready==ready,"hold and dwell restore");story_flow_tick(&flow,1);need(flow.page==page,"NW must not flip before restored UI commit");
  story_flow_mark_presented(&flow);story_flow_tick(&flow,1);need(flow.page==page+1,"NW resumes after actual commit");
 }
 else if(!strcmp(test,"gfx")){
  for(unsigned i=0;i<16;i++)need(story_gfx_show(i%3,i,i%3,0,-1,100+i),"sixteen tags");
  need(story_gfx_show(3,5,-1,0,2,121),"same tag retain placement and reorder");StoryGfxSnapshot s;need(story_gfx_snapshot(&s)&&s.count==16,"gfx capture");
  need(s.layers[2].tag==5&&s.layers[2].start==121&&s.layers[2].placement==2,"order placement clock");
  uint16_t before[49152],after[49152];need(story_gfx_draw(1000,before),"old draw");story_gfx_clear();need(story_gfx_restore(&s),"gfx restore");need(story_gfx_draw(1000,after)&&!memcmp(before,after,sizeof(before)),"same clock pixels");
  StoryGfxSnapshot b=s;b.layers[1].tag=b.layers[0].tag;need(!story_gfx_snapshot_valid(&b),"duplicate tag");b=s;b.layers[0].image=99;need(!story_gfx_restore(&b)&&story_gfx_layer_count()==16,"bad gfx retains live");
  story_gfx_begin_batch(1000);need(!story_gfx_snapshot(&b),"active batch unsafe");need(story_gfx_move_transition(5,1000,60)&&story_gfx_end_batch(),"move");need(!story_gfx_snapshot(&b),"active move unsafe");
 }
 else if(!strcmp(test,"gfxbudget")){
  StoryGfxSnapshot s={.count=4};for(unsigned i=0;i<4;i++)s.layers[i]=(StoryGfxLayerSnapshot){.image=i,.tag=i};
  need(!story_gfx_snapshot_valid(&s),"snapshot must fit steady graphics budget");
  need(!story_gfx_restore(&s)&&story_gfx_layer_count()==0,"overbudget target cannot replace live scene");
 }
 else if(!strcmp(test,"gfxio")){
  need(story_gfx_show(0,3,0,0,-1,100),"old large bank");uint16_t before[49152],after[49152];need(story_gfx_draw(1000,before),"old draw");
  StoryGfxSnapshot target={.count=1,.layers={{.image=1,.tag=9,.start=200}}};need(story_gfx_snapshot_valid(&target),"valid target metadata");size_t bytes=story_gfx_resident_bytes();
  FILE*bad=fopen(argv[4],"r+b");need(bad!=NULL,"fault file");unsigned char offset[4];need(!fseek(bad,56,SEEK_SET)&&fread(offset,1,4,bad)==4,"second bank offset");uint32_t at=offset[0]|(uint32_t)offset[1]<<8|(uint32_t)offset[2]<<16|(uint32_t)offset[3]<<24;
  need(!fseek(bad,at,SEEK_SET)&&fputc(255,bad)!=EOF&&!fclose(bad),"corrupt unloaded bank");
  need(!story_gfx_restore(&target),"I/O codec failure");StoryGfxSnapshot kept;need(story_gfx_snapshot(&kept)&&kept.count==1&&kept.layers[0].tag==3,"keep prior scene");
  need(story_gfx_resident_bytes()==bytes&&story_gfx_draw(1000,after)&&!memcmp(before,after,sizeof(before)),"rollback resources and pixels");
 }
 else if(!strcmp(test,"movingload")){
  begin(197,0);need(story_gfx_show(1,9,0,0,-1,100),"saved ordinary scene");StoryBackendSnapshot b={0};uint8_t wire[768];StoryCheckpointDecoded d;
  need(story_checkpoint_capture(&flow,&b,wire)&&story_checkpoint_decode(wire,&d),"valid saved target");
  story_gfx_clear();need(story_gfx_show(0,3,0,0,-1,100),"live scene");story_gfx_begin_batch(flow.now);need(story_gfx_show(0,3,1,0,-1,flow.now),"live move target");
  need(story_gfx_move_transition(5,flow.now,60)&&story_gfx_end_batch(),"active move transition");
  need(story_checkpoint_valid(&flow,&d),"saved target remains compatible");
  need(story_checkpoint_restore(&flow,&d),"valid saved checkpoint loads from an interrupted move");
  StoryGfxSnapshot restored;need(!story_gfx_restore_pending()&&story_gfx_snapshot(&restored)&&restored.count==1&&restored.layers[0].tag==9,"owned checkpoint transaction commits its target");
 }
 else if(!strcmp(test,"transaction")||!strcmp(test,"transactioncommit")||!strcmp(test,"transactionclose")||!strcmp(test,"transactionbatch")||!strcmp(test,"transactionfail")){
  uint16_t before[49152],after[49152];size_t base=story_gfx_resident_bytes();
  need(story_gfx_show(0,3,0,0,-1,100),"old scene resources");story_gfx_begin_batch(1000);
  need(story_gfx_show(0,3,1,0,-1,1000),"new batch layer");
  if(strcmp(test,"transactionbatch"))need(story_gfx_move_transition(5,1000,60)&&story_gfx_end_batch(),"interrupted move");
  need(story_gfx_draw(1010,before),"old moving pixels");uint32_t version=story_gfx_version(1010),reads=story_gfx_read_calls();size_t old_bytes=story_gfx_resident_bytes();
  need(story_gfx_restore_begin()&&story_gfx_restore_pending(),"begin transient transaction");
  need(story_gfx_read_calls()==reads&&story_gfx_resident_bytes()==old_bytes,"begin retains refs without reads or allocation");
  need(!story_gfx_restore_begin(),"nested begin is rejected");StoryGfxSnapshot blocked;need(!story_gfx_snapshot(&blocked),"pending transaction is not persistable");
  StoryGfxSnapshot target={.count=1,.layers={{.image=1,.tag=9,.placement=2,.start=500}}};
  if(!strcmp(test,"transactionfail")){
   FILE*bad=fopen(argv[4],"r+b");need(bad!=NULL,"fault file");unsigned char bytes[4];need(!fseek(bad,56,SEEK_SET)&&fread(bytes,1,4,bad)==4,"new bank offset");unsigned at=bytes[0]|(unsigned)bytes[1]<<8|(unsigned)bytes[2]<<16|(unsigned)bytes[3]<<24;
   need(!fseek(bad,at,SEEK_SET)&&fputc(255,bad)!=EOF&&!fclose(bad),"bad destination bank");
   need(!story_gfx_restore(&target)&&story_gfx_restore_pending(),"failed target keeps outer transaction open");reads=story_gfx_read_calls();
   need(story_gfx_version(1010)==version&&story_gfx_draw(1010,after)&&!memcmp(before,after,sizeof(before)),"failed target leaves the live move unchanged before rollback");
   story_gfx_restore_rollback();need(story_gfx_read_calls()==reads&&story_gfx_resident_bytes()==old_bytes,"failed target rollback retains original refs without reads");
   story_gfx_clear();need(story_gfx_resident_bytes()==base,"failed target has no resource leak");story_gfx_close();story_text_close();story_script_close(&script);return 0;
  }
  need(story_gfx_restore(&target),"replace interrupted move inside transaction");
  reads=story_gfx_read_calls();
  if(!strcmp(test,"transactionclose")){
   story_gfx_close();need(!story_gfx_restore_pending()&&!story_gfx_resident_bytes(),"close releases both current and retained resources");
  }else if(!strcmp(test,"transactioncommit")){
   story_gfx_restore_commit();need(!story_gfx_restore_pending()&&story_gfx_snapshot(&blocked)&&blocked.layers[0].tag==9,"commit keeps target scene");
   need(story_gfx_read_calls()==reads,"commit performs no reads");story_gfx_clear();need(story_gfx_resident_bytes()==base,"commit retained no old resource refs");
  }else{
   need(story_gfx_resident_bytes()>old_bytes,"old bank must remain owned while target is active");
   FILE*bad=fopen(argv[4],"r+b");need(bad!=NULL,"fault file");unsigned char at_bytes[4];
   need(!fseek(bad,40,SEEK_SET)&&fread(at_bytes,1,4,bad)==4,"old bank offset");unsigned at=at_bytes[0]|(unsigned)at_bytes[1]<<8|(unsigned)at_bytes[2]<<16|(unsigned)at_bytes[3]<<24;
   need(!fseek(bad,at,SEEK_SET)&&fputc(255,bad)!=EOF&&!fclose(bad),"old source bank can no longer reload");
   story_gfx_restore_rollback();need(!story_gfx_restore_pending()&&story_gfx_read_calls()==reads,"rollback uses retained refs, not file reads");
   need(story_gfx_resident_bytes()==old_bytes&&story_gfx_version(1010)==version,"rollback retains exact heap and animation version");
   need(story_gfx_draw(1010,after)&&!memcmp(before,after,sizeof(before)),"rollback preserves moving pixels and clocks");
   need(!story_gfx_snapshot(&blocked),"rollback restored the interrupted move/batch");
   if(!strcmp(test,"transactionbatch"))need(story_gfx_move_transition(5,1000,60)&&story_gfx_end_batch(),"rolled-back source batch can still install its move");
   story_gfx_clear();need(story_gfx_resident_bytes()==base,"rollback transferred exactly the old owner refs");
  }
 }
 else if(!strcmp(test,"outertransaction")){
  begin(197,0);need(story_gfx_show(1,9,0,0,-1,100),"saved scene");StoryBackendSnapshot b={0};uint8_t wire[768];StoryCheckpointDecoded d;
  need(story_checkpoint_capture(&flow,&b,wire)&&story_checkpoint_decode(wire,&d),"saved target");
  story_gfx_clear();need(story_gfx_show(0,3,0,0,-1,100),"live scene");uint16_t before[49152],after[49152];need(story_gfx_draw(flow.now,before),"old pixels");
  need(story_gfx_restore_begin()&&story_checkpoint_restore(&flow,&d)&&story_gfx_restore_pending(),"checkpoint leaves an outer transaction open");
  uint32_t reads=story_gfx_read_calls();story_gfx_restore_rollback();need(story_gfx_read_calls()==reads&&story_gfx_draw(flow.now,after)&&!memcmp(before,after,sizeof(before)),"outer caller can rollback after later cache failure");
 }
 else if(!strcmp(test,"wire")){
  begin(197,0);need(story_gfx_show(3,9,1,0,-1,7654),"animated layer");StoryBackendSnapshot backend={0};uint8_t wire[STORY_CHECKPOINT_BYTES],bad[STORY_CHECKPOINT_BYTES];StoryCheckpointDecoded d;
  need(story_checkpoint_capture(&flow,&backend,wire),"checkpoint capture");need(story_checkpoint_decode(wire,&d)&&story_checkpoint_valid(&flow,&d),"wire decode valid");
  need(d.flow.value[1]==flow.now&&d.gfx.layers[0].start==7654&&d.audio.music.offset_frames==12345,"LE fields");
  need(story_checkpoint_restore(&flow,&d)&&!flow.paused&&!flow.presented&&audio_restores==1,"combined restore retains unpaused user state");
  memcpy(bad,wire,sizeof(wire));bad[0]^=1;need(!story_checkpoint_decode(bad,&d),"bad magic");memcpy(bad,wire,sizeof(wire));bad[767]=1;need(!story_checkpoint_decode(bad,&d),"trailing reserved");
  memcpy(bad,wire,sizeof(wire));putword(bad+100,d.flow.flags|(STORY_FLOW_SNAPSHOT_FLAGS+1u));need(!story_checkpoint_decode(bad,&d),"unknown flow flag on wire");
  memcpy(bad,wire,sizeof(wire));bad[488]|=128;need(!story_checkpoint_decode(bad,&d),"unknown voice flag on wire");
  memcpy(bad,wire,sizeof(wire));bad[200]=1;need(!story_checkpoint_decode(bad,&d),"unused layer bytes must be zero");
  memcpy(bad,wire,sizeof(wire));bad[540]=2;need(!story_checkpoint_decode(bad,&d),"backend bool flag");
  memcpy(bad,wire,sizeof(wire));bad[594]=1;need(!story_checkpoint_decode(bad,&d),"backend event flags reserved");
  for(unsigned i=0;i<16;i++){memcpy(bad,wire,sizeof(wire));bad[i]^=0x80;need(!story_checkpoint_decode(bad,&d),"header corruption rejected");}
 }
 else if(!strcmp(test,"viewwire")){
  begin(0,0);flow.local=12;story_flow_refresh(&flow);story_flow_mark_presented(&flow);
  need(flow.visible&&flow.visible<story_text_info(0)->char_count,"real partial CN for view wire");
  StoryBackendSnapshot b={0};uint8_t old[768],wire[768],bad[768];StoryCheckpointDecoded d;
  need(story_checkpoint_capture(&flow,&b,old),"legacy capture for independent v2 fixture");
  for(unsigned i=616;i<768;i++){memcpy(bad,old,sizeof(bad));bad[i]=1;need(!story_checkpoint_decode(bad,&d),"each v1 reserved byte remains forbidden");}
  memcpy(wire,old,sizeof(wire));putword(wire+8,2);putword(wire+12,612);
  putword(wire+616,0x1234);putword(wire+620,1);putword(wire+624,flow.visible-1);
  need(story_checkpoint_decode(wire,&d),"independently encoded v2 view accepted");
  need(d.scroll==0x1234&&d.scroll_manual==1&&d.scroll_covered==flow.visible-1,"independent LE view fields and incomplete display coverage");
  need(story_checkpoint_valid(&flow,&d),"v2 remains a semantically valid partial point");
  memcpy(bad,wire,sizeof(bad));putword(bad+620,2);need(!story_checkpoint_decode(bad,&d),"v2 bad manual rejected");
  memcpy(bad,wire,sizeof(bad));putword(bad+616,65536);need(!story_checkpoint_decode(bad,&d),"v2 oversized scroll rejected");
  memcpy(bad,wire,sizeof(bad));putword(bad+624,flow.visible+1);need(!story_checkpoint_decode(bad,&d),"coverage cannot exceed visible progress");
  for(unsigned i=628;i<768;i++){memcpy(bad,wire,sizeof(bad));bad[i]=1;need(!story_checkpoint_decode(bad,&d),"each v2 reserved byte must be zero");}
  memcpy(bad,wire,sizeof(bad));putword(bad+12,600);need(!story_checkpoint_decode(bad,&d),"v2 cannot claim legacy payload length");
  memcpy(bad,old,sizeof(bad));putword(bad+12,608);need(!story_checkpoint_decode(bad,&d),"v1 cannot claim view payload length");
  memcpy(bad,wire,sizeof(bad));putword(bad+12,608);need(!story_checkpoint_decode(bad,&d),"intermediate v2 length608 is unsupported");
 }
 else if(!strcmp(test,"viewcapture")){
  begin(0,0);flow.local=12;flow.paused=true;story_flow_refresh(&flow);story_flow_mark_presented(&flow);
  StoryBackendSnapshot b={0};uint8_t old[768],wire[768],unchanged[768];StoryCheckpointDecoded d,legacy;
  StoryFlow before=flow;StoryFlowSnapshot saved;capture(&saved);
  need(story_checkpoint_capture(&flow,&b,old),"legacy API still captures partial point");
  need(!memcmp(old,"ZATCKP01",8)&&old[8]==1&&old[12]==0x58&&old[13]==2,"legacy header version1 payload600 unchanged");
  for(unsigned i=616;i<768;i++)need(!old[i],"legacy has 152 zero reserved bytes");
  need(story_checkpoint_decode(old,&legacy)&&!legacy.scroll&&!legacy.scroll_manual&&legacy.scroll_covered==flow.visible,"v1 defaults to zero viewport and presented-page coverage");
  need(flow.visible>0,"partial point has visible progress");uint32_t covered=flow.visible-1;
  need(story_checkpoint_capture_view(&flow,&b,321,true,covered,wire),"view API captures partial point");
  need(!memcmp(wire,"ZATCKP01",8)&&wire[8]==3&&wire[12]==0x68&&wire[13]==2,"view header version3 payload616");
  need(!memcmp(old+16,wire+16,600),"all old600 payload bytes unchanged");
  need(wire[616]==0x41&&wire[617]==1&&wire[618]==0&&wire[619]==0&&wire[620]==1,"view fields appended explicitly LE");
  for(unsigned i=632;i<768;i++)need(!wire[i],"view has 136 zero reserved bytes");
  need(story_checkpoint_decode(wire,&d)&&story_checkpoint_valid(&flow,&d)&&d.scroll==321&&d.scroll_manual==1&&d.scroll_covered==covered&&d.layout_key==story_text_layout_key(),"v3 roundtrip viewport/key and incomplete coverage");
  need(!memcmp(&saved,&d.flow,sizeof(saved))&&!memcmp(&flow,&before,sizeof(flow)),"capture retains partial progress and all live flow fields");
  flow.local=0;flow.visible=0;need(story_checkpoint_restore(&flow,&d),"v3 restore keeps existing semantic restore path");
  need(flow.local==12&&flow.visible==saved.value[STORY_FLOW_VISIBLE]&&flow.paused&&!flow.presented,"v3 partial progress and user pause restored with new ACK required");
  story_flow_mark_presented(&flow);
  need(story_checkpoint_capture_view(&flow,&b,65535,false,0,wire)&&story_checkpoint_decode(wire,&d)&&d.scroll==65535&&!d.scroll_manual&&!d.scroll_covered,"inclusive view bound, automatic viewport and zero coverage");
  memset(wire,0x5a,sizeof(wire));memcpy(unchanged,wire,sizeof(wire));
  need(!story_checkpoint_capture_view(&flow,&b,65536,false,0,wire)&&!memcmp(wire,unchanged,sizeof(wire)),"oversized capture rejects without touching destination");
  need(!story_checkpoint_capture_view(&flow,&b,0,false,flow.visible+1,wire)&&!memcmp(wire,unchanged,sizeof(wire)),"over-covered capture rejects without touching destination");
  d.scroll=65536;need(!story_checkpoint_valid(&flow,&d),"semantic validation rejects oversized view");
  d.scroll=0;d.scroll_manual=2;need(!story_checkpoint_valid(&flow,&d),"semantic validation rejects bad manual flag");
  d.scroll_manual=0;d.scroll_covered=flow.visible+1;need(!story_checkpoint_valid(&flow,&d),"semantic validation rejects over-covered view");
 }
 else if(!strcmp(test,"safety")){
  begin(197,0);StoryBackendSnapshot b={.special=1};uint8_t wire[STORY_CHECKPOINT_BYTES];need(!story_checkpoint_capture(&flow,&b,wire),"special unsafe");
  b=(StoryBackendSnapshot){.transition=TR_DISSOLVE,.transition_start=flow.now-1,.transition_duration=30};need(!story_checkpoint_capture(&flow,&b,wire),"active transition unsafe");b.transition_start=flow.now-31;need(story_checkpoint_capture(&flow,&b,wire),"finished transition safe");
 }
 else if(!strcmp(test,"audiofail")){
  begin(197,0);StoryBackendSnapshot b={0};uint8_t wire[STORY_CHECKPOINT_BYTES];StoryCheckpointDecoded d;need(story_gfx_show(1,9,0,0,-1,100),"target scene");
  need(story_checkpoint_capture(&flow,&b,wire)&&story_checkpoint_decode(wire,&d),"target checkpoint");
  begin(0,0);story_gfx_clear();need(story_gfx_show(0,3,0,0,-1,200),"live scene");sound.music.offset_frames=55;StoryFlow before=flow;audio_fail_once=1;
  need(!story_checkpoint_restore(&flow,&d)&&!memcmp(&flow,&before,sizeof(flow)),"failed audio leaves live flow");StoryGfxSnapshot kept;need(story_gfx_snapshot(&kept)&&kept.layers[0].tag==3&&sound.music.offset_frames==55,"failed audio keeps graphics and restores audio");
 }
 else return 9;
 story_gfx_close();story_text_close();story_script_close(&script);return 0;
}
'''

def budget_fixture():
    count=4;index_end=40+16*count+196*count+count*2;payload=bytearray();banks=[];frames=[]
    def block(raw):
        offset=index_end+len(payload);packed=literal_lz(raw);payload.extend(packed);return offset,len(packed)
    for i in range(count):
        at,size=block(b'\x1f\x80'*(262144//2));banks.append(struct.pack('<4I',at,size,262144,128))
        at,size=block(b'\0\0');frames.append(struct.pack('<4I4H2h2HI32s128s',i,at,size,2,8,8,1,1,124,92,1,0,0,b'\0'*32,b'budget'))
    assets=struct.pack('<8s8I',b'ZATONDP1',1,count,count,1,3,0,0,196)+b''.join(banks)+b''.join(frames)+struct.pack('<4H',0,1,2,3)+payload
    images=b''.join(struct.pack('<12i',0,i,1,0,0,0,0,0,0,0,0,0) for i in range(count));program=struct.pack('<12i',4096,0,65536,65536,0,32768,32768,32768,32768,1,-1,0)
    geometry=struct.pack('<8i',16,8<<16,8<<16,0,0,192<<16,0,0)*count;so=64+len(images);go=so+len(program);body=images+program+geometry
    import zlib
    catalog=struct.pack('<8s14I',b'ZSTGFX01',1,64+len(body),count,0,1,count,64,so,so,go,len(assets),zlib.crc32(body),0,0)+body
    return assets,catalog

class StoryCheckpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();folder=Path(cls.temp.name)
        src=folder/'checkpoint.c';src.write_text(HARNESS);cls.exe=folder/'checkpoint.exe'
        cls.assets=folder/'assets.zat';cls.assets.write_bytes(fixture_assets());cls.catalog=folder/'gfx.bin';cls.catalog.write_bytes(fixture_catalog(cls.assets.stat().st_size))
        heavy_assets,heavy_catalog=budget_fixture();cls.heavy_assets=folder/'budget.zat';cls.heavy_assets.write_bytes(heavy_assets);cls.heavy_catalog=folder/'budget.bin';cls.heavy_catalog.write_bytes(heavy_catalog)
        manifest=json.loads((ROOT/'nds/artifacts/story_text_manifest.json').read_text(encoding='utf8'))
        cls.internal=next(r['id'] for r in manifest['records'] if r['kind']=='say' and r['mode']!=3 and r['pause_count'])
        sources=['story_flow.c','story_gfx.c','story_checkpoint.c','story_text.c','story_script.c','asset_pack.c']
        command=[shutil.which('gcc') or 'gcc','-std=c11','-O2','-Wall','-Wextra','-Werror','-I'+str(ROOT/'nds/source')]+[str(ROOT/'nds/source'/s) for s in sources]+[str(src),'-o',str(cls.exe)]
        result=subprocess.run(command,capture_output=True,text=True)
        if result.returncode:raise AssertionError(result.stderr)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def check(self,case):
        assets,catalog=(self.heavy_assets,self.heavy_catalog) if case in ('gfxbudget','gfxio','transaction','transactioncommit','transactionclose','transactionbatch','transactionfail') else (self.assets,self.catalog)
        if assets==self.heavy_assets:
            fresh_assets,fresh_catalog=budget_fixture();assets.write_bytes(fresh_assets);catalog.write_bytes(fresh_catalog)
        p=subprocess.run([str(self.exe),case,str(ROOT/'nds/nitrofs/story/script.bin'),str(ROOT/'nds/nitrofs/story/text.bin'),str(assets),str(catalog),str(self.internal),'unused'],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
    def test_real_partial_cn(self):self.check('partial')
    def test_real_bilingual_second_page(self):self.check('bipage')
    def test_real_extend197_and_nvl974(self):self.check('extend')
    def test_real_internal_pause(self):self.check('internal')
    def test_invalid_flow_is_rejected_without_mutation(self):self.check('invalid')
    def test_restored_or_stale_ui_ack_cannot_capture(self):self.check('ack')
    def test_real_auto_page_hold_preserves_dwell_and_waits_for_new_ack(self):self.check('pagehold')
    def test_gfx_sixteen_ordered_layers_and_animation_clocks(self):self.check('gfx')
    def test_gfx_restored_scene_must_fit_steady_budget(self):self.check('gfxbudget')
    def test_gfx_failed_io_keeps_old_scene_resources_and_pixels(self):self.check('gfxio')
    def test_wire_roundtrip_and_corrupt_reserved(self):self.check('wire')
    def test_independent_version2_view_wire_and_strict_reserved(self):self.check('viewwire')
    def test_version3_capture_preserves_legacy_payload_and_partial_progress(self):self.check('viewcapture')
    def test_backend_special_and_active_transition_safety(self):self.check('safety')
    def test_failed_audio_restore_keeps_old_live_flow_and_graphics(self):self.check('audiofail')
    def test_valid_checkpoint_can_replace_an_interrupted_move(self):self.check('movingload')
    def test_transient_rollback_keeps_move_refs_pixels_and_clock_without_io(self):self.check('transaction')
    def test_transient_commit_releases_old_owner_refs_without_io(self):self.check('transactioncommit')
    def test_transient_close_releases_current_and_retained_resources(self):self.check('transactionclose')
    def test_checkpoint_leaves_outer_transaction_open_for_later_cache_work(self):self.check('outertransaction')
    def test_transient_rollback_retains_an_unfinished_source_batch(self):self.check('transactionbatch')
    def test_failed_transaction_destination_keeps_the_interrupted_move(self):self.check('transactionfail')

if __name__=='__main__':unittest.main()
