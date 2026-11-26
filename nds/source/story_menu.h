#ifndef ZATO_STORY_MENU_H
#define ZATO_STORY_MENU_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Independent UI pack/fonts. No save/audio/gfx/runtime calls from this module.
 * Input down bits match libnds KEY_*; caller supplies actual edge/repeat bits.
 * Slots are views owned by caller; thumbnails remain valid through draw(). */
typedef enum {
    STORY_MENU_TITLE,STORY_MENU_GAME,STORY_MENU_SAVE,STORY_MENU_LOAD,
    STORY_MENU_GALLERY,STORY_MENU_SETTINGS,STORY_MENU_CREDITS,
    STORY_MENU_CONTROLS,STORY_MENU_CONFIRM,STORY_MENU_MESSAGE,STORY_MENU_QUICK
} StoryMenuMode;
typedef enum {
    STORY_MENU_NONE,STORY_MENU_NEW_GAME,STORY_MENU_RESUME,
    STORY_MENU_OPEN_SAVE,STORY_MENU_OPEN_LOAD,STORY_MENU_OPEN_GALLERY,
    STORY_MENU_OPEN_SETTINGS,STORY_MENU_OPEN_CREDITS,STORY_MENU_OPEN_CONTROLS,
    STORY_MENU_OPEN_HISTORY,STORY_MENU_RETURN_TITLE,STORY_MENU_QUIT,
    STORY_MENU_SAVE_SLOT,STORY_MENU_LOAD_SLOT,STORY_MENU_DELETE_SLOT,
    STORY_MENU_PAGE_CHANGED,STORY_MENU_SETTINGS_CHANGED,
    STORY_MENU_GALLERY_SELECT,STORY_MENU_BACK,STORY_MENU_CANCEL,
    STORY_MENU_OPEN_DEBUG,STORY_MENU_TOGGLE_SKIP,STORY_MENU_TOGGLE_AUTO,
    STORY_MENU_TOGGLE_LANGUAGE,STORY_MENU_QUICK_SAVE,STORY_MENU_QUICK_LOAD,
    STORY_MENU_CLOSE
} StoryMenuActionKind;
typedef enum {
    STORY_MENU_PROMPT_NONE,STORY_MENU_PROMPT_OVERWRITE,
    STORY_MENU_PROMPT_LOAD,STORY_MENU_PROMPT_DELETE,STORY_MENU_PROMPT_TITLE,
    STORY_MENU_PROMPT_QUIT,STORY_MENU_PROMPT_SAVED,STORY_MENU_PROMPT_ERROR,
    STORY_MENU_PROMPT_SHUTDOWN
} StoryMenuPrompt;
enum {
    STORY_MENU_KEY_A=1,STORY_MENU_KEY_B=2,STORY_MENU_KEY_SELECT=4,STORY_MENU_KEY_START=8,
    STORY_MENU_KEY_RIGHT=16,STORY_MENU_KEY_LEFT=32,
    STORY_MENU_KEY_UP=64,STORY_MENU_KEY_DOWN=128,
    STORY_MENU_KEY_R=256,STORY_MENU_KEY_L=512,
    STORY_MENU_KEY_X=1024,STORY_MENU_KEY_Y=2048,STORY_MENU_KEY_TOUCH=4096,
    STORY_MENU_PAGE_AUTO=0,STORY_MENU_PAGE_QUICK=17,
    STORY_MENU_SLOT_EMPTY=0,STORY_MENU_SLOT_VALID=1,STORY_MENU_SLOT_INVALID=2,
    STORY_MENU_QUICK_Y=156,STORY_MENU_QUICK_COLLAPSED_Y=174,
    STORY_MENU_QUICK_ROW_HEIGHT=18,STORY_MENU_QUICK_TEXT_PADDING=3,
    STORY_MENU_QUICK_PAGES=3,STORY_MENU_QUICK_ITEMS=4
};
/* value: actual slot ID for SAVE/LOAD/DELETE; gallery table index for
 * GALLERY_SELECT (resolve via story_menu_gallery); page for PAGE_CHANGED. */
