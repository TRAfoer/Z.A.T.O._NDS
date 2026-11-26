#ifdef ZATO_STORY_DEMO
#include <nds.h>
#include <filesystem.h>
#include <stdio.h>
#include <string.h>
#include "story_runtime.h"
#include "story_flow.h"
#include "story_text.h"
#include "story_gfx.h"
#include "story_checkpoint.h"
#include "story_history.h"
#include "story_shell.h"
#include "story_save_fat.h"
#include "scene_gfx.h"
#include "audio_demo.h"
#ifdef ZATO_STORY_VIDEO
#include "story_video.h"
#endif
#define PIXELS (256*192)
#define NONE UINT32_MAX
volatile StoryStatus g_story_status;
volatile StoryRenderPerf g_story_render_perf={.magic=0x5052465a,.version=2};
volatile StoryPresentationLog g_story_presentations={.magic=0x5054535a,.capacity=STORY_PRESENTATION_CAPACITY};
static StoryScript script;static StoryFlow flow;
static StoryHistory history;
static StoryHistoryView review_view;
static bool review_upper_pending;
static StoryGfxSnapshot review_live_gfx;
static bool review_special_loaded;
static bool history_seed_pending;
static uint16_t upper[PIXELS] __attribute__((aligned(32)));
static uint16_t lower[PIXELS] __attribute__((aligned(32)));
/* Date screens use the fixed, audited (31,36) 8px text region. Cache the small
 * transparent overlay so history can borrow text slot1 without a third record. */
static uint16_t date_pixels[128*16];
static unsigned cached_date=NONE;
static int top_old,top_new,bottom;
static volatile uint32_t vblanks,input_down;
static uint32_t scroll_held,scroll_repeat;
static uint32_t last_vblank,special_reads;
static bool reviewing,review_was_paused,debug,debug_was_paused;
static unsigned review_id=NONE,review_page,review_pc;
static unsigned review_scroll;
static unsigned scroll_text=NONE,scroll_language,scroll_row,scroll_covered;
static bool scroll_manual,scroll_restore_hold;
static StoryTextViewport viewport;
typedef struct {
    bool loading,special,special_loaded,batch_open,have_music;
    unsigned group,phase;uint32_t origin[5],music_tick;
    StoryEvent music,queue[32];unsigned queue_count;
    unsigned transition;uint32_t transition_start,transition_duration;
} Backend;
static Backend backend;
typedef struct {uint32_t group,frame,literal,literal_page,text,visible,page,language,date;} UpperKey;
typedef struct {uint32_t text,visible,page,language,window,quick,debug,debug_tick,scroll,automatic;} LowerKey;
static UpperKey upper_key;static LowerKey lower_key;
static bool upper_valid,lower_valid,upper_dirty,lower_dirty;
static uint32_t prepared_version;
static bool audio_resume_pending;
static uint32_t shell_ticks;
static uint32_t menu_version;
static bool menu_valid;
static void perf_peak(volatile uint32_t*peak,uint32_t elapsed){if(elapsed>*peak)*peak=elapsed;}
static void perf_prepared(uint32_t started)
{
    uint32_t elapsed=audio_perf_now()-started;
    g_story_render_perf.prepare_calls++;g_story_render_perf.last_prepare=elapsed;
    perf_peak(&g_story_render_perf.max_prepare,elapsed);
    g_story_render_perf.text_bytes=story_text_resident_bytes();
    g_story_render_perf.text_peak=story_text_peak_bytes();
    g_story_render_perf.gfx_bytes=story_gfx_resident_bytes()+scene_gfx_resident_bytes();
}

static bool cache_date(unsigned id)
{
    if(id==NONE){cached_date=NONE;return true;}
    if(!story_text_select(id,1))return false;
    memset(upper,0,sizeof(upper));story_text_draw_date(id,upper);
    for(unsigned y=0;y<16;y++)memcpy(date_pixels+y*128,upper+(y+32)*256,128*2);
    cached_date=id;upper_valid=false;return true;
}

