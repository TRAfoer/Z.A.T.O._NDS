#ifdef ZATO_SCENE_DEMO
#include <nds.h>
#include <filesystem.h>
#include <stdio.h>
#include <string.h>
#include <limits.h>
#include "scene_runtime.h"
#include "scene_script.h"
#include "scene_text.h"
#include "scene_gfx.h"
#include "audio_demo.h"

#define PIXELS (256*192)
#define NONE UINT32_MAX
volatile SceneStatus g_scene_status;
static SceneScript script;
static uint16_t upper[PIXELS] __attribute__((aligned(32)));
static uint16_t lower[PIXELS] __attribute__((aligned(32)));
static int top_old,top_new,bottom;
static volatile uint32_t vblanks,input_down;
static uint32_t now,pc,group,origin[5],phase,wait_end,wait_started;
static uint32_t text_id=NONE,literal_id=NONE,local,pause_index,page,visible;
static uint32_t presented_local,presented_visible,page_ready;
static uint32_t last_gfx_group=NONE,last_gfx_frame=NONE;
static uint32_t transition_start,transition_duration;
static bool blocked,waiting,hard,done,window=true,text_active,source_auto;
static bool revealed,page_hold,presented,paused,bilingual,automatic,debug,skip;
static bool batch_captured,batch_needs_capture,transition_active;
static uint32_t checkpoint=1;
typedef struct {uint32_t group,frame,literal,text,visible,page;} UpperKey;
typedef struct {uint32_t text,visible,page,bilingual,window,debug,debug_tick;} LowerKey;
static UpperKey upper_key;static LowerKey lower_key;
static bool upper_valid,lower_valid,upper_dirty,lower_dirty;
static bool reviewing,review_was_paused;
static uint32_t review_id=NONE,review_pc,review_page;
static uint32_t first_tick(unsigned count);

static void on_vblank(void)
{
    scanKeys();input_down|=keysDown();vblanks++;
}
static unsigned pages(void)
{
    const SceneTextInfo*t=scene_text_info(text_id);
    return t?(bilingual?t->pages_bi:t->pages_cn):1;
}
static void audio_stop_all(void)
{
    audio_scene_stop(true,0);audio_scene_stop(false,0);
}
static void capture_before_change(void)
{
    if(batch_captured||!batch_needs_capture)return;
    dmaCopy(bgGetGfxPtr(top_new),bgGetGfxPtr(top_old),PIXELS*2);
    batch_captured=true;
}
static void end_interaction(void)
{
    const SceneTextInfo*t=scene_text_info(text_id);
    if(t&&t->mode==3)text_active=false;
    blocked=waiting=false;hard=false;
}
static void start_text(const SceneEvent*e)
{
    text_id=(unsigned)e->a;
    const SceneTextInfo*t=scene_text_info(text_id);
    if(!t){g_scene_status.errors++;done=true;return;}
    text_active=blocked=true;source_auto=(e->flags&1)!=0;
    local=pause_index=0;revealed=page_hold=presented=false;page_ready=NONE;
    page=t->continuation?scene_text_page(text_id,t->new_char_start+1,0):0;
    visible=scene_text_visible(text_id,0);
    if(t->mode!=3)window=true;
}
static void apply_graphic(const SceneEvent*e)
{
    capture_before_change();group=e->a;phase=e->b;
    if(e->c)origin[phase]=now;
    if(!scene_gfx_select(group)){g_scene_status.errors++;done=true;}
}
static void pump(void)
{
    batch_captured=false;
    batch_needs_capture=false;
    /* Visual changes and their With form one atomic script batch. Back up VRAM
     * only when that batch really dissolves; ordinary cuts need no old image. */
    for(unsigned at=pc;at<script.count;at++){
        unsigned op=script.events[at].op;
        if(op==SC_TRANSITION){batch_needs_capture=true;break;}
        if(op==SC_TEXT||op==SC_WAIT||op==SC_END)break;
    }
    for(unsigned budget=0;budget<1024&&!blocked&&!waiting&&!done;budget++){
        if(pc>=script.count){done=true;break;}
        const SceneEvent*e=&script.events[pc++];
        g_scene_status.source_line=e->line;g_scene_status.commands++;
        switch(e->op){
        case SC_TEXT:start_text(e);break;
        case SC_GFX:apply_graphic(e);break;
        case SC_WAIT:waiting=true;hard=e->b;wait_started=now;wait_end=now+e->a;break;
        case SC_TRANSITION:
            transition_active=true;transition_start=now;transition_duration=e->a;
            waiting=true;hard=true;wait_started=now;wait_end=now+e->a;break;
        case SC_MUSIC:case SC_SFX:
            if(!audio_scene_play(e->op==SC_MUSIC,e->a,e->b,0,e->c))g_scene_status.errors++;
            break;
        case SC_STOP_MUSIC:case SC_STOP_SFX:audio_scene_stop(e->op==SC_STOP_MUSIC,e->a);break;
        case SC_WINDOW:capture_before_change();window=e->a;break;
        case SC_CLEAR:text_id=NONE;text_active=false;break;
        case SC_LITERAL:capture_before_change();literal_id=e->a;break;
        case SC_HIDE_LITERAL:capture_before_change();literal_id=NONE;break;
        case SC_END:done=true;break;
        default:g_scene_status.errors++;done=true;break;
        }
    }
}
/* Checkpoint replay reconstructs the authored minimum time, never invents a
 * reader's dwell time. No audio is started until the target state is ready. */
