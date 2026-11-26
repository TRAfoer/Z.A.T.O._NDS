// Execute the shipping decoder, rings and callback; only hardware/path I/O is stubbed.
#include <assert.h>
#include <limits.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *fixture_root;
static int in_callback;
static size_t file_bytes_read;
static FILE *fixture_fopen(const char *path, const char *mode)
{
    assert(!in_callback);
    char local[1024];
    if (!strncmp(path, "nitro:/", 7)) {
        snprintf(local, sizeof(local), "%s/%s", fixture_root, path + 7);
        return fopen(local, mode);
    }
    return fopen(path, mode);
}
static int fixture_fclose(FILE *file) { assert(!in_callback); return fclose(file); }
static size_t fixture_fread(void *data, size_t size, size_t count, FILE *file)
{
    assert(!in_callback);
    size_t read = fread(data, size, count, file);
    file_bytes_read += read * size;
    return read;
}
static int fixture_fseek(FILE *file, long offset, int origin)
{ assert(!in_callback); return fseek(file, offset, origin); }
#define fopen fixture_fopen
#define fclose fixture_fclose
#define fread fixture_fread
#define fseek fixture_fseek
#include "../source/audio_demo.c"
#undef fopen
#undef fclose
#undef fread
#undef fseek
mm_stream mock_stream;

_Static_assert(sizeof(AudioStatus) == 22 * sizeof(uint32_t), "Legacy status ABI");
_Static_assert(offsetof(AudioStatus, output_peak) == 21 * sizeof(uint32_t), "Legacy status ABI tail");

static void render(unsigned frames, int16_t *out)
{
    in_callback = 1;
    assert(mock_stream.callback(frames, out, MM_STREAM_16BIT_STEREO) == frames);
    in_callback = 0;
}
static void expect_frames(const int16_t *expected, unsigned frames)
{
    int16_t out[128];
    assert(frames <= 64);
    render(frames, out);
    for (unsigned i = 0; i < frames * 2; ++i) {
        if (out[i] != expected[i]) {
            fprintf(stderr, "sample %u: got %d, expected %d\n", i, out[i], expected[i]);
            abort();
        }
    }
}
static void expect_silence(unsigned frames)
{
    const int16_t silence[128] = {0};
    expect_frames(silence, frames);
}

