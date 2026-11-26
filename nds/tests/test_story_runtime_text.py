"""Real full-story runtime/flow + streamed reader tests; only hardware is fake."""
from pathlib import Path
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('scene_runtime_stub_source',ROOT/'nds/tests/test_scene_runtime.py')
stub_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(stub_module)
NDS_STUB=stub_module.NDS_STUB+r'''
enum {KEY_UP=64,KEY_DOWN=128};
static inline uint32_t keysHeld(void){return 0;}
static uint16_t REG_BLDY;
enum {BLEND_FADE_BLACK=128,BLEND_SRC_BG2=256};
static inline void bgSetScroll(int a,int x,int y){(void)a;(void)x;(void)y;}
static inline void bgUpdate(void){}
'''
NDS_STUB=NDS_STUB.replace(
    'static inline void DC_FlushRange(const void*p,size_t n){(void)p;(void)n;}',
    'static size_t host_flush_bytes;\nstatic inline void DC_FlushRange(const void*p,size_t n){(void)p;host_flush_bytes+=n;}')
HARNESS=r'''
#define ZATO_STORY_DEMO 1
#include <stddef.h>
#include <string.h>
static void*host_frame_copy(void*dest,const void*src,size_t n);
#define memcpy host_frame_copy
#include "RUNTIME_SOURCE"
#undef memcpy
#include <stdlib.h>
#include "asset_pack.h"
volatile AudioStatus g_audio_status;
static bool host_upload_tracking;
static size_t host_upload_bytes,host_upload_service_bytes;
static unsigned host_upload_calls,host_upload_services,host_upload_head;
static void*host_frame_copy(void*dest,const void*src,size_t n){
    if(host_upload_tracking&&(uintptr_t)dest>=(uintptr_t)host_vram[bottom]&&
       (uintptr_t)dest<(uintptr_t)(host_vram[bottom]+PIXELS)){
        if(n>2048||dest!=(uint8_t*)host_vram[bottom]+host_upload_bytes||
           (host_upload_bytes&&host_upload_service_bytes!=host_upload_bytes)||
           flow.presented||g_story_presentations.head!=host_upload_head){
            fprintf(stderr,"FAIL: framebuffer upload must be bounded, serviceable and published only after completion\n");exit(2);
        }
        host_upload_bytes+=n;host_upload_calls++;
    }
    return memcpy(dest,src,n);
}
static unsigned host_audio_paused,host_sfx,host_sfx_visible,host_music_offset,host_special_reads;
static unsigned host_ui_cues,host_ui_last;
static bool host_history_pixels;
static StoryGfxSnapshot host_gfx,host_gfx_backup;
static bool host_special_loaded,host_cross_budget;
static bool host_checkpoint_decodable;
static bool host_special_io;
static uint32_t host_perf_ticks;
uint32_t audio_perf_now(void){return ++host_perf_ticks;}
bool audio_demo_init(void){return true;}
void audio_demo_service(void){
    if(host_upload_tracking){
        if(flow.presented||g_story_presentations.head!=host_upload_head||
           memcmp(host_vram[bottom],lower,host_upload_bytes)){
            fprintf(stderr,"FAIL: audio service saw a published or incomplete upload prefix\n");exit(2);
        }
        host_upload_service_bytes=host_upload_bytes;host_upload_services++;
    }
}
bool audio_scene_play(bool music,unsigned index,int gain,uint32_t offset,uint32_t fade){
    (void)index;(void)gain;(void)fade;if(!music){host_sfx++;host_sfx_visible=g_story_status.visible;}else host_music_offset=offset;return true;
}
void audio_scene_stop(bool music,uint32_t fade){(void)music;(void)fade;}
void audio_scene_pause(bool paused){host_audio_paused=paused;}
void audio_scene_volumes(unsigned m,unsigned s){(void)m;(void)s;}
bool audio_ui_prepare(void){return true;}
void audio_ui_play(AudioUiCue cue){host_ui_cues++;host_ui_last=cue;}
void audio_ui_stop(void){}
bool audio_scene_snapshot(AudioSceneSnapshot*s){memset(s,0,sizeof(*s));s->version=1;s->paused=host_audio_paused;return true;}
bool audio_scene_restore(const AudioSceneSnapshot*s){(void)s;return true;}
volatile StoryShellStatus g_story_shell_status;
static bool host_menu;
static StoryMenuMode host_menu_mode;
static StorySaveGlobals host_globals={.music_q12=4096,.sfx_q12=4096};
bool story_shell_active(void){return host_menu;}
bool story_shell_draw(uint32_t t,uint16_t*u,uint16_t*l){(void)t;for(unsigned i=0;i<PIXELS;i++){u[i]=0x8000;l[i]=0x8000;}return true;}
uint32_t story_shell_version(uint32_t t){return t+1;}
void story_shell_open(StoryMenuMode m){host_menu=true;host_menu_mode=m;}
void story_shell_init(const StoryShellOps*o,void*c){(void)o;(void)c;}
void story_shell_title(void){host_menu=true;}
void story_shell_input(StoryMenuInput i){(void)i;}
void story_shell_observe(unsigned c,unsigned t,bool complete,uint32_t lo,uint32_t hi,uint32_t d){(void)c;(void)t;(void)complete;(void)lo;(void)hi;(void)d;}
void story_shell_quicksave(void){}
void story_shell_quickload(void){}
void story_shell_language(bool b){host_globals.language=b;}
bool story_menu_open(const char*p){(void)p;return true;}
bool story_menu_draw_bar(const StoryMenuState*s,bool expanded,uint16_t*l){(void)s;(void)expanded;for(unsigned x=4;x<=251;x++)l[178*256+x]=l[191*256+x]=0xffff;return true;}
const uint8_t*story_menu_identity(void){static uint8_t id[32];return id;}
StorySaveGlobals*story_save_globals(void){return &host_globals;}
bool story_save_available(void){return false;}
bool story_save_fat_open(int argc,char*const argv[],const uint8_t id[32]){(void)argc;(void)argv;(void)id;return false;}
bool story_checkpoint_capture(const StoryFlow*f,const StoryBackendSnapshot*b,uint8_t p[768]){(void)f;(void)b;(void)p;return false;}
bool story_checkpoint_capture_view(const StoryFlow*f,const StoryBackendSnapshot*b,uint32_t row,bool manual,uint32_t covered,uint8_t p[768]){(void)f;(void)b;(void)row;(void)manual;(void)covered;(void)p;return false;}
bool story_checkpoint_decode(const uint8_t p[768],StoryCheckpointDecoded*d){(void)p;memset(d,0,sizeof(*d));return host_checkpoint_decodable;}
bool story_checkpoint_prepare(const StoryFlow*f,const StoryCheckpointDecoded*d,StoryCheckpointDecoded*out){(void)f;(void)d;(void)out;return false;}
bool story_checkpoint_valid(const StoryFlow*f,const StoryCheckpointDecoded*d){(void)f;(void)d;return false;}
bool story_checkpoint_restore(StoryFlow*f,const StoryCheckpointDecoded*d){(void)f;(void)d;return false;}
bool story_gfx_open(const char*a,const char*b){(void)a;(void)b;return true;}
void story_gfx_close(void){}
void story_gfx_clear(void){memset(&host_gfx,0,sizeof(host_gfx));}
bool story_gfx_show(unsigned a,unsigned b,int c,unsigned d,int e,uint32_t f){(void)a;(void)b;(void)c;(void)d;(void)e;(void)f;return true;}
bool story_gfx_hide(unsigned a){(void)a;return true;}
bool story_gfx_draw(uint32_t t,uint16_t*s){for(unsigned i=0;i<PIXELS;i++)s[i]=host_history_pixels?(uint16_t)(0x8000|((host_gfx.layers[0].image*31+t-host_gfx.layers[0].start)&0x7fff)):0x8842;return true;}
uint32_t story_gfx_version(uint32_t t){(void)t;return 0;}
const char*story_gfx_error(void){return "";}
size_t story_gfx_resident_bytes(void){return 0;}
uint32_t story_gfx_read_calls(void){return 0;}
unsigned story_gfx_layer_count(void){return 0;}
void story_gfx_begin_batch(uint32_t t){(void)t;}
bool story_gfx_move_transition(unsigned k,uint32_t n,uint32_t d){(void)k;(void)n;(void)d;return true;}
bool story_gfx_end_batch(void){return true;}
void story_gfx_set_replay(bool p){(void)p;}
bool story_gfx_sync(void){return true;}
bool story_gfx_snapshot(StoryGfxSnapshot*s){*s=host_gfx;return true;}
bool story_gfx_restore(const StoryGfxSnapshot*s){if(host_cross_budget&&host_special_loaded&&s->count)return false;host_gfx=*s;return true;}
bool story_gfx_restore_begin(void){host_gfx_backup=host_gfx;return true;}
void story_gfx_restore_commit(void){}
void story_gfx_restore_rollback(void){host_gfx=host_gfx_backup;}
bool story_gfx_restore_pending(void){return false;}
bool scene_gfx_open(const char*p){(void)p;if(host_cross_budget&&host_gfx.count)return false;host_special_loaded=true;if(host_special_io){host_special_reads=7;vblanks+=3;}return true;}
void scene_gfx_close(void){host_special_loaded=false;host_special_reads=0;}
bool scene_gfx_draw(unsigned g,uint32_t t,uint16_t*s){if(!host_history_pixels)return story_gfx_draw(t,s);for(unsigned i=0;i<PIXELS;i++)s[i]=(uint16_t)(0x9000|(g<<8)|(t&255));return true;}
unsigned scene_gfx_frame(unsigned g,uint32_t t){(void)g;(void)t;return 0;}
size_t scene_gfx_resident_bytes(void){return 0;}
uint32_t scene_gfx_read_calls(void){return host_special_reads;}
const char*scene_gfx_error(void){return "";}
bool story_script_open(StoryScript*s,const char*p){(void)s;(void)p;return false;}
static void need(int ok,const char*why){if(!ok){fprintf(stderr,"FAIL: %s\n",why);exit(2);}}
static StoryEvent events[16];static StoryChapter chapters[2];
static void begin(unsigned n){
    script=(StoryScript){.events=events,.count=n,.chapters=chapters,.chapter_count=1,.text_count=11975};
    chapters[0]=(StoryChapter){.first_pc=0,.end_pc=n};strcpy(chapters[0].name,"host0");
    top_old=0;top_new=1;bottom=2;
    story_history_close(&history);
    need(story_history_open(&history,&script),"history journal allocation");
    need(story_flow_init(&flow,&script,&flow_ops,NULL),"flow init");
}
static void text(unsigned id,unsigned flags){events[0]=(StoryEvent){.op=ST_TEXT,.a=(int)id,.flags=flags};events[1]=(StoryEvent){.op=ST_END};begin(2);}
static void render(void){prepare();need(flush_audio(),"audio queue");commit();}
static unsigned first(unsigned id,unsigned count){const SceneTextInfo*t=story_text_info(id);for(unsigned n=0;n<=t->duration_ticks;n++)if(story_text_visible(id,n)>=count)return n;return t->duration_ticks;}
static unsigned date_ink(void){unsigned n=0;for(unsigned y=36;y<44;y++)for(unsigned x=31;x<100;x++)if(host_vram[top_new][y*256+x]==0xffff)n++;return n;}
int main(int argc,char**argv){
    need(argc==3,"pack and case");need(story_text_open(argv[1]),story_text_error());
    if(!strcmp(argv[2],"lower_cpu_upload")){
        text(ID_SCROLL,0);const SceneTextInfo*t=story_text_info(ID_SCROLL);
        flow.scrolling=flow.bilingual=true;flow.page=t->pages_bi-1;flow.local=t->duration_ticks;story_flow_refresh(&flow);
        unsigned guard=0;do{render();}while(!flow.scroll_ready&&guard++<t->char_count);
        need(flow.scroll_ready,"complete starting text");
        scroll_row=0;scroll_manual=true;lower_valid=false;render();
        unsigned upper_hash=pixel_hash(host_vram[top_new],sizeof(host_vram[top_new]));
        for(unsigned i=PIXELS;i<256u*256u;i++)host_vram[bottom][i]=0x1357;
        unsigned flush=host_flush_bytes,dma=host_dma_copy_calls;
        scroll_input(KEY_DOWN);prepare();
        need(lower_dirty&&!upper_dirty,"ordinary scroll must only dirty lower");
        need(host_flush_bytes==flush,"CPU framebuffer upload must not discard its dirty cache with a full DMA flush");
        flow.presented=false;host_upload_head=g_story_presentations.head;host_upload_tracking=true;
        commit();host_upload_tracking=false;
        need(host_upload_bytes==sizeof(lower)&&host_upload_calls>=48&&host_upload_services>=47,"scroll upload must yield audio between at most2048-byte CPU copies");
        need(host_dma_copy_calls==dma,"ordinary lower upload must not monopolize the bus with DMA");
        need(!memcmp(lower,host_vram[bottom],sizeof(lower)),"CPU lower upload changed pixels");
        for(unsigned i=PIXELS;i<256u*256u;i++)need(host_vram[bottom][i]==0x1357,"CPU upload exceeded visible framebuffer");
        need(pixel_hash(host_vram[top_new],sizeof(host_vram[top_new]))==upper_hash,"lower-only upload changed upper VRAM");
        need(flow.presented&&g_story_presentations.head==host_upload_head+1,"full-copy completion must retain presentation ACK");
        prepare();need(!lower_dirty&&!upper_dirty,"static frame must not submit either framebuffer again");
    }else if(!strcmp(argv[2],"scroll_boundary_quiet")){
        text(ID_SCROLL,0);const SceneTextInfo*t=story_text_info(ID_SCROLL);
        flow.scrolling=flow.bilingual=true;flow.page=t->pages_bi-1;flow.local=t->duration_ticks;story_flow_refresh(&flow);
        unsigned guard=0;do{render();}while(!flow.scroll_ready&&guard++<t->char_count);
        need(flow.scroll_ready,"complete starting text");
        unsigned rows=story_text_scroll_max(ID_SCROLL,flow.visible,true);need(rows>2,"real multiline fixture");
        for(unsigned edge=0;edge<2;edge++){
            scroll_row=edge?rows:0;scroll_manual=!edge;lower_valid=false;render();
            unsigned reads=story_text_read_calls(),copies=host_dma_copy_calls,head=g_story_presentations.head;
            for(unsigned repeat=0;repeat<240;repeat++){
                scroll_input(edge?KEY_DOWN:KEY_UP);prepare();
                need(!lower_dirty,"holding against a scroll boundary must not redraw or submit identical pixels");
                commit();
            }
            need(story_text_read_calls()==reads&&host_dma_copy_calls==copies,"no-op scrolling caused file reads or framebuffer DMA");
            need(g_story_presentations.head==head+240,"no-op scroll must retain ordinary committed acknowledgements");
        }
        scroll_row=1;scroll_manual=true;lower_valid=false;render();
        scroll_input(KEY_UP|KEY_DOWN);prepare();need(!lower_dirty&&scroll_row==1,"opposing directions with no net movement must not redraw");
        scroll_input(KEY_UP);prepare();need(lower_dirty&&scroll_row==0,"a real row change still redraws");commit();
    }else if(!strcmp(argv[2],"scroll_coverage")){
        text(ID_SCROLL,0);const SceneTextInfo*t=story_text_info(ID_SCROLL);
        events[0].line=flow.line=t->source_line;events[0].d=t->duration_ticks;events[0].flags=t->mode<<1;
        flow.scrolling=flow.bilingual=true;flow.page=t->pages_bi-1;flow.local=t->duration_ticks;story_flow_refresh(&flow);
        render();need(flow.presented&&scroll_covered<flow.visible&&!flow.scroll_ready,"a true first viewport ACK must not count the hidden fast batch as shown");
        StoryFlowSnapshot point;need(story_flow_snapshot(&flow,&point),"visible partial viewport remains a legal save point");
        unsigned saved_row=scroll_row;scroll_restore_hold=true;lower_valid=false;render();
        need(scroll_row==saved_row&&!scroll_restore_hold,"restored viewport must first commit unchanged before follow resumes");
        unsigned guard=0;while(!flow.scroll_ready&&guard++<t->char_count)render();
        need(flow.scroll_ready&&scroll_covered==flow.visible&&scroll_row>saved_row,"automatic follow must actually show every part of the fast batch");
        scroll_input(KEY_UP);render();need(scroll_manual,"UP must scroll back without advancing the source");
        need(story_flow_snapshot(&flow,&point),"manual scrollback must not cause false save-point rejection");
        unsigned pc=flow.pc;host_touch=(touchPosition){90,100};touch_action();
        need(!scroll_manual&&flow.pc==pc,"first body touch in scrollback must return to follow only");
    }else if(!strcmp(argv[2],"resume_ack")){
        text(ID608,0);render();unsigned local=flow.local,visible=flow.visible;
        need(flow.presented,"real story frame starts acknowledged");
        shell_resume(NULL);
        need(flow.presented&&flow.local==local&&flow.visible==visible,"I/O-only autosave resume must retain the exact visible save point");
        story_shell_open(STORY_MENU_GAME);render();host_menu=false;shell_resume(NULL);
        need(!flow.presented,"closing a drawn overlay must require a fresh real story commit");
        render();need(flow.presented,"returned real story frame restores acknowledgement");
        host_globals.language=1;shell_resume(NULL);
        need(flow.bilingual&&!flow.presented,"a changed language layout must invalidate the old presentation");
    }else if(!strcmp(argv[2],"failed_load_pause")){
        text(ID608,0);render();host_checkpoint_decodable=true;uint8_t data[768]={0};
        audio_scene_pause(false);need(!shell_restore(NULL,data),"incompatible state must be refused");
        need(!host_audio_paused,"failed title load must preserve an unpaused old menu soundtrack");
        audio_scene_pause(true);need(!shell_restore(NULL,data)&&host_audio_paused,"failed game-menu load retains its already paused soundtrack");
    }else if(!strcmp(argv[2],"menu_hidden")){
        text(ID608,0);render();flow.presented=false;unsigned now=flow.now,local=flow.local;
        story_shell_open(STORY_MENU_SAVE);render();
        need(!flow.presented&&!(g_story_presentations.entries[1].flags&1),"menu pixels must never ACK hidden live text");
        need(lower[100*256+4]==0x8000&&flow.now==now&&flow.local==local,"menu render changed source clocks");
        host_menu=false;render();need(flow.presented,"actual returned dialogue must ACK again");
    }else if(!strcmp(argv[2],"footer_save")){
        text(ID608,0);render();host_touch=(touchPosition){130,185};touch_action();
        need(host_menu&&host_menu_mode==STORY_MENU_QUICK&&!debug,"footer must expand the four-item quick bar");
        need(host_ui_cues==1&&host_ui_last==AUDIO_UI_CONFIRM,"footer touch must play one UI open cue");
    }else if(!strcmp(argv[2],"debug_hidden")){
        text(ID629,1);render();unsigned now=flow.now,local=flow.local;flow.presented=false;
        set_debug(true);need(flow.paused&&host_audio_paused,"debug must pause audio and the whole story");
        render();need(!flow.presented,"diagnostic lower pixels must not acknowledge hidden live dialogue");
        story_flow_tick(&flow,1000);need(flow.now==now&&flow.local==local,"debug advanced the story clock");
        set_debug(false);need(!flow.paused&&!host_audio_paused,"closing debug must restore running state");render();need(flow.presented,"actual restored dialogue must be acknowledged");
        flow.paused=true;set_debug(true);set_debug(false);need(flow.paused&&host_audio_paused,"debug must restore an already-paused scene");
    }else if(!strcmp(argv[2],"debug_centered")){
        text(ID576,1);set_debug(true);flow.presented=false;render();
        need(flow.presented&&flow.paused,"visible upper centered text can acknowledge while debug still pauses its clock");
    }else if(!strcmp(argv[2],"history_visual")){
        events[0]=(StoryEvent){.op=ST_SHOW,.a=1,.b=0,.c=0xffff0000};
        events[1]=(StoryEvent){.op=ST_TEXT,.a=ID629};
        events[2]=(StoryEvent){.op=ST_SHOW,.a=7,.b=0,.c=0xffff0000};
        events[3]=(StoryEvent){.op=ST_TEXT,.a=ID608};events[4]=(StoryEvent){.op=ST_END};begin(5);
        story_history_clear(&history);
        story_history_note_event(&history,0,90,false);story_history_note_event(&history,1,100,false);
        host_gfx=(StoryGfxSnapshot){.count=1,.layers={{1,0,0,0,90}}};host_history_pixels=true;
        const SceneTextInfo*t=story_text_info(ID629);flow.local=t->duration_ticks;flow.pause_index=t->pause_count;flow.revealed=true;flow.now=123;story_flow_refresh(&flow);render();
        uint16_t completed_pixel=host_vram[top_new][0];need(completed_pixel==0x8000+31+33,"literal independent animation fixture");
        flow.now=400;render(); /* A later reading dwell must not replace the first full commit. */
        story_history_note_event(&history,2,450,false);story_history_note_event(&history,3,500,false);
        need(story_text_select(ID608,0),"live target");flow.pc=4;flow.text_id=ID608;flow.now=501;flow.local=1;flow.pause_index=0;flow.revealed=false;
        host_gfx=(StoryGfxSnapshot){.count=1,.layers={{7,0,0,0,450}}};upper_valid=false;render();
        unsigned live_pixel=host_vram[top_new][0],now=flow.now,local=flow.local,pc=flow.pc;
        previous_dialogue();render();
        need(reviewing&&host_vram[top_new][0]==completed_pixel,"B must recover previous scene at its first completed text frame");
        need(host_gfx.layers[0].image==1&&host_gfx.layers[0].start==90,"review must keep only the selected historical scene resident");
        unsigned reads=story_text_read_calls();story_flow_tick(&flow,120);render();
        need(host_vram[top_new][0]==completed_pixel&&story_text_read_calls()==reads,"history frame must remain stable without repeated file reads");
        close_review();render();
        need(host_vram[top_new][0]==live_pixel&&flow.now==now&&flow.local==local&&flow.pc==pc,"exit review must restore unchanged live scene and clocks");
        need(host_gfx.layers[0].image==7&&host_gfx.layers[0].start==450,"exit review must reload the saved live layer recipe");
    }else if(!strcmp(argv[2],"history_loaded_origin")){
        events[0]=(StoryEvent){.op=ST_SHOW,.a=1,.b=0,.c=0xffff0000};events[1]=(StoryEvent){.op=ST_TEXT,.a=ID629};
        events[2]=(StoryEvent){.op=ST_SHOW,.a=7,.b=0,.c=0xffff0000};events[3]=(StoryEvent){.op=ST_TEXT,.a=ID608};events[4]=(StoryEvent){.op=ST_END};begin(5);
        story_history_clear(&history);host_history_pixels=true;host_gfx=(StoryGfxSnapshot){.count=1,.layers={{1,0,0,0,900}}};
        flow.now=950;const SceneTextInfo*t=story_text_info(ID629);flow.local=t->duration_ticks;flow.pause_index=t->pause_count;flow.revealed=true;
        need(finish_loading(),"finish loaded graphics");render();unsigned original=host_vram[top_new][0];
        need(original==0x8051,"independent saved-scene animation age");
        need(story_text_select(ID608,0),"next loaded text");flow.pc=4;flow.text_id=ID608;flow.now=1001;flow.local=0;flow.pause_index=0;flow.revealed=false;
        story_history_note_event(&history,2,1000,false);story_history_note_event(&history,3,1001,false);
        host_gfx=(StoryGfxSnapshot){.count=1,.layers={{7,0,0,0,1000}}};upper_valid=false;render();previous_dialogue();render();
        need(!flow.failed&&host_vram[top_new][0]==original,"a loaded scene observed to completion must retain its actual saved animation origin");
    }else if(!strcmp(argv[2],"history_cross_special")){
        bool live_special=getenv("ZATO_LIVE_SPECIAL")!=NULL;
        events[0]=live_special?(StoryEvent){.op=ST_SHOW,.a=1,.b=0,.c=0xffff0000}:(StoryEvent){.op=ST_SPECIAL,.a=2,.b=0,.c=1};
        events[1]=(StoryEvent){.op=ST_TEXT,.a=ID629};events[2]=(StoryEvent){.op=ST_SPECIAL,.a=-1};
        events[3]=live_special?(StoryEvent){.op=ST_SPECIAL,.a=3,.b=0,.c=1}:(StoryEvent){.op=ST_SHOW,.a=7,.b=0,.c=0xffff0000};
        events[4]=(StoryEvent){.op=ST_TEXT,.a=ID608};events[5]=(StoryEvent){.op=ST_END};begin(6);
        story_history_clear(&history);story_history_note_event(&history,0,10,false);story_history_note_event(&history,1,12,false);story_history_note_complete(&history,1,19);
        need(story_text_select(ID608,0),"live target");flow.pc=5;flow.text_id=ID608;flow.now=501;
        host_gfx=(StoryGfxSnapshot){.count=live_special?0:1,.layers={{7,0,0,0,450}}};
        backend.special=backend.special_loaded=host_special_loaded=live_special;backend.group=3;backend.phase=0;backend.origin[0]=500;
        host_history_pixels=host_cross_budget=true;upper_valid=false;render();
        unsigned live_pixel=host_vram[top_new][0];previous_dialogue();render();
        need(!flow.failed&&host_vram[top_new][0]==(live_special?0x8028:0x9209),"history must cross ordinary/special without retaining both large resource sets");
        need(host_special_loaded!=live_special&&host_gfx.count==(live_special?1u:0u),"review must retain only historical graphics, with no live resource pinning");
        close_review();render();need(!flow.failed&&host_vram[top_new][0]==live_pixel,"cross-special history exit must restore live screen");
        need(host_special_loaded==live_special&&host_gfx.count==(live_special?0u:1u),"exit review must reload the original kind of live resources");
    }else if(!strcmp(argv[2],"history_audio")){
        text(ID608,0);events[0]=(StoryEvent){.op=ST_TEXT,.a=ID629};events[1]=(StoryEvent){.op=ST_TEXT,.a=ID608};events[2]=(StoryEvent){.op=ST_END};script.count=3;flow.pc=2;
        render();unsigned now=flow.now,local=flow.local,pc=flow.pc,music=host_music_offset,sfx=host_sfx;
        previous_dialogue();need(reviewing&&flow.paused&&!host_audio_paused,"history must freeze plot without pausing the live soundtrack");
        story_flow_tick(&flow,120);render();
        need(flow.now==now&&flow.local==local&&flow.pc==pc&&!host_audio_paused,"review frames must leave current plot frozen and audio running");
        close_review();need(!flow.paused&&!host_audio_paused&&host_music_offset==music&&host_sfx==sfx,"history must not restart music or replay historical sound effects");
        flow.paused=true;audio_scene_pause(true);previous_dialogue();
        need(reviewing&&flow.paused&&host_audio_paused,"history must preserve an explicit pre-existing pause");
        close_review();need(flow.paused&&host_audio_paused,"history exit must restore an explicit pause");
    }else if(!strncmp(argv[2],"overlay_",8)){
        text(ID608,0);events[0]=(StoryEvent){.op=ST_TEXT,.a=ID629};events[1]=(StoryEvent){.op=ST_TEXT,.a=ID608};events[2]=(StoryEvent){.op=ST_END};script.count=3;flow.pc=2;
        bool base=strstr(argv[2],"paused")!=NULL,review_first=strstr(argv[2],"review_first")!=NULL;
        flow.paused=base;audio_scene_pause(base);render();
        if(review_first){previous_dialogue();set_debug(true);}
        else{set_debug(true);previous_dialogue();}
        need(reviewing&&debug&&flow.paused&&host_audio_paused,"both overlays must pause story and audio");
        if(review_first)close_review();else set_debug(false);
        need(flow.paused&&host_audio_paused==(review_first||base),"remaining debug pauses audio, while remaining review preserves only the explicit pause");
        unsigned now=flow.now,local=flow.local;story_flow_tick(&flow,30);render();
        need(flow.now==now&&flow.local==local,"remaining overlay must freeze story and text clocks");
        if(review_first)set_debug(false);else close_review();
        need(!reviewing&&!debug&&flow.paused==base&&host_audio_paused==base,"closing both overlays must restore the original running/paused state");
    }else if(!strcmp(argv[2],"presentation_ring")){
        text(ID608,0);events[0]=(StoryEvent){.op=ST_TEXT,.a=ID629};events[1]=(StoryEvent){.op=ST_TEXT,.a=ID608};events[2]=(StoryEvent){.op=ST_END};script.count=3;flow.pc=2;
        need(g_story_presentations.head==0,"presentation log starts empty");render();
        need(g_story_presentations.head==1&&g_story_presentations.entries[0].serial==1&&(g_story_presentations.entries[0].flags&1),"a visible ordinary dialogue commit must publish an acknowledged presentation");
        set_debug(true);flow.presented=false;render();
        need(!(g_story_presentations.entries[1].flags&1)&&!flow.presented,"diagnostics hiding the body must not publish an acknowledgement");
        previous_dialogue();flow.presented=false;render();
        need(!(g_story_presentations.entries[2].flags&1)&&!flow.presented,"history hiding live text must not publish an acknowledgement");
        close_review();set_debug(false);text(ID576,1);set_debug(true);flow.presented=false;render();
        need((g_story_presentations.entries[3].flags&1)&&(g_story_presentations.entries[3].flags&4)&&flow.presented,"centered upper text stays visible and acknowledged with lower diagnostics open");
        for(unsigned n=0;n<40;n++){
            flow.local=n;flow.now=n+100;render();unsigned serial=n+5;
            const volatile StoryPresentation*p=&g_story_presentations.entries[(serial-1)%STORY_PRESENTATION_CAPACITY];
            need(g_story_presentations.head==serial&&p->serial==serial&&p->commands==flow.commands&&p->text_id==ID576&&p->page==flow.page&&p->visible==flow.visible&&p->local==n&&p->ticks==n+100&&(p->flags&1),"wrapped log entry must identify its actual submitted frame");
        }
        for(unsigned serial=13;serial<=44;serial++){
            const volatile StoryPresentation*p=&g_story_presentations.entries[(serial-1)%STORY_PRESENTATION_CAPACITY];
            need(p->serial==serial&&p->local==serial-5&&p->ticks==serial+95,"the latest32 presentation serials survive ring wrap in order");
        }
    }else if(!strcmp(argv[2],"date_review")){
        text(ID608,0);events[0]=(StoryEvent){.op=ST_TEXT,.a=ID576};events[1]=(StoryEvent){.op=ST_TEXT,.a=ID608};events[2]=(StoryEvent){.op=ST_END};script.count=3;flow.pc=2;
        flow.date_id=11970;need(cache_date(11970),"date cache");render();unsigned ink=date_ink();need(ink>20,"date actually rendered");
        unsigned live=flow.text_id,pc=flow.pc,clock=flow.local,vis=flow.visible;flow.presented=false;
        previous_dialogue();need(reviewing&&flow.paused&&review_id==ID576,"history uses independent slot1");
        need(story_text_info(11970)==NULL&&story_text_info(live),"history may evict date record but must pin live text");
        unsigned reads=story_text_read_calls();render();need(date_ink()==0,"history before the live date must not inherit a future date overlay");
        need(story_text_read_calls()==reads&&!flow.presented,"review rendering did I/O or acknowledged hidden live text");
        close_review();render();need(!flow.paused&&!reviewing&&flow.text_id==live&&flow.pc==pc&&flow.local==clock&&flow.visible==vis,"review exit changed live state");
        need(date_ink()==ink&&story_text_read_calls()==reads,"cached date must survive review exit without record reload");
    }else if(!strcmp(argv[2],"literal_review_restore")){
        text(ID608,0);events[0]=(StoryEvent){.op=ST_TEXT,.a=ID576};events[1]=(StoryEvent){.op=ST_TEXT,.a=ID608};events[2]=(StoryEvent){.op=ST_END};script.count=3;flow.pc=2;
        flow.literal_id=11960;flow.literal_duration=120;need(story_text_select(11960,1),"literal slot");render();
        previous_dialogue();need(!story_text_info(11960),"history must borrow literal slot");close_review();
        need(story_text_info(11960)&&story_text_info(ID608)&&!flow.failed,"review exit must restore literal and retain live pin");
        unsigned reads=story_text_read_calls();render();need(story_text_read_calls()==reads,"literal draw must remain read-only");
    }else if(!strcmp(argv[2],"poem_pages")){
        const unsigned ids[]={11952,11953,11960,11961};uint16_t previous[PIXELS],expected[PIXELS],cn_mode[PIXELS];
        for(unsigned n=0;n<4;n++)for(unsigned bi=0;bi<2;bi++){
            events[0]=(StoryEvent){.op=ST_WINDOW};events[1]=(StoryEvent){.op=ST_QUICK};events[2]=(StoryEvent){.op=ST_LITERAL,.a=(int)ids[n]};
            events[3]=(StoryEvent){.op=ST_WAIT,.a=120,.b=1};events[4]=(StoryEvent){.op=ST_HIDE_LITERAL};events[5]=(StoryEvent){.op=ST_END};begin(6);
            flow.bilingual=bi;upper_valid=lower_valid=false;
            need(story_text_is_poem(ids[n]),"both authored stages need split-screen poem metadata");
            const SceneTextInfo*t=story_text_info(ids[n]);unsigned pages=t->pages_cn;
            need(pages==(n<2?2u:3u)&&t->pages_bi==pages,"poem page groups must be shared by both languages and screens");
            need(flow.literal_duration==120&&flow.waiting&&flow.hard&&!flow.window&&!flow.quick_menu,"preserve authored wait and hidden ordinary window/footer");
            unsigned reads=story_text_read_calls();
            for(unsigned page=0;page<pages;page++){
                unsigned tick=(120*page+pages-1)/pages;
                if(flow.now<tick)story_flow_tick(&flow,tick-flow.now);
                render();need(upper_key.literal_page==page,"poem page switched at the wrong source tick");
                story_text_draw_lower_poem(ids[n],page,1,0,expected);
                need(!memcmp(lower,expected,sizeof(lower)),"Chinese poem must appear on lower even when source window is hidden");
                unsigned ink=0;for(unsigned y=24;y<168;y++)for(unsigned x=8;x<248;x++)ink+=lower[y*256+x]!=0x8000;
                need(ink>30,"Chinese poem page has no visible body glyphs");
                if(page)need(memcmp(previous,lower,sizeof(lower)),"lower poem remained stuck on the first page");
                memcpy(previous,lower,sizeof(lower));
                if(!page){if(!bi)memcpy(cn_mode,lower,sizeof(lower));else need(!memcmp(cn_mode,lower,sizeof(lower)),"BI switch must not hide the Chinese poem");}
                for(unsigned i=0;i<PIXELS;i++)expected[i]=0x8842;
                story_text_draw_upper_bilingual(ids[n],t->char_count,page,0,expected);
                need(!memcmp(upper,expected,sizeof(upper)),"upper RU/EN poem differs by language setting or page");
            }
            need(story_text_read_calls()==reads,"poem page switch performed hidden I/O");
            story_flow_tick(&flow,120-flow.now);render();need(flow.done&&flow.literal_id==NONE,"poem must hide at authored endpoint");
            for(unsigned i=0;i<PIXELS;i++)need(lower[i]==0x8000,"Chinese poem leaked after hide text");
        }
    }else if(!strcmp(argv[2],"auto_indicator")){
        text(ID608,0);render();uint16_t before[PIXELS];memcpy(before,lower,sizeof(before));
        unsigned reads=story_text_read_calls();flow.automatic=true;prepare();
        need(lower_dirty&&!upper_dirty,"enabling AUTO must refresh its lower indicator even when text and scene are unchanged");commit();
        unsigned ink=0;for(unsigned y=0;y<192;y++)for(unsigned x=0;x<256;x++){
            unsigned at=y*256+x;
            if(lower[at]!=before[at]){need(x>=112&&x<144&&y>=179&&y<187&&lower[at]==0xc631,"AUTO must use the centered small gray footer region only");ink++;}
        }
        need(ink>20,"AUTO marker has no visible glyphs");prepare();need(!lower_dirty&&!upper_dirty,"static AUTO must not redraw every frame");
        flow.automatic=false;prepare();need(lower_dirty,"disabling AUTO must clear its old pixels immediately");commit();
        need(!memcmp(before,lower,sizeof(before))&&story_text_read_calls()==reads,"AUTO toggle must restore the footer without font or text I/O");
        flow.quick_menu=false;flow.automatic=true;render();for(unsigned y=179;y<187;y++)for(unsigned x=112;x<144;x++)need(lower[y*256+x]==0x8000,"hidden footer must hide AUTO too");
    }else if(!strcmp(argv[2],"quick_window")){
        text(ID608,0);flow.window=false;flow.quick_menu=true;render();need(lower[100*256+4]==0x8000,"hidden window frame");
        unsigned ink=0;for(unsigned y=178;y<192;y++)for(unsigned x=0;x<256;x++)if(lower[y*256+x]!=0x8000)ink++;need(ink>100,"expandable footer survives window hide");
        flow.quick_menu=false;render();for(unsigned i=0;i<PIXELS;i++)need(lower[i]==0x8000,"quick_menu=False must clear footer");
    }else if(!strcmp(argv[2],"hidden_footer_touch")){
        text(ID608,0);flow.quick_menu=false;flow.automatic=flow.bilingual=false;
        host_touch=(touchPosition){92,185};touch_action();need(!flow.automatic,"hidden AUTO label must not receive touches");
        host_touch=(touchPosition){112,185};touch_action();need(!flow.bilingual,"hidden CN/BI label must not receive touches");
        host_touch=(touchPosition){150,185};touch_action();need(!debug,"hidden SAVE label must not open diagnostics");
    }else if(!strcmp(argv[2],"nw_real_text")){
        events[0]=(StoryEvent){.op=ST_TEXT,.a=ID629,.flags=1};events[1]=(StoryEvent){.op=ST_SFX,.a=0,.b=2048};events[2]=(StoryEvent){.op=ST_END};begin(3);render();
        unsigned count=story_text_info(ID629)->char_count;
        for(unsigned n=0;n<300&&!host_sfx;n++){story_flow_tick(&flow,1);render();}
        need(host_sfx==1&&host_sfx_visible==count,"real streamed NW text did not reach a hardware commit before following SFX");
    }else if(!strcmp(argv[2],"real_page")){
        text(ID709,0);flow.page=0;story_flow_refresh(&flow);render();unsigned cap=story_text_page_limit(ID709,0,0),stamp=first(ID709,cap);
        story_flow_tick(&flow,20);render();need(flow.page_hold&&flow.visible==cap&&flow.local==stamp,"real NVL page must cap both visible count and text clock");
        story_flow_advance(&flow);render();need(flow.page==1&&!flow.page_hold,"A must continue the next real NVL page");
    }else if(!strcmp(argv[2],"waiting_no_history")){
        text(ID608,0);flow.pc=2;events[1]=events[0];
        for(unsigned h=0;h<2;h++){flow.waiting=true;flow.hard=h;unsigned reads=story_text_read_calls();previous_dialogue();need(!reviewing&&!flow.paused&&story_text_read_calls()==reads,"B must not borrow slot1 during soft/hard waits");}
    }else if(!strcmp(argv[2],"quiet_flags_window")){
        need(story_text_select(ID576,0),"centered metadata");unsigned automatic_duration=story_text_info(ID576)->duration_ticks;
        need(story_text_select(ID608,0),"ordinary metadata");unsigned ordinary_duration=story_text_info(ID608)->duration_ticks;
        events[0]=(StoryEvent){.op=ST_MUSIC,.a=0,.b=2048};events[1]=(StoryEvent){.op=ST_WINDOW,.a=0};events[2]=(StoryEvent){.op=ST_QUICK,.a=0};
        events[3]=(StoryEvent){.op=ST_TEXT,.a=ID576,.flags=7,.d=(int)automatic_duration};events[4]=(StoryEvent){.op=ST_CLEAR};
        events[5]=(StoryEvent){.op=ST_TEXT,.a=ID608,.flags=2,.b=TR_VPUNCH,.c=17,.d=(int)ordinary_duration};
        events[6]=(StoryEvent){.op=ST_WINDOW,.a=0};events[7]=(StoryEvent){.op=ST_WAIT,.a=30,.b=1};events[8]=(StoryEvent){.op=ST_SFX,.a=0,.b=2048};
        events[9]=(StoryEvent){.op=ST_JUMP,.a=10,.b=1};events[10]=(StoryEvent){.op=ST_WAIT,.a=-1};events[11]=(StoryEvent){.op=ST_END};begin(12);
        script.chapter_count=2;chapters[0].end_pc=10;chapters[1]=(StoryChapter){.first_pc=10,.end_pc=12};strcpy(chapters[1].name,"host1");
        need(seek(1),"seek ordinary state");
        need(flow.now==automatic_duration+17+30,"quiet replay must use only bit0auto duration plus attached transition, not mode bits or ordinary typing");
        need(flow.text_id==ID608&&flow.text_active&&!flow.window&&!flow.quick_menu&&flow.forever&&!flow.hard,"quiet replay lost text/window/quick state");
        need(flow.visible==story_text_info(ID608)->char_count&&flow.local==ordinary_duration,"quiet replay must select and fully reveal retained ordinary text");
        need(!host_sfx&&host_music_offset==(uint64_t)flow.now*22050/60,"quiet replay must not play historical SFX; music resumes at reconstructed offset");
    }else if(!strcmp(argv[2],"quiet_clear_centered")){
        events[0]=(StoryEvent){.op=ST_TEXT,.a=ID608,.flags=2};events[1]=(StoryEvent){.op=ST_CLEAR};
        events[2]=(StoryEvent){.op=ST_TEXT,.a=ID576,.flags=6};events[3]=(StoryEvent){.op=ST_QUICK,.a=0};events[4]=(StoryEvent){.op=ST_WINDOW,.a=0};
        events[5]=(StoryEvent){.op=ST_JUMP,.a=6,.b=1};events[6]=(StoryEvent){.op=ST_WAIT,.a=-1};events[7]=(StoryEvent){.op=ST_END};begin(8);
        script.chapter_count=2;chapters[0].end_pc=6;chapters[1]=(StoryChapter){.first_pc=6,.end_pc=8};strcpy(chapters[1].name,"host1");
        need(seek(1),"seek past centered");render();need(!flow.text_active&&!flow.window&&!flow.quick_menu,"quiet completed centered must not remain active or reopen window");
        for(unsigned i=0;i<PIXELS;i++)need(upper[i]==0x8842&&lower[i]==0x8000,"quiet clear/centered replay leaked old dialogue pixels");
    }else if(!strcmp(argv[2],"attached_punch_live")){
        events[0]=(StoryEvent){.op=ST_TEXT,.a=355,.flags=0,.b=TR_VPUNCH,.c=17,.d=3};events[1]=(StoryEvent){.op=ST_END};begin(2);render();
        unsigned initial=flow.visible;need(backend.transition==TR_VPUNCH&&backend.transition_start==flow.now&&!flow.waiting,"Say.with_ must install transition for the same dialogue interaction");
        story_flow_tick(&flow,2);render();need(flow.local==2&&flow.visible>initial&&flow.blocked&&!flow.waiting,"attached punch must not insert an extra blocking wait before text typing");
    }else if(!strcmp(argv[2],"special_io_monotonic")){
        text(ID608,0);host_special_io=true;unsigned base=io_reads();last_vblank=vblanks;
        need(open_special(),"first special open");need(io_reads()==base+7,"first special read count");exclude_io_time(base);
        need(g_story_status.io_vblanks==3&&last_vblank==vblanks,"first special I/O frames must be excluded");
        scene_gfx_close();need(io_reads()==base+7,"special close must not roll back cumulative I/O telemetry");unsigned before=io_reads();
        need(open_special(),"repeated special open");need(io_reads()==base+14,"same local7 reads on a second open must accumulate to14");exclude_io_time(before);
        need(g_story_status.io_vblanks==6&&last_vblank==vblanks&&flow.now==0,"second3-frame load must be counted/excluded without advancing story time");
        reset_backend(NULL);need(io_reads()==base+14,"backend seek/reset must retain the cumulative special read count");
    }else need(0,"unknown case");
    story_text_close();puts("PASS real story runtime text");return 0;
}
'''


class StoryRuntimeTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='zato_story_runtime_');folder=Path(cls.temp.name)
        (folder/'nds.h').write_text(NDS_STUB,encoding='utf-8')
        (folder/'filesystem.h').write_text('static inline int nitroFSInit(const char*p){(void)p;return 1;}\n',encoding='utf-8')
        manifest=json.loads((ROOT/'nds/artifacts/story_text_manifest.json').read_text(encoding='utf-8'))
        ids={line:next(r['id'] for r in manifest['records'] if r['source_file']=='game/ep2d3.rpy' and r['source_line']==line and r['kind']=='say') for line in (576,608,629,709)}
        runtime_source=Path(os.environ.get('STORY_RUNTIME_TEST_SOURCE',ROOT/'nds/source/story_runtime.c'))
        source=HARNESS.replace('RUNTIME_SOURCE',runtime_source.as_posix())
        for line,id in ids.items():source=source.replace(f'ID{line}',str(id))
        scroll=max((r for r in manifest['records'] if r['kind']=='say' and r['mode']<3 and not r['continuation'] and not r['pause_count']),key=lambda r:r['char_count'])
        source=source.replace('ID_SCROLL',str(scroll['id']))
        (folder/'runtime.c').write_text(source,encoding='utf-8');cls.exe=folder/'runtime.exe'
        result=subprocess.run([shutil.which('gcc') or 'gcc','-std=c11','-O2','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable',f'-I{folder}',f'-I{ROOT/"nds/source"}',str(folder/'runtime.c'),str(ROOT/'nds/source/story_flow.c'),str(ROOT/'nds/source/story_history.c'),str(ROOT/'nds/source/story_text.c'),str(ROOT/'nds/source/asset_pack.c'),'-o',str(cls.exe)],capture_output=True,text=True)
        if result.returncode:raise AssertionError(result.stdout+result.stderr)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def check(self,name):
        result=subprocess.run([str(self.exe),str(ROOT/'nds/nitrofs/story/text.bin'),name],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_menu_frame_cannot_ack_hidden_live_dialogue(self):self.check('menu_hidden')
    def test_scrolling_covers_fast_batch_preserves_saved_view_and_allows_scrollback_save(self):self.check('scroll_coverage')
    def test_held_scroll_at_both_boundaries_leaves_unchanged_framebuffer_quiet(self):self.check('scroll_boundary_quiet')
    def test_lower_scroll_upload_is_bounded_audio_serviceable_and_acknowledged_after_full_copy(self):self.check('lower_cpu_upload')
    def test_io_resume_preserves_savepoint_but_overlay_and_language_require_commit(self):self.check('resume_ack')
    def test_failed_load_preserves_old_audio_pause_state(self):self.check('failed_load_pause')
    def test_footer_save_opens_real_save_view(self):self.check('footer_save')
    def test_diagnostics_pause_audio_and_do_not_ack_hidden_live_text(self):self.check('debug_hidden')
    def test_visible_centered_upper_text_can_ack_while_diagnostics_pause(self):self.check('debug_centered')
    def test_history_freezes_plot_but_keeps_live_music_and_preserves_explicit_pause(self):self.check('history_audio')
    def test_history_recovers_completed_scene_and_exit_restores_live_scene_without_progress(self):self.check('history_visual')
    def test_history_of_loaded_scene_keeps_its_saved_animation_origin(self):self.check('history_loaded_origin')
    def test_normal_live_can_review_special_without_simultaneous_large_caches(self):self.check('history_cross_special')
    def test_special_live_can_review_normal_without_simultaneous_large_caches(self):
        result=subprocess.run([str(self.exe),str(ROOT/'nds/nitrofs/story/text.bin'),'history_cross_special'],capture_output=True,text=True,env=dict(os.environ,ZATO_LIVE_SPECIAL='1'))
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_review_then_debug_restores_running_state_after_both_close(self):self.check('overlay_review_first_running')
    def test_review_then_debug_restores_paused_state_after_both_close(self):self.check('overlay_review_first_paused')
    def test_debug_then_review_restores_running_state_after_both_close(self):self.check('overlay_debug_first_running')
    def test_debug_then_review_restores_paused_state_after_both_close(self):self.check('overlay_debug_first_paused')
    def test_actual_commit_presentation_ring_records_visible_acks_and_wraps(self):self.check('presentation_ring')
    def test_date_pixels_survive_history_borrowing_slot1_without_hidden_io(self):self.check('date_review')
    def test_history_exit_restores_literal_and_preserves_live_slot(self):self.check('literal_review_restore')
    def test_both_poems_show_synchronized_upper_ru_en_lower_cn_in_both_language_modes(self):self.check('poem_pages')
    def test_quick_menu_and_window_visibility_preserve_original_footer(self):self.check('quick_window')
    def test_auto_footer_indicator_toggles_without_stale_pixels_or_repeated_redraw(self):self.check('auto_indicator')
    def test_hidden_footer_does_not_accept_invisible_menu_touches(self):self.check('hidden_footer_touch')
    def test_actual_streamed_nw_endpoint_precedes_following_sound(self):self.check('nw_real_text')
    def test_actual_streamed_nvl_page_freezes_visible_count_and_clock(self):self.check('real_page')
    def test_history_is_ignored_during_soft_and_hard_waits(self):self.check('waiting_no_history')
    def test_quiet_seek_reconstructs_flags_window_quick_and_music_clock(self):self.check('quiet_flags_window')
    def test_quiet_seek_clear_and_completed_centered_do_not_leak_pixels(self):self.check('quiet_clear_centered')
    def test_attached_punch_starts_with_text_and_does_not_delay_typing(self):self.check('attached_punch_live')
    def test_repeated_special_opens_keep_monotonic_io_and_exclude_each_load(self):self.check('special_io_monotonic')


if __name__=='__main__':unittest.main()
