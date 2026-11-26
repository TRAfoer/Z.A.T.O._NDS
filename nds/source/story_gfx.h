#ifndef ZATO_STORY_GFX_H
#define ZATO_STORY_GFX_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "story_state_types.h"

/* Normal story scene compositor. Source tag IDs are distinct from image IDs.
 * placement: -1 retain existing placement, otherwise center; 0 center, 1 left,
 * 2 right. Game script:58..60 overrides xalign to .5/.25/.75. Actor centers
 * begin at the PC center/1280 ratio while sprites keep approved 2x2 size:
 * base left=floor(64 + source_width/20 - width/2), right=floor(192 -
 * source_width/20 - width/2). Category2 left/right then use packed x +
 * floor((base-packed x)*3/2), with exact canvas center clamped to [48,208].
 * Center/explicit anchor programs and all other categories keep their prior
 * positions. Same-tag placement retention and per-tag move endpoints apply
 * this policy once; enter/leave factories can still move fully offscreen.
 * behind_tag=-1 means none.
 * show preloads every bank/map needed by a sequence; draw/version do no I/O.
 * The caller clears normal layers before entering the dedicated scene_gfx route.
 * Output: opaque RGB15 256x192; transparent source pixels preserve lower layers. */
bool story_gfx_open(const char *assets_path,const char *catalog_path);
void story_gfx_close(void);
void story_gfx_clear(void);
bool story_gfx_show(unsigned image_id,unsigned tag_id,int placement,
                    unsigned program_id,int behind_tag,uint32_t now_ticks);
bool story_gfx_hide(unsigned tag_id);
bool story_gfx_draw(uint32_t now_ticks,uint16_t *screen256x192);
uint32_t story_gfx_version(uint32_t now_ticks);
const char *story_gfx_error(void);
size_t story_gfx_resident_bytes(void);
uint32_t story_gfx_read_calls(void);
unsigned story_gfx_layer_count(void);
/* Batch snapshots retain old per-tag resources only until end_batch or the
 * move ends. kind is StoryTransition TR_EASE..TR_EASEINLEFT (5..9). */
void story_gfx_begin_batch(uint32_t now_ticks);
bool story_gfx_move_transition(unsigned kind,uint32_t now_ticks,uint32_t duration);
bool story_gfx_end_batch(void);
void story_gfx_set_replay(bool replay);
bool story_gfx_sync(void);
/* Ordered, self-contained metadata only; no pointers, loaded flags or caches.
 * Capture rejects replay/batch/move. Validation does not alter live graphics.
 * Restore acquires all resources before replacing the old scene. */
bool story_gfx_snapshot(StoryGfxSnapshot*);
bool story_gfx_snapshot_valid(const StoryGfxSnapshot*);
bool story_gfx_restore(const StoryGfxSnapshot*);
/* Transient resource transaction, never serialized into a save. Retained refs
 * preserve an interrupted transition without rereading its files on rollback. */
bool story_gfx_restore_begin(void);
void story_gfx_restore_commit(void);
void story_gfx_restore_rollback(void);
bool story_gfx_restore_pending(void);
#endif
