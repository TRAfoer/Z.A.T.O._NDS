#ifndef ZATO_STORY_SAVE_H
#define ZATO_STORY_SAVE_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define STORY_SAVE_BYTES 524288u
#define STORY_SAVE_SLOT_COUNT 116u
#define STORY_SAVE_MANUAL_COUNT 96u
#define STORY_SAVE_AUTO_FIRST 96u
#define STORY_SAVE_QUICK_FIRST 106u
#define STORY_SAVE_RING_COUNT 10u
#define STORY_SAVE_CHECKPOINT_BYTES 768u
#define STORY_SAVE_THUMB_BYTES 3072u
#define STORY_SAVE_SEEN_BYTES 1494u
typedef struct {
    bool valid;
    uint32_t seq,unix_time,play_seconds,chapter,text_id,page,bilingual;
    char caption[96];
} StorySaveMeta;
typedef struct {
    uint8_t seen[STORY_SAVE_SEEN_BYTES];
    uint32_t unlocks[2],language,music_q12,sfx_q12,play_ticks;
    /* FORMAT1 global offset 1572 was zero-reserved: old saves read disabled.
     * Append in RAM as well so every existing member keeps its offset. */
    uint32_t chapter_skip; /* 0=disabled (default), 1=enabled */
} StorySaveGlobals;
/* Single main-thread store. Identity is SHA256 of story/script.bin, not ROM.
 * A missing/empty/all-zero file may initialize; foreign/corrupt files never do.
 * An empty slot has info()==true and meta.valid==false. Corruption returns false.
 * Every mutating operation checks write/sync/close and reports no false success. */
bool story_save_open(const char *path,const uint8_t story_id[32]);
void story_save_close(void);
bool story_save_available(void);
const char *story_save_error(void);
const char *story_save_path(void);
/* Always returns RAM preferences, including when persistence is unavailable.
 * Check available()/commit_globals() to know whether they can reach .sav. */
StorySaveGlobals *story_save_globals(void);
bool story_save_commit_globals(void);
bool story_save_info(unsigned slot,StorySaveMeta *meta);
bool story_save_read(unsigned slot,StorySaveMeta *meta,uint8_t checkpoint[768],uint8_t thumb[3072]);
bool story_save_write(unsigned slot,const StorySaveMeta *meta,const uint8_t checkpoint[768],const uint8_t thumb[3072]);
bool story_save_delete(unsigned slot);
/* Newest first; returns 0 on an I/O/corruption error (see error()). */
unsigned story_save_recent(unsigned first,unsigned count,unsigned *slots,unsigned capacity);
/* First empty, otherwise oldest generation. UINT32_MAX on error. */
unsigned story_save_ring_slot(unsigned first,unsigned count);
#endif
