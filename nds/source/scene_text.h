#ifndef ZATO_SCENE_TEXT_H
#define ZATO_SCENE_TEXT_H
#include <stdint.h>

typedef struct {
    uint32_t source_line, char_count, duration_ticks, pause_count;
    uint32_t pages_cn, pages_bi, mode, continuation, new_char_start;
} SceneTextInfo;
typedef struct { uint32_t start_tick, end_tick, glyphs; } SceneTextPause;

/* This module owns its loaded pack. Every draw is CPU-only, with no file I/O.
 * visible is the logical Chinese character count (newlines included).
 * UINT32_MAX is a blank lower window. RGB15 buffers contain 256*192 pixels.
 * Upper draw preserves the existing background, lower draw fills the buffer. */
int scene_text_open(const char *path);
void scene_text_close(void);
const char *scene_text_error(void);
unsigned scene_text_count(void);
const SceneTextInfo *scene_text_info(unsigned id);
const SceneTextPause *scene_text_pause(unsigned id, unsigned index);
unsigned scene_text_visible(unsigned id, uint32_t ticks);
unsigned scene_text_page(unsigned id, unsigned visible, int bilingual);
/* Exclusive logical count at the next Chinese page's first entry. A final
 * Chinese page or any English-only page returns char_count. Invalid id: 0. */
unsigned scene_text_page_limit(unsigned id, unsigned page, int bilingual);
void scene_text_draw_lower(unsigned id, unsigned visible, unsigned page,
                           int bilingual, int window, int quick_menu, uint16_t *screen);
void scene_text_draw_upper(unsigned id, unsigned visible, unsigned page, uint16_t *screen);
void scene_text_draw_ascii(uint16_t *screen, int x, int y, const char *text, uint16_t color);
#endif
