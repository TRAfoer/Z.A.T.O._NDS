#ifndef ZATO_STORY_TEXT_H
#define ZATO_STORY_TEXT_H
#include <stdint.h>
#include <stddef.h>
#include "scene_text.h"

/* Explicit two-slot cache. slot0 pins the live interaction; slot1 is review.
 * Only open/select read files. All getters return NULL/0 when id is uncached.
 * Info/Pause pointers remain valid until their slot is replaced or close(). */
int story_text_open(const char *path);
void story_text_close(void);
void story_text_set_service(void (*service)(void));
int story_text_select(unsigned id,unsigned slot);
unsigned story_text_count(void);
const char *story_text_error(void);
size_t story_text_resident_bytes(void);
size_t story_text_peak_bytes(void);
uint32_t story_text_read_calls(void);
/* Nonzero shared-package CRC key (0 when closed). Checkpoints use it only for
 * derived layout compatibility, independently of the story/save identity. */
uint32_t story_text_layout_key(void);
/* Cached ordinary record only. Expanded-area packages require legacy reflow;
 * old-policy packages retain their native8/12 geometry heuristic. */
int story_text_legacy_layout_compatible(unsigned id);
const SceneTextInfo *story_text_info(unsigned id);
/* Cached split-poem literal only; never selects or reads a record. */
int story_text_is_poem(unsigned id);
const SceneTextPause *story_text_pause(unsigned id,unsigned index);
unsigned story_text_visible(unsigned id,uint32_t ticks);
unsigned story_text_page(unsigned id,unsigned visible,int bilingual);
unsigned story_text_page_limit(unsigned id,unsigned page,int bilingual);
/* Drawable English glyphs visible on this paired page at a CN logical count. */
unsigned story_text_english_visible(unsigned id,unsigned visible,unsigned page);
void story_text_draw_lower(unsigned id,unsigned visible,unsigned page,int bilingual,int window,int quick_menu,uint16_t *screen);
/* Split poems show the complete CN page below. window=1 draws the usual thin
 * frame and body; window=0 clears the body. The original RU/EN page is drawn
 * above by draw_upper[_bilingual], independently of the language setting. */
void story_text_draw_lower_poem(unsigned id,unsigned page,int window,int quick_menu,uint16_t *screen);
typedef struct {
    unsigned scroll,max_scroll,cn_first,cn_end,en_first,en_end;
} StoryTextViewport;
/* Cached ordinary lower Say only; row units, zero I/O. CN and English ranges
 * cover only completely placed rows and revealed commands, including blank/
 * newline logical indices. EN indices are CNcount+EnglishIndex. Empty=0/0.
 * Whole rows/pairs yield downward if wide ink would cross the pinned portrait.
 * Queries never modify a screen. max is the last revealed legal row origin. */
unsigned story_text_scroll_max(unsigned id,unsigned visible,int bilingual);
unsigned story_text_scroll_for_visible(unsigned id,unsigned visible,int bilingual);
/* Keep the first not-yet-covered revealed CN command in view before following
 * a later fast batch. Runtime holds follow for the first restored commit. */
unsigned story_text_scroll_follow(unsigned id,unsigned visible,int bilingual,unsigned scroll,unsigned covered_cn);
void story_text_draw_lower_scroll(unsigned id,unsigned visible,unsigned scroll,int bilingual,int window,int quick_menu,uint16_t *screen,StoryTextViewport*);
void story_text_draw_upper(unsigned id,unsigned visible,unsigned page,uint16_t *screen);
/* Ordinary literals switch Chinese/original on X. Split poems always draw
 * their RU/EN plane here; centered Say keeps its Chinese source timeline. */
void story_text_draw_upper_bilingual(unsigned id,unsigned visible,unsigned page,int bilingual,uint16_t *screen);
void story_text_draw_ascii(uint16_t *screen,int x,int y,const char *text,uint16_t color);
void story_text_draw_date(unsigned id,uint16_t *screen);
#endif
