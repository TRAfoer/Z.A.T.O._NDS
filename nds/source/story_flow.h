#ifndef ZATO_STORY_FLOW_H
#define ZATO_STORY_FLOW_H
#include <stdbool.h>
#include <stdint.h>
#include "story_script.h"
#include "scene_text.h"
#include "story_state_types.h"
#define STORY_NONE UINT32_MAX
typedef struct {
    bool (*event)(void*,const StoryEvent*,uint32_t now,bool quiet);
    bool (*select_text)(void*,unsigned id,unsigned slot);
    const SceneTextInfo* (*info)(void*,unsigned id);
    const SceneTextPause* (*pause)(void*,unsigned id,unsigned index);
    unsigned (*visible)(void*,unsigned id,uint32_t ticks);
    unsigned (*page)(void*,unsigned id,unsigned visible,int bilingual);
    unsigned (*limit)(void*,unsigned id,unsigned page,int bilingual);
    void (*batch)(void*,bool begin,uint32_t now,bool quiet);
    void (*reset)(void*);
} StoryFlowOps;
typedef struct {
    const StoryScript*script;const StoryFlowOps*ops;void*context;
    uint32_t pc,now,chapter,line,commands;
    uint32_t text_id,literal_id,date_id,local,visible,page,pause_index,page_ready;
    uint32_t wait_started,wait_end,presented_local,presented_visible;
    uint32_t literal_start,literal_duration,unlocks[2];
    bool window,quick_menu,blocked,waiting,hard,forever,done,failed,quiet;
    bool text_active,source_auto,revealed,page_hold,presented;
    bool paused,bilingual,automatic,skip;
    /* Two former tail padding bytes; ARM ABI remains116. Runtime owns the
     * last frame's contiguous viewport coverage, independently of true ACK. */
    bool scrolling,scroll_ready;
} StoryFlow;
bool story_flow_init(StoryFlow*,const StoryScript*,const StoryFlowOps*,void*);
bool story_flow_seek(StoryFlow*,unsigned chapter);
void story_flow_tick(StoryFlow*,uint32_t delta_ticks);
void story_flow_advance(StoryFlow*);
void story_flow_refresh(StoryFlow*);
void story_flow_mark_presented(StoryFlow*);
void story_flow_toggle_language(StoryFlow*);
unsigned story_flow_pages(const StoryFlow*);

enum StoryFlowSnapshotValue {
    STORY_FLOW_PC,STORY_FLOW_NOW,STORY_FLOW_CHAPTER,STORY_FLOW_LINE,STORY_FLOW_COMMANDS,
    STORY_FLOW_TEXT_ID,STORY_FLOW_LITERAL_ID,STORY_FLOW_DATE_ID,STORY_FLOW_LOCAL,
    STORY_FLOW_VISIBLE,STORY_FLOW_PAGE,STORY_FLOW_PAUSE_INDEX,STORY_FLOW_PAGE_READY,
    STORY_FLOW_WAIT_STARTED,STORY_FLOW_WAIT_END,STORY_FLOW_PRESENTED_LOCAL,
    STORY_FLOW_PRESENTED_VISIBLE,STORY_FLOW_LITERAL_START,STORY_FLOW_LITERAL_DURATION,
    STORY_FLOW_UNLOCK_LOW,STORY_FLOW_UNLOCK_HIGH
};
enum StoryFlowSnapshotFlag {
    STORY_FLOW_F_WINDOW=1u<<0,STORY_FLOW_F_QUICK_MENU=1u<<1,STORY_FLOW_F_BLOCKED=1u<<2,
    STORY_FLOW_F_WAITING=1u<<3,STORY_FLOW_F_HARD=1u<<4,STORY_FLOW_F_FOREVER=1u<<5,
    STORY_FLOW_F_DONE=1u<<6,STORY_FLOW_F_FAILED=1u<<7,STORY_FLOW_F_QUIET=1u<<8,
    STORY_FLOW_F_TEXT_ACTIVE=1u<<9,STORY_FLOW_F_SOURCE_AUTO=1u<<10,
    STORY_FLOW_F_REVEALED=1u<<11,STORY_FLOW_F_PAGE_HOLD=1u<<12,
    STORY_FLOW_F_PRESENTED=1u<<13,STORY_FLOW_F_PAUSED=1u<<14,
    STORY_FLOW_F_BILINGUAL=1u<<15,STORY_FLOW_F_AUTOMATIC=1u<<16,STORY_FLOW_F_SKIP=1u<<17,
    STORY_FLOW_F_SCROLLING=1u<<18
};
#define STORY_FLOW_SNAPSHOT_FLAGS ((1u<<19)-1u)
/* Capture accepts only an exactly presented, blocked ordinary Say. Validation
 * may select an uncached target in slot1; slot0/live flow remain unchanged.
 * The runtime restores its literal slot1 on a failed/cancelled load. Restore
 * keeps the saved user pause state, clears ACK, and never executes a script op. */
bool story_flow_snapshot(const StoryFlow*,StoryFlowSnapshot*);
bool story_flow_snapshot_valid(const StoryFlow*,const StoryFlowSnapshot*);
/* Original source/ACK/time/pause invariants, without old page-cap equality.
 * Reflow is for a verified layout change only; normal restores remain strict. */
bool story_flow_snapshot_source_valid(const StoryFlow*,const StoryFlowSnapshot*);
bool story_flow_snapshot_reflow(const StoryFlow*,const StoryFlowSnapshot*,unsigned anchor,StoryFlowSnapshot*);
bool story_flow_restore(StoryFlow*,const StoryFlowSnapshot*);
#endif
