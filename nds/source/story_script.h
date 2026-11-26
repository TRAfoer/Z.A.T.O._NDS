#ifndef ZATO_STORY_SCRIPT_H
#define ZATO_STORY_SCRIPT_H
#include <stdbool.h>
#include <stdint.h>
enum StoryOp { ST_TEXT=1,ST_SHOW,ST_HIDE,ST_SCENE,ST_WAIT,ST_TRANSITION,
    ST_MUSIC,ST_STOP_MUSIC,ST_SFX,ST_STOP_SFX,ST_WINDOW,ST_QUICK,ST_CLEAR,
    ST_LITERAL,ST_HIDE_LITERAL,ST_DATE,ST_UNLOCK,ST_JUMP,ST_END,ST_SPECIAL };
enum StoryTransition { TR_NONE=0,TR_DISSOLVE,TR_FADE,TR_HPUNCH,TR_VPUNCH,
    TR_EASE,TR_EASEOUTLEFT,TR_EASEINRIGHT,TR_EASEOUTRIGHT,TR_EASEINLEFT };
typedef struct {uint16_t op,flags;uint32_t line;int32_t a,b,c,d;} StoryEvent;
typedef struct {uint32_t first_pc,end_pc,first_text,text_count;char name[16];} StoryChapter;
typedef struct {
    uint8_t *data;uint32_t count,chapter_count,text_count,source_count;
    const StoryEvent *events;const StoryChapter *chapters;char error[96];
} StoryScript;
bool story_script_open(StoryScript*,const char*);
void story_script_close(StoryScript*);
#endif
