#ifndef ZATO_STORY_RUNTIME_H
#define ZATO_STORY_RUNTIME_H
#include <stdint.h>
typedef struct {
    uint32_t magic,frames,ready,pc,chapter,source_line,text_id,visible,page;
    uint32_t layers,special_group,gfx_version,paused,bilingual,automatic;
    uint32_t errors,ticks,wait_hard,done,commands,text_bytes,text_peak,text_reads;
    uint32_t gfx_bytes,gfx_reads,missed_vblanks,io_vblanks,requested_chapter;
    uint32_t unlock_low,unlock_high;
    /* Stable, committed display snapshot for read-only verification. A reader
     * accepts only an unchanged even sequence around the complete structure. */
    uint32_t sequence,local,pause_index,char_count,page_limit,duration_ticks,text_mode;
    uint32_t source_auto,blocked,waiting,forever,window,quick_menu,pause_count;
    uint32_t pause_start,pause_end,pause_glyphs,wait_started,wait_end;
    uint32_t literal_id,literal_start,literal_duration,literal_page,date_id;
    uint32_t transition_kind,transition_start,transition_duration;
    uint32_t scroll,scroll_max,scroll_manual,scroll_covered;
} StoryStatus;
extern volatile StoryStatus g_story_status;
/* Timer1 elapsed ticks, independent of plot time and persistent save ABI.
 * Audio perf publishes the shared clock frequency/divisor. */
typedef struct {
    uint32_t magic,version,prepare_calls,last_prepare,max_prepare;
    uint32_t max_upper,max_lower,max_commit,max_scroll;
    uint32_t text_bytes,text_peak,gfx_bytes;
    uint32_t max_flow;
} StoryRenderPerf;
extern volatile StoryRenderPerf g_story_render_perf;
/* Read-only proof of every submitted frame, including presentations that fall
 * between debugger snapshots. Entries publish serial last, then head. */
#define STORY_PRESENTATION_CAPACITY 32u
typedef struct {
    uint32_t serial,commands,text_id,page,visible,local,flags,ticks;
    uint32_t cn_first,cn_end,en_first,en_end;
    uint32_t scroll,scroll_manual,scroll_covered;
} StoryPresentation;
typedef struct {
    uint32_t magic,capacity,head,reserved;
    StoryPresentation entries[STORY_PRESENTATION_CAPACITY];
} StoryPresentationLog;
extern volatile StoryPresentationLog g_story_presentations;
int story_demo_main(int argc,char **argv);
#endif
