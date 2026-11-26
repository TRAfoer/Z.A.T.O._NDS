"""Exercise the actual runtime state machine with portable hardware stubs.

The production .c is included unchanged, so static state and entry points remain
the real implementation. Text data and text rendering use the production pack.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
NDS_STUB=r'''
#ifndef HOST_NDS_STUB
#define HOST_NDS_STUB
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
typedef struct {int px,py;} touchPosition;
static touchPosition host_touch;
static uint16_t host_vram[3][256*256];
static uint16_t REG_BLDCNT,REG_BLDALPHA;
static unsigned host_dma_copy_calls;
static inline void scanKeys(void){}
static inline uint32_t keysDown(void){return 0;}
static inline void touchRead(touchPosition*p){*p=host_touch;}
static inline void*bgGetGfxPtr(int id){return host_vram[id];}
static inline void dmaCopy(const void*s,void*d,size_t n){host_dma_copy_calls++;memcpy(d,s,n);}
static inline void DC_FlushRange(const void*p,size_t n){(void)p;(void)n;}
static inline void dmaFillHalfWords(uint16_t v,void*d,size_t n){uint16_t*p=d;while(n>=2){*p++=v;n-=2;}}
static inline void videoSetModeSub(int n){(void)n;}
static inline void videoSetMode(int n){(void)n;}
static inline void consoleInit(void*p,...){(void)p;}
static inline void powerOn(int n){(void)n;}
static inline void lcdMainOnTop(void){}
static inline void vramSetBankA(int n){(void)n;}
static inline void vramSetBankB(int n){(void)n;}
static inline void vramSetBankC(int n){(void)n;}
static inline int bgInit(int a,int b,int c,int d,int e){(void)a;(void)b;(void)c;(void)d;(void)e;return 0;}
static inline int bgInitSub(int a,int b,int c,int d,int e){return bgInit(a,b,c,d,e);}
static inline void bgSetPriority(int a,int b){(void)a;(void)b;}
static inline void swiWaitForVBlank(void){}
static inline void irqSet(int n,void(*f)(void)){(void)n;(void)f;}
static inline void irqEnable(int n){(void)n;}
static inline int enterCriticalSection(void){return 0;}
static inline void leaveCriticalSection(int n){(void)n;}
enum {MODE_0_2D,MODE_5_2D,BgType_Text4bpp,BgSize_T_256x256,POWER_ALL_2D,
VRAM_A_MAIN_BG_0x06000000,VRAM_B_MAIN_BG_0x06020000,VRAM_C_SUB_BG,
BgType_Bmp16,BgSize_B16_256x256,IRQ_VBLANK,BLEND_ALPHA,BLEND_SRC_BG3,BLEND_DST_BG2};
enum {KEY_A=1,KEY_B=2,KEY_RIGHT=4,KEY_LEFT=8,KEY_START=16,KEY_L=32,
KEY_R=64,KEY_X=128,KEY_SELECT=256,KEY_TOUCH=512};
#endif
'''
HARNESS=r'''
#define ZATO_SCENE_DEMO 1
#include "RUNTIME_SOURCE"
#include <stdlib.h>
static void render(void){prepare_frame();commit_frame();}
volatile AudioStatus g_audio_status;
static unsigned host_sfx_count,host_sfx_visible,host_music_offset;
bool audio_demo_init(void){return true;}
void audio_demo_service(void){}
bool audio_scene_play(bool music,unsigned index,int gain,uint32_t offset,uint32_t fade){
    (void)index;(void)gain;(void)fade;
    if(music)host_music_offset=offset;
    else{host_sfx_count++;host_sfx_visible=g_scene_status.visible;}
    return true;
}
void audio_scene_stop(bool music,uint32_t fade){(void)music;(void)fade;}
void audio_scene_pause(bool p){(void)p;}
bool scene_gfx_open(const char*p){(void)p;return true;}
bool scene_gfx_select(unsigned g){(void)g;return true;}
bool scene_gfx_draw(unsigned g,uint32_t t,uint16_t*s){(void)g;(void)t;for(unsigned i=0;i<PIXELS;i++)s[i]=0x8000;return true;}
unsigned scene_gfx_frame(unsigned g,uint32_t t){(void)g;(void)t;return 0;}
size_t scene_gfx_resident_bytes(void){return 0;}
uint32_t scene_gfx_read_calls(void){return 0;}
const char*scene_gfx_error(void){return "";}
bool scene_script_open(SceneScript*s,const char*p){(void)s;(void)p;return false;}
static void need(int ok,const char*why){if(!ok){fprintf(stderr,"FAIL: %s\n",why);exit(2);}}
static unsigned find(unsigned line){for(unsigned i=0;i<scene_text_count();i++)if(scene_text_info(i)->source_line==line)return i;need(0,"source line");return 0;}
static SceneEvent events[8];
static void begin(unsigned line,unsigned flags){
    memset(events,0,sizeof(events));memset(&script,0,sizeof(script));
    events[0]=(SceneEvent){.op=SC_TEXT,.flags=flags,.line=line,.a=(int)find(line)};
    events[1]=(SceneEvent){.op=SC_END};script.events=events;script.count=2;
    static const SceneCheckpoint point[]={{0,"host case"}};
    script.checkpoints=point;script.checkpoint_count=1;checkpoint=0;
    top_old=0;top_new=1;bottom=2;pc=1;now=0;done=false;paused=false;
    start_text(&events[0]);
}
int main(int argc,char**argv){
    need(argc==3,"text pack and case");need(scene_text_open(argv[1]),scene_text_error());
    if(!strcmp(argv[2],"w_reveal")){
        begin(629,1);render();
        const SceneTextPause*next=scene_text_pause(text_id,1);need(next!=NULL,"second w");
        advance();render();
        need(visible>=next->glyphs,"A dismissing first w must actually render the following segment before another A can dismiss it");
    }else if(!strcmp(argv[2],"page_freeze")){
        begin(709,0);page=0;render();
        unsigned limit=scene_text_page_limit(text_id,page,0),end=first_tick(limit);
        need(limit<scene_text_info(text_id)->char_count,"multi-page Chinese fixture");
        now+=20;update_text(20);render();
        need(visible==limit && page_hold,"manual page cap");
        need(local==end,"manual page hold must freeze text local time at its actual page endpoint");
    }else if(!strcmp(argv[2],"touch_auto")){
        begin(608,0);automatic=bilingual=debug=skip=false;
        host_touch=(touchPosition){92,185};touch_action();
        need(automatic && !bilingual && !skip,"the actual AUTO label hit box is x84..99, not an equal 21px slot");
    }else if(!strcmp(argv[2],"touch_bilingual")){
        begin(709,0);bilingual=true;page=scene_text_info(text_id)->pages_bi-1;page_hold=true;
        host_touch=(touchPosition){112,185};touch_action();
        need(!bilingual && page<pages() && !page_hold,"touch CN/BI must reset page state just like X");
    }else if(!strcmp(argv[2],"review_centered")){
        begin(608,0);
        events[0]=(SceneEvent){.op=SC_TEXT,.flags=1,.line=576,.a=(int)find(576)};
        events[1]=(SceneEvent){.op=SC_TEXT,.line=608,.a=(int)find(608)};
        events[2]=(SceneEvent){.op=SC_END};script.count=3;pc=2;
        previous();render();
        unsigned pixels=0;for(unsigned i=0;i<PIXELS;i++)if(host_vram[top_new][i]!=0x8000)pixels++;
        need(pixels>0,"centered history must appear on the upper screen during B review");
    }else if(!strcmp(argv[2],"review_identity")){
        begin(608,0);
        events[0]=(SceneEvent){.op=SC_TEXT,.line=709,.a=(int)find(709)};
        events[1]=(SceneEvent){.op=SC_TEXT,.line=608,.a=(int)find(608)};
        events[2]=(SceneEvent){.op=SC_END};script.count=3;pc=2;local=2;render();
        unsigned saved[]={text_id,pc,local,visible,page,now};
        previous();need(reviewing && paused && review_id==find(709),"B must enter paused independent review");
        review_page=1;render();close_review();render();
        unsigned after[]={text_id,pc,local,visible,page,now};
        need(!reviewing && !paused && !memcmp(saved,after,sizeof(saved)),"B review/exit changed the live script state");
        need(!host_sfx_count && !host_music_offset,"B review restarted audio");
        paused=true;previous();close_review();need(paused,"review must restore an already-paused live scene");
    }else if(!strcmp(argv[2],"review_wait_guard")){
        begin(608,0);pc=2;events[1]=events[0];events[0].a=(int)find(576);
        for(unsigned forced=0;forced<2;forced++){
            waiting=true;hard=forced;blocked=true;unsigned saved=pc;
            previous();need(!reviewing && pc==saved && !paused,"B must not open review/replay during ordinary or hard waits");
        }
        waiting=false;blocked=false;previous();need(!reviewing,"B must not enter review outside text interaction");
    }else if(!strcmp(argv[2],"debug_cache")){
        begin(608,0);render();debug=true;prepare_frame();need(lower_dirty,"opening diagnostics must redraw lower");commit_frame();
        prepare_frame();need(!lower_dirty,"unchanged diagnostics must reuse lower pixels");
        paused=true;prepare_frame();need(lower_dirty,"paused status must invalidate diagnostics even while now is frozen");commit_frame();
        automatic=true;prepare_frame();need(lower_dirty,"auto toggle must invalidate frozen diagnostics");commit_frame();
        debug=false;prepare_frame();need(lower_dirty,"closing diagnostics must restore dialogue pixels");
    }else if(!strcmp(argv[2],"capture_without_transition")){
        begin(608,0);blocked=false;pc=0;
        events[0]=(SceneEvent){.op=SC_GFX,.a=2};
        events[1]=(SceneEvent){.op=SC_WINDOW,.a=1};
        events[2]=(SceneEvent){.op=SC_GFX,.a=3};
        events[3]=(SceneEvent){.op=SC_TEXT,.line=608,.a=(int)find(608)};script.count=4;
        host_vram[top_new][17]=0x9abc;host_vram[top_old][17]=0x8123;host_dma_copy_calls=0;
        pump();need(!host_dma_copy_calls && host_vram[top_old][17]==0x8123,"GFX/WINDOW batch ending at TEXT must not copy96KiB without a transition");
    }else if(!strcmp(argv[2],"capture_transition_once")){
        begin(608,0);blocked=false;pc=0;
        events[0]=(SceneEvent){.op=SC_GFX,.a=2};
        events[1]=(SceneEvent){.op=SC_WINDOW,.a=1};
        events[2]=(SceneEvent){.op=SC_GFX,.a=3};
        events[3]=(SceneEvent){.op=SC_TRANSITION,.a=10};script.count=4;
        for(unsigned i=0;i<PIXELS;i++){host_vram[top_new][i]=(uint16_t)(0x8000|(i*17&32767));host_vram[top_old][i]=0x8000;}
        host_dma_copy_calls=0;pump();
        need(host_dma_copy_calls==1 && batch_captured && transition_active && waiting,"multi-GFX transition batch must capture the old screen exactly once");
        need(!memcmp(host_vram[top_old],host_vram[top_new],PIXELS*2),"transition must preserve the actual previous upper VRAM pixels");
    }else if(!strcmp(argv[2],"seek_clock")){
        begin(240,0);
        events[0]=(SceneEvent){.op=SC_MUSIC,.a=0,.b=2048};
        events[1]=(SceneEvent){.op=SC_TEXT,.flags=0,.a=(int)find(240)};
        events[2]=(SceneEvent){.op=SC_WAIT,.a=60};events[3]=(SceneEvent){.op=SC_END};
        static const SceneCheckpoint points[]={{2,"after ordinary"}};
        script.count=4;script.checkpoint_count=1;script.checkpoints=points;
        seek_checkpoint(0);
        need(now==0 && host_music_offset==0,"checkpoint minimum replay must match ScenePlayer: ordinary say adds no authored time");
    }else if(!strcmp(argv[2],"nw_boundary")){
        begin(629,1);events[1]=(SceneEvent){.op=SC_SFX,.a=0,.b=2048};events[2]=(SceneEvent){.op=SC_END};script.count=3;
        render();
        for(unsigned n=0;n<200&&!host_sfx_count;n++){now++;update_text(1);render();}
        need(host_sfx_count==1,"nw did not finish");
        need(host_sfx_visible==scene_text_info(text_id)->char_count,"following sound happened before final text was actually rendered");
    }else if(!strcmp(argv[2],"mode_and_continuation")){
        begin(576,1);const SceneTextInfo*t=scene_text_info(text_id);
        need(t->mode==3 && t->continuation && t->new_char_start==4,"centered EXTEND ABI");
        need(page==scene_text_page(text_id,t->new_char_start+1,0),"continuation initial page");
        end_interaction();need(!text_active,"centered must disappear after interaction");
        begin(608,0);need(scene_text_info(text_id)->mode==1,"Asya ABI");
        end_interaction();need(text_active,"ordinary dialogue must stay visible after interaction");
    }else need(0,"unknown case");
    puts("PASS runtime state case");scene_text_close();return 0;
}
'''


class SceneRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='zato_runtime_host_')
        folder=Path(cls.temp.name)
        (folder/'nds.h').write_text(NDS_STUB,encoding='utf-8')
        (folder/'filesystem.h').write_text('static inline int nitroFSInit(const char*p){(void)p;return 1;}\n',encoding='utf-8')
        source=HARNESS.replace('RUNTIME_SOURCE',(ROOT/'nds/source/scene_runtime.c').as_posix())
        (folder/'runtime_test.c').write_text(source,encoding='utf-8')
        cls.exe=folder/'runtime_test.exe'
        command=[shutil.which('gcc') or 'gcc','-std=c11','-O0','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable',f'-I{folder}',f'-I{ROOT/"nds/source"}',str(folder/'runtime_test.c'),str(ROOT/'nds/source/scene_text.c'),'-o',str(cls.exe)]
        result=subprocess.run(command,capture_output=True,text=True)
        if result.returncode:
            raise AssertionError(result.stdout+result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_case(self,name):
        result=subprocess.run([str(self.exe),str(ROOT/'nds/nitrofs/scene/text.bin'),name],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_A_renders_each_w_segment_before_dismissing_next_pause(self):self.run_case('w_reveal')
    def test_manual_page_stops_the_author_text_clock_at_page_end(self):self.run_case('page_freeze')
    def test_touch_auto_uses_actual_footer_geometry(self):self.run_case('touch_auto')
    def test_touch_bilingual_resets_page_state_like_X(self):self.run_case('touch_bilingual')
    def test_centered_previous_dialogue_review_is_visible(self):self.run_case('review_centered')
    def test_review_exit_preserves_live_text_pc_and_pause_state(self):self.run_case('review_identity')
    def test_B_does_not_enter_review_during_soft_or_hard_wait(self):self.run_case('review_wait_guard')
    def test_diagnostics_dirty_cache_updates_pause_and_auto(self):self.run_case('debug_cache')
    def test_graphic_batch_without_transition_does_not_backup_VRAM(self):self.run_case('capture_without_transition')
    def test_transition_batch_captures_old_VRAM_once_before_multiple_changes(self):self.run_case('capture_transition_once')
    def test_checkpoint_replay_clock_matches_preview_minimum_time(self):self.run_case('seek_clock')
    def test_nw_sound_waits_for_real_final_text_frame(self):self.run_case('nw_boundary')
    def test_centered_mode_and_extend_initial_page(self):self.run_case('mode_and_continuation')


if __name__=='__main__':unittest.main()