static void input_vblank(void)
{
    scanKeys();uint32_t down=keysDown(),held=keysHeld()&(KEY_UP|KEY_DOWN);
    if(held&&held==scroll_held){scroll_repeat++;if(scroll_repeat>=20&&(scroll_repeat-20)%5==0)down|=held;}
    else scroll_repeat=0;
    scroll_held=held;input_down|=down;vblanks++;
}
static uint32_t io_reads(void){return story_text_read_calls()+story_gfx_read_calls()+special_reads+g_story_shell_status.io_epoch;}
static bool open_special(void)
{
    bool ok=scene_gfx_open("nitro:/scene/gfx.bin");
    /* scene_gfx resets its own counters on open. Keep a monotonic total across
     * repeated effect entries so every load is excluded from the plot clock. */
    special_reads+=scene_gfx_read_calls();return ok;
}
static void exclude_io_time(uint32_t before)
{
    if(before!=io_reads()&&vblanks!=last_vblank){uint32_t n=vblanks-last_vblank;
        g_story_status.io_vblanks+=n;g_story_status.missed_vblanks+=n;last_vblank=vblanks;}
}
static void stop_audio(void){audio_scene_stop(true,0);audio_scene_stop(false,0);}
static bool apply_audio(const StoryEvent*e)
{
    if(e->op==ST_MUSIC||e->op==ST_SFX)return audio_scene_play(e->op==ST_MUSIC,e->a,e->b,0,e->c);
    audio_scene_stop(e->op==ST_STOP_MUSIC,e->a);return true;
}
static bool flush_audio(void)
{
    bool ok=true;uint32_t before=vblanks;
    for(unsigned i=0;i<backend.queue_count;i++)if(!apply_audio(&backend.queue[i]))ok=false;
    if(backend.queue_count&&vblanks!=before){uint32_t n=vblanks-last_vblank;
        g_story_status.io_vblanks+=n;g_story_status.missed_vblanks+=n;last_vblank=vblanks;}
    backend.queue_count=0;return ok;
}
static void reset_backend(void*context)
{
    (void)context;bool loading=backend.loading;
    stop_audio();audio_scene_pause(false);story_gfx_clear();scene_gfx_close();
    memset(&backend,0,sizeof(backend));backend.loading=loading;
    story_gfx_set_replay(loading);reviewing=false;cached_date=NONE;
    review_special_loaded=history_seed_pending=false;
    scroll_text=NONE;scroll_manual=scroll_restore_hold=false;scroll_row=scroll_covered=0;
    story_history_clear(&history);
}
static bool text_select(void*c,unsigned id,unsigned slot){(void)c;return story_text_select(id,slot)!=0;}
static const SceneTextInfo*text_info(void*c,unsigned id){(void)c;return story_text_info(id);}
static const SceneTextPause*text_pause(void*c,unsigned id,unsigned index){(void)c;return story_text_pause(id,index);}
static unsigned text_visible(void*c,unsigned id,uint32_t t){(void)c;return story_text_visible(id,t);}
static unsigned text_page(void*c,unsigned id,unsigned n,int bi){(void)c;return story_text_page(id,n,bi);}
static unsigned text_limit(void*c,unsigned id,unsigned p,int bi){(void)c;return story_text_page_limit(id,p,bi);}
static void start_transition(unsigned kind,uint32_t duration,uint32_t now)
{
    backend.transition=kind;backend.transition_start=now;backend.transition_duration=duration;
}
static bool dispatch(void*context,const StoryEvent*e,uint32_t now,bool quiet)
{
    (void)context;
    if(flow.pc)story_history_note_event(&history,flow.pc-1,now,quiet);
    switch(e->op){
    case ST_SHOW:
        return story_gfx_show(e->a,e->b,(int16_t)(e->c&65535),e->d,(int16_t)((uint32_t)e->c>>16),now);
    case ST_HIDE:return story_gfx_hide(e->a);
    case ST_SCENE:story_gfx_clear();break;
    case ST_SPECIAL:
        if(e->a<0){if(backend.special_loaded)scene_gfx_close();backend.special_loaded=backend.special=false;break;}
        if(!backend.special){
            story_gfx_clear();
            if(backend.batch_open){if(!story_gfx_end_batch())return false;backend.batch_open=false;}
            if(!quiet&&!backend.loading){
                if(!open_special())return false;
                backend.special_loaded=true;
            }
        }
        backend.special=true;backend.group=e->a;backend.phase=e->b;
        if(e->c)backend.origin[e->b]=now;
        break;
    case ST_TEXT:
        if(!quiet&&e->b!=TR_NONE)start_transition(e->b,e->c,now);
        break;
    case ST_TRANSITION:
        if(!quiet){
            start_transition(e->a,e->b,now);
            if(e->a>=TR_EASE){if(!story_gfx_move_transition(e->a,now,e->b))return false;}
        }
        break;
    case ST_LITERAL:
        if(!quiet&&!story_text_select(e->a,1))return false;
        break;
    case ST_DATE:
        if(!quiet&&e->a>=0&&!cache_date(e->a))return false;
        break;
    case ST_MUSIC:case ST_STOP_MUSIC:case ST_SFX:case ST_STOP_SFX:
        if(e->op==ST_MUSIC){backend.have_music=true;backend.music=*e;backend.music_tick=now;}
        else if(e->op==ST_STOP_MUSIC)backend.have_music=false;
        if(!quiet){
            if(backend.queue_count==32)return false;
            backend.queue[backend.queue_count++]=*e;
        }
        break;
    default:break;
    }
    return true;
}
static void batch(void*c,bool begin,uint32_t now,bool quiet)
{
    (void)c;
    if(begin){
        story_gfx_begin_batch(now);backend.batch_open=true;
        if(!quiet&&!backend.loading){
            /* Back up the previous visible bitmap only for a real alpha/fade.
             * Per-tag move transitions retain their own small metadata. */
            for(unsigned at=flow.pc;at<script.count;at++){
                const StoryEvent*e=&script.events[at];
                if(e->op==ST_TRANSITION){
                    if(e->a==TR_DISSOLVE||e->a==TR_FADE)dmaCopy(bgGetGfxPtr(top_new),bgGetGfxPtr(top_old),PIXELS*2);
                    break;
                }
                if(e->op==ST_TEXT||e->op==ST_WAIT||e->op==ST_END)break;
            }
        }
    }else if(backend.batch_open){
        if(!story_gfx_end_batch())flow.failed=flow.done=true;
        backend.batch_open=false;
    }
}
static const StoryFlowOps flow_ops={.event=dispatch,.select_text=text_select,.info=text_info,.pause=text_pause,
    .visible=text_visible,.page=text_page,.limit=text_limit,.batch=batch,.reset=reset_backend};
/* A loaded scene already has exact animation origins in its layer recipe.
 * Seed those origins without inventing completed history before the load. */
