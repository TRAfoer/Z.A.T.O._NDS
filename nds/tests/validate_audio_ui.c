/* Shipping mixer/decoder, hardware-only stubs. Constant IMA blocks below have
 * index zero and zero nibbles: every sample equals its header predictor. */
#include <assert.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../source/ima_adpcm.h"

static const char *fixture_root;
static unsigned calls, forbidden;
static const char *fail_operation;
static int fail_once(const char *operation)
{
    if (!fail_operation || strcmp(fail_operation, operation)) return 0;
    fail_operation = NULL;
    return 1;
}
static void io_call(void) { assert(!forbidden); calls++; }
static FILE *local_open(const char *path, const char *mode)
{
    io_call();
    if (fail_once("open")) return NULL;
    char local[1024];
    if (!strncmp(path, "nitro:/", 7)) {
        snprintf(local, sizeof(local), "%s/%s", fixture_root, path + 7);
        return fopen(local, mode);
    }
    return fopen(path, mode);
}
static size_t local_read(void *p, size_t s, size_t n, FILE *f)
{ io_call(); return fail_once("read") ? 0 : fread(p, s, n, f); }
static int local_close(FILE *f) { io_call(); int rc = fclose(f); return fail_once("close") ? EOF : rc; }
static int local_seek(FILE *f, long offset, int origin)
{ io_call(); return fail_once("seek") ? -1 : fseek(f, offset, origin); }
static int local_decode(const uint8_t *b, unsigned bytes, unsigned ch, int16_t *p, unsigned cap)
{ io_call(); return ima_decode_block(b, bytes, ch, p, cap); }
#define fopen local_open
#define fread local_read
#define fclose local_close
#define fseek local_seek
#define ima_decode_block local_decode
#include "../source/audio_demo.c"
#undef fopen
#undef fread
#undef fclose
#undef fseek
#undef ima_decode_block
mm_stream mock_stream;
_Static_assert(sizeof(AudioStatus) == 88, "Unchanged diagnostic ABI");
_Static_assert(sizeof(AudioSceneSnapshot) == 112, "UI is not persisted");