typedef struct {StoryMenuActionKind kind;int value;} StoryMenuAction;
typedef struct {uint32_t down;bool touch;uint16_t x,y;} StoryMenuInput;
typedef struct {
    uint32_t slot_id,status,unix_time,chapter,text_id,page;
    const uint8_t *thumbnail; /* Exactly 64*48 RGB332 bytes, NULL if unavailable. */
} StoryMenuSlot;
typedef struct {
    StoryMenuMode mode,return_mode;
    unsigned selection,page,focus,scroll; /* page: 0=A,1..16 manual,17=Q. */
    bool in_game,bilingual;
    unsigned music_volume,sfx_volume; /* UI percentages, each 0..100. */
    uint32_t unlock_low,unlock_high;
    StoryMenuSlot slots[6];
    const uint16_t *gallery_preview; /* Optional full 256*192 RGB15, caller-owned. */
    StoryMenuPrompt prompt;
    StoryMenuAction confirm_action;
    char message[96]; /* Optional ASCII error detail, NUL-terminated by caller. */
    unsigned return_selection,return_focus; /* Prompt preserves underlying UI. */
    const uint16_t *game_preview; /* Frozen full 256x192; must not alias output.
     * GAME/SETTINGS display it; CREDITS/CONTROLS keep text on lower with scroll. */
    const uint16_t *game_lower_preview; /* GAME dimmed background; QUICK overlay. */
    unsigned quick_page; /* 0..2; selection is page-local0..3 (last page0..1). */
    bool chapter_skip; /* Settings row3; default false, persisted by caller. */
} StoryMenuState;

bool story_menu_open(const char *path);
void story_menu_close(void);
const char *story_menu_error(void);
size_t story_menu_resident_bytes(void);
uint32_t story_menu_read_calls(void);
const uint8_t *story_menu_identity(void); /* 32-byte SHA256 of story/script.bin. */
void story_menu_init(StoryMenuState *state,StoryMenuMode mode,bool in_game);
void story_menu_confirm(StoryMenuState *state,StoryMenuPrompt prompt,
                        StoryMenuActionKind action,int value);
void story_menu_message(StoryMenuState *state,StoryMenuPrompt prompt,
                        const char *ascii_detail);
StoryMenuAction story_menu_input(StoryMenuState *state,StoryMenuInput input);
/* ticks animates original-pattern title snow; no file I/O during draw/input. */
bool story_menu_draw(const StoryMenuState *state,uint32_t ticks,
                     uint16_t *upper256x192,uint16_t *lower256x192);
/* Static UI ignores ticks. Title snow draws at ticks rounded down to 4 ticks.
 * Caller also invalidates after externally mutating thumbnail/preview bytes. */
uint32_t story_menu_version(const StoryMenuState *state,uint32_t ticks);
/* Collapsed: only x4..251,y174..191 changes; touch in that box or SELECT opens.
 * Expanded: x4..251,y156..191; four62px cells in y156..173. Bottom y174..191
 * has previous page x4..65, next page x190..251, middle is inert page label.
 * Each row is18px;12px text starts at row top+3 (items159, nav/collapsed177).
 * QUICK input consumes outside touch/SELECT/B/other non-menu keys as CLOSE.
 * Menu keys: LEFT/RIGHT selection, L/R pages, A execute; all pages font12.
 * QUICK OPEN_HISTORY value0=original Return,1=Log (both old runtime history).
 * Caller owns pause/ACK and MUST consume CLOSE input, never forward it. */
bool story_menu_draw_bar(const StoryMenuState *state,bool expanded,uint16_t *lower);
bool story_menu_bar_hit(unsigned x,unsigned y);
/* Credits/Controls: scroll remains a zero-based ROW offset. UP/DOWN scroll
 * one row; L/R or LEFT/RIGHT move a viewport, clamped to max(0,rows-capacity).
 * Font12,14px pitch:10 rows with game_preview,20 rows across both screens
 * from Title. Counter uses the last page covered by the viewport:
 * min(1+(scroll+capacity-1)/capacity,total)/ceil(rows/capacity). */
unsigned story_menu_text_page_count(const StoryMenuState *state);
unsigned story_menu_gallery_count(void);
bool story_menu_gallery(unsigned index,unsigned *unlock_bit,unsigned *frame_id);
#endif
