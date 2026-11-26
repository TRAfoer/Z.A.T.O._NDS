#ifndef ZATO_STORY_SHELL_H
#define ZATO_STORY_SHELL_H
#include "story_menu.h"
#include "story_save.h"
typedef struct {
    bool (*new_game)(void *);
    bool (*capture)(void *,StorySaveMeta *,uint8_t checkpoint[768],uint8_t thumb[3072]);
    bool (*restore)(void *,const uint8_t checkpoint[768]);
    bool (*gallery)(void *,unsigned frame,uint16_t screen[256*192]);
    void (*history)(void *);
    void (*title)(void *);
    void (*resume)(void *);
    const uint16_t *(*screen)(void *);
    const uint16_t *(*lower_screen)(void *);
    void (*quick_action)(void *,StoryMenuActionKind);
} StoryShellOps;
typedef struct {
    uint32_t magic,active,mode,page,selection,available,save_ops,load_ops;
    uint32_t save_errors,last_slot,io_epoch,autosaves,global_writes;
    uint32_t title_visits;
} StoryShellStatus;
extern volatile StoryShellStatus g_story_shell_status;
void story_shell_init(const StoryShellOps *,void *);
bool story_shell_active(void);
void story_shell_title(void);
void story_shell_open(StoryMenuMode);
void story_shell_input(StoryMenuInput);
bool story_shell_draw(uint32_t ticks,uint16_t *upper,uint16_t *lower);
uint32_t story_shell_version(uint32_t ticks);
void story_shell_observe(unsigned chapter,unsigned text_id,bool complete,
                         uint32_t unlock_low,uint32_t unlock_high,uint32_t play_delta);
void story_shell_quicksave(void);
void story_shell_quickload(void);
void story_shell_language(bool bilingual);
void story_shell_commit_globals(void);
#endif
