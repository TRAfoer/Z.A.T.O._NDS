#if defined(ZATO_STORY_DEMO)
#include "story_runtime.h"
int main(int argc,char **argv) { return story_demo_main(argc,argv); }
#elif defined(ZATO_SCENE_DEMO)
#include "scene_runtime.h"
int main(void) { return scene_demo_main(); }
#else
#include <nds.h>
#ifndef ZATO_NITROFS
#include "assets_bin.h"
#endif
#include <stdio.h>
#include <string.h>
#include "asset_pack.h"
#include "audio_demo.h"
#if defined(ZATO_ADAPTIVE) || defined(ZATO_NITROFS)
#include <filesystem.h>
#endif

typedef struct {
    uint32_t magic, frames, ready, stage, actor, visible, stage_id, actor_id;
    uint32_t assets, stages, actors, errors, stage_hash, actor_hash, loads, reserved;
    uint32_t mount_attempted, mount_ok, fallbacks, fallback_reason;
} DemoStatus;
volatile DemoStatus g_demo_status;

static AssetPack pack;
// Keep only the current decompressed dictionary in main RAM. Uploaded layers
// remain independent in VRAM; the default NitroFS build has no embedded pack.
static BankCache asset_cache = {.id = -1};
static uint16_t transfer_pixels[256 * 192] __attribute__((aligned(32)));
static int stage_bg, actor_bg;
typedef struct { uint32_t repeat, down; } InputEvent;
static InputEvent input_queue[32];
static volatile uint32_t input_write, input_read;
volatile uint32_t g_input_drops;

static void input_vblank(void)
{
    scanKeys();
    InputEvent event = {.repeat = keysDownRepeat(), .down = keysDown()};
    if (!event.repeat && !event.down) return;
    uint32_t write = input_write;
    if (write - input_read == 32) { g_input_drops++; return; }
    input_queue[write & 31] = event;
    __asm__ volatile("" ::: "memory");
    input_write = write + 1;
}

static InputEvent input_next(void)
{
    uint32_t read = input_read;
    if (read == input_write) return (InputEvent){0, 0};
    InputEvent event = input_queue[read & 31];
    __asm__ volatile("" ::: "memory");
    input_read = read + 1;
    return event;
}

static const char *basename_of(const char *name)
{
    const char *slash = strrchr(name, '/');
    return slash ? slash + 1 : name;
}

static void show_status(void)
{
    const PackFrame *stage = &pack.frames[g_demo_status.stage_id];
    const PackFrame *actor = &pack.frames[g_demo_status.actor_id];
    consoleClear();
    printf("ZATO / ASSET + AUDIO LAB\n");
    printf("LEFT/RIGHT  Previous/next scene\n");
    printf("A/B Actor    R Actor ON/OFF\n");
    printf("L Music ON/OFF\n");
    printf("UP/DOWN Song    X Next SFX\n\n");
    printf("SCENE %lu/%lu  [%s]\n", (unsigned long)g_demo_status.stage + 1,
           (unsigned long)g_demo_status.stages, stage->category == 0 ? "BG" : "CG");
    printf("%.30s\n", basename_of(stage->name));
    printf("%ux%u / centered\n", stage->width, stage->height);
    printf("ACTOR %lu/%lu  [%s]\n", (unsigned long)g_demo_status.actor + 1,
           (unsigned long)g_demo_status.actors, g_demo_status.visible ? "ON" : "OFF");
    const char *short_name = actor->name;
    if (!strncmp(short_name, "images/sprites/", 15)) short_name += 15;
    printf("%.30s\n", short_name);
    printf("%ux%u / 2x2 conversion\n\n", actor->width, actor->height);
    if (g_audio_status.initialized) {
        printf("MUSIC %lu/%lu [%s]\n", (unsigned long)g_audio_status.music_index + 1,
               (unsigned long)g_audio_status.music_count, g_audio_status.music_playing ? "PLAY" : "STOP");
        printf("%.30s\n", audio_demo_music_name());
        unsigned long number = g_audio_status.sfx_index < g_audio_status.sfx_count ? g_audio_status.sfx_index + 1 : 0;
        printf("SFX %lu/%lu [%s]\n", number, (unsigned long)g_audio_status.sfx_count,
               g_audio_status.sfx_playing ? "PLAY" : "READY");
        printf("%.30s\n", audio_demo_sfx_name());
        printf("22k stereo music / mono SFX\n");
        if (g_audio_status.errors) printf("Audio errors: %lu\n", (unsigned long)g_audio_status.errors);
    }
#ifdef ZATO_NITROFS
    printf("Resources: NitroFS\n");
#elif defined(ZATO_ADAPTIVE)
    printf("Resources: %s\n", g_demo_status.reserved == 2 ? "NitroFS" : "RAM fallback");
#else
    printf("Resources: embedded RAM\n");
#endif
}

