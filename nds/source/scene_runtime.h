#ifndef ZATO_SCENE_RUNTIME_H
#define ZATO_SCENE_RUNTIME_H
#include <stdint.h>
/* All fields are u32, also read by the independent ROM verification tool.
 * requested_checkpoint is a test hook; UINT32_MAX means no pending request. */
typedef struct {
    uint32_t magic,frames,ready,pc,source_line,text_id,visible,page,group,group_frame;
    uint32_t checkpoint,paused,bilingual,automatic,errors,ticks,wait_hard,done;
    uint32_t gfx_bytes,gfx_reads,commands,missed_vblanks,requested_checkpoint;
} SceneStatus;
extern volatile SceneStatus g_scene_status;
int scene_demo_main(void);
#endif
