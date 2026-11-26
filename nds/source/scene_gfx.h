#ifndef ZATO_SCENE_GFX_H
#define ZATO_SCENE_GFX_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Fully resident CPU compositor. ticks are 60 Hz relative to the group show.
 * The output is opaque A1B5G5R5, 256x192, with the stage at (0,24).
 * open is the only function that reads files; it services audio each <=4 KiB.
 * red/hands share their phase origin, maintained by the caller. */
bool scene_gfx_open(const char *path);
bool scene_gfx_select(unsigned group);
bool scene_gfx_draw(unsigned group, uint32_t ticks, uint16_t *screen256x192);
void scene_gfx_close(void);
const char *scene_gfx_error(void);
size_t scene_gfx_resident_bytes(void);
uint32_t scene_gfx_read_calls(void);
unsigned scene_gfx_frame(unsigned group, uint32_t ticks);
#endif