static void synthetic(void)
{
    /* Save consumes no decoder state, preserves a fractional envelope exactly,
     * and resumes intro/loop positions without replaying prefetched ring data. */
    int16_t replay[80],restored[80];AudioSceneSnapshot saved,after,bad;
    audio_scene_volumes(4096,4096);
    assert(audio_scene_play(true,0,4096,1398,7));
    assert(audio_scene_play(false,2,4096,100,5));
    render(3,replay);audio_scene_stop(false,11);render(2,replay);
    audio_scene_pause(true);g_audio_status.music_frames=123456;
    assert(audio_scene_snapshot(&saved));assert(audio_scene_snapshot_valid(&saved));
    assert(saved.music.offset_frames==512&&saved.sfx.offset_frames==105);
    assert(saved.music.fade_left==2&&saved.sfx.fade_left==9);
    expect_silence(3);assert(audio_scene_snapshot(&after));assert(!memcmp(&saved,&after,sizeof(saved)));
    audio_scene_pause(false);render(40,replay);
    assert(audio_scene_restore(&saved));assert(scene_paused);expect_silence(4);
    audio_scene_pause(false);render(40,restored);assert(!memcmp(replay,restored,sizeof(replay)));
    audio_scene_pause(true);assert(audio_scene_snapshot(&saved));bad=saved;bad.music.index=999;
    assert(!audio_scene_snapshot_valid(&bad)&&!audio_scene_restore(&bad));
    assert(audio_scene_snapshot(&after));assert(!memcmp(&saved,&after,sizeof(saved)));
    bad=saved;bad.music.fade_left=bad.music.fade_total+1;assert(!audio_scene_snapshot_valid(&bad));
    bad=saved;bad.master_music=4097;assert(!audio_scene_snapshot_valid(&bad));
    bad=saved;bad.music.flags=4;assert(!audio_scene_snapshot_valid(&bad));
    bad=saved;bad.music.flags|=AUDIO_SCENE_FADE_STOP;assert(!audio_scene_snapshot_valid(&bad));
    assert(!audio_scene_snapshot_valid(NULL)&&!audio_scene_restore(NULL));
    audio_scene_stop(true,0);audio_scene_stop(false,0);audio_scene_pause(false);
    assert(audio_scene_play(true,0,4096,0,0));audio_scene_volumes(2048,0);
    const int16_t half_master[]={6000,-4000};expect_frames(half_master,1);
    assert(audio_scene_play(false,0,4096,0,0));expect_frames(half_master,1);
    audio_scene_volumes(9999,9999);assert(audio_scene_play(true,0,INT_MAX,0,UINT32_MAX));
    render(5,replay);audio_scene_pause(true);assert(audio_scene_snapshot(&saved));
    assert(saved.master_music==4096&&saved.master_sfx==4096&&audio_scene_snapshot_valid(&saved));
    saved.paused=0;assert(audio_scene_restore(&saved)&&scene_paused);
    audio_scene_stop(true,0);audio_scene_stop(false,0);audio_scene_pause(false);
    audio_scene_volumes(4096,4096);

    // A scene start must not decode a 1.5s full ring before returning to video.
    // This fixture's partial-block loop needs <=16 reads for 4096 ready frames.
    int16_t skipped[2048 * 2];
    for (unsigned kind = 0; kind < 2; ++kind) {
        bool is_music = kind == 0;
        Voice *v = is_music ? &music : &sfx;
        size_t reads_before = file_bytes_read;
        assert(audio_scene_play(is_music, is_music ? 0 : 2, 4096, 0, 0));
        assert(v->write - v->read == 4096);
        assert(file_bytes_read - reads_before <= 16 * 512);
        reads_before = file_bytes_read;
        uint32_t write_before = v->write;
        for (unsigned idle = 0; idle < 16; ++idle) audio_demo_service();
        assert(file_bytes_read == reads_before && v->write == write_before);
        render(256, skipped);
        audio_demo_service();
        assert(v->write - write_before == 256 && v->write - v->read == 4096);
        render(2048, skipped);
        write_before = v->write;
        audio_demo_service();
        assert(v->write - write_before == 1024);
        audio_demo_service();
        assert(v->write - v->read == 4096);
        audio_scene_stop(is_music, 0);
    }

    // Q12 is the final gain: no implicit 0.5 or overlap-dependent gain.
    assert(audio_scene_play(true, 0, 2048, 0, 4));
    const int16_t fade_head[] = {0,0,1500,-1000};
    expect_frames(fade_head, 2);
    uint32_t before = music.read, frames = g_audio_status.music_frames;
    audio_scene_pause(true);
    expect_silence(7);
    assert(music.read == before && g_audio_status.music_frames == frames);
    audio_demo_service();
    expect_silence(3);
    audio_scene_pause(false);
    const int16_t fade_tail[] = {3000,-2000,4500,-3000,6000,-4000};
    expect_frames(fade_tail, 3);
    assert(audio_scene_play(false, 0, 1024, 0, 0));
    const int16_t mixed[] = {7500,-2500,7500,-2500};
    expect_frames(mixed, 2);
    audio_scene_stop(false, 0);
    assert(!g_audio_status.sfx_playing);
    audio_scene_stop(true, 4);
    const int16_t fade_out[] = {6000,-4000,4500,-3000,3000,-2000,1500,-1000};
    expect_frames(fade_out, 4);
    assert(!g_audio_status.music_playing && !music.enabled);
    // The IRQ must leave the file for service, which also retires the queued ring.
    assert(music.file);
    expect_silence(3);
    audio_demo_service();
    assert(!music.file && music.read == music.write);

    // A stop during fade-in starts at the current envelope level.
    assert(audio_scene_play(true, 0, 4096, 0, 4));
    const int16_t partial_in[] = {0,0,3000,-2000};
    expect_frames(partial_in, 2);
    audio_scene_stop(true, 2);
    const int16_t partial_out[] = {6000,-4000,3000,-2000};
    expect_frames(partial_out, 2);
    expect_silence(1);
    audio_demo_service();

    // A non-divisible fade retains its remainder; long durations do not overflow.
    assert(audio_scene_play(true, 0, 4096, 0, 3));
    const int16_t thirds[] = {0,0,3999,-2666,7998,-5332,12000,-8000};
    expect_frames(thirds, 4);
    audio_scene_stop(true, 0);
    assert(audio_scene_play(true, 0, 4096, 0, UINT32_MAX));
    expect_silence(4);
    audio_scene_stop(true, 0);

    assert(audio_scene_play(true, 0, 4096, 0, 0));
    audio_scene_pause(true);
    audio_scene_stop(true, 3);
    expect_silence(4);
    assert(g_audio_status.music_playing && !g_audio_status.music_frames);
    audio_scene_pause(false);
    const int16_t paused_stop[] = {12000,-8000,8000,-5333,4001,-2667};
    expect_frames(paused_stop, 3);
    expect_silence(1);
    audio_demo_service();

    // Both signs clip after summing gained voices, retaining independent channels.
    assert(audio_scene_play(true, 0, 8192, 0, 0));
    assert(audio_scene_play(false, 0, 8192, 0, 0));
    const int16_t positive_clip[] = {32767,-4000};
    expect_frames(positive_clip, 1);
    assert(audio_scene_play(true, 0, 8192, 1010, 0));
    assert(audio_scene_play(false, 1, 4096, 0, 0));
    const int16_t negative_clip[] = {-32768,-10000};
    expect_frames(negative_clip, 1);
    audio_scene_stop(false, 0);
    assert(audio_scene_play(true, 0, INT_MAX, 0, 0));
    const int16_t extreme_clip[] = {32767,-32768};
    expect_frames(extreme_clip, 1);
    assert(audio_scene_play(true, 0, 65536, 0, 0));
    assert(audio_scene_play(false, 0, 65536, 0, 0));
    const int16_t fast_limit[] = {32767,-32000};
    expect_frames(fast_limit, 1);
    assert(audio_scene_play(true, 0, INT_MAX, 0, 0));
    assert(audio_scene_play(false, 1, 858993458, 0, 0));
    const int16_t high_gain_cancellation[] = {6,-32768};
    expect_frames(high_gain_cancellation, 1);
    audio_scene_stop(false, 0);

    // Hand-derived timeline: intro 0..508, loop 509..1399 (891 frames).
    // The two seam cases cross physical ADPCM block boundaries and partial blocks.
    struct Seek { uint32_t elapsed; int16_t samples[8]; } seeks[] = {
        {504, {12000,-8000,16000,-6000,16000,-6000,16000,-6000}},
        {1008, {16000,-6000,16000,-6000,-20000,10000,-20000,10000}},
        {1398, {-20000,10000,-20000,10000,16000,-6000,16000,-6000}},
        {1400, {16000,-6000,16000,-6000,16000,-6000,16000,-6000}},
        {2291, {16000,-6000,16000,-6000,16000,-6000,16000,-6000}},
        {2790, {16000,-6000,16000,-6000,-20000,10000,-20000,10000}},
    };
    for (unsigned i = 0; i < sizeof(seeks)/sizeof(seeks[0]); ++i) {
        assert(audio_scene_play(true, 0, 4096, seeks[i].elapsed, 0));
        assert(g_audio_status.music_frames == seeks[i].elapsed);
        expect_frames(seeks[i].samples, 4);
        assert(g_audio_status.music_frames == seeks[i].elapsed + 4);
    }
    audio_scene_stop(true, 0);

    // Mono seek and natural EOF: 40-frame effect, 3 frames remaining.
    assert(audio_scene_play(false, 0, 4096, 37, 0));
    const int16_t effect_end[] = {6000,6000,6000,6000,6000,6000,0,0};
    expect_frames(effect_end, 4);
    assert(!g_audio_status.sfx_playing && g_audio_status.sfx_frames == 40);
    assert(audio_scene_play(false, 0, 4096, 40, 0));
    assert(!g_audio_status.sfx_playing);
    expect_silence(1);

    assert(!audio_scene_play(true, g_audio_status.music_count, 4096, 0, 0));
    assert(!audio_scene_play(false, g_audio_status.sfx_count, 4096, 0, 0));
    assert(!audio_scene_play(true, 0, -1, 0, 0));
    assert(audio_scene_play(true, 0, 0, 0, 0));
    expect_silence(2);
    assert(g_audio_status.music_frames == 2);
    audio_scene_stop(true, 0);

    // Existing browser APIs retain unity alone and equal halves at overlap.
    g_audio_status.music_index = 0;
    g_audio_status.sfx_index = UINT32_MAX;
    audio_demo_toggle_music();
    const int16_t legacy_music[] = {12000,-8000};
    expect_frames(legacy_music, 1);
    audio_demo_next_sfx();
    const int16_t legacy_overlap[] = {9000,-1000};
    expect_frames(legacy_overlap, 1);
    int16_t ignored[40 * 2];
    render(40, ignored);
    expect_frames(legacy_music, 1);
    assert(!g_audio_status.errors && !g_audio_status.music_underruns && !g_audio_status.sfx_underruns);
    puts("PASS scene synthetic: sample fades/pause, stereo gains/clips, seek/wrap/EOF, deferred close, legacy mix/status");
}

