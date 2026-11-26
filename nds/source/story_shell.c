#include "story_shell.h"
#include "audio_demo.h"
#include <stdio.h>
#include <string.h>
#include <time.h>
#define NONE UINT32_MAX
volatile StoryShellStatus g_story_shell_status;
static const StoryShellOps *ops;
static void *context;
static StoryMenuState menu;
static StoryMenuState parents[4];
static unsigned parent_count;
static bool active,has_game,point_ready,globals_dirty;
static unsigned last_chapter=NONE;
static bool auto_pending;
static uint8_t checkpoint[768],point_thumb[3072],slot_thumbs[6][3072];
static uint16_t gallery_pixels[256*192];
static uint16_t game_lower_pixels[256*192];
static StorySaveMeta point_meta;

static void status(void){
    g_story_shell_status.active=active;g_story_shell_status.mode=menu.mode;
    g_story_shell_status.page=menu.mode==STORY_MENU_QUICK?menu.quick_page+1:menu.page;
    g_story_shell_status.selection=menu.selection;
    g_story_shell_status.available=story_save_available();
}
static void settings(void){
    const StorySaveGlobals*g=story_save_globals();
    menu.bilingual=g->language!=0;menu.music_volume=(g->music_q12*100+2048)/4096;
    menu.sfx_volume=(g->sfx_q12*100+2048)/4096;
    menu.chapter_skip=g->chapter_skip!=0;
    menu.unlock_low=g->unlocks[0];menu.unlock_high=g->unlocks[1];
    menu.game_preview=has_game?gallery_pixels:NULL;
    menu.game_lower_preview=has_game?game_lower_pixels:NULL;
}
static void error(const char*s){
    g_story_shell_status.save_errors++;active=true;
    story_menu_message(&menu,STORY_MENU_PROMPT_ERROR,s);status();
}
static void fill_slots(void){
    unsigned ids[10],n=0,first=0;
    if(menu.page==STORY_MENU_PAGE_AUTO||menu.page==STORY_MENU_PAGE_QUICK){
        first=menu.page==STORY_MENU_PAGE_AUTO?STORY_SAVE_AUTO_FIRST:STORY_SAVE_QUICK_FIRST;
        g_story_shell_status.io_epoch++;n=story_save_recent(first,10,ids,10);
        for(unsigned p=0;p<10&&n<6;p++){
            bool found=false;for(unsigned k=0;k<n;k++)if(ids[k]==first+p)found=true;
            if(!found)ids[n++]=first+p;
        }
    }else for(unsigned i=0;i<6;i++)ids[n++]=(menu.page-1)*6+i;
    for(unsigned i=0;i<6;i++){
        StoryMenuSlot*s=&menu.slots[i];memset(s,0,sizeof(*s));s->slot_id=ids[i];
        StorySaveMeta m;g_story_shell_status.io_epoch++;
        if(!story_save_info(ids[i],&m)){s->status=STORY_MENU_SLOT_INVALID;continue;}
        if(!m.valid){s->status=STORY_MENU_SLOT_EMPTY;continue;}
        uint8_t discard[768];g_story_shell_status.io_epoch++;
        if(!story_save_read(ids[i],&m,discard,slot_thumbs[i])){s->status=STORY_MENU_SLOT_INVALID;continue;}
        s->status=STORY_MENU_SLOT_VALID;s->unix_time=m.unix_time;s->chapter=m.chapter;
        s->text_id=m.text_id;s->page=m.page;s->thumbnail=slot_thumbs[i];
    }
}
static bool capture(void){
    point_ready=ops&&ops->capture&&ops->capture(context,&point_meta,checkpoint,point_thumb);
    return point_ready;
}
void story_shell_commit_globals(void){
    if(!globals_dirty||!story_save_available())return;
    g_story_shell_status.io_epoch++;
    if(story_save_commit_globals()){globals_dirty=false;g_story_shell_status.global_writes++;}
    else g_story_shell_status.save_errors++;
}
static void resume(void){
    active=false;point_ready=false;parent_count=0;
    if(ops&&ops->resume)ops->resume(context);
    status();
}
void story_shell_init(const StoryShellOps*o,void*c){
    ops=o;context=c;active=has_game=point_ready=globals_dirty=auto_pending=false;last_chapter=NONE;
    parent_count=0;
    memset((void*)&g_story_shell_status,0,sizeof(g_story_shell_status));
    g_story_shell_status.magic=0x4c48535a;g_story_shell_status.last_slot=NONE;
    story_menu_init(&menu,STORY_MENU_TITLE,false);settings();status();
    (void)audio_ui_prepare();
}
bool story_shell_active(void){return active;}
void story_shell_title(void){
    story_shell_commit_globals();has_game=false;active=true;point_ready=false;
    parent_count=0;
    story_menu_init(&menu,STORY_MENU_TITLE,false);settings();
    if(ops&&ops->title)ops->title(context);
    g_story_shell_status.title_visits++;
    status();
}
void story_shell_open(StoryMenuMode mode){
    if(!active){
        parent_count=0;
        audio_scene_pause(true);capture();
        const uint16_t*p=ops&&ops->screen?ops->screen(context):NULL;
        if(p)memcpy(gallery_pixels,p,sizeof(gallery_pixels));
        p=ops&&ops->lower_screen?ops->lower_screen(context):NULL;
        if(p)memcpy(game_lower_pixels,p,sizeof(game_lower_pixels));
        else memset(game_lower_pixels,0,sizeof(game_lower_pixels));
    }else if(mode!=menu.mode){
        if(parent_count<sizeof(parents)/sizeof(*parents))parents[parent_count++]=menu;
    }
    active=true;story_menu_init(&menu,mode,has_game);settings();
    if(mode==STORY_MENU_SAVE||mode==STORY_MENU_LOAD){
        if(!story_save_available()){error("Writable storage is unavailable");return;}
        fill_slots();
    }
    status();
}
static bool write_point(unsigned slot,bool automatic){
    if(!point_ready)return false;
    if(!story_save_available())return false;
    point_meta.play_seconds=story_save_globals()->play_ticks/60;
    time_t now=time(NULL);if(now>0&&(uint64_t)now<=UINT32_MAX)point_meta.unix_time=(uint32_t)now;
    g_story_shell_status.io_epoch++;
    if(!story_save_write(slot,&point_meta,checkpoint,point_thumb))return false;
    g_story_shell_status.last_slot=slot;
    if(automatic)g_story_shell_status.autosaves++;else g_story_shell_status.save_ops++;
    story_shell_commit_globals();return true;
}
static bool unlocked(unsigned bit){
    const StorySaveGlobals*g=story_save_globals();return bit<64&&(g->unlocks[bit>>5]&(1u<<(bit&31)));
}
static void dispatch(StoryMenuAction a,bool confirmed){
    switch(a.kind){
    case STORY_MENU_NONE:case STORY_MENU_CANCEL:break;
    case STORY_MENU_NEW_GAME:
        story_shell_commit_globals();
        if(!ops||!ops->new_game||!ops->new_game(context)){error("Cannot start story");break;}
        has_game=true;last_chapter=NONE;auto_pending=false;resume();break;
    case STORY_MENU_RESUME:if(has_game)resume();break;
    case STORY_MENU_CLOSE:if(has_game)resume();break;
    case STORY_MENU_OPEN_DEBUG:case STORY_MENU_TOGGLE_SKIP:
    case STORY_MENU_TOGGLE_AUTO:case STORY_MENU_TOGGLE_LANGUAGE:
        if(has_game){resume();if(ops&&ops->quick_action)ops->quick_action(context,a.kind);}break;
    case STORY_MENU_QUICK_SAVE:
        if(has_game){
            /* The point was captured before the bar covered the dialogue. */
            if(!point_ready){error("This scene is not a save point");break;}
            g_story_shell_status.io_epoch++;
            unsigned slot=story_save_ring_slot(STORY_SAVE_QUICK_FIRST,STORY_SAVE_RING_COUNT);
            if(slot==NONE||!write_point(slot,false)){error(story_save_error());break;}
            resume();
        }break;
    case STORY_MENU_QUICK_LOAD:if(has_game)story_shell_quickload();break;
    case STORY_MENU_OPEN_SAVE:story_shell_open(STORY_MENU_SAVE);break;
    case STORY_MENU_OPEN_LOAD:story_shell_open(STORY_MENU_LOAD);break;
    case STORY_MENU_OPEN_GALLERY:story_shell_open(STORY_MENU_GALLERY);break;
    case STORY_MENU_OPEN_SETTINGS:story_shell_open(STORY_MENU_SETTINGS);break;
    case STORY_MENU_OPEN_CREDITS:story_shell_open(STORY_MENU_CREDITS);break;
    case STORY_MENU_OPEN_CONTROLS:story_shell_open(STORY_MENU_CONTROLS);break;
    case STORY_MENU_OPEN_HISTORY:
        if(has_game){resume();if(ops&&ops->history)ops->history(context);}break;
    case STORY_MENU_SAVE_SLOT:{
        if(a.value<0||(unsigned)a.value>=STORY_SAVE_SLOT_COUNT){error("Invalid save slot");break;}
        if(!point_ready){error("This scene is not a save point");break;}
        StorySaveMeta m;g_story_shell_status.io_epoch++;
        bool ok=story_save_info((unsigned)a.value,&m);
        if((!ok||m.valid)&&!confirmed){story_menu_confirm(&menu,STORY_MENU_PROMPT_OVERWRITE,a.kind,a.value);break;}
        if(!write_point((unsigned)a.value,false)){error(story_save_error());break;}
        fill_slots();story_menu_message(&menu,STORY_MENU_PROMPT_SAVED,NULL);break;
    }
    case STORY_MENU_LOAD_SLOT:{
        if(a.value<0||(unsigned)a.value>=STORY_SAVE_SLOT_COUNT){error("Invalid load slot");break;}
        if(has_game&&!confirmed){story_menu_confirm(&menu,STORY_MENU_PROMPT_LOAD,a.kind,a.value);break;}
        StorySaveMeta m;uint8_t data[768],thumb[3072];g_story_shell_status.io_epoch++;
        if(!story_save_read((unsigned)a.value,&m,data,thumb)||!m.valid){error(story_save_error());break;}
        if(!ops||!ops->restore||!ops->restore(context,data)){error("Unable to restore this save");break;}
        has_game=true;last_chapter=m.chapter;auto_pending=false;
        g_story_shell_status.load_ops++;g_story_shell_status.last_slot=(unsigned)a.value;
        audio_scene_volumes(story_save_globals()->music_q12,story_save_globals()->sfx_q12);resume();break;
    }
    case STORY_MENU_DELETE_SLOT:
        if(a.value<0||(unsigned)a.value>=STORY_SAVE_SLOT_COUNT){error("Invalid delete slot");break;}
        if(!confirmed){story_menu_confirm(&menu,STORY_MENU_PROMPT_DELETE,a.kind,a.value);break;}
        g_story_shell_status.io_epoch++;
        if(!story_save_delete((unsigned)a.value)){error(story_save_error());break;}
        fill_slots();break;
    case STORY_MENU_PAGE_CHANGED:fill_slots();break;
    case STORY_MENU_SETTINGS_CHANGED:{
        StorySaveGlobals*g=story_save_globals();g->language=menu.bilingual;
        g->chapter_skip=menu.chapter_skip;
        g->music_q12=(menu.music_volume*4096+50)/100;g->sfx_q12=(menu.sfx_volume*4096+50)/100;
        globals_dirty=true;audio_scene_volumes(g->music_q12,g->sfx_q12);story_shell_commit_globals();break;
    }
    case STORY_MENU_GALLERY_SELECT:{
        unsigned bit,frame;
        if(a.value<0||!story_menu_gallery((unsigned)a.value,&bit,&frame)||!unlocked(bit))break;
        if(ops&&ops->gallery&&ops->gallery(context,frame,gallery_pixels))menu.gallery_preview=gallery_pixels;
        break;
    }
    case STORY_MENU_RETURN_TITLE:
        if(has_game&&!confirmed){story_menu_confirm(&menu,STORY_MENU_PROMPT_TITLE,a.kind,0);break;}
        story_shell_title();break;
    case STORY_MENU_QUIT:
        if(has_game&&!confirmed){story_menu_confirm(&menu,STORY_MENU_PROMPT_QUIT,a.kind,0);break;}
        story_shell_title();audio_scene_stop(true,0);audio_scene_stop(false,0);
        story_menu_message(&menu,STORY_MENU_PROMPT_SHUTDOWN,NULL);break;
    case STORY_MENU_BACK:
        if(parent_count){
            menu=parents[--parent_count];settings();
            if(menu.mode==STORY_MENU_SAVE||menu.mode==STORY_MENU_LOAD)fill_slots();
        }else if((menu.mode==STORY_MENU_GAME||menu.mode==STORY_MENU_QUICK)&&has_game)resume();
        else{story_menu_init(&menu,has_game?STORY_MENU_GAME:STORY_MENU_TITLE,has_game);settings();}
        break;
    }
    status();
}
static bool changed_settings(const StoryMenuState*before){
    return before->bilingual!=menu.bilingual||before->chapter_skip!=menu.chapter_skip||
        before->music_volume!=menu.music_volume||before->sfx_volume!=menu.sfx_volume;
}
static void input_sound(const StoryMenuState*before,StoryMenuAction a,StoryMenuInput input){
    if(!input.down&&!input.touch)return;
    if(menu.mode==STORY_MENU_MESSAGE&&menu.prompt==STORY_MENU_PROMPT_ERROR&&
       (before->mode!=STORY_MENU_MESSAGE||before->prompt!=STORY_MENU_PROMPT_ERROR)){
        audio_ui_play(AUDIO_UI_ERROR);return;
    }
    if(a.kind==STORY_MENU_BACK||a.kind==STORY_MENU_CANCEL||a.kind==STORY_MENU_CLOSE||a.kind==STORY_MENU_RESUME){
        audio_ui_play(AUDIO_UI_BACK);return;
    }
    bool click=(input.down&STORY_MENU_KEY_A)||input.touch;
    bool confirm=menu.mode==STORY_MENU_CONFIRM&&before->mode!=STORY_MENU_CONFIRM;
    switch(a.kind){
    case STORY_MENU_NEW_GAME:case STORY_MENU_OPEN_SAVE:case STORY_MENU_OPEN_LOAD:
    case STORY_MENU_OPEN_GALLERY:case STORY_MENU_OPEN_SETTINGS:case STORY_MENU_OPEN_CREDITS:
    case STORY_MENU_OPEN_CONTROLS:case STORY_MENU_OPEN_HISTORY:case STORY_MENU_RETURN_TITLE:
    case STORY_MENU_QUIT:case STORY_MENU_SAVE_SLOT:case STORY_MENU_LOAD_SLOT:case STORY_MENU_DELETE_SLOT:
    case STORY_MENU_OPEN_DEBUG:case STORY_MENU_TOGGLE_SKIP:case STORY_MENU_TOGGLE_AUTO:
    case STORY_MENU_TOGGLE_LANGUAGE:case STORY_MENU_QUICK_SAVE:case STORY_MENU_QUICK_LOAD:
    case STORY_MENU_GALLERY_SELECT:confirm|=click;break;
    case STORY_MENU_SETTINGS_CHANGED:confirm|=click&&changed_settings(before);break;
    default:break;
    }
    if(confirm){audio_ui_play(AUDIO_UI_CONFIRM);return;}
    if(before->selection!=menu.selection||before->page!=menu.page||before->focus!=menu.focus||
       before->scroll!=menu.scroll||before->quick_page!=menu.quick_page||changed_settings(before))
        audio_ui_play(AUDIO_UI_MOVE);
}
void story_shell_input(StoryMenuInput input){
    if(!active)return;
    StoryMenuState before=menu;
    bool confirmed=menu.mode==STORY_MENU_CONFIRM;
    StoryMenuAction a=story_menu_input(&menu,input);dispatch(a,confirmed);status();
    input_sound(&before,a,input);
}
bool story_shell_draw(uint32_t ticks,uint16_t*upper,uint16_t*lower){
    status();return active&&story_menu_draw(&menu,ticks,upper,lower);
}
uint32_t story_shell_version(uint32_t ticks){
    return story_menu_version(&menu,ticks)^(g_story_shell_status.io_epoch*104729u);
}
void story_shell_quicksave(void){
    if(!capture()){
        if(!active)story_shell_open(STORY_MENU_GAME);
        error("This scene is not a save point");return;
    }
    audio_scene_pause(true);g_story_shell_status.io_epoch++;
    unsigned slot=story_save_ring_slot(STORY_SAVE_QUICK_FIRST,STORY_SAVE_RING_COUNT);
    if(slot==NONE||!write_point(slot,false)){
        if(!active)story_shell_open(STORY_MENU_GAME);
        error(story_save_error());return;
    }
    if(!active&&ops&&ops->resume)ops->resume(context);
    status();
}
void story_shell_quickload(void){
    if(!active)story_shell_open(STORY_MENU_GAME);
    unsigned slot;g_story_shell_status.io_epoch++;
    if(!story_save_recent(STORY_SAVE_QUICK_FIRST,10,&slot,1)){error("No quick save");return;}
    dispatch((StoryMenuAction){STORY_MENU_LOAD_SLOT,(int)slot},false);
}
void story_shell_language(bool bilingual){
    story_save_globals()->language=bilingual;globals_dirty=true;
}
void story_shell_observe(unsigned chapter,unsigned text_id,bool complete,uint32_t low,uint32_t high,uint32_t delta){
    if(active||!has_game)return;
    StorySaveGlobals*g=story_save_globals();
    if(delta){g->play_ticks=delta>UINT32_MAX-g->play_ticks?UINT32_MAX:g->play_ticks+delta;globals_dirty=true;}
    if((g->unlocks[0]|low)!=g->unlocks[0]||(g->unlocks[1]|high)!=g->unlocks[1]){
        g->unlocks[0]|=low;g->unlocks[1]|=high;globals_dirty=true;
    }
    if(complete&&text_id<11951){uint8_t b=1u<<(text_id&7);if(!(g->seen[text_id>>3]&b)){g->seen[text_id>>3]|=b;globals_dirty=true;}}
    if(chapter!=last_chapter){last_chapter=chapter;auto_pending=true;}
    if(auto_pending&&story_save_available()&&capture()){
        audio_scene_pause(true);g_story_shell_status.io_epoch++;
        unsigned slot=story_save_ring_slot(STORY_SAVE_AUTO_FIRST,10);
        if(slot!=NONE&&write_point(slot,true))auto_pending=false;
        else{auto_pending=false;g_story_shell_status.save_errors++;}
        if(ops&&ops->resume)ops->resume(context);
    }
    status();
}
