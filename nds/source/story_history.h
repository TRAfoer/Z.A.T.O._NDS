#ifndef ZATO_STORY_HISTORY_H
#define ZATO_STORY_HISTORY_H
#include <stdbool.h>
#include <stdint.h>
#include "story_script.h"
#include "story_state_types.h"

typedef struct {
    const StoryScript *script;
    uint32_t *starts,*completed;
} StoryHistory;

typedef struct {
    uint32_t now,literal_id,literal_start,literal_duration,date_id;
    StoryBackendSnapshot backend;
    StoryGfxSnapshot gfx;
} StoryHistoryView;

/* Zero/unused object only; close before reopening. The script remains owned by
 * the caller and must outlive this object. Two tables use4*(count+text_count)
 * bytes. UINT32_MAX denotes an unknown timestamp. No assets/audio are read. */
bool story_history_open(StoryHistory*,const StoryScript*);
void story_history_close(StoryHistory*);
void story_history_clear(StoryHistory*);
/* pc is a zero-based event index, i.e. flow.pc-1 during dispatch/commit.
 * First actual execution/completion wins; quiet replay does not write. */
void story_history_note_event(StoryHistory*,uint32_t pc,uint32_t now,bool quiet);
void story_history_note_complete(StoryHistory*,uint32_t pc,uint32_t now);
/* target_pc must name ST_TEXT. Source-order replay is metadata only; ST_JUMP
 * is not followed. Unknown timestamps use authored waits/transitions/text
 * duration, anchored once to the next observed timestamp when available.
 * Known actual timestamps are never shifted. out is untouched on failure. */
bool story_history_build(const StoryHistory*,uint32_t target_pc,StoryHistoryView*);
#endif