static void reference(bool is_music, unsigned index, const char *path)
{
    const AudioEntry *e = &entries[(is_music ? 0 : g_audio_status.music_count) + index];
    FILE *f = fopen(path, "rb"); assert(f);
    int16_t *pcm = malloc(e->frames * e->channels * sizeof(*pcm)); assert(pcm);
    assert(fread(pcm, sizeof(*pcm) * e->channels, e->frames, f) == e->frames);
    fclose(f);
    uint32_t offsets[] = {0, e->samples_per_block - 1, e->loop_start + 3,
        e->loop_end - 1, e->loop_end, e->loop_end + (e->loop_end - e->loop_start) * 3 + 17, UINT32_MAX};
    for (unsigned seek = 0; seek < sizeof(offsets)/sizeof(offsets[0]); ++seek) {
        uint32_t frame = offsets[seek];
        if (is_music && frame >= e->loop_end)
            frame = e->loop_start + (frame - e->loop_end) % (e->loop_end - e->loop_start);
        assert(audio_scene_play(is_music, index, 4096, offsets[seek], 0));
        int16_t out[512];
        for (unsigned batch = 0; batch < 24; ++batch) {
            audio_demo_service();
            render(256, out);
            for (unsigned i = 0; i < 256; ++i) {
                int16_t left = 0, right = 0;
                if (frame < e->frames) {
                    left = pcm[frame * e->channels];
                    right = pcm[frame * e->channels + (e->channels == 2)];
                    ++frame;
                    if (is_music && frame == e->loop_end) frame = e->loop_start;
                }
                assert(out[i*2] == left && out[i*2+1] == right);
            }
        }
        audio_scene_stop(is_music, 0);
    }
    assert(!g_audio_status.errors && !g_audio_status.music_underruns && !g_audio_status.sfx_underruns);
    printf("PASS scene PCM: %s, 7 total-elapsed seeks x 6144 frames against FFmpeg\n", e->name);
    free(pcm);
}

int main(int argc, char **argv)
{
    assert(argc >= 3);
    fixture_root = argv[2];
    assert(audio_demo_init());
    if (!strcmp(argv[1], "--synthetic")) synthetic();
    else {
        assert(argc == 6 && !strcmp(argv[1], "--reference"));
        reference(!strcmp(argv[3], "music"), strtoul(argv[4], NULL, 10), argv[5]);
    }
    voice_stop(&music); voice_stop(&sfx); free(entries);
    return 0;
}