static bool seed_history_scene(void)
{
    StoryGfxSnapshot scene;if(!story_gfx_snapshot(&scene))return false;
    for(unsigned i=0;i<scene.count;i++){
        const StoryGfxLayerSnapshot*l=&scene.layers[i];
        for(unsigned pc=flow.pc;pc;){
            const StoryEvent*e=&script.events[--pc];
            if(e->op==ST_SHOW&&(unsigned)e->b==l->tag){
                if((unsigned)e->a==l->image&&(unsigned)e->d==l->program)
                    story_history_note_event(&history,pc,l->start,false);
                break;
            }
            if(!(pc&255u))audio_demo_service();
        }
    }
    for(unsigned phase=0;phase<5;phase++)if(backend.special){
        for(unsigned pc=flow.pc;pc;){
            const StoryEvent*e=&script.events[--pc];
            if(e->op==ST_SPECIAL&&e->a>=0&&(unsigned)e->b==phase&&e->c){
                story_history_note_event(&history,pc,backend.origin[phase],false);break;
            }
            if(!(pc&255u))audio_demo_service();
        }
    }
    if(backend.transition_duration)for(unsigned pc=flow.pc;pc;){
        const StoryEvent*e=&script.events[--pc];
        if((e->op==ST_TRANSITION&&(unsigned)e->a==backend.transition)||
           (e->op==ST_TEXT&&e->b!=TR_NONE&&(unsigned)e->b==backend.transition)){
            story_history_note_event(&history,pc,backend.transition_start,false);break;
        }
        if(!(pc&255u))audio_demo_service();
    }
    return true;
}
static bool finish_loading(void)
{
    story_gfx_set_replay(false);if(!story_gfx_sync())return false;
    if(backend.special&&!backend.special_loaded){
        if(!open_special())return false;
        backend.special_loaded=true;
    }
    if(flow.date_id!=NONE&&!cache_date(flow.date_id))return false;
    if(flow.literal_id!=NONE&&!story_text_select(flow.literal_id,1))return false;
    bool pending_music=false;
    for(unsigned i=0;i<backend.queue_count;i++)if(backend.queue[i].op==ST_MUSIC||backend.queue[i].op==ST_STOP_MUSIC)pending_music=true;
    if(!pending_music&&backend.have_music){
        uint32_t offset=(uint64_t)(flow.now-backend.music_tick)*22050/60;
        if(!audio_scene_play(true,backend.music.a,backend.music.b,offset,0))return false;
    }
    history_seed_pending=!seed_history_scene();
    backend.loading=false;upper_valid=lower_valid=false;return !flow.failed;
}
static bool seek(unsigned chapter)
{
    backend.loading=true;bool ok=story_flow_seek(&flow,chapter)&&finish_loading();
    int irq=enterCriticalSection();input_down=0;leaveCriticalSection(irq);
    if(debug){debug_was_paused=false;flow.paused=true;audio_scene_pause(true);}
    last_vblank=vblanks;g_story_status.requested_chapter=NONE;return ok;
}
static void set_debug(bool value)
{
    if(value==debug)return;
    if(value){debug_was_paused=reviewing?review_was_paused:flow.paused;flow.paused=true;audio_scene_pause(true);}
    else{flow.paused=reviewing||debug_was_paused;audio_scene_pause(reviewing?review_was_paused:flow.paused);}
    debug=value;
}
static void close_review(void)
{
    /* History owns the current image cache. Return by loading the saved live
     * recipe, without keeping either set of image resources pinned. */
    story_gfx_clear();
    if(review_special_loaded){scene_gfx_close();review_special_loaded=false;}
    bool ok;
    if(backend.special){ok=open_special();backend.special_loaded=ok;}
    else ok=story_gfx_restore(&review_live_gfx);
    if(!ok)flow.failed=true;
    reviewing=false;flow.paused=debug||review_was_paused;audio_scene_pause(flow.paused);
    if(flow.literal_id!=NONE&&!story_text_select(flow.literal_id,1))flow.failed=true;
    upper_valid=lower_valid=false;
}
static void previous_dialogue(void)
{
    if(flow.waiting||!flow.blocked||flow.pc<2)return;
    unsigned at=reviewing?(review_pc?review_pc-1:0):flow.pc-2;
    while(at&&script.events[at].op!=ST_TEXT)at--;
    if(script.events[at].op!=ST_TEXT)return;
    unsigned id=script.events[at].a;
    StoryHistoryView view;
    if(!story_history_build(&history,at,&view)){flow.failed=true;return;}
    if(!story_text_select(id,1)){flow.failed=true;return;}
    if(!reviewing){
        if(!story_gfx_snapshot(&review_live_gfx)){flow.failed=true;return;}
        review_was_paused=debug?debug_was_paused:flow.paused;
        review_special_loaded=backend.special_loaded;backend.special_loaded=false;
    }
    reviewing=true;review_id=id;review_pc=at;review_page=0;flow.paused=true;
    review_view=view;review_upper_pending=true;upper_valid=lower_valid=false;
    audio_scene_pause(debug||review_was_paused);
    review_scroll=0;
}
static void clear_input(void)
{
    int irq=enterCriticalSection();input_down=0;leaveCriticalSection(irq);
    last_vblank=vblanks;
}
static void shell_resume(void*unused)
{
    (void)unused;const StorySaveGlobals*g=story_save_globals();
    if(flow.bilingual!=(g->language!=0))story_flow_toggle_language(&flow);
    /* Background auto/quick-save I/O has not replaced the displayed frame.
     * An actual overlay, language change, or restore still needs a new commit. */
    if(menu_valid)flow.presented=false;
    upper_valid=lower_valid=false;
    audio_scene_pause(audio_resume_pending||flow.paused);clear_input();
}
static bool shell_new_game(void*unused)
{
    (void)unused;debug=reviewing=false;audio_resume_pending=false;backend.loading=true;
    if(!story_flow_init(&flow,&script,&flow_ops,NULL)||!finish_loading())return false;
    flow.scrolling=true;flow.scroll_ready=false;scroll_text=NONE;scroll_manual=false;
    flow.bilingual=story_save_globals()->language!=0;flow.automatic=flow.skip=false;
    upper_valid=lower_valid=false;clear_input();return true;
}
static void shell_title(void*unused)
{
    (void)unused;debug=reviewing=false;audio_resume_pending=false;
    stop_audio();scene_gfx_close();story_gfx_clear();
    memset(&backend,0,sizeof(backend));cached_date=NONE;REG_BLDCNT=0;
    audio_scene_pause(false);
    audio_scene_volumes(story_save_globals()->music_q12,story_save_globals()->sfx_q12);
    if(!audio_scene_play(true,0,4096,0,0))flow.failed=true;
    upper_valid=lower_valid=false;clear_input();
}
static bool shell_capture(void*unused,StorySaveMeta*m,uint8_t data[768],uint8_t thumb[3072])
{
    (void)unused;
    if(reviewing||debug||backend.loading||backend.batch_open||backend.special||backend.queue_count)return false;
    if(backend.transition_duration&&flow.now-backend.transition_start<backend.transition_duration)return false;
    StoryBackendSnapshot b={.special=backend.special,.group=backend.group,.phase=backend.phase,
        .have_music=backend.have_music,.music_tick=backend.music_tick,.music=backend.music,
        .transition=backend.transition,.transition_start=backend.transition_start,.transition_duration=backend.transition_duration};
    memcpy(b.origin,backend.origin,sizeof(b.origin));
    if(!story_checkpoint_capture_view(&flow,&b,scroll_row,scroll_manual,scroll_covered,data))return false;
    memset(m,0,sizeof(*m));m->chapter=flow.chapter;m->text_id=flow.text_id;m->page=flow.page;m->bilingual=flow.bilingual;
    snprintf(m->caption,sizeof(m->caption),"Chapter %lu / line %lu",(unsigned long)flow.chapter+1,(unsigned long)flow.line);
    /* BOX the complete upper screen, keeping oversized actors' heads/hands. */
    for(unsigned y=0;y<48;y++)for(unsigned x=0;x<64;x++){
        unsigned r=0,g=0,bl=0;
        for(unsigned dy=0;dy<4;dy++)for(unsigned dx=0;dx<4;dx++){
            uint16_t v=upper[(y*4+dy)*256+x*4+dx];r+=v&31;g+=(v>>5)&31;bl+=(v>>10)&31;
        }
        r=(r+8)/16;g=(g+8)/16;bl=(bl+8)/16;
        thumb[y*64+x]=(uint8_t)((r>>2)<<5|(g>>2)<<2|(bl>>3));
    }
    return true;
}
static void apply_backend(const StoryBackendSnapshot*b)
{
    memset(&backend,0,sizeof(backend));backend.special=b->special;backend.group=b->group;backend.phase=b->phase;
    memcpy(backend.origin,b->origin,sizeof(backend.origin));backend.have_music=b->have_music;
    backend.music_tick=b->music_tick;backend.music=b->music;
    backend.transition=b->transition;backend.transition_start=b->transition_start;backend.transition_duration=b->transition_duration;
}
static bool shell_restore(void*unused,const uint8_t data[768])
{
    (void)unused;StoryCheckpointDecoded target;
    if(!story_checkpoint_decode(data,&target))return false;
    /* This is a temporary live transaction, never a persistent C-struct dump.
     * Keep even an initial WAIT or moving scene intact until ALL target I/O,
     * including date/literal overlays, has completed. The graphics transaction
     * pins the old banks, so rollback does not have to reread old pictures. */
    StoryFlow old_flow=flow;Backend old_backend=backend;AudioSceneSnapshot old_audio;
    uint16_t old_date[128*16];memcpy(old_date,date_pixels,sizeof(old_date));
    unsigned old_cached_date=cached_date;bool old_pending=audio_resume_pending;
    if(!audio_scene_snapshot(&old_audio)||!story_gfx_restore_begin())return false;
    audio_scene_pause(true);
    bool ok=story_checkpoint_prepare(&flow,&target,&target)&&story_checkpoint_restore(&flow,&target);
    if(ok&&target.scroll>story_text_scroll_max(flow.text_id,flow.visible,flow.bilingual))ok=false;
    if(ok){
        apply_backend(&target.backend);cached_date=NONE;
        if(flow.date_id!=NONE)ok=cache_date(flow.date_id);
        if(ok&&flow.literal_id!=NONE)ok=story_text_select(flow.literal_id,1)!=0;
    }
    if(!ok){
        story_gfx_restore_rollback();flow=old_flow;backend=old_backend;
        memcpy(date_pixels,old_date,sizeof(old_date));cached_date=old_cached_date;
        bool recovered=audio_scene_restore(&old_audio);
        if(flow.text_id!=NONE)recovered=(story_text_select(flow.text_id,0)!=0)&&recovered;
        if(flow.literal_id!=NONE)recovered=(story_text_select(flow.literal_id,1)!=0)&&recovered;
        audio_resume_pending=old_pending;audio_scene_pause(old_audio.paused!=0);upper_valid=lower_valid=false;
        if(!recovered)flow.failed=true;
        return false;
    }
    story_gfx_restore_commit();scene_gfx_close();reviewing=debug=false;
    story_history_clear(&history);
    history_seed_pending=!seed_history_scene();
    scroll_text=flow.text_id;scroll_language=flow.bilingual;
    scroll_row=target.scroll;scroll_manual=target.scroll_manual!=0;scroll_covered=target.scroll_covered;
    if(!flow.scrolling)scroll_row=story_text_scroll_for_visible(flow.text_id,flow.visible,flow.bilingual);
    flow.scrolling=true;flow.scroll_ready=false;
    scroll_restore_hold=true;
    audio_resume_pending=true;audio_scene_pause(true);
    story_shell_language(flow.bilingual);upper_valid=lower_valid=false;clear_input();return true;
}
static bool shell_gallery(void*unused,unsigned frame,uint16_t*screen)
{
    (void)unused;story_gfx_clear();
    return story_gfx_show(frame,0,0,0,-1,0)&&story_gfx_draw(0,screen);
}
static void shell_history(void*unused){(void)unused;previous_dialogue();}
static const uint16_t*shell_screen(void*unused){(void)unused;return upper;}
static const uint16_t*shell_lower_screen(void*unused){(void)unused;return lower;}
static void shell_quick_action(void*unused,StoryMenuActionKind action)
{
    (void)unused;
    if(action==STORY_MENU_OPEN_DEBUG)set_debug(true);
    else if(action==STORY_MENU_TOGGLE_SKIP){flow.skip=!flow.skip;flow.automatic=flow.skip;}
    else if(action==STORY_MENU_TOGGLE_AUTO)flow.automatic=!flow.automatic;
    else if(action==STORY_MENU_TOGGLE_LANGUAGE){story_flow_toggle_language(&flow);story_shell_language(flow.bilingual);review_page=0;review_upper_pending=reviewing;}
    lower_valid=false;
}
static const StoryShellOps shell_ops={.new_game=shell_new_game,.capture=shell_capture,.restore=shell_restore,
    .gallery=shell_gallery,.history=shell_history,.title=shell_title,.resume=shell_resume,.screen=shell_screen,
    .lower_screen=shell_lower_screen,.quick_action=shell_quick_action};