static void error_screen(const char *context)
{
    g_demo_status.errors++;
    consoleClear();
    printf("ZATO ASSET LAB - ERROR\n\n%s\n%s\n", context, pack.error);
    while (1) swiWaitForVBlank();
}

#ifdef ZATO_ADAPTIVE
static void fallback_memory(unsigned reason)
{
    bank_cache_clear(&asset_cache);
    pack_use_memory(&pack);
    if (g_demo_status.mount_ok) nitroFSExit();
    g_demo_status.reserved = 1;
    g_demo_status.fallbacks++;
    g_demo_status.fallback_reason = reason;
}
#endif

static bool draw_asset(unsigned id, uint32_t *hash)
{
    if (pack_draw(&pack, &asset_cache, id, transfer_pixels, hash)) return true;
#ifdef ZATO_ADAPTIVE
    if (g_demo_status.reserved == 2) {
        fallback_memory(3);
        return pack_draw(&pack, &asset_cache, id, transfer_pixels, hash);
    }
#endif
    return false;
}

static void load_layers(bool scenery, bool actor)
{
    audio_demo_service();
    g_demo_status.ready = 0;
    uint32_t hash;
    if (scenery) {
        g_demo_status.stage_id = pack.stages[g_demo_status.stage];
        if (!draw_asset(g_demo_status.stage_id, &hash))
            error_screen("Scenery decode");
        g_demo_status.stage_hash = hash;
        g_demo_status.loads++;
        DC_FlushRange(transfer_pixels, sizeof(transfer_pixels));
        swiWaitForVBlank();
        dmaCopy(transfer_pixels, bgGetGfxPtr(stage_bg), sizeof(transfer_pixels));
    }
    if (actor) {
        g_demo_status.actor_id = pack.actors[g_demo_status.actor];
        if (!draw_asset(g_demo_status.actor_id, &hash))
            error_screen("Actor decode");
        g_demo_status.actor_hash = hash;
        g_demo_status.loads++;
        DC_FlushRange(transfer_pixels, sizeof(transfer_pixels));
        swiWaitForVBlank();
        dmaCopy(transfer_pixels, bgGetGfxPtr(actor_bg), sizeof(transfer_pixels));
    }
    // Only the changed hardware layer is uploaded. The other layer stays in VRAM.
    swiWaitForVBlank();
    if (g_demo_status.visible) bgShow(actor_bg); else bgHide(actor_bg);
    bgUpdate();
    show_status();
    g_demo_status.ready = 1;
}