static void render(unsigned n, int16_t *out)
{
    unsigned before = calls;
    forbidden++;
    assert(mock_stream.callback(n, out, MM_STREAM_16BIT_STEREO) == n);
    forbidden--;
    assert(calls == before);
}
static void play(AudioUiCue cue)
{
    unsigned before = calls;
    forbidden++;
    audio_ui_play(cue);
    forbidden--;
    assert(calls == before);
}
static void expect_constant(int expected, unsigned n)
{
    int16_t output[256 * 2];
    while (n) {
        unsigned count = n > 256 ? 256 : n;
        render(count, output);
        for (unsigned i = 0; i < count * 2; i++) {
            if (output[i] != expected) {
                fprintf(stderr, "sample %u: expected %d, got %d\n", i, expected, output[i]);
                abort();
            }
        }
        n -= count;
    }
}
static unsigned find(const char *name)
{
    for (unsigned i = 0; i < g_audio_status.sfx_count; i++)
        if (!strcmp(entries[g_audio_status.music_count + i].name, name)) return i;
    abort();
}
static void prepare(void)
{
    assert(audio_ui_prepare());
    unsigned before = calls;
    assert(audio_ui_prepare());
    assert(calls == before); /* Cached prepare is zero I/O. */
}
static void setup_plot(void)
{
    assert(audio_scene_play(true, 0, 4096, 0, 41));
    assert(audio_scene_play(false, find("plot"), 3071, 0, 43));
    int16_t output[14];
    render(7, output);
    audio_scene_stop(false, 53);
    audio_scene_pause(true);
}
static void paused(void)
{
    prepare(); setup_plot();
    AudioSceneSnapshot saved, after;
    assert(audio_scene_snapshot(&saved));
    uint32_t mr = music.read, sr = sfx.read;
    uint32_t mf = g_audio_status.music_frames, sf = g_audio_status.sfx_frames;
    uint32_t ms = g_audio_status.music_starts, ss = g_audio_status.sfx_starts;
    unsigned index = g_audio_status.sfx_index;
    const unsigned gains[] = {512, 1536, 1024, 512};
    for (unsigned cue = 0; cue < 4; cue++) {
        play((AudioUiCue)cue);
        expect_constant(12000 * (int)gains[cue] / 4096, 17);
        assert(audio_scene_snapshot(&after));
        assert(!memcmp(&saved, &after, sizeof(saved)));
        assert(music.read == mr && sfx.read == sr);
        assert(g_audio_status.music_frames == mf && g_audio_status.sfx_frames == sf);
        assert(g_audio_status.music_starts == ms && g_audio_status.sfx_starts == ss);
        assert(g_audio_status.sfx_index == index);
    }
    assert(!g_audio_status.errors && !g_audio_status.music_underruns && !g_audio_status.sfx_underruns);
}
static void controls(void)
{
    prepare(); scene_mode = scene_paused = true;
    play(AUDIO_UI_MOVE);
    assert(ui_voice.cursor == 2205);
    expect_constant(1500, 1128); /* 846 source frames at 3/4 speed. No delay. */
    for (unsigned block = 3; block < 29; block++)
        expect_constant(block % 2 ? -2000 : 1500, 1356);
    expect_constant(-2000, 191); /* ceil(143 * 4/3), true tail only. */
    assert(!ui_voice.active && ui_voice.cursor == 29636);
    expect_constant(0, 25);
    play(AUDIO_UI_CONFIRM);
    expect_constant(4500, 1017);
    expect_constant(-6000, 1017);
    for (unsigned block = 2; block < 29; block++)
        expect_constant(block % 2 ? -6000 : 4500, 1017);
    expect_constant(-6000, 143); /* 29636 - 29*1017. */
    expect_constant(0, 25);
    play(AUDIO_UI_CONFIRM); expect_constant(4500, 10);
    play(AUDIO_UI_MOVE); expect_constant(1500, 10); /* Replacement, no overlap. */
    play(AUDIO_UI_MOVE); /* Held-key repeats immediately restart one voice. */
    assert(ui_voice.cursor == 2205);
    expect_constant(1500, 1);
    play((AudioUiCue)4); play((AudioUiCue)-1);
    expect_constant(1500, 1); /* Invalid enum cannot disturb the active cue. */
    audio_ui_stop(); expect_constant(0, 10);
    audio_scene_volumes(4096, 0);
    play(AUDIO_UI_MOVE); expect_constant(0, 36575);
    assert(!ui_voice.active && ui_voice.cursor == 29636);
    audio_scene_volumes(4096, 4096); expect_constant(0, 10);
    audio_scene_volumes(4096, 2048);
    play(AUDIO_UI_BACK); expect_constant(1500, 2);
    audio_ui_stop();
}
static void mixed(void)
{
    prepare();
    assert(audio_scene_play(true, 0, 4096, 0, 0));
    assert(audio_scene_play(false, find("plot"), 4096, 0, 0));
    play(AUDIO_UI_MOVE);
    int16_t out[4]; render(2, out);
    const int16_t want[] = {19500, -500, 19500, -500};
    assert(!memcmp(want, out, sizeof(want)));
    /* Gains exceed the 32-bit multiply path. UI joins BEFORE final clip:
     * -32768*100000/4096 + 32767*100000/4096 = -25; UI adds +1500. */
    music.ring[(music.read & (MUSIC_FRAMES - 1)) * 2] = -32768;
    music.ring[(music.read & (MUSIC_FRAMES - 1)) * 2 + 1] = 32767;
    sfx.ring[sfx.read & (SFX_FRAMES - 1)] = 32767;
    music.gain_q12 = sfx.gain_q12 = 100000;
    render(1, out);
    assert(out[0] == 1475 && out[1] == 32767);
    /* Opposite-sign UI must be allowed to cancel an over-range plot sum. */
    audio_scene_stop(true, 0); audio_scene_stop(false, 0);
    assert(audio_scene_play(true, 0, 11000, 0, 0));
    play(AUDIO_UI_MOVE);
    int16_t scratch[1128 * 2]; render(1128, scratch);
    music.ring[(music.read & (MUSIC_FRAMES - 1)) * 2] = 12000;
    music.ring[(music.read & (MUSIC_FRAMES - 1)) * 2 + 1] = -8000;
    render(1, out);
    assert(out[0] == 30226 && out[1] == -23484);
}
static void restore(void)
{
    prepare(); setup_plot();
    AudioSceneSnapshot saved;
    assert(audio_scene_snapshot(&saved));
    play(AUDIO_UI_MOVE);
    assert(audio_scene_restore(&saved));
    assert(scene_paused); expect_constant(0, 2);
    unsigned before = calls;
    assert(audio_ui_prepare() && before == calls);
    play(AUDIO_UI_MOVE); expect_constant(1500, 2);
    /* File mode forbids UI even with a warm cache; restore also leaves a
     * queued UI cue silent before the newly-opened stream's initial prefill. */
    assert(audio_scene_play_file("audio/clip.wav", 5050, 4096, 0, 0));
    assert(file_clock.active);
    before = calls;
    assert(!audio_ui_prepare()); play(AUDIO_UI_CONFIRM);
    expect_constant(0, 2); assert(calls == before);
    audio_scene_pause(false);
    int16_t output[2]; render(1, output);
    assert(output[0] == 12000 && output[1] == -8000); /* No button overlay. */
    audio_scene_stop_file(); expect_constant(0, 2); /* No stale UI revival. */
    before = calls;
    assert(audio_ui_prepare() && before == calls);
    play(AUDIO_UI_MOVE); expect_constant(1500, 2);
}
static void unavailable(void)
{
    uint32_t errors = g_audio_status.errors;
    assert(!audio_ui_prepare());
    assert(g_audio_status.errors == errors && g_audio_status.initialized);
    assert(audio_scene_play(true, 0, 4096, 0, 0));
    int16_t out[2]; play(AUDIO_UI_MOVE); render(1, out);
    assert(out[0] == 12000 && out[1] == -8000);
}
static void short_button(void)
{
    prepare(); scene_mode = scene_paused = true;
    play(AUDIO_UI_MOVE); expect_constant(0, 2); assert(!ui_voice.active);
    play(AUDIO_UI_CONFIRM); expect_constant(4500, 2); /* Original start remains usable. */
}
static void io_failure(const char *failure)
{
    fail_operation = failure;
    unavailable();
    assert(!fail_operation);
    prepare(); /* A failed optional preparation does not poison later retry. */
    audio_scene_pause(true); play(AUDIO_UI_MOVE); expect_constant(1500, 1);
}
static void reinitialize(const char *next_root)
{
    prepare(); scene_mode = scene_paused = true;
    play(AUDIO_UI_MOVE); expect_constant(1500, 1);
    fixture_root = next_root;
    assert(audio_demo_init());
    for (unsigned i = 0; i < sizeof(mock_stream_prefill) / sizeof(mock_stream_prefill[0]); i++)
        assert(mock_stream_prefill[i] == 0);
    assert(!audio_ui_prepare()); /* The new catalog has no button entry. */
    play(AUDIO_UI_CONFIRM); expect_constant(0, 2);
}
static void approved(const char *button_path, const char *move_path)
{
    prepare(); scene_mode = scene_paused = true;
    const char *paths[] = {move_path, button_path, button_path, button_path};
    const AudioUiCue cues[] = {AUDIO_UI_MOVE, AUDIO_UI_CONFIRM, AUDIO_UI_BACK, AUDIO_UI_ERROR};
    const int gains[] = {4096, 1536, 1024, 512};
    for (unsigned c = 0; c < 4; c++) {
        FILE *f = fopen(paths[c], "rb"); assert(f);
        unsigned frames = c ? 29636 : 36575;
        play(cues[c]);
        for (unsigned i = 0; i < frames; i++) {
            int16_t reference, out[2];
            assert(fread(&reference, 2, 1, f) == 1);
            render(1, out);
            int expected = reference * gains[c] / 4096;
            assert(out[0] == expected && out[1] == expected);
        }
        assert(fgetc(f) == EOF); fclose(f); expect_constant(0, 3);
    }
}
int main(int argc, char **argv)
{
    assert(argc >= 3); fixture_root = argv[2];
    assert(!audio_ui_prepare());
    assert(audio_demo_init());
    if (!strcmp(argv[1], "paused")) paused();
    else if (!strcmp(argv[1], "controls")) controls();
    else if (!strcmp(argv[1], "mix")) mixed();
    else if (!strcmp(argv[1], "restore")) restore();
    else if (!strcmp(argv[1], "unavailable")) unavailable();
    else if (!strcmp(argv[1], "short")) short_button();
    else if (!strcmp(argv[1], "io_failure")) { assert(argc == 4); io_failure(argv[3]); }
    else if (!strcmp(argv[1], "reinit")) { assert(argc == 4); reinitialize(argv[3]); }
    else if (!strcmp(argv[1], "approved")) { assert(argc == 5); approved(argv[3], argv[4]); }
    else abort();
    puts("audio UI: PASS");
    return 0;
}