static void diagnostic(void)
{
    for(unsigned i=0;i<PIXELS;i++)lower[i]=0x8000;
    char text[640];snprintf(text,sizeof(text),
        "ZATO / FULL STORY CHAIN\nChapter %lu/10 %s\nLine %lu  op %lu/%lu\nText %lu  page %lu/%u\n"
        "A: reveal/next B: history\nUP/DOWN: scroll text\nLEFT/RIGHT: chapter if enabled\nSTART: menu SELECT: quick bar\nL: pause R: auto X: CN/BI\nAny key: close panel\n"
        "Auto %s  Pause %s\nRAM text %luK gfx %luK\nAudio errors %lu underrun %lu/%lu\nMissed %lu (I/O %lu)\nSAVE %s ops %lu/%lu errors %lu",
        (unsigned long)flow.chapter+1,script.chapters[flow.chapter].name,(unsigned long)flow.line,
        (unsigned long)flow.pc,(unsigned long)script.count,(unsigned long)flow.text_id,
        (unsigned long)flow.page+1,story_flow_pages(&flow),flow.automatic?"ON":"OFF",flow.paused?"YES":"NO",
        (unsigned long)story_text_resident_bytes()/1024,(unsigned long)(story_gfx_resident_bytes()+scene_gfx_resident_bytes())/1024,
        (unsigned long)g_audio_status.errors,(unsigned long)g_audio_status.music_underruns,(unsigned long)g_audio_status.sfx_underruns,
        (unsigned long)g_story_status.missed_vblanks,(unsigned long)g_story_status.io_vblanks,
        story_save_available()?"READY":"OFF",(unsigned long)g_story_shell_status.save_ops,
        (unsigned long)g_story_shell_status.load_ops,(unsigned long)g_story_shell_status.save_errors);
    story_text_draw_ascii(lower,4,3,text,0xffff);
}
static void scroll_sync(unsigned id,unsigned visible,const SceneTextInfo*t)
{
    if(scroll_text!=id||scroll_language!=(unsigned)flow.bilingual){
        scroll_text=id;scroll_language=flow.bilingual;scroll_manual=false;
        scroll_covered=t&&t->continuation?t->new_char_start:0;
        scroll_row=0;flow.scroll_ready=false;
    }
    unsigned max=story_text_scroll_max(id,visible,flow.bilingual);
    if(scroll_row>max)scroll_row=max;
    if(!scroll_manual&&!scroll_restore_hold)scroll_row=story_text_scroll_follow(id,visible,flow.bilingual,scroll_row,scroll_covered);
}
static void scroll_input(uint32_t keys)
{
    uint32_t started=audio_perf_now();
    unsigned id=reviewing?review_id:flow.text_id;
    const SceneTextInfo*t=story_text_info(id);
    if(!t||t->mode==3||(!reviewing&&!flow.window))return;
    unsigned visible=reviewing?t->char_count:flow.visible;
    unsigned max=story_text_scroll_max(id,visible,flow.bilingual);
    unsigned*p=reviewing?&review_scroll:&scroll_row;
    unsigned previous=*p;
    if((keys&KEY_UP)&&*p)(*p)--;
    if((keys&KEY_DOWN)&&*p<max)(*p)++;
    if(!reviewing){
        unsigned tail=story_text_scroll_for_visible(id,visible,flow.bilingual);
        scroll_manual=*p<tail;
    }
    if(*p!=previous)lower_valid=false;
    perf_peak(&g_story_render_perf.max_scroll,audio_perf_now()-started);
}
/* Review keeps only its selected scene resident. The live layer recipe was
 * saved on entry; A reloads it. Audio service continues during all reads. */
