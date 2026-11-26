#ifndef ZATO_STORY_STATE_TYPES_H
#define ZATO_STORY_STATE_TYPES_H
#include <stdint.h>
#include "story_script.h"
#define STORY_CHECKPOINT_BYTES 768u
#define STORY_SNAPSHOT_LAYERS 16u
/* Fixed-width semantic fields only. Wire encoders must write little-endian
 * explicitly, never memcpy native structs into the persistent container. */
typedef struct { uint32_t value[21],flags; } StoryFlowSnapshot;
typedef struct { uint32_t image,tag,program; int32_t placement; uint32_t start; } StoryGfxLayerSnapshot;
typedef struct { uint32_t count; StoryGfxLayerSnapshot layers[STORY_SNAPSHOT_LAYERS]; } StoryGfxSnapshot;
typedef struct {
    uint32_t enabled,index,loop,offset_frames;
    int32_t gain_q12,fade_step,fade_target;
    uint32_t fade_total,fade_left,fade_remainder,fade_error,flags;
} AudioVoiceSnapshot;
typedef struct {
    uint32_t version,paused,master_music,master_sfx;
    AudioVoiceSnapshot music,sfx;
} AudioSceneSnapshot;
typedef struct {
    uint32_t special,group,phase,origin[5],have_music,music_tick;
    uint32_t transition,transition_start,transition_duration;
    StoryEvent music;
} StoryBackendSnapshot;
#endif