static void seek_checkpoint(unsigned target)
{
    if(target>=script.checkpoint_count)return;
    audio_stop_all();audio_scene_pause(false);paused=false;checkpoint=target;
    reviewing=false;
    now=pc=0;group=1;phase=0;memset(origin,0,sizeof(origin));
    text_id=literal_id=NONE;text_active=blocked=waiting=hard=done=false;
    transition_active=false;window=true;page=visible=0;page_ready=NONE;
    last_gfx_group=last_gfx_frame=NONE;
    const SceneEvent*music=NULL;uint32_t music_tick=0;
    uint32_t target_pc=script.checkpoints[target].pc;
    while(pc<target_pc){
        const SceneEvent*e=&script.events[pc++];
        switch(e->op){
        case SC_GFX:group=e->a;phase=e->b;if(e->c)origin[phase]=now;break;
        case SC_TEXT:{const SceneTextInfo*t=scene_text_info(e->a);
            text_id=e->a;text_active=true;visible=t?t->char_count:0;
            page=scene_text_page(text_id,visible,0);
            if(t){if(e->flags&1)now+=t->duration_ticks;if(t->mode==3)text_active=false;else window=true;}
            break;}
        case SC_WAIT:case SC_TRANSITION:now+=e->a;break;
        case SC_MUSIC:music=e;music_tick=now;break;
        case SC_STOP_MUSIC:music=NULL;break;
        case SC_WINDOW:window=e->a;break;
        case SC_CLEAR:text_id=NONE;text_active=false;break;
        case SC_LITERAL:literal_id=e->a;break;
        case SC_HIDE_LITERAL:literal_id=NONE;break;
        default:break;
        }
    }
    if(!scene_gfx_select(group))g_scene_status.errors++;
    if(music){
        uint32_t offset=(uint64_t)(now-music_tick)*22050/60;
        /* Checkpoint jumps land after earlier fades, using the approved stream. */
        if(!audio_scene_play(true,music->a,music->b,offset,0))g_scene_status.errors++;
    }
    pump();
    /* Loading/seeking is outside the real-time frame accounting. */
    g_scene_status.requested_checkpoint=NONE;
}
static unsigned cap_visible(unsigned raw)
{
    if(!revealed){
        const SceneTextPause*p=scene_text_pause(text_id,pause_index);
        if(p&&raw>p->glyphs)raw=p->glyphs;
    }
    if(!source_auto){
        unsigned limit=scene_text_page_limit(text_id,page,bilingual);
        if(raw>limit)raw=limit;
    }
    return raw;
}
static void update_text(uint32_t delta)
{
    const SceneTextInfo*t=scene_text_info(text_id);if(!t)return;
    if(!page_hold&&!revealed)local+=delta;
    visible=cap_visible(revealed?t->char_count:scene_text_visible(text_id,local));
    if(source_auto)page=scene_text_page(text_id,visible,bilingual);
    unsigned limit=scene_text_page_limit(text_id,page,bilingual);
    if(!source_auto&&page+1<pages()&&visible>=limit){
        uint32_t last_tick=first_tick(limit);
        if(local>last_tick)local=last_tick;
        page_hold=true;
        if(page_ready==NONE)page_ready=now;
        if(automatic&&now-page_ready>=90&&presented){
            page++;page_hold=false;page_ready=NONE;presented=false;
        }
        return;
    }
    const SceneTextPause*p=!revealed?scene_text_pause(text_id,pause_index):NULL;
    if(p){
        if(local>=p->end_tick&&presented&&presented_local>=p->end_tick&&presented_visible>=p->glyphs){
            local=p->end_tick;pause_index++;presented=false;
        }
        return;
    }
    if(visible>=t->char_count&&local>=t->duration_ticks){
        if(page_ready==NONE)page_ready=now;
        if((source_auto||automatic)&&presented&&presented_visible>=t->char_count&&
           presented_local>=t->duration_ticks&&(source_auto||now-page_ready>=(skip?4u:90u))){
            end_interaction();pump();
        }
    }
}
static uint32_t first_tick(unsigned count)
{
    const SceneTextInfo*t=scene_text_info(text_id);if(!t)return 0;
    uint32_t lo=0,hi=t->duration_ticks;
    while(lo<hi){uint32_t mid=lo+(hi-lo)/2;
        if(scene_text_visible(text_id,mid)>=count)hi=mid;else lo=mid+1;
    }
    return lo;
}
static void advance(void)
{
    if(done)return;
    if(waiting){if(!hard){end_interaction();pump();}return;}
    if(!blocked)return;
    const SceneTextInfo*t=scene_text_info(text_id);if(!t)return;
    unsigned limit=scene_text_page_limit(text_id,page,bilingual);
    const SceneTextPause*p=!revealed?scene_text_pause(text_id,pause_index):NULL;
    if(p&&p->glyphs<=limit){
        if(local<p->start_tick)local=p->start_tick;
        else{local=p->end_tick;pause_index++;}
        presented=false;return;
    }
    if(visible<limit){local=first_tick(limit);visible=limit;presented=false;return;}
    if(page+1<pages()){
        page++;page_hold=false;page_ready=NONE;presented=false;return;
    }
    if(local<t->duration_ticks){local=t->duration_ticks;revealed=true;presented=false;return;}
    end_interaction();pump();
}
static void previous(void)
{
    /* Return is a read-only previous dialogue page, not a partial script rewind.
     * Replaying effects/audio is only done through a full section restart. */
    if(waiting||!blocked||pc<2)return;
    uint32_t at=reviewing?(review_pc?review_pc-1:0):pc-2;
    while(at&&script.events[at].op!=SC_TEXT)at--;
    if(script.events[at].op!=SC_TEXT)return;
    if(!reviewing)review_was_paused=paused;
    reviewing=true;review_id=script.events[at].a;review_pc=at;review_page=0;
    paused=true;audio_scene_pause(true);
}
static void close_review(void)
{
    reviewing=false;paused=review_was_paused;audio_scene_pause(paused);
}
static void toggle_bilingual(void)
{
    bilingual=!bilingual;page=0;page_hold=false;page_ready=NONE;review_page=0;
}
static void debug_screen(void)
{
    for(unsigned i=0;i<PIXELS;i++)lower[i]=0x8000;
    char info[512];snprintf(info,sizeof(info),
        "EP2D3 / HARDWARE TEST\n%s\nLine %lu  op %lu/%lu\nText %lu  page %lu/%u\n"
        "Gfx %lu frame %lu\nA: reveal / next   B: previous\nLEFT/RIGHT: scene section\nSTART: restart section\n"
        "L: pause  R: auto  X: CN/BI\nSELECT: close this panel\nAuto %s  Paused %s\n"
        "Audio errors %lu  underrun %lu/%lu\nGfx RAM %luK  reads %lu\nMissed VBlank %lu\nNo saves in this test ROM",
        script.checkpoints[checkpoint].name,(unsigned long)g_scene_status.source_line,
        (unsigned long)pc,(unsigned long)script.count,(unsigned long)text_id,(unsigned long)page+1,pages(),
        (unsigned long)group,(unsigned long)scene_gfx_frame(group,now-origin[phase]),
        automatic?"ON":"OFF",paused?"YES":"NO",(unsigned long)g_audio_status.errors,
        (unsigned long)g_audio_status.music_underruns,(unsigned long)g_audio_status.sfx_underruns,
        (unsigned long)scene_gfx_resident_bytes()/1024,(unsigned long)scene_gfx_read_calls(),
        (unsigned long)g_scene_status.missed_vblanks);
    scene_text_draw_ascii(lower,4,3,info,0xffff);
}
static void prepare_frame(void)
{
    if(blocked){
        const SceneTextInfo*t=scene_text_info(text_id);
        visible=cap_visible(revealed&&t?t->char_count:scene_text_visible(text_id,local));
        if(source_auto)page=scene_text_page(text_id,visible,bilingual);
    }
    unsigned frame=scene_gfx_frame(group,now-origin[phase]);
    const SceneTextInfo*t=text_active?scene_text_info(text_id):NULL;
    const SceneTextInfo*rt=reviewing?scene_text_info(review_id):NULL;
    uint32_t upper_text=reviewing?(rt&&rt->mode==3?review_id:NONE):(t&&t->mode==3?text_id:NONE);
    unsigned upper_visible=reviewing&&rt?rt->char_count:visible,upper_page=reviewing?review_page:page;
    UpperKey uk={group,frame,literal_id,upper_text,upper_text!=NONE?upper_visible:0,upper_text!=NONE?upper_page:0};
    uint32_t draw_id=reviewing?review_id:(text_active?text_id:NONE);
    unsigned draw_visible=rt?rt->char_count:visible,draw_page=reviewing?review_page:page;
    bool draw_window=reviewing||window;
    const SceneTextInfo*lt=scene_text_info(draw_id);
    bool lower_has_text=draw_window&&lt&&lt->mode!=3;
    LowerKey lk={lower_has_text?draw_id:NONE,lower_has_text?draw_visible:0,lower_has_text?draw_page:0,bilingual,draw_window,debug,
                 debug?(now/15*4+(unsigned)paused*2+(unsigned)automatic):0};
    upper_dirty=!upper_valid||memcmp(&uk,&upper_key,sizeof(uk));
    lower_dirty=!lower_valid||memcmp(&lk,&lower_key,sizeof(lk));
    if(upper_dirty){
        if(!scene_gfx_draw(group,now-origin[phase],upper))g_scene_status.errors++;
        if(literal_id!=NONE){const SceneTextInfo*l=scene_text_info(literal_id);
            scene_text_draw_upper(literal_id,l?l->char_count:0,0,upper);}
        if(upper_text!=NONE)scene_text_draw_upper(upper_text,upper_visible,upper_page,upper);
        DC_FlushRange(upper,sizeof(upper));upper_key=uk;upper_valid=true;
    }
    last_gfx_group=group;last_gfx_frame=frame;
    if(lower_dirty){
        scene_text_draw_lower(draw_id,draw_visible,draw_page,bilingual,reviewing?1:window,true,lower);
        if(debug)debug_screen();
        DC_FlushRange(lower,sizeof(lower));lower_key=lk;lower_valid=true;
    }
}
static void commit_frame(void)
{
    if(transition_active){
        uint32_t elapsed=now-transition_start;
        if(elapsed>=transition_duration)transition_active=false;
        else{unsigned alpha=transition_duration?elapsed*16/transition_duration:16;
            REG_BLDCNT=BLEND_ALPHA|BLEND_SRC_BG3|BLEND_DST_BG2;
            REG_BLDALPHA=alpha|((16-alpha)<<8);}
    }
    if(!transition_active)REG_BLDCNT=0;
    if(upper_dirty)dmaCopy(upper,bgGetGfxPtr(top_new),sizeof(upper));
    if(lower_dirty)dmaCopy(lower,bgGetGfxPtr(bottom),sizeof(lower));
    if(blocked){presented=true;presented_local=local;presented_visible=visible;}
    g_scene_status.pc=pc;g_scene_status.text_id=text_id;g_scene_status.visible=visible;
    g_scene_status.page=page;g_scene_status.group=group;g_scene_status.group_frame=last_gfx_frame;
    g_scene_status.checkpoint=checkpoint;g_scene_status.paused=paused;g_scene_status.bilingual=bilingual;
    g_scene_status.automatic=automatic;g_scene_status.ticks=now;g_scene_status.wait_hard=hard;
    g_scene_status.done=done;g_scene_status.gfx_bytes=scene_gfx_resident_bytes();
    g_scene_status.gfx_reads=scene_gfx_read_calls();
}
static void touch_action(void)
{
    touchPosition p;touchRead(&p);
    if(p.py<180){advance();return;}
    /* Exact footer hit boxes are shared with the packed native bitmap UI. */
    if(p.px>=24&&p.px<40)previous();
    else if(p.px>=44&&p.px<60)previous();
    else if(p.px>=64&&p.px<80){skip=!skip;automatic=skip;}
    else if(p.px>=84&&p.px<100)automatic=!automatic;
    else if(p.px>=104&&p.px<120)toggle_bilingual();
    else if(p.px>=124&&p.px<232)debug=true;
}
static void fail(const char*title,const char*detail)
{
    /* Text pack may not be available yet; use a separate console only on fatal boot. */
    videoSetModeSub(MODE_0_2D);
    consoleInit(NULL,0,BgType_Text4bpp,BgSize_T_256x256,31,0,false,true);
    printf("ZATO SCENE TEST\n%s\n%s\n",title,detail);g_scene_status.errors++;
    while(1)swiWaitForVBlank();
}
int scene_demo_main(void)
{
    powerOn(POWER_ALL_2D);lcdMainOnTop();
    videoSetMode(MODE_5_2D);videoSetModeSub(MODE_5_2D);
    vramSetBankA(VRAM_A_MAIN_BG_0x06000000);vramSetBankB(VRAM_B_MAIN_BG_0x06020000);
    vramSetBankC(VRAM_C_SUB_BG);
    top_old=bgInit(2,BgType_Bmp16,BgSize_B16_256x256,0,0);
    top_new=bgInit(3,BgType_Bmp16,BgSize_B16_256x256,8,0);
    bottom=bgInitSub(2,BgType_Bmp16,BgSize_B16_256x256,0,0);
    bgSetPriority(top_new,1);bgSetPriority(top_old,2);
    dmaFillHalfWords(0x8000,bgGetGfxPtr(top_old),128*1024);
    dmaFillHalfWords(0x8000,bgGetGfxPtr(top_new),128*1024);
    dmaFillHalfWords(0x8000,bgGetGfxPtr(bottom),128*1024);
    g_scene_status.magic=0x4353545a;g_scene_status.requested_checkpoint=NONE;
    if(!nitroFSInit(NULL))fail("NitroFS mount failed","Use Wood-compatible loader.");
    if(!audio_demo_init())fail("Audio init failed","nitro:/audio/index.bin");
    if(!scene_text_open("nitro:/scene/text.bin"))fail("Text pack",scene_text_error());
    if(!scene_script_open(&script,"nitro:/scene/script.bin"))fail("Script pack",script.error);
    if(!scene_gfx_open("nitro:/scene/gfx.bin"))fail("Graphics pack",scene_gfx_error());
    irqSet(IRQ_VBLANK,on_vblank);irqEnable(IRQ_VBLANK);
    seek_checkpoint(1);prepare_frame();swiWaitForVBlank();commit_frame();g_scene_status.ready=1;
    uint32_t previous_vblank=vblanks;
    while(1){
        audio_demo_service();prepare_frame();swiWaitForVBlank();commit_frame();
        uint32_t frame=vblanks,delta=frame-previous_vblank;previous_vblank=frame;
        if(delta>1)g_scene_status.missed_vblanks+=delta-1;
        g_scene_status.frames++;
        int irq=enterCriticalSection();uint32_t keys=input_down;input_down=0;leaveCriticalSection(irq);
        bool jumped=false;
        if(g_scene_status.requested_checkpoint!=NONE){seek_checkpoint(g_scene_status.requested_checkpoint);jumped=true;}
        else if(keys&KEY_RIGHT){seek_checkpoint((checkpoint+1)%script.checkpoint_count);jumped=true;}
        else if(keys&KEY_LEFT){seek_checkpoint((checkpoint+script.checkpoint_count-1)%script.checkpoint_count);jumped=true;}
        else if(keys&KEY_START){seek_checkpoint(checkpoint);jumped=true;}
        if(jumped){previous_vblank=vblanks;delta=0;}
        if(reviewing){
            if(keys&KEY_B)previous();
            if(keys&(KEY_A|KEY_TOUCH)){
                const SceneTextInfo*t=scene_text_info(review_id);
                unsigned count=t?(bilingual?t->pages_bi:t->pages_cn):1;
                if(review_page+1<count)review_page++;else close_review();
            }
            /* Review consumes the navigation key; live text resumes intact. */
            keys&=~(KEY_A|KEY_B|KEY_TOUCH|KEY_L);
        }
        if(keys&KEY_L){paused=!paused;audio_scene_pause(paused);}
        if(keys&KEY_R)automatic=!automatic;
        if(keys&KEY_X)toggle_bilingual();
        if(keys&KEY_SELECT)debug=!debug;
        if(!paused){
            now+=delta;
            if(keys&KEY_B)previous();
            else if(keys&KEY_A)advance();
            else if(keys&KEY_TOUCH)touch_action();
            else if(waiting){
                if(now>=wait_end&&now>wait_started){end_interaction();pump();}
            }else if(blocked)update_text(delta);
        }
        audio_demo_service();
    }
    return 0;
}
#endif
