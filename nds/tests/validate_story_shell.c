#include "story_shell.h"
#include "audio_demo.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static StoryMenuAction queued;
static bool navigation_override;
static unsigned navigation_selection,navigation_page,navigation_focus,navigation_scroll,navigation_quick;
static bool safe=true;
static bool paused,require_capture_paused,check_previews,draw_chapter_skip;
static unsigned starts,restores,captures,resumes,quick_calls,history_calls,menu_inputs;
static unsigned upper_reads,lower_reads,callback_serial,resume_order,quick_order;
static StoryMenuActionKind expected_quick;
static uint16_t live_upper[256*192],live_lower[256*192];
static uint16_t expected_upper[256*192],expected_lower[256*192];
static uint16_t output_upper[256*192],output_lower[256*192];
static uint8_t live=0x17;
static uint8_t live_thumb=42;
void audio_scene_pause(bool p){paused=p;}
void audio_scene_stop(bool p,uint32_t f){(void)p;(void)f;}
void audio_scene_volumes(unsigned m,unsigned s){(void)m;(void)s;}
bool audio_ui_prepare(void){return true;}
void audio_ui_play(AudioUiCue cue){(void)cue;}
void audio_ui_stop(void){}
void story_menu_init(StoryMenuState*s,StoryMenuMode m,bool game){memset(s,0,sizeof(*s));s->mode=s->return_mode=m;s->in_game=game;s->page=1;}
void story_menu_confirm(StoryMenuState*s,StoryMenuPrompt p,StoryMenuActionKind a,int v){s->return_mode=s->mode;s->mode=STORY_MENU_CONFIRM;s->prompt=p;s->confirm_action=(StoryMenuAction){a,v};}
void story_menu_message(StoryMenuState*s,StoryMenuPrompt p,const char*d){s->return_mode=s->mode;s->mode=STORY_MENU_MESSAGE;s->prompt=p;(void)d;}
StoryMenuAction story_menu_input(StoryMenuState*s,StoryMenuInput i){
    (void)i;menu_inputs++;
    if(navigation_override){
        s->selection=navigation_selection;s->page=navigation_page;s->focus=navigation_focus;
        s->scroll=navigation_scroll;s->quick_page=navigation_quick;navigation_override=false;
    }
    if(queued.kind==STORY_MENU_PAGE_CHANGED)s->page=(unsigned)queued.value;
    if(queued.kind==STORY_MENU_SETTINGS_CHANGED)s->chapter_skip=queued.value!=0;
    StoryMenuAction a=queued;queued.kind=STORY_MENU_NONE;return a;
}
static void disjoint(const uint16_t*a,const uint16_t*b){
    uintptr_t x=(uintptr_t)a,y=(uintptr_t)b,size=sizeof(live_upper);
    assert(x+size<=y||y+size<=x);
}
bool story_menu_draw(const StoryMenuState*s,uint32_t t,uint16_t*u,uint16_t*l){
    (void)t;draw_chapter_skip=s->chapter_skip;
    if(check_previews){
        assert(s->in_game&&s->game_preview&&s->game_lower_preview);
        assert(!memcmp(s->game_preview,expected_upper,sizeof(expected_upper)));
        assert(!memcmp(s->game_lower_preview,expected_lower,sizeof(expected_lower)));
        disjoint(s->game_preview,s->game_lower_preview);
        disjoint(s->game_preview,u);disjoint(s->game_preview,l);
        disjoint(s->game_lower_preview,u);disjoint(s->game_lower_preview,l);
        /* Real menu draw overwrites the framebuffer returned by screen(). */
        memset(u,0xa5,sizeof(live_upper));memset(l,0x5a,sizeof(live_lower));
    }
    return true;
}
uint32_t story_menu_version(const StoryMenuState*s,uint32_t t){(void)s;(void)t;return 1;}
unsigned story_menu_gallery_count(void){return 0;}
bool story_menu_gallery(unsigned n,unsigned*b,unsigned*f){(void)n;(void)b;(void)f;return false;}
static bool start(void*c){(void)c;starts++;return true;}
static bool capture(void*c,StorySaveMeta*m,uint8_t*p,uint8_t*t){
    (void)c;captures++;if(require_capture_paused)assert(paused);
    if(!safe)return false;
    memset(m,0,sizeof(*m));m->chapter=1;m->text_id=7;
    memset(p,live,768);memset(t,live_thumb,3072);return true;
}
static bool restore(void*c,const uint8_t*p){(void)c;restores++;live=p[0];return true;}
static void nothing(void*c){(void)c;}
static void resume_story(void*c){
    (void)c;assert(!story_shell_active());resumes++;resume_order=++callback_serial;
    audio_scene_pause(false); /* Runtime owns audio resume after shell closes. */
}
static const uint16_t*upper_screen(void*c){(void)c;upper_reads++;return live_upper;}
static const uint16_t*lower_screen(void*c){(void)c;lower_reads++;return live_lower;}
static void quick_action(void*c,StoryMenuActionKind kind){
    (void)c;assert(kind==expected_quick);assert(!story_shell_active()&&!paused);
    quick_calls++;quick_order=++callback_serial;assert(quick_order==resume_order+1);
}
static void history(void*c){(void)c;assert(!story_shell_active()&&!paused);history_calls++;}
static void act(StoryMenuActionKind kind,int value){queued=(StoryMenuAction){kind,value};story_shell_input((StoryMenuInput){.down=1});}
static void navigate(StoryMenuActionKind action,unsigned selection,unsigned page,unsigned focus,unsigned scroll,unsigned quick){
    navigation_override=true;navigation_selection=selection;navigation_page=page;
    navigation_focus=focus;navigation_scroll=scroll;navigation_quick=quick;act(action,0);
}
static void navigation_regressions(void){
    story_shell_title();navigate(STORY_MENU_OPEN_SETTINGS,3,1,0,0,0);
    act(STORY_MENU_BACK,0);assert(g_story_shell_status.mode==STORY_MENU_TITLE&&g_story_shell_status.selection==3);
    navigate(STORY_MENU_OPEN_LOAD,1,1,0,0,0);
    navigate(STORY_MENU_OPEN_SETTINGS,3,5,1,4,0);
    act(STORY_MENU_BACK,0);assert(g_story_shell_status.mode==STORY_MENU_LOAD&&g_story_shell_status.selection==3&&g_story_shell_status.page==5);
    act(STORY_MENU_BACK,0);assert(g_story_shell_status.mode==STORY_MENU_TITLE&&g_story_shell_status.selection==1);
    act(STORY_MENU_NEW_GAME,0);story_shell_open(STORY_MENU_QUICK);
    navigate(STORY_MENU_OPEN_SETTINGS,0,1,0,0,2);act(STORY_MENU_BACK,0);
    assert(g_story_shell_status.mode==STORY_MENU_QUICK&&g_story_shell_status.page==3&&g_story_shell_status.selection==0);
    act(STORY_MENU_CLOSE,0);assert(!story_shell_active());
}
static void frame(unsigned seed){
    for(unsigned i=0;i<256*192;i++){
        live_upper[i]=(uint16_t)((i+seed)&0x7fff);
        live_lower[i]=(uint16_t)((i*3+seed+0x2345)&0x7fff);
    }
    memcpy(expected_upper,live_upper,sizeof(expected_upper));
    memcpy(expected_lower,live_lower,sizeof(expected_lower));
}
static void frozen_draws(void){
    check_previews=true;
    assert(story_shell_draw(1,live_upper,live_lower));
    assert(story_shell_draw(2,output_upper,output_lower));
    check_previews=false;
}
static void quick_regressions(void){
    uint8_t cp[768],thumb[3072];StorySaveMeta meta;unsigned slots[10];
    unsigned captures_before=captures,resumes_before=resumes,upper_before=upper_reads,lower_before=lower_reads;
    unsigned writes_before=g_story_shell_status.save_ops,errors_before=g_story_shell_status.save_errors;
    unsigned quick_before=quick_calls,old_count=story_save_recent(106,10,slots,10);
    safe=true;require_capture_paused=true;live=0x35;live_thumb=0x52;frame(7);
    story_shell_open(STORY_MENU_QUICK);
    assert(story_shell_active()&&paused&&captures==captures_before+1);
    assert(upper_reads==upper_before+1&&lower_reads==lower_before+1);
    frozen_draws();
    /* Menu navigation and rendering must retain the pre-overlay checkpoint. */
    live=0x91;live_thumb=0xc6;safe=false;
    act(STORY_MENU_OPEN_SETTINGS,0);assert(story_shell_active()&&paused);frozen_draws();
    act(STORY_MENU_BACK,0);assert(g_story_shell_status.mode==STORY_MENU_QUICK);frozen_draws();
    story_shell_open(STORY_MENU_QUICK);frozen_draws();
    assert(captures==captures_before+1&&upper_reads==upper_before+1&&lower_reads==lower_before+1);
    act(STORY_MENU_QUICK_SAVE,0);
    assert(!story_shell_active()&&!paused&&resumes==resumes_before+1);
    assert(captures==captures_before+1&&quick_calls==quick_before);
    assert(g_story_shell_status.save_ops==writes_before+1&&g_story_shell_status.save_errors==errors_before);
    assert(story_save_recent(106,10,slots,10)==old_count+1);
    assert(story_save_read(slots[0],&meta,cp,thumb)&&meta.valid&&meta.text_id==7);
    for(unsigned i=0;i<sizeof(cp);i++)assert(cp[i]==0x35);
    for(unsigned i=0;i<sizeof(thumb);i++)assert(thumb[i]==0x52);
    safe=true;

    const StoryMenuActionKind actions[]={STORY_MENU_OPEN_DEBUG,STORY_MENU_TOGGLE_SKIP,
        STORY_MENU_TOGGLE_AUTO,STORY_MENU_TOGGLE_LANGUAGE};
    for(unsigned i=0;i<sizeof(actions)/sizeof(*actions);i++){
        captures_before=captures;resumes_before=resumes;quick_before=quick_calls;
        story_shell_open(STORY_MENU_QUICK);assert(paused&&captures==captures_before+1);
        expected_quick=actions[i];act(actions[i],0);
        assert(!story_shell_active()&&!paused&&resumes==resumes_before+1);
        assert(quick_calls==quick_before+1&&captures==captures_before+1);
    }
    resumes_before=resumes;quick_before=quick_calls;
    story_shell_open(STORY_MENU_QUICK);act(STORY_MENU_CLOSE,0);
    assert(!story_shell_active()&&!paused&&resumes==resumes_before+1&&quick_calls==quick_before);
    unsigned input_before=menu_inputs;
    story_shell_input((StoryMenuInput){.down=STORY_MENU_KEY_A});
    assert(menu_inputs==input_before&&resumes==resumes_before+1&&quick_calls==quick_before);
    story_shell_open(STORY_MENU_QUICK);act(STORY_MENU_OPEN_HISTORY,1);
    assert(history_calls==1&&!story_shell_active()&&!paused);

    /* START/GAME uses both frozen story screens, not a prior menu frame. */
    captures_before=captures;frame(73);story_shell_open(STORY_MENU_GAME);frozen_draws();
    assert(captures==captures_before+1&&story_shell_active()&&paused);
    act(STORY_MENU_BACK,0);assert(!story_shell_active()&&!paused);

    /* Settings state goes through shell -> actual .sav, not a stub store. */
    assert(story_save_globals()->chapter_skip==0);
    story_shell_open(STORY_MENU_GAME);act(STORY_MENU_OPEN_SETTINGS,0);
    assert(story_shell_draw(3,output_upper,output_lower)&&!draw_chapter_skip);
    unsigned globals_before=g_story_shell_status.global_writes;
    act(STORY_MENU_SETTINGS_CHANGED,1);
    assert(story_save_globals()->chapter_skip==1&&g_story_shell_status.global_writes==globals_before+1);
    assert(story_shell_draw(4,output_upper,output_lower)&&draw_chapter_skip);
    act(STORY_MENU_BACK,0);act(STORY_MENU_BACK,0);
    assert(!story_shell_active()&&!paused);
    require_capture_paused=false;
}
int main(int argc,char**argv){
    assert(argc==2||argc==3);uint8_t id[32]={1};
    StoryShellOps ops={.new_game=start,.capture=capture,.restore=restore,.title=nothing,
        .resume=resume_story,.history=history,.screen=upper_screen,.lower_screen=lower_screen,.quick_action=quick_action};
    if(argc==3){
        assert(!strcmp(argv[2],"--check-chapter"));assert(story_save_open(argv[1],id));
        assert(story_save_globals()->chapter_skip==1);story_shell_init(&ops,0);story_shell_title();
        story_shell_open(STORY_MENU_SETTINGS);
        assert(story_shell_draw(0,output_upper,output_lower)&&draw_chapter_skip);
        story_save_close();puts("PASS shell: chapter setting cold-process restore");return 0;
    }
    assert(!story_save_open(NULL,id));
    assert(story_save_globals()&&story_save_globals()->music_q12==4096&&story_save_globals()->sfx_q12==4096);
    story_shell_init(&ops,0);story_shell_title();assert(story_shell_active());
    act(STORY_MENU_NEW_GAME,0);assert(!story_shell_active());
    story_shell_open(STORY_MENU_SAVE);assert(story_shell_active()&&g_story_shell_status.save_errors);
    assert(!story_save_commit_globals());starts=0;
    assert(story_save_open(argv[1],id));
    story_shell_init(&ops,0);story_shell_title();assert(story_shell_active());
    act(STORY_MENU_NEW_GAME,0);assert(starts==1&&!story_shell_active());
    story_shell_observe(1,7,false,4,0,3);assert(!(story_save_globals()->seen[0]&128));
    story_shell_observe(1,7,true,4,0,3);assert(story_save_globals()->seen[0]&128);
    unsigned recent[10];assert(story_save_recent(96,10,recent,10)==1);
    story_shell_open(STORY_MENU_SAVE);act(STORY_MENU_SAVE_SLOT,0);
    StorySaveMeta meta;uint8_t cp[768],thumb[3072];assert(story_save_read(0,&meta,cp,thumb)&&meta.valid&&cp[0]==0x17&&thumb[0]==42);
    assert(g_story_shell_status.save_ops==1);act(STORY_MENU_RESUME,0);
    live=0x77;story_shell_quicksave();assert(story_save_recent(106,10,recent,10)==1);
    act(STORY_MENU_RESUME,0);story_save_close();assert(story_save_open(argv[1],id));
    assert(story_save_globals()->seen[0]&128);story_shell_init(&ops,0);story_shell_title();
    story_shell_open(STORY_MENU_LOAD);act(STORY_MENU_LOAD_SLOT,0);
    assert(restores==1&&live==0x17&&!story_shell_active());
    safe=false;unsigned before=g_story_shell_status.save_ops;story_shell_quicksave();
    assert(g_story_shell_status.save_ops==before&&g_story_shell_status.save_errors);
    assert(story_save_recent(106,10,recent,10)==1);
    act(STORY_MENU_RESUME,0);safe=true;navigation_regressions();quick_regressions();
    story_save_close();puts("PASS shell: cold reload, manual/quick/auto, unsafe no-write, cached QUICK point, resume/action order, frozen dual previews, chapter setting");return 0;
}