static bool render_review_upper(void)
{
    bool ok;unsigned frame;
    story_gfx_clear();
    if(review_special_loaded){scene_gfx_close();review_special_loaded=false;}
    if(review_view.backend.special){
        ok=open_special();review_special_loaded=ok;
        uint32_t tick=review_view.now-review_view.backend.origin[review_view.backend.phase];
        frame=ok?scene_gfx_frame(review_view.backend.group,tick):0;
        if(ok)ok=scene_gfx_draw(review_view.backend.group,tick,upper);
    }else{
        ok=story_gfx_restore(&review_view.gfx);
        frame=ok?story_gfx_version(review_view.now):0;
        if(ok)ok=story_gfx_draw(review_view.now,upper);
    }
    if(!ok)return false;
    unsigned literal_page=0;
    if(review_view.literal_id!=NONE){
        if(!story_text_select(review_view.literal_id,1))return false;
        const SceneTextInfo*t=story_text_info(review_view.literal_id);
        unsigned pages=flow.bilingual?t->pages_bi:t->pages_cn;
        if(pages>1&&review_view.literal_duration){
            literal_page=(uint64_t)(review_view.now-review_view.literal_start)*pages/review_view.literal_duration;
            if(literal_page>=pages)literal_page=pages-1;
        }
        story_text_draw_upper_bilingual(review_view.literal_id,t->char_count,literal_page,flow.bilingual,upper);
    }
    if(review_view.date_id!=NONE){
        if(!story_text_select(review_view.date_id,1))return false;
        story_text_draw_date(review_view.date_id,upper);
    }
    if(!story_text_select(review_id,1))return false;
    const SceneTextInfo*t=story_text_info(review_id);
    if(t->mode==3)story_text_draw_upper(review_id,t->char_count,review_page,upper);
    upper_key=(UpperKey){review_view.backend.special?review_view.backend.group:NONE,frame,
        review_view.literal_id,literal_page,t->mode==3?review_id:NONE,t->mode==3?t->char_count:0,
        t->mode==3?review_page:0,flow.bilingual,review_view.date_id};
    DC_FlushRange(upper,sizeof(upper));upper_valid=true;review_upper_pending=false;
    return true;
}
static void draw_quick_bar(void)
{
    if(!flow.quick_menu)return;
    story_menu_draw_bar(NULL,false,lower);
    if(flow.automatic)story_text_draw_ascii(lower,112,179,"//AUTO//",0xc631);
}
static void prepare_review(void)
{
    upper_dirty=review_upper_pending||!upper_valid;
    if(upper_dirty&&!render_review_upper()){flow.failed=true;return;}
    const SceneTextInfo*t=story_text_info(review_id);
    bool body=t&&t->mode!=3;
    LowerKey lk={body?review_id:NONE,body?t->char_count:0,review_page,flow.bilingual,1,
        flow.quick_menu,debug,debug?(flow.now/15*4+2+(unsigned)flow.automatic):0,body?review_scroll:0,flow.automatic};
    lower_dirty=!lower_valid||memcmp(&lk,&lower_key,sizeof(lk));
    if(lower_dirty){
        if(body)story_text_draw_lower_scroll(review_id,t->char_count,review_scroll,flow.bilingual,1,0,lower,&viewport);
        else story_text_draw_lower(review_id,t?t->char_count:0,review_page,flow.bilingual,1,0,lower);
        draw_quick_bar();
        if(debug)diagnostic();
        lower_key=lk;lower_valid=true;
    }
    prepared_version=upper_key.frame;
}
static void prepare(void)
{
    uint32_t started=audio_perf_now();
    if(story_shell_active()){
        uint32_t version=story_shell_version(shell_ticks);
        upper_dirty=lower_dirty=!menu_valid||version!=menu_version;
        if(upper_dirty){
            if(!story_shell_draw(shell_ticks,upper,lower))flow.failed=true;
            DC_FlushRange(upper,sizeof(upper));
        }
        menu_valid=true;menu_version=version;upper_valid=lower_valid=false;
        prepared_version=version;perf_prepared(started);return;
    }
    menu_valid=false;
    if(reviewing){prepare_review();perf_prepared(started);return;}
    story_flow_refresh(&flow);
    unsigned frame=backend.special?scene_gfx_frame(backend.group,flow.now-backend.origin[backend.phase]):story_gfx_version(flow.now);
    const SceneTextInfo*live=flow.text_active?story_text_info(flow.text_id):NULL;
    const SceneTextInfo*review=reviewing?story_text_info(review_id):NULL;
    unsigned top_text=reviewing?(review&&review->mode==3?review_id:NONE):(live&&live->mode==3?flow.text_id:NONE);
    unsigned top_visible=reviewing&&review?review->char_count:flow.visible,top_page=reviewing?review_page:flow.page;
    const SceneTextInfo*literal=story_text_info(flow.literal_id);
    bool poem=!reviewing&&literal&&story_text_is_poem(flow.literal_id);
    unsigned literal_pages=literal?(flow.bilingual?literal->pages_bi:literal->pages_cn):1;
    unsigned literal_page=literal&&literal_pages>1?(uint64_t)(flow.now-flow.literal_start)*literal_pages/flow.literal_duration:0;
    if(literal&&literal_page>=literal_pages)literal_page=literal_pages-1;
    UpperKey uk={backend.special?backend.group:NONE,frame,flow.literal_id,literal_page,top_text,
        top_text!=NONE?top_visible:0,top_text!=NONE?top_page:0,flow.bilingual,flow.date_id};
    unsigned id=reviewing?review_id:(flow.text_active?flow.text_id:NONE);
    unsigned count=review?review->char_count:flow.visible,page=reviewing?review_page:flow.page;
    const SceneTextInfo*t=story_text_info(id);bool window=reviewing||flow.window;
    bool body=window&&t&&t->mode!=3;
    if(body&&!reviewing)scroll_sync(id,count,t);
    unsigned row=reviewing?review_scroll:scroll_row;
    LowerKey lk={poem?flow.literal_id:body?id:NONE,poem?literal->char_count:body?count:0,poem?literal_page:body?page:0,flow.bilingual,window,flow.quick_menu,debug,
        debug?(flow.now/15*4+(unsigned)flow.paused*2+(unsigned)flow.automatic):0,body?row:0,flow.automatic};
    upper_dirty=!upper_valid||memcmp(&uk,&upper_key,sizeof(uk));
    lower_dirty=!lower_valid||memcmp(&lk,&lower_key,sizeof(lk));
    if(upper_dirty){
        uint32_t render_started=audio_perf_now();
        bool ok=backend.special?scene_gfx_draw(backend.group,flow.now-backend.origin[backend.phase],upper):story_gfx_draw(flow.now,upper);
        if(!ok)flow.failed=true;
        if(literal)story_text_draw_upper_bilingual(flow.literal_id,literal->char_count,literal_page,flow.bilingual,upper);
        if(top_text!=NONE)story_text_draw_upper(top_text,top_visible,top_page,upper);
        if(flow.date_id!=NONE&&cached_date==flow.date_id){
            for(unsigned y=0;y<16;y++)for(unsigned x=0;x<128;x++){
                uint16_t v=date_pixels[y*128+x];if(v&0x8000)upper[(y+32)*256+x]=v;
            }
        }
        DC_FlushRange(upper,sizeof(upper));upper_key=uk;upper_valid=true;
        perf_peak(&g_story_render_perf.max_upper,audio_perf_now()-render_started);
    }
    if(lower_dirty){
        uint32_t render_started=audio_perf_now();
        if(poem){memset(&viewport,0,sizeof(viewport));story_text_draw_lower_poem(flow.literal_id,literal_page,1,0,lower);}
        else if(body)story_text_draw_lower_scroll(id,count,row,flow.bilingual,window,0,lower,&viewport);
        else{memset(&viewport,0,sizeof(viewport));story_text_draw_lower(id,count,page,flow.bilingual,window,0,lower);}
        draw_quick_bar();
        if(debug)diagnostic();
        lower_key=lk;lower_valid=true;
        perf_peak(&g_story_render_perf.max_lower,audio_perf_now()-render_started);
    }
    prepared_version=frame;
    perf_prepared(started);
}
/* Main-RAM DMA can stall ARM9 IRQ handling on hardware even with IME enabled.
 * The single lower bitmap has no page flip: copy top-to-bottom after VBlank,
 * using the CPU's current cache contents, and keep audio service points between
 * 4-row spans. Do not flush this buffer for DMA; it is never read by DMA here. */
