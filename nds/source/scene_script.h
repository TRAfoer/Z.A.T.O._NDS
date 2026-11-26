#ifndef ZATO_SCENE_SCRIPT_H
#define ZATO_SCENE_SCRIPT_H
#include <stdbool.h>
#include <stdint.h>

enum SceneOp { SC_TEXT=1,SC_GFX,SC_WAIT,SC_TRANSITION,SC_MUSIC,SC_STOP_MUSIC,
    SC_SFX,SC_STOP_SFX,SC_WINDOW,SC_CLEAR,SC_LITERAL,SC_HIDE_LITERAL,SC_END };
typedef struct { uint16_t op,flags;uint32_t line;int32_t a,b,c,d; } SceneEvent;
typedef struct { uint32_t pc;char name[24]; } SceneCheckpoint;
typedef struct {
    uint8_t *data;uint32_t count,checkpoint_count;
    const SceneEvent *events;const SceneCheckpoint *checkpoints;
    char error[96];
} SceneScript;
bool scene_script_open(SceneScript*,const char*);
void scene_script_close(SceneScript*);
#endif
