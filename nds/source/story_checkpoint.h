#ifndef ZATO_STORY_CHECKPOINT_H
#define ZATO_STORY_CHECKPOINT_H
#include <stdbool.h>
#include <stdint.h>
#include "story_state_types.h"
#include "story_flow.h"
#define STORY_CHECKPOINT_REFLOW_VERSION 3u
typedef struct {
    StoryFlowSnapshot flow;
    StoryGfxSnapshot gfx;
    AudioSceneSnapshot audio;
    StoryBackendSnapshot backend;
    uint32_t scroll,scroll_manual,scroll_covered,layout_key;
} StoryCheckpointDecoded;
/* Wire: 16-byte header, explicit LE payload and zero reserved bytes.
 * Legacy capture writes version1/600 bytes; capture_view writes version3/616
 * with scroll, scroll_manual, scroll_covered and the current layout key.
 * Version2/612 remains readable. All versions remain 768 bytes.
 * Version1 decodes with scroll=scroll_manual=0 and coverage=flow.visible.
 * View coverage cannot exceed flow.visible. Runtime validates the saved
 * scroll against the current text viewport and applies it after restore.
 * Backend application, text date/literal caches, menu pause and the first real
 * display commit belong to runtime. Restore never replays source instructions.
 * A caller's pending gfx transaction remains open on success/failure so later
 * backend/cache work can commit or rollback it. Otherwise restore owns it. */
bool story_checkpoint_capture(const StoryFlow*,const StoryBackendSnapshot*,uint8_t out[STORY_CHECKPOINT_BYTES]);
bool story_checkpoint_capture_view(const StoryFlow*,const StoryBackendSnapshot*,uint32_t scroll,bool manual,uint32_t covered,uint8_t out[STORY_CHECKPOINT_BYTES]);
bool story_checkpoint_decode(const uint8_t in[STORY_CHECKPOINT_BYTES],StoryCheckpointDecoded*);
bool story_checkpoint_valid(const StoryFlow*,const StoryCheckpointDecoded*);
/* Validate the original source state before normalizing only derived layout.
 * Does not modify input/live flow or persistent bytes. Same-key is strict;
 * legacy native8/12 records retain their exact manual view. */
bool story_checkpoint_prepare(const StoryFlow*,const StoryCheckpointDecoded*,StoryCheckpointDecoded*);
bool story_checkpoint_restore(StoryFlow*,const StoryCheckpointDecoded*);
#endif