static void upload_lower(void)
{
    uint16_t*dest=bgGetGfxPtr(bottom);
    for(unsigned row=0;row<192;row+=4){
        memcpy(dest+row*256,lower+row*256,4*256*sizeof(*lower));
        if(row+4<192)audio_demo_service();
    }
}
static void commit(void)
{
    uint32_t started=audio_perf_now();
    g_story_status.sequence++;
    __asm__ volatile("" ::: "memory");
    bgSetPriority(top_new,1);bgSetPriority(top_old,2);bgSetScroll(top_new,0,0);REG_BLDCNT=0;
    uint32_t elapsed=reviewing?review_view.now-review_view.backend.transition_start:flow.now-backend.transition_start;
    uint32_t duration=reviewing?review_view.backend.transition_duration:backend.transition_duration;
    unsigned transition=reviewing?review_view.backend.transition:backend.transition;
    if(!story_shell_active()&&duration&&elapsed<duration){
        if(transition==TR_DISSOLVE){unsigned a=elapsed*16/duration;REG_BLDCNT=BLEND_ALPHA|BLEND_SRC_BG3|BLEND_DST_BG2;REG_BLDALPHA=a|((16-a)<<8);}
        else if(transition==TR_FADE){
            if(elapsed*2<duration){bgSetPriority(top_old,1);bgSetPriority(top_new,2);REG_BLDCNT=BLEND_FADE_BLACK|BLEND_SRC_BG2;REG_BLDY=elapsed*32/duration;}
            else{REG_BLDCNT=BLEND_FADE_BLACK|BLEND_SRC_BG3;REG_BLDY=16-(elapsed*2-duration)*16/duration;}
        }else if(transition==TR_HPUNCH||transition==TR_VPUNCH){
            int phase=elapsed%12;int value=phase<=6?3-phase:phase-9;
            if(transition==TR_HPUNCH)bgSetScroll(top_new,-value,0);
            else bgSetScroll(top_new,0,-value*2/3);
        }
    }
    bgUpdate();
    if(upper_dirty)dmaCopy(upper,bgGetGfxPtr(top_new),sizeof(upper));
    if(lower_dirty)upload_lower();
    const SceneTextInfo*t=story_text_info(flow.text_id);
    bool acknowledged=!story_shell_active()&&!reviewing&&(!debug||(t&&t->mode==3));
    bool scrolling=acknowledged&&flow.window&&t&&t->mode!=3&&flow.scrolling;
    if(scrolling){
        if(viewport.cn_first<=scroll_covered&&viewport.cn_end>scroll_covered)scroll_covered=viewport.cn_end;
        flow.scroll_ready=scroll_covered>=flow.visible;
        scroll_restore_hold=false;
    }
    if(acknowledged)story_flow_mark_presented(&flow);
    if(acknowledged&&flow.blocked&&history_seed_pending)history_seed_pending=!seed_history_scene();
    if(acknowledged&&flow.blocked&&t&&flow.visible>=t->char_count&&
       flow.page+1>=story_flow_pages(&flow)&&(!scrolling||flow.scroll_ready)&&flow.pc)
        story_history_note_complete(&history,flow.pc-1,flow.now);
    uint32_t serial=g_story_presentations.head+1;
    volatile StoryPresentation*entry=&g_story_presentations.entries[(serial-1)%STORY_PRESENTATION_CAPACITY];
    entry->commands=flow.commands;entry->text_id=flow.text_id;entry->page=flow.page;
    entry->visible=flow.visible;entry->local=flow.local;entry->ticks=flow.now;
    entry->flags=(acknowledged&&flow.blocked?1u:0u)|(flow.window?2u:0u)|
        (t&&t->mode==3?4u:0u)|(flow.bilingual?8u:0u)|(scrolling?16u:0u)|(audio_resume_pending?32u:0u);
    entry->cn_first=scrolling?viewport.cn_first:0;entry->cn_end=scrolling?viewport.cn_end:0;
    entry->en_first=scrolling?viewport.en_first:0;entry->en_end=scrolling?viewport.en_end:0;
    entry->scroll=scrolling?viewport.scroll:0;entry->scroll_manual=scrolling?scroll_manual:0;
    entry->scroll_covered=scrolling?scroll_covered:0;
    __asm__ volatile("" ::: "memory");
    entry->serial=serial;
    __asm__ volatile("" ::: "memory");
    g_story_presentations.head=serial;
    g_story_status.frames++;
    g_story_status.pc=flow.pc;g_story_status.chapter=flow.chapter;g_story_status.source_line=flow.line;
    g_story_status.text_id=flow.text_id;g_story_status.visible=flow.visible;g_story_status.page=flow.page;
    g_story_status.layers=story_gfx_layer_count();g_story_status.special_group=backend.special?backend.group:NONE;
    g_story_status.gfx_version=prepared_version;
    g_story_status.paused=flow.paused;g_story_status.bilingual=flow.bilingual;g_story_status.automatic=flow.automatic;
    g_story_status.ticks=flow.now;g_story_status.wait_hard=flow.hard;g_story_status.done=flow.done;g_story_status.commands=flow.commands;
    g_story_status.text_bytes=story_text_resident_bytes();g_story_status.text_peak=story_text_peak_bytes();g_story_status.text_reads=story_text_read_calls();
    g_story_status.gfx_bytes=story_gfx_resident_bytes()+scene_gfx_resident_bytes();g_story_status.gfx_reads=story_gfx_read_calls()+special_reads;
    g_story_status.unlock_low=flow.unlocks[0];g_story_status.unlock_high=flow.unlocks[1];
    const SceneTextPause*p=flow.revealed?NULL:story_text_pause(flow.text_id,flow.pause_index);
    g_story_status.local=flow.local;g_story_status.pause_index=flow.pause_index;
    g_story_status.char_count=t?t->char_count:0;
    g_story_status.page_limit=t?story_text_page_limit(flow.text_id,flow.page,flow.bilingual):0;
    g_story_status.duration_ticks=t?t->duration_ticks:0;g_story_status.text_mode=t?t->mode:NONE;
    g_story_status.source_auto=flow.source_auto;g_story_status.blocked=flow.blocked;g_story_status.waiting=flow.waiting;
    g_story_status.forever=flow.forever;g_story_status.window=flow.window;g_story_status.quick_menu=flow.quick_menu;
    g_story_status.pause_count=t?t->pause_count:0;g_story_status.pause_start=p?p->start_tick:0;
    g_story_status.pause_end=p?p->end_tick:0;g_story_status.pause_glyphs=p?p->glyphs:0;
    g_story_status.wait_started=flow.wait_started;g_story_status.wait_end=flow.wait_end;
    g_story_status.literal_id=flow.literal_id;g_story_status.literal_start=flow.literal_start;
    g_story_status.literal_duration=flow.literal_duration;g_story_status.literal_page=upper_key.literal_page;
    g_story_status.date_id=flow.date_id;g_story_status.transition_kind=backend.transition;
    g_story_status.transition_start=backend.transition_start;g_story_status.transition_duration=backend.transition_duration;
    g_story_status.scroll=scroll_row;g_story_status.scroll_max=viewport.max_scroll;
    g_story_status.scroll_manual=scroll_manual;g_story_status.scroll_covered=scroll_covered;
    __asm__ volatile("" ::: "memory");
    g_story_status.sequence++;
    if(audio_resume_pending&&!story_shell_active()){
        audio_resume_pending=false;audio_scene_pause(flow.paused);
    }
    perf_peak(&g_story_render_perf.max_commit,audio_perf_now()-started);
}
static void open_user_menu(StoryMenuMode mode)
{
    story_shell_open(mode);audio_ui_play(AUDIO_UI_CONFIRM);
}
static void touch_action(void)
{
    touchPosition p;touchRead(&p);
    if(flow.quick_menu&&p.py>=STORY_MENU_QUICK_COLLAPSED_Y&&p.py<=191&&p.px>=4&&p.px<=251){open_user_menu(STORY_MENU_QUICK);return;}
    if(scroll_manual){scroll_manual=false;lower_valid=false;return;}
    story_flow_advance(&flow);
}
static void fatal(const char*what)
{
    stop_audio();g_story_status.errors++;
    videoSetModeSub(MODE_0_2D);consoleInit(NULL,0,BgType_Text4bpp,BgSize_T_256x256,31,0,false,true);
    printf("ZATO STORY CHAIN\n%s\nText: %.60s\nGfx: %.60s\nSpecial: %.60s\n",what,story_text_error(),story_gfx_error(),scene_gfx_error());
    while(1)swiWaitForVBlank();
}
static void setup_displays(void)
{
    videoSetMode(MODE_5_2D);videoSetModeSub(MODE_5_2D);
    top_old=bgInit(2,BgType_Bmp16,BgSize_B16_256x256,0,0);
    top_new=bgInit(3,BgType_Bmp16,BgSize_B16_256x256,8,0);
    bottom=bgInitSub(2,BgType_Bmp16,BgSize_B16_256x256,0,0);
    bgSetPriority(top_new,1);bgSetPriority(top_old,2);
    dmaFillHalfWords(0x8000,bgGetGfxPtr(top_old),128*1024);
    dmaFillHalfWords(0x8000,bgGetGfxPtr(top_new),128*1024);
    dmaFillHalfWords(0x8000,bgGetGfxPtr(bottom),128*1024);
    REG_BLDCNT=0;upper_valid=lower_valid=false;
}
#ifdef ZATO_STORY_VIDEO
static void play_opening(void)
{
    if(!story_video_open("movie/op.zpv","movie/op.wav",1283072,2048))fatal("Opening video");
    bool paused=false;
    while(story_video_active()&&!story_video_finished()){
        story_video_service();swiWaitForVBlank();story_video_commit();
        int irq=enterCriticalSection();uint32_t keys=input_down;input_down=0;leaveCriticalSection(irq);
        if(keys&(KEY_A|KEY_START|KEY_TOUCH))break;
        if(keys&KEY_L){paused=!paused;story_video_pause(paused);}
        if(g_story_video_status.errors)fatal("Opening playback");
    }
    story_video_close();audio_scene_pause(false);
    setup_displays();last_vblank=vblanks;
    int irq=enterCriticalSection();input_down=0;leaveCriticalSection(irq);
}
#endif
int story_demo_main(int argc,char**argv)
{
    powerOn(POWER_ALL_2D);lcdMainOnTop();videoSetMode(MODE_5_2D);videoSetModeSub(MODE_5_2D);
    vramSetBankA(VRAM_A_MAIN_BG_0x06000000);vramSetBankB(VRAM_B_MAIN_BG_0x06020000);vramSetBankC(VRAM_C_SUB_BG);
    setup_displays();
    g_story_status.magic=0x5254535a;g_story_status.requested_chapter=NONE;
    if(!nitroFSInit(NULL))fatal("NitroFS mount failed; use Wood");
    if(!audio_demo_init())fatal("Audio catalog");
    if(!story_text_open("nitro:/story/text.bin"))fatal("Text package");
    if(!story_script_open(&script,"nitro:/story/script.bin"))fatal(script.error);
    if(!story_history_open(&history,&script))fatal("History timing allocation");
    if(!story_gfx_open("nitro:/assets.zat","nitro:/story/gfx.bin"))fatal("Graphics catalog");
    irqSet(IRQ_VBLANK,input_vblank);irqEnable(IRQ_VBLANK);last_vblank=vblanks;
#ifdef ZATO_STORY_VIDEO
    play_opening();
#endif
    backend.loading=true;
    if(!story_flow_init(&flow,&script,&flow_ops,NULL)||!finish_loading())fatal("Initial story state");
    if(!story_menu_open("nitro:/story/menu.bin"))fatal("Menu package");
    bool writable=story_save_fat_open(argc,argv,story_menu_identity());(void)writable;
    story_shell_init(&shell_ops,NULL);story_shell_title();
    prepare();if(!flush_audio())fatal("Initial audio cue");swiWaitForVBlank();commit();g_story_status.ready=1;last_vblank=vblanks;
    while(1){
        audio_demo_service();uint32_t before=io_reads();prepare();exclude_io_time(before);
        if(!flush_audio())fatal("Audio cue");
        swiWaitForVBlank();commit();
        uint32_t delta=vblanks-last_vblank;last_vblank=vblanks;
        if(delta>1)g_story_status.missed_vblanks+=delta-1;
        int irq=enterCriticalSection();uint32_t keys=input_down;input_down=0;leaveCriticalSection(irq);
        before=io_reads();bool jumped=false;
        shell_ticks+=delta;
        if(story_shell_active()){
            touchPosition point={0};if(keys&KEY_TOUCH)touchRead(&point);
            story_shell_input((StoryMenuInput){.down=keys,.touch=(keys&KEY_TOUCH)!=0,.x=point.px,.y=point.py});
            exclude_io_time(before);last_vblank=vblanks;
            if(flow.failed)fatal("Menu action");
            continue;
        }
        /* The dismissal key belongs only to diagnostics. */
        if(debug&&keys){set_debug(false);audio_ui_play(AUDIO_UI_BACK);clear_input();continue;}
        const SceneTextInfo*shown=story_text_info(flow.text_id);
        bool complete=flow.presented&&flow.blocked&&shown&&flow.visible>=shown->char_count&&
            flow.page+1>=story_flow_pages(&flow)&&(!flow.scrolling||shown->mode==3||flow.scroll_ready);
        story_shell_observe(flow.chapter,flow.text_id,complete,flow.unlocks[0],flow.unlocks[1],flow.paused?0:delta);
        exclude_io_time(before);before=io_reads();
        if(flow.done){story_shell_title();continue;}
        if(g_story_status.requested_chapter!=NONE){if(!seek(g_story_status.requested_chapter))fatal("Chapter seek");jumped=true;}
        else if(story_save_globals()->chapter_skip&&(keys&KEY_RIGHT)){if(!seek((flow.chapter+1)%script.chapter_count))fatal("Chapter seek");jumped=true;}
        else if(story_save_globals()->chapter_skip&&(keys&KEY_LEFT)){if(!seek((flow.chapter+script.chapter_count-1)%script.chapter_count))fatal("Chapter seek");jumped=true;}
        else if(keys&KEY_START){if(reviewing)close_review();if(debug)set_debug(false);open_user_menu(STORY_MENU_GAME);jumped=true;}
        else if(keys&KEY_SELECT){if(reviewing)close_review();open_user_menu(STORY_MENU_QUICK);jumped=true;}
        if(jumped){
            delta=0;keys=0;
            if(story_shell_active()){exclude_io_time(before);last_vblank=vblanks;continue;}
        }
        if(reviewing){
            if(keys&KEY_B){unsigned old=review_pc;previous_dialogue();if(review_pc!=old)audio_ui_play(AUDIO_UI_MOVE);}
            if(keys&(KEY_UP|KEY_DOWN))scroll_input(keys);
            if(keys&(KEY_A|KEY_TOUCH)){
                close_review();
                audio_ui_play(AUDIO_UI_BACK);
                delta=0;
            }
            keys&=~(KEY_A|KEY_B|KEY_TOUCH|KEY_L|KEY_UP|KEY_DOWN);
        }
        if(keys&(KEY_UP|KEY_DOWN))scroll_input(keys);
        bool control_feedback=false;
        if((keys&KEY_L)&&!debug){flow.paused=!flow.paused;audio_scene_pause(flow.paused);control_feedback=true;}
        if(keys&KEY_R){flow.automatic=!flow.automatic;control_feedback=true;}
        if(keys&KEY_X){story_flow_toggle_language(&flow);story_shell_language(flow.bilingual);review_page=0;review_upper_pending=reviewing;control_feedback=true;}
        if(control_feedback)audio_ui_play(AUDIO_UI_CONFIRM);
        if(!flow.paused){
            uint32_t flow_started=audio_perf_now();
            if(keys&KEY_B){flow.now+=delta;previous_dialogue();if(reviewing)audio_ui_play(AUDIO_UI_MOVE);}
            else if(keys&KEY_A){
                flow.now+=delta;
                if(scroll_manual){scroll_manual=false;lower_valid=false;}
                else story_flow_advance(&flow);
            }
            else if(keys&KEY_TOUCH){touchPosition p;touchRead(&p);if(p.py<STORY_MENU_QUICK_COLLAPSED_Y||!flow.quick_menu)flow.now+=delta;touch_action();}
            else story_flow_tick(&flow,delta);
            perf_peak(&g_story_render_perf.max_flow,audio_perf_now()-flow_started);
        }
        exclude_io_time(before);audio_demo_service();
        if(flow.failed)fatal("Story playback");
    }
    return 0;
}
#endif