int main(void)
{
    powerOn(POWER_ALL_2D);
    lcdMainOnTop();
    videoSetMode(MODE_5_2D);
    videoSetModeSub(MODE_0_2D);
    vramSetBankA(VRAM_A_MAIN_BG_0x06000000);
    vramSetBankB(VRAM_B_MAIN_BG_0x06020000);
    vramSetBankC(VRAM_C_SUB_BG);
    consoleInit(NULL, 0, BgType_Text4bpp, BgSize_T_256x256, 31, 0, false, true);
    printf("ZATO Asset Lab\nLoading packed resources...\n");
    stage_bg = bgInit(2, BgType_Bmp16, BgSize_B16_256x256, 0, 0);
    actor_bg = bgInit(3, BgType_Bmp16, BgSize_B16_256x256, 8, 0);
    bgSetPriority(stage_bg, 2);
    bgSetPriority(actor_bg, 1);
    setBackdropColor(RGB15(0, 0, 0));
    dmaFillHalfWords(0, bgGetGfxPtr(stage_bg), 128 * 1024);
    dmaFillHalfWords(0, bgGetGfxPtr(actor_bg), 128 * 1024);
    g_demo_status.magic = 0x4f54415a; // ZATO
#ifdef ZATO_NITROFS
    printf("Mounting NitroFS...\n");
    g_demo_status.mount_attempted = 1;
    if (!nitroFSInit(NULL)) {
        snprintf(pack.error, sizeof(pack.error), "NitroFS mount failed");
        error_screen("NitroFS initialization");
    }
    g_demo_status.mount_ok = 1;
    if (!pack_open(&pack, "nitro:/assets.zat")) error_screen("NitroFS asset package");
    g_demo_status.reserved = 2;
#else
    if (!pack_open_memory(&pack, assets_bin, assets_bin_size)) error_screen("Embedded asset package");
    g_demo_status.reserved = 1; // Embedded, filesystem-free resource backend.
#ifdef ZATO_ADAPTIVE
    printf("Trying NitroFS...\n");
    g_demo_status.mount_attempted = 1;
    if (nitroFSInit(NULL)) {
        g_demo_status.mount_ok = 1;
        if (pack_try_file(&pack, "nitro:/assets.zat")) g_demo_status.reserved = 2;
        else fallback_memory(2);
    } else fallback_memory(1);
#endif
#endif
    #ifdef ZATO_NITROFS
    if (!audio_demo_init()) {
        snprintf(pack.error, sizeof(pack.error), "Cannot initialize audio catalog/stream");
        error_screen("Audio initialization");
    }
    #endif
    g_demo_status.assets = pack.header.frames;
    g_demo_status.stages = pack.header.stages;
    g_demo_status.actors = pack.header.actors;
    g_demo_status.stage = pack.header.default_stage;
    g_demo_status.actor = pack.header.default_actor;
    g_demo_status.visible = 1;
    keysSetRepeat(24, 8);
    irqSet(IRQ_VBLANK, input_vblank);
    irqEnable(IRQ_VBLANK);
    load_layers(true, true);
    g_demo_status.ready = 1;
    uint32_t audio_ui_version = g_audio_status.ui_version;
    while (1) {
        audio_demo_service();
        swiWaitForVBlank();
        g_demo_status.frames++;
        InputEvent input = input_next();
        uint32_t keys = input.repeat;
        uint32_t down = input.down;
        bool stage_changed = false, actor_changed = false, toggle = false;
        if (keys & KEY_RIGHT) { g_demo_status.stage = (g_demo_status.stage + 1) % g_demo_status.stages; stage_changed = true; }
        else if (keys & KEY_LEFT) { g_demo_status.stage = (g_demo_status.stage + g_demo_status.stages - 1) % g_demo_status.stages; stage_changed = true; }
        if (keys & KEY_A) { g_demo_status.actor = (g_demo_status.actor + 1) % g_demo_status.actors; actor_changed = true; }
        else if (keys & KEY_B) { g_demo_status.actor = (g_demo_status.actor + g_demo_status.actors - 1) % g_demo_status.actors; actor_changed = true; }
        if (down & KEY_R) { g_demo_status.visible ^= 1; toggle = true; }
        if (down & KEY_L) audio_demo_toggle_music();
        if (keys & KEY_DOWN) audio_demo_change_music(1);
        else if (keys & KEY_UP) audio_demo_change_music(-1);
        if (down & KEY_X) audio_demo_next_sfx();
        if (stage_changed || actor_changed) load_layers(stage_changed, actor_changed);
        else if (toggle) {
            if (g_demo_status.visible) bgShow(actor_bg); else bgHide(actor_bg);
            bgUpdate();
            show_status();
        }
        if (audio_ui_version != g_audio_status.ui_version) {
            audio_ui_version = g_audio_status.ui_version;
            show_status();
        }
    }
    return 0;
}
#endif
