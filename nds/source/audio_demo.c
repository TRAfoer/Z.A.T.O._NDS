#include <nds.h>
#include <maxmod9.h>
#include <stdio.h>
#include <stdlib.h>
#include <stddef.h>
#include <string.h>
#include "audio_demo.h"
#include "ima_adpcm.h"

volatile AudioStatus g_audio_status;
volatile AudioPerfStatus g_audio_perf_status;
_Static_assert(sizeof(AudioPerfStatus) == 18 * sizeof(uint32_t), "Audio perf layout");

#ifdef ZATO_NITROFS
#define AUDIO_RATE 22050
#define MUSIC_FRAMES 32768
#define SFX_FRAMES 16384
#define SCENE_READY_FRAMES 4096
#define SCENE_FILL_BUDGET 1024

typedef struct {
    char magic[8];
    uint32_t version, music, sfx, entry_size;
} CatalogHeader;
typedef struct {
    char name[64], path[48];
    uint32_t frames, rate;
    uint16_t channels, block, samples_per_block, reserved;
    uint32_t data_offset, data_bytes, loop_start, loop_end;
} AudioEntry;
_Static_assert(sizeof(CatalogHeader) == 24, "Audio header layout");
_Static_assert(sizeof(AudioEntry) == 144, "Audio entry layout");

typedef struct {
    FILE *file;
    const AudioEntry *entry;
    int16_t *ring;
    unsigned capacity, channels;
    volatile uint32_t read, write, source_base;
    volatile bool enabled, eof, close_pending;
    bool loop;
    uint32_t input_frames;
    unsigned block_frames, block_pos, next_skip;
    volatile int32_t gain_q12, fade_step, fade_target;
    volatile uint32_t fade_total, fade_left, fade_remainder, fade_error;
    volatile bool fade_down, fade_stop;
    uint8_t encoded[512];
    int16_t decoded[2048];
} Voice;

static AudioEntry *entries;
static int16_t music_ring[MUSIC_FRAMES * 2];
static int16_t sfx_ring[SFX_FRAMES];
static Voice music, sfx;
static bool servicing;
static volatile bool scene_mode, scene_paused;
static volatile uint32_t master_music=4096,master_sfx=4096;
/* Full existing button SFX, rounded to mono IMA blocks. This stays in BSS, not ROM;
 * only true catalog frames are played. No plot decoder scratch is shared. */
#define UI_BUTTON_CAPACITY (30u * 1017u)
#define UI_MOVE_CUT 2205u
static int16_t ui_button_pcm[UI_BUTTON_CAPACITY];
static uint32_t ui_button_frames;
static bool ui_ready, ui_preparing;
typedef struct {
    const int16_t *pcm;
    uint32_t frames, gain;
    volatile uint32_t cursor;
    volatile bool active;
    volatile uint8_t phase;
    uint8_t step, cue;
} UiVoice;
static UiVoice ui_voice;
_Static_assert(sizeof(void *) != 4 || (sizeof(UiVoice) == 20 &&
    offsetof(UiVoice, cursor) == 12 && offsetof(UiVoice, active) == 16 &&
    offsetof(UiVoice, phase) == 17 && offsetof(UiVoice, step) == 18 &&
    offsetof(UiVoice, cue) == 19), "UI observer layout");
static AudioEntry file_entry;
static uint32_t stream_mixed_frames;
typedef struct { uint32_t start, end; } OutputGap;
static struct {
    bool active, anchored, end_known, failed;
    uint32_t anchor, end, mixed, offset, total, retired_gap;
    unsigned gap_read, gap_write;
    OutputGap gaps[32];
} file_clock;

static void file_output_frame(uint32_t output_frame, bool source)
{
    if (!file_clock.active || file_clock.end_known) return;
    if (source) {
        if (!file_clock.anchored) {
            file_clock.anchor = output_frame;
            file_clock.anchored = true;
        }
        if (++file_clock.mixed == file_clock.total - file_clock.offset) {
            file_clock.end = output_frame + 1;
            file_clock.end_known = true;
        }
    } else if (file_clock.anchored) {
        unsigned write = file_clock.gap_write;
        if (write != file_clock.gap_read && file_clock.gaps[(write - 1) & 31].end == output_frame)
            file_clock.gaps[(write - 1) & 31].end++;
        else if (write - file_clock.gap_read < 32) {
            file_clock.gaps[write & 31] = (OutputGap){output_frame, output_frame + 1};
            file_clock.gap_write = write + 1;
        } else if (!file_clock.failed) {
            file_clock.failed = true;
            g_audio_status.errors++;
        }
    }
}

// Decode and file I/O always run with IRQs enabled. Only the few envelope/control
// stores shared with the callback need a critical section on the target.
static int audio_lock(void)
{
#ifdef __arm__
    return enterCriticalSection();
#else
    return 0;
#endif
}
static void audio_unlock(int state)
{
#ifdef __arm__
    leaveCriticalSection(state);
#else
    (void)state;
#endif
}

static const AudioEntry *ui_entry(const char *name)
{
    for (unsigned i = 0; i < g_audio_status.sfx_count; i++) {
        const AudioEntry *entry = &entries[g_audio_status.music_count + i];
        if (!strcmp(entry->name, name)) return entry;
    }
    return NULL;
}

static bool ui_load(const AudioEntry *entry, int16_t *pcm, unsigned capacity)
{
    if (!entry || !entry->frames || entry->frames > capacity) return false;
    unsigned blocks = (entry->frames + 1016u) / 1017u;
    if (blocks > capacity / 1017u) return false;
    char path[64];
    snprintf(path, sizeof(path), "nitro:/%s", entry->path);
    FILE *file = fopen(path, "rb");
    if (!file) return false;
    bool good = fseek(file, entry->data_offset, SEEK_SET) == 0;
    uint8_t encoded[512];
    for (unsigned block = 0; good && block < blocks; block++) {
        unsigned offset = block * 1017u;
        good = fread(encoded, 1, sizeof(encoded), file) == sizeof(encoded) &&
            ima_decode_block(encoded, sizeof(encoded), 1, pcm + offset, capacity - offset) == 1017;
        /* Each file operation has returned before servicing ordinary voices. */
        audio_demo_service();
    }
    if (fclose(file) != 0) good = false;
    return good;
}

bool audio_ui_prepare(void)
{
    if (!g_audio_status.initialized || file_clock.active || ui_preparing) return false;
    if (ui_ready) return true;
    ui_preparing = true;
    const AudioEntry *button = ui_entry("button.mp3");
    bool good = ui_load(button, ui_button_pcm, UI_BUTTON_CAPACITY);
    if (good) {
        ui_button_frames = button->frames;
        ui_ready = true;
    }
    ui_preparing = false;
    return good; /* Optional UI failures never invalidate the scene catalog. */
}

void audio_ui_play(AudioUiCue cue)
{
    static const uint16_t gains[] = {512, 1536, 1024, 512};
    if (!ui_ready || file_clock.active || (unsigned)cue >= sizeof(gains) / sizeof(gains[0])) return;
    if (cue == AUDIO_UI_MOVE && ui_button_frames <= UI_MOVE_CUT) return;
    int state = audio_lock();
    ui_voice.pcm = ui_button_pcm;
    ui_voice.frames = ui_button_frames;
    ui_voice.gain = gains[cue];
    ui_voice.cursor = cue == AUDIO_UI_MOVE ? UI_MOVE_CUT : 0;
    ui_voice.phase = 0;
    ui_voice.step = cue == AUDIO_UI_MOVE ? 3 : 4;
    ui_voice.cue = cue;
    ui_voice.active = true;
    audio_unlock(state);
}

void audio_ui_stop(void)
{
    int state = audio_lock();
    ui_voice.active = false;
    audio_unlock(state);
}

static int ui_output_sample(void)
{
    if (!ui_voice.active || file_clock.active) return 0;
    uint32_t cursor = ui_voice.cursor;
    int sample = ui_voice.pcm[cursor];
    /* Nearest sampling, exactly source[2205 + floor(3*i/4)] for MOVE.
     * Keep cursor as an absolute integer source position for diagnostics. */
    unsigned phase = ui_voice.phase + ui_voice.step;
    ui_voice.cursor = cursor + (phase >> 2);
    ui_voice.phase = phase & 3u;
    if (ui_voice.cursor >= ui_voice.frames) ui_voice.active = false;
    unsigned gain = ui_voice.gain * master_sfx / 4096u;
    return sample * (int)gain / 4096;
}

static bool perf_clock_ready;
static uint16_t perf_timer_last;
static uint32_t perf_timer_ticks;

uint32_t audio_perf_now(void)
{
    if (!perf_clock_ready) return 0;
    int state = audio_lock();
    uint16_t current = TIMER_DATA(1), previous = perf_timer_last;
    uint32_t ticks = perf_timer_ticks + (uint16_t)(current - previous);
    perf_timer_last = current;
    perf_timer_ticks = ticks;
    if (current < previous) g_audio_perf_status.timer_wraps++;
    audio_unlock(state);
    return ticks;
}

/* Called once before the first mmStreamOpen, while callbacks do not exist. */
static void audio_perf_init(void)
{
    TIMER_CR(1) = 0;
    TIMER_DATA(1) = 0;
    perf_timer_last = 0;
    perf_timer_ticks = 0;
    memset((void *)&g_audio_perf_status, 0, sizeof(g_audio_perf_status));
    g_audio_perf_status.magic = 0x4650415a; // ZAPF
    g_audio_perf_status.version = AUDIO_PERF_VERSION;
    g_audio_perf_status.clock_hz = BUS_CLOCK;
    g_audio_perf_status.timer_div = 1024;
    g_audio_perf_status.music_min_ready = UINT32_MAX;
    g_audio_perf_status.sfx_min_ready = UINT32_MAX;
    TIMER_CR(1) = TIMER_ENABLE | TIMER_DIV_1024;
    perf_clock_ready = true;
}

static void voice_fade(Voice *v, int target, uint32_t frames, bool stop)
{
    int delta = target - v->gain_q12;
    uint32_t magnitude = delta < 0 ? (uint32_t)-delta : (uint32_t)delta;
    v->fade_target = target;
    v->fade_down = delta < 0;
    v->fade_total = v->fade_left = frames;
    v->fade_error = 0;
    v->fade_stop = stop;
    v->fade_step = frames ? delta / (int64_t)frames : 0;
    v->fade_remainder = frames ? magnitude % frames : 0;
    if (!frames) v->gain_q12 = target;
}

static void voice_stop(Voice *v)
{
    v->enabled = false;
    __asm__ volatile("" ::: "memory");
    if (v->file) fclose(v->file);
    v->file = NULL;
    v->read = v->write = 0;
    v->source_base = 0;
    v->eof = false;
    v->close_pending = false;
    v->block_frames = v->block_pos = v->next_skip = 0;
    v->gain_q12 = 4096;
    voice_fade(v, 4096, 0, false);
}

static bool next_block(Voice *v)
{
    unsigned limit = v->loop ? v->entry->loop_end : v->entry->frames;
    unsigned skip = v->next_skip;
    v->next_skip = 0;
    if (v->input_frames == limit) {
        if (!v->loop) {
            v->eof = true;
            fclose(v->file); v->file = NULL;
            return false;
        }
        unsigned block = v->entry->loop_start / v->entry->samples_per_block;
        skip = v->entry->loop_start % v->entry->samples_per_block;
        if (fseek(v->file, v->entry->data_offset + block * 512, SEEK_SET)) return false;
        v->input_frames = block * v->entry->samples_per_block;
        g_audio_status.music_loops++;
    }
    if (fread(v->encoded, 1, 512, v->file) != 512) return false;
    int decoded = ima_decode_block(v->encoded, 512, v->channels, v->decoded, 1024);
    if (decoded != v->entry->samples_per_block) return false;
    unsigned remaining = limit - v->input_frames;
    v->block_frames = remaining < (unsigned)decoded ? remaining : (unsigned)decoded;
    v->block_pos = skip;
    v->input_frames += v->block_frames;
    return true;
}

static void fill_voice(Voice *v, unsigned budget)
{
    while (v->file && !v->eof && budget) {
        uint32_t write = v->write;
        unsigned used = write - v->read;
        if (used >= v->capacity) break;
        if (v->block_pos == v->block_frames && !next_block(v)) {
            if (!v->eof) {
                g_audio_status.errors++;
                v->eof = true;
                if (v->file) { fclose(v->file); v->file = NULL; }
                g_audio_status.ui_version++;
            }
            break;
        }
        unsigned count = v->block_frames - v->block_pos;
        unsigned offset = write & (v->capacity - 1);
        if (count > v->capacity - used) count = v->capacity - used;
        if (count > v->capacity - offset) count = v->capacity - offset;
        if (count > budget) count = budget;
        memcpy(v->ring + offset * v->channels, v->decoded + v->block_pos * v->channels,
               count * v->channels * sizeof(int16_t));
        v->block_pos += count;
        budget -= count;
        __asm__ volatile("" ::: "memory");
        v->write = write + count;
    }
}

static bool voice_start(Voice *v, const AudioEntry *entry, bool loop, uint32_t frame,
                        unsigned prefill)
{
    voice_stop(v);
    char path[80];
    snprintf(path, sizeof(path), "nitro:/%s", entry->path);
    v->file = fopen(path, "rb");
    if (!v->file) { g_audio_status.errors++; return false; }
    if (fseek(v->file, 0, SEEK_END)) { voice_stop(v); g_audio_status.errors++; return false; }
    long bytes = ftell(v->file);
    if (bytes < 0 || entry->data_offset > (uint32_t)bytes ||
        entry->data_bytes > (uint32_t)bytes - entry->data_offset) {
        voice_stop(v); g_audio_status.errors++; return false;
    }
    if (loop && frame >= entry->loop_end)
        frame = entry->loop_start + (frame - entry->loop_end) % (entry->loop_end - entry->loop_start);
    v->source_base = frame;
    unsigned block = frame / entry->samples_per_block;
    if (fseek(v->file, entry->data_offset + block * 512, SEEK_SET)) {
        voice_stop(v); g_audio_status.errors++; return false;
    }
    v->entry = entry; v->loop = loop;
    v->input_frames = block * entry->samples_per_block;
    v->next_skip = frame % entry->samples_per_block;
    unsigned before_errors = g_audio_status.errors;
    fill_voice(v, prefill);
    if (g_audio_status.errors != before_errors || v->write == v->read) {
        voice_stop(v); return false;
    }
    return true;
}

// Callback-only retirement. fclose and ring reset are deferred to main service.
static void voice_finish(Voice *v, bool is_music, bool natural)
{
    v->enabled = false;
    v->close_pending = true;
    if (is_music) g_audio_status.music_playing = 0;
    else {
        g_audio_status.sfx_playing = 0;
        if (natural) g_audio_status.sfx_finishes++;
    }
    g_audio_status.ui_version++;
}

static void voice_advance_fade(Voice *v, bool is_music)
{
    if (!v->enabled || !v->fade_left) return;
    v->gain_q12 += v->fade_step;
    // This form avoids overflowing error + remainder for UINT32_MAX durations.
    if (v->fade_error >= v->fade_total - v->fade_remainder) {
        v->fade_error -= v->fade_total - v->fade_remainder;
        v->gain_q12 += v->fade_down ? -1 : 1;
    } else v->fade_error += v->fade_remainder;
    if (!--v->fade_left) {
        v->gain_q12 = v->fade_target;
        if (v->fade_stop) voice_finish(v, is_music, false);
    }
}

static int clip_sample(int64_t sample)
{
    if (sample > 32767) return 32767;
    if (sample < -32768) return -32768;
    return (int)sample;
}

static uint32_t voice_position(const Voice *v,uint64_t consumed)
{
    uint64_t position=(uint64_t)v->source_base+consumed;
    if(v->loop&&position>=v->entry->loop_end)
        position=v->entry->loop_start+(position-v->entry->loop_end)%(v->entry->loop_end-v->entry->loop_start);
    return (uint32_t)position;
}

#ifdef __arm__
__attribute__((target("arm")))
#endif
static mm_word stream_callback(mm_word length, mm_addr destination, mm_stream_formats format)
{
    uint32_t perf_start = audio_perf_now();
    uint32_t perf_gap = perf_start - g_audio_perf_status.last_callback;
    if (g_audio_perf_status.callback_calls && perf_gap > g_audio_perf_status.max_callback_gap)
        g_audio_perf_status.max_callback_gap = perf_gap;
    g_audio_perf_status.last_callback = perf_start;
    g_audio_perf_status.callback_calls++;
    if (length > g_audio_perf_status.max_callback_frames) g_audio_perf_status.max_callback_frames = length;
    uint32_t music_empty = 0, sfx_empty = 0;
    (void)format;
    int16_t *out = destination;
    uint32_t output_base = stream_mixed_frames;
    stream_mixed_frames += length;
    bool music_starved = false, sfx_starved = false;
    g_audio_status.callbacks++;
    for (unsigned i = 0; i < length; i++) {
        int left = 0, right = 0, effect = 0;
        bool have_music = false, have_sfx = false;
        bool paused = scene_mode && scene_paused;
        if (!paused && music.enabled) {
            uint32_t read = music.read;
            if (read != music.write) {
                unsigned offset = (read & (MUSIC_FRAMES - 1)) * 2;
                left = music.ring[offset]; right = music.ring[offset + 1];
                music.read = read + 1;
                if(read==UINT32_MAX)music.source_base=voice_position(&music,UINT64_C(1)<<32);
                g_audio_status.music_frames++;
                have_music = true;
            } else if (!music.eof) { music_starved = true; music_empty++; }
            else if (scene_mode) voice_finish(&music, true, true);
        }
        if (!paused && sfx.enabled) {
            uint32_t read = sfx.read;
            if (read != sfx.write) {
                effect = sfx.ring[read & (SFX_FRAMES - 1)];
                sfx.read = read + 1;
                if(read==UINT32_MAX)sfx.source_base=voice_position(&sfx,UINT64_C(1)<<32);
                g_audio_status.sfx_frames++;
                have_sfx = true;
            } else if (sfx.eof) {
                voice_finish(&sfx, false, true);
            } else { sfx_starved = true; sfx_empty++; }
        }
        file_output_frame(output_base + i, have_music);
        int ui_sample = ui_output_sample();
        if (scene_mode && !paused) {
            int music_gain = music.gain_q12, effect_gain = sfx.gain_q12;
            if(master_music!=4096)music_gain=(int)((int64_t)music_gain*master_music/4096);
            if(master_sfx!=4096)effect_gain=(int)((int64_t)effect_gain*master_sfx/4096);
            if (music_gain <= 65536 && effect_gain <= 65536) {
                // Every sample*gain fits signed 32 bits, including -32768*65536.
                // Division stays signed (toward zero); gained sums fit 21 bits.
                int gained_effect = effect * effect_gain / 4096;
                int mixed_left = left * music_gain / 4096 + gained_effect + ui_sample;
                int mixed_right = right * music_gain / 4096 + gained_effect + ui_sample;
                left = mixed_left > 32767 ? 32767 : mixed_left < -32768 ? -32768 : mixed_left;
                right = mixed_right > 32767 ? 32767 : mixed_right < -32768 ? -32768 : mixed_right;
            } else {
                // Unusually large API gains must still sum before clipping: two
                // amplified sources can cancel, and neither sum may overflow.
                int64_t gained_effect = (int64_t)effect * effect_gain / 4096;
                left = clip_sample((int64_t)left * music_gain / 4096 + gained_effect + ui_sample);
                right = clip_sample((int64_t)right * music_gain / 4096 + gained_effect + ui_sample);
            }
            voice_advance_fade(&music, true);
            voice_advance_fade(&sfx, false);
        // Browser overlap retains its established equal -6dB behavior.
        } else if (!scene_mode) {
            if (have_music && have_sfx) {
                left = (left + effect) / 2; right = (right + effect) / 2;
            } else if (have_sfx) left = right = effect;
            left = clip_sample(left + ui_sample); right = clip_sample(right + ui_sample);
        } else left = right = ui_sample;
        out[i * 2] = left; out[i * 2 + 1] = right;
        unsigned peak = left < 0 ? -left : left;
        unsigned other = right < 0 ? -right : right;
        if (other > peak) peak = other;
        if (peak > g_audio_status.output_peak) g_audio_status.output_peak = peak;
        if (left) g_audio_status.nonzero_samples++;
        if (right) g_audio_status.nonzero_samples++;
    }
    if (music_starved) g_audio_status.music_underruns++;
    if (sfx_starved) g_audio_status.sfx_underruns++;
    g_audio_perf_status.music_empty_frames += music_empty;
    g_audio_perf_status.sfx_empty_frames += sfx_empty;
    if (!(scene_mode && scene_paused)) {
        uint32_t ready = music.write - music.read;
        if (music.enabled && ready < g_audio_perf_status.music_min_ready)
            g_audio_perf_status.music_min_ready = ready;
        ready = sfx.write - sfx.read;
        if (sfx.enabled && ready < g_audio_perf_status.sfx_min_ready)
            g_audio_perf_status.sfx_min_ready = ready;
    }
    uint32_t perf_work = audio_perf_now() - perf_start;
    if (perf_work > g_audio_perf_status.max_callback_work) g_audio_perf_status.max_callback_work = perf_work;
    return length;
}

static void open_stream(bool manual)
{
    mm_stream stream = {.sampling_rate = AUDIO_RATE, .buffer_length = 2048, .callback = stream_callback,
                        .format = MM_STREAM_16BIT_STEREO, .timer = MM_TIMER0, .manual = manual};
    stream_mixed_frames = 0;
    mmStreamOpen(&stream);
    mmStreamVolume(127);
}

bool audio_demo_init(void)
{
    audio_ui_stop();
    ui_ready = false;
    ui_button_frames = 0;
    memset((void *)&g_audio_status, 0, sizeof(g_audio_status));
    g_audio_status.magic = 0x4455415a; // ZAUD
    FILE *catalog = fopen("nitro:/audio/index.bin", "rb");
    if (!catalog) return false;
    CatalogHeader h;
    if (fread(&h, 1, sizeof(h), catalog) != sizeof(h) || memcmp(h.magic, "ZATAUD02", 8) ||
        h.version != 2 || h.entry_size != sizeof(AudioEntry) || !h.music || !h.sfx || h.music + h.sfx > 512) {
        fclose(catalog); return false;
    }
    entries = calloc(h.music + h.sfx, sizeof(AudioEntry));
    if (!entries || fread(entries, sizeof(AudioEntry), h.music + h.sfx, catalog) != h.music + h.sfx) {
        fclose(catalog); free(entries); entries = NULL; return false;
    }
    fclose(catalog);
    for (unsigned i = 0; i < h.music + h.sfx; i++) {
        const AudioEntry *e = &entries[i];
        if (!e->frames || e->rate != AUDIO_RATE || e->channels != (i < h.music ? 2 : 1) ||
            e->block != 512 || e->samples_per_block != (512 - e->channels * 4) * 2 / e->channels + 1 ||
            e->data_bytes % 512 || e->name[63] || e->path[47] ||
            e->frames > (e->data_bytes / 512) * e->samples_per_block ||
            e->loop_start >= e->loop_end || e->loop_end > e->frames) {
            free(entries); entries = NULL; return false;
        }
    }
    music.ring = music_ring; music.capacity = MUSIC_FRAMES; music.channels = 2;
    sfx.ring = sfx_ring; sfx.capacity = SFX_FRAMES; sfx.channels = 1;
    music.gain_q12 = sfx.gain_q12 = 4096;
    scene_mode = scene_paused = false;
    master_music = master_sfx = 4096;
    g_audio_status.rate = AUDIO_RATE;
    g_audio_status.music_count = h.music; g_audio_status.sfx_count = h.sfx;
    g_audio_status.sfx_index = UINT32_MAX;
    soundEnable();
    if (!mmInitNoSoundbank()) { free(entries); entries = NULL; return false; }
    audio_perf_init();
    g_audio_status.initialized = 1;
    open_stream(false);
    return true;
}

void audio_demo_service(void)
{
    if (!g_audio_status.initialized || servicing) return;
    servicing = true;
    uint32_t perf_start = audio_perf_now();
    uint32_t perf_gap = perf_start - g_audio_perf_status.last_service;
    if (g_audio_perf_status.service_calls && perf_gap > g_audio_perf_status.max_service_gap)
        g_audio_perf_status.max_service_gap = perf_gap;
    g_audio_perf_status.last_service = perf_start;
    g_audio_perf_status.service_calls++;
    if (file_clock.active) {
        // Manual Maxmod timer position remains meaningful only if the output
        // queue is serviced before its unprepared tail reaches the hardware.
        uint32_t position = mmStreamGetPosition();
        if ((int32_t)(position - stream_mixed_frames) > 0 && !file_clock.end_known && !file_clock.failed) {
            file_clock.failed = true;
            g_audio_status.music_underruns++;
        }
        mmStreamUpdate();
        uint32_t unused;
        audio_scene_file_position(&unused); // retire audible pause spans
    }
    if (music.close_pending) voice_stop(&music);
    if (sfx.close_pending) voice_stop(&sfx);
    if (scene_mode) {
        Voice *voices[] = {&music, &sfx};
        for (unsigned i = 0; i < 2; ++i) {
            Voice *v = voices[i];
            uint32_t ready = v->write - v->read;
            if (ready >= SCENE_READY_FRAMES) continue;
            unsigned budget = SCENE_READY_FRAMES - ready;
            if (budget > SCENE_FILL_BUDGET) budget = SCENE_FILL_BUDGET;
            fill_voice(v, budget);
        }
    } else {
        fill_voice(&music, 4096);
        fill_voice(&sfx, 4096);
    }
    uint32_t perf_work = audio_perf_now() - perf_start;
    if (perf_work > g_audio_perf_status.max_service_work) g_audio_perf_status.max_service_work = perf_work;
    servicing = false;
}

// Called only in the main thread, after asset file I/O has released its lock.
void asset_io_yield(void) { audio_demo_service(); }

static uint32_t audio_u32(const uint8_t *p)
{ return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static unsigned audio_u16(const uint8_t *p) { return p[0] | p[1] << 8; }

static bool read_file_entry(const char *path, uint32_t frames, AudioEntry *entry)
{
    if (!path || !*path || strlen(path) >= sizeof(entry->path) || !frames ||
        path[0] == '/' || strchr(path, ':') || strchr(path, '\\') || strstr(path, "..")) return false;
    char full[80]; snprintf(full, sizeof(full), "nitro:/%s", path);
    FILE *file = fopen(full, "rb"); if (!file) return false;
    bool good = false, format = false;
    uint8_t head[20];
    if (fseek(file, 0, SEEK_END)) goto done;
    long size = ftell(file);
    if (size < 12 || fseek(file, 0, SEEK_SET) || fread(head, 1, 12, file) != 12 ||
        memcmp(head, "RIFF", 4) || memcmp(head + 8, "WAVE", 4) ||
        audio_u32(head + 4) != (uint32_t)size - 8) goto done;
    memset(entry, 0, sizeof(*entry));
    uint32_t at = 12;
    for (uint32_t chunks = 0; at < (uint32_t)size && chunks < 128; ++chunks) {
        if ((uint32_t)size - at < 8 || fseek(file, at, SEEK_SET) || fread(head, 1, 8, file) != 8) goto done;
        uint32_t bytes = audio_u32(head + 4), payload = at + 8;
        if (bytes > (uint32_t)size - payload) goto done;
        if (!memcmp(head, "fmt ", 4)) {
            if (format || bytes < 20 || fread(head, 1, 20, file) != 20 ||
                audio_u16(head) != 0x11 || audio_u16(head + 2) != 2 ||
                audio_u32(head + 4) != AUDIO_RATE || audio_u16(head + 12) != 512 ||
                audio_u16(head + 14) != 4 || audio_u16(head + 16) < 2 ||
                audio_u16(head + 18) != 505) goto done;
            format = true;
        } else if (!memcmp(head, "data", 4)) {
            if (entry->data_bytes || !bytes || bytes % 512 || frames > (uint64_t)(bytes / 512) * 505) goto done;
            entry->data_offset = payload; entry->data_bytes = bytes;
        }
        uint32_t end = payload + bytes;
        if ((bytes & 1) && end == (uint32_t)size) goto done;
        at = end + (bytes & 1);
    }
    if (at != (uint32_t)size || !format || !entry->data_bytes) goto done;
    strcpy(entry->path, path); strcpy(entry->name, "Video soundtrack");
    entry->frames = entry->loop_end = frames; entry->rate = AUDIO_RATE;
    entry->channels = 2; entry->block = 512; entry->samples_per_block = 505;
    good = true;
done:
    fclose(file); return good;
}

bool audio_scene_play_file(const char *path, uint32_t true_frames, int gain_q12,
                           uint32_t offset_frames, uint32_t fade_frames)
{
    AudioEntry entry;
    if (!g_audio_status.initialized || gain_q12 < 0 || offset_frames > true_frames ||
        !read_file_entry(path, true_frames, &entry)) return false;
    audio_ui_stop();
    mmStreamClose();
    memset(&file_clock, 0, sizeof(file_clock));
    voice_stop(&music); voice_stop(&sfx);
    g_audio_status.music_playing = g_audio_status.sfx_playing = 0;
    file_entry = entry;
    if (offset_frames < true_frames &&
        !voice_start(&music, &file_entry, false, offset_frames, SCENE_READY_FRAMES)) {
        open_stream(false); return false;
    }
    scene_mode = true;
    music.gain_q12 = fade_frames ? 0 : gain_q12;
    voice_fade(&music, gain_q12, fade_frames, false);
    file_clock.active = true; file_clock.total = true_frames; file_clock.offset = offset_frames;
    if (offset_frames == true_frames) file_clock.anchored = file_clock.end_known = true;
    else {
        music.enabled = true; g_audio_status.music_playing = 1;
        g_audio_status.music_starts++;
    }
    g_audio_status.music_frames = offset_frames;
    // Maxmod's manual stream alone exposes its hardware sample clock. The first
    // callback is the 2032-frame initial prefill, before that clock starts at 0.
    open_stream(true);
    g_audio_status.ui_version++;
    return true;
}

bool audio_scene_file_timeline(uint32_t *frame)
{
    if (!frame || !file_clock.active || !file_clock.anchored || file_clock.failed) return false;
    uint32_t position = mmStreamGetPosition(), gaps = file_clock.retired_gap;
    if ((int32_t)(position - file_clock.anchor) < 0) return false;
    while (file_clock.gap_read != file_clock.gap_write) {
        OutputGap gap = file_clock.gaps[file_clock.gap_read & 31];
        if ((int32_t)(position - gap.start) < 0) break;
        if ((int32_t)(position - gap.end) < 0) { gaps += position - gap.start; break; }
        gaps += gap.end - gap.start; file_clock.gap_read++;
        file_clock.retired_gap = gaps;
    }
    uint32_t audible = (int32_t)(position - file_clock.anchor) < 0 ? 0 : position - file_clock.anchor;
    audible = audible < gaps ? 0 : audible - gaps;
    *frame = file_clock.offset + audible;
    return true;
}

bool audio_scene_file_position(uint32_t *frame)
{
    if (!audio_scene_file_timeline(frame)) return false;
    uint32_t prepared = file_clock.offset + file_clock.mixed;
    if (*frame > prepared) *frame = prepared;
    return true;
}

bool audio_scene_file_finished(void)
{
    return file_clock.active && !file_clock.failed && file_clock.end_known &&
           (int32_t)(mmStreamGetPosition() - file_clock.end) >= 0;
}

void audio_scene_stop_file(void)
{
    if (!file_clock.active) return;
    mmStreamClose();
    memset(&file_clock, 0, sizeof(file_clock));
    voice_stop(&music); voice_stop(&sfx);
    g_audio_status.music_playing = g_audio_status.sfx_playing = 0;
    scene_paused = false;
    open_stream(false);
}

bool audio_scene_play(bool is_music, unsigned index, int gain_q12,
                      uint32_t offset_frames, uint32_t fade_frames)
{
    if (!g_audio_status.initialized || gain_q12 < 0 ||
        index >= (is_music ? g_audio_status.music_count : g_audio_status.sfx_count)) return false;
    audio_scene_stop_file();
    Voice *v = is_music ? &music : &sfx;
    const AudioEntry *entry = &entries[(is_music ? 0 : g_audio_status.music_count) + index];
    voice_stop(v);
    if (is_music) {
        g_audio_status.music_index = index;
        g_audio_status.music_frames = offset_frames;
        g_audio_status.music_playing = 0;
    } else {
        g_audio_status.sfx_index = index;
        g_audio_status.sfx_frames = offset_frames;
        g_audio_status.sfx_playing = 0;
    }
    // Restoring beyond a one-shot's end is a successful, already-finished play.
    if (!is_music && offset_frames >= entry->frames) {
        scene_mode = true;
        g_audio_status.ui_version++;
        return true;
    }
    if (!voice_start(v, entry, is_music, offset_frames, SCENE_READY_FRAMES)) {
        g_audio_status.ui_version++;
        return false;
    }
    int state = audio_lock();
    scene_mode = true;
    v->gain_q12 = fade_frames ? 0 : gain_q12;
    voice_fade(v, gain_q12, fade_frames, false);
    if (is_music) {
        g_audio_status.music_playing = 1;
        g_audio_status.music_starts++;
    } else {
        g_audio_status.sfx_playing = 1;
        g_audio_status.sfx_starts++;
    }
    __asm__ volatile("" ::: "memory");
    v->enabled = true;
    audio_unlock(state);
    g_audio_status.ui_version++;
    return true;
}

void audio_scene_stop(bool is_music, uint32_t fade_frames)
{
    if (!g_audio_status.initialized) return;
    Voice *v = is_music ? &music : &sfx;
    int state = audio_lock();
    scene_mode = true;
    if (v->enabled && fade_frames) voice_fade(v, 0, fade_frames, true);
    else {
        v->enabled = false;
        if (is_music) g_audio_status.music_playing = 0;
        else g_audio_status.sfx_playing = 0;
        v->close_pending = true;
    }
    audio_unlock(state);
    // Immediate stops originate on the main thread and can release the file now.
    if (v->close_pending) voice_stop(v);
    g_audio_status.ui_version++;
}

void audio_scene_pause(bool paused)
{
    scene_paused = paused;
}

void audio_scene_volumes(unsigned music_q12,unsigned sfx_q12)
{
    int state=audio_lock();
    master_music=music_q12>4096?4096:music_q12;
    master_sfx=sfx_q12>4096?4096:sfx_q12;
    audio_unlock(state);
}

static void voice_snapshot(const Voice*v,AudioVoiceSnapshot*s,bool is_music)
{
    memset(s,0,sizeof(*s));
    if(!v->enabled)return;
    uint32_t position=voice_position(v,v->read);
    if(!v->loop&&position>=v->entry->frames)return;
    s->enabled=1;s->index=(uint32_t)(v->entry-entries)-(is_music?0:g_audio_status.music_count);
    s->loop=v->loop;s->offset_frames=position;s->gain_q12=v->gain_q12;
    s->fade_step=v->fade_step;s->fade_target=v->fade_target;
    s->fade_total=v->fade_total;s->fade_left=v->fade_left;
    s->fade_remainder=v->fade_remainder;s->fade_error=v->fade_error;
    s->flags=(v->fade_down?AUDIO_SCENE_FADE_DOWN:0)|(v->fade_stop?AUDIO_SCENE_FADE_STOP:0);
}
bool audio_scene_snapshot(AudioSceneSnapshot*s)
{
    if(!s||!g_audio_status.initialized||file_clock.active||(!scene_mode&&(music.enabled||sfx.enabled)))return false;
    int state=audio_lock();memset(s,0,sizeof(*s));s->version=AUDIO_SCENE_SNAPSHOT_VERSION;
    s->paused=scene_paused;s->master_music=master_music;s->master_sfx=master_sfx;
    voice_snapshot(&music,&s->music,true);voice_snapshot(&sfx,&s->sfx,false);
    audio_unlock(state);return true;
}
static bool voice_snapshot_valid(const AudioVoiceSnapshot*s,bool is_music)
{
    if(s->enabled>1)return false;
    if(!s->enabled){AudioVoiceSnapshot empty={0};return !memcmp(s,&empty,sizeof(empty));}
    unsigned count=is_music?g_audio_status.music_count:g_audio_status.sfx_count;
    if(s->index>=count||s->loop>1||s->gain_q12<0||s->fade_target<0||s->flags&~3u)return false;
    if((s->flags&AUDIO_SCENE_FADE_STOP)&&(!s->fade_left||s->fade_target))return false;
    const AudioEntry*entry=&entries[(is_music?0:g_audio_status.music_count)+s->index];
    if(s->offset_frames>=(s->loop?entry->loop_end:entry->frames))return false;
    if(s->fade_left>s->fade_total)return false;
    if(!s->fade_total)return !s->fade_left&&!s->fade_remainder&&!s->fade_error&&!s->fade_step&&s->fade_target==s->gain_q12&&!s->flags;
    if(s->fade_remainder>=s->fade_total||s->fade_error>=s->fade_total)return false;
    bool down=(s->flags&AUDIO_SCENE_FADE_DOWN)!=0;
    if((down&&s->fade_step>0)||(!down&&s->fade_step<0))return false;
    uint64_t extra=((uint64_t)s->fade_error+(uint64_t)s->fade_remainder*s->fade_left)/s->fade_total;
    int64_t target=(int64_t)s->gain_q12+(int64_t)s->fade_step*s->fade_left+(down?-(int64_t)extra:(int64_t)extra);
    return target==s->fade_target;
}
bool audio_scene_snapshot_valid(const AudioSceneSnapshot*s)
{
    return s&&entries&&g_audio_status.initialized&&s->version==AUDIO_SCENE_SNAPSHOT_VERSION&&s->paused<=1&&
        s->master_music<=4096&&s->master_sfx<=4096&&voice_snapshot_valid(&s->music,true)&&voice_snapshot_valid(&s->sfx,false);
}
static bool voice_restore(Voice*v,const AudioVoiceSnapshot*s,bool is_music)
{
    if(!s->enabled)return true;
    const AudioEntry*entry=&entries[(is_music?0:g_audio_status.music_count)+s->index];
    if(!voice_start(v,entry,s->loop,s->offset_frames,SCENE_READY_FRAMES))return false;
    v->gain_q12=s->gain_q12;v->fade_step=s->fade_step;v->fade_target=s->fade_target;
    v->fade_total=s->fade_total;v->fade_left=s->fade_left;v->fade_remainder=s->fade_remainder;v->fade_error=s->fade_error;
    v->fade_down=(s->flags&AUDIO_SCENE_FADE_DOWN)!=0;v->fade_stop=(s->flags&AUDIO_SCENE_FADE_STOP)!=0;
    return true;
}
bool audio_scene_restore(const AudioSceneSnapshot*s)
{
    if(!audio_scene_snapshot_valid(s))return false;
    audio_ui_stop();
    /* Remove queued pre-restore samples. Reopen to a silent paused prefill;
     * resource I/O and decoding are never performed under the IRQ lock. */
    scene_paused=true;mmStreamClose();memset(&file_clock,0,sizeof(file_clock));
    voice_stop(&music);voice_stop(&sfx);scene_mode=true;
    g_audio_status.music_playing=g_audio_status.sfx_playing=0;
    bool good=voice_restore(&music,&s->music,true)&&voice_restore(&sfx,&s->sfx,false);
    if(!good){voice_stop(&music);voice_stop(&sfx);open_stream(false);return false;}
    master_music=s->master_music;master_sfx=s->master_sfx;
    music.enabled=s->music.enabled;sfx.enabled=s->sfx.enabled;
    g_audio_status.music_index=s->music.index;g_audio_status.sfx_index=s->sfx.enabled?s->sfx.index:UINT32_MAX;
    g_audio_status.music_frames=s->music.offset_frames;g_audio_status.sfx_frames=s->sfx.offset_frames;
    g_audio_status.music_playing=music.enabled;g_audio_status.sfx_playing=sfx.enabled;
    if(music.enabled)g_audio_status.music_starts++;
    if(sfx.enabled)g_audio_status.sfx_starts++;
    open_stream(false);g_audio_status.ui_version++;return true;
}

static void browser_mode(void)
{
    audio_scene_stop_file();
    int state = audio_lock();
    scene_mode = scene_paused = false;
    music.gain_q12 = sfx.gain_q12 = 4096;
    voice_fade(&music, 4096, 0, false);
    voice_fade(&sfx, 4096, 0, false);
    audio_unlock(state);
}

void audio_demo_toggle_music(void)
{
    if (!g_audio_status.initialized) return;
    browser_mode();
    if (g_audio_status.music_playing) {
        voice_stop(&music);
        g_audio_status.music_playing = 0;
    } else {
        g_audio_status.music_frames = 0;
        if (voice_start(&music, &entries[g_audio_status.music_index], true, 0, music.capacity)) {
            g_audio_status.music_playing = 1;
            g_audio_status.music_starts++;
            __asm__ volatile("" ::: "memory");
            music.enabled = true;
        }
    }
    g_audio_status.ui_version++;
}

void audio_demo_change_music(int direction)
{
    if (!g_audio_status.initialized) return;
    browser_mode();
    bool playing = g_audio_status.music_playing;
    if (playing) { voice_stop(&music); g_audio_status.music_playing = 0; }
    unsigned count = g_audio_status.music_count;
    g_audio_status.music_index = (g_audio_status.music_index + count + direction) % count;
    if (playing) audio_demo_toggle_music();
    g_audio_status.ui_version++;
}

void audio_demo_next_sfx(void)
{
    if (!g_audio_status.initialized) return;
    browser_mode();
    g_audio_status.sfx_playing = 0;
    g_audio_status.sfx_index = (g_audio_status.sfx_index + 1) % g_audio_status.sfx_count;
    g_audio_status.sfx_frames = 0;
    if (voice_start(&sfx, &entries[g_audio_status.music_count + g_audio_status.sfx_index], false, 0, sfx.capacity)) {
        g_audio_status.sfx_playing = 1;
        g_audio_status.sfx_starts++;
        __asm__ volatile("" ::: "memory");
        sfx.enabled = true;
    }
    g_audio_status.ui_version++;
}

const char *audio_demo_music_name(void)
{
    if (file_clock.active) return file_entry.name;
    return entries ? entries[g_audio_status.music_index].name : "unavailable";
}

const char *audio_demo_sfx_name(void)
{
    return entries && g_audio_status.sfx_index < g_audio_status.sfx_count ?
        entries[g_audio_status.music_count + g_audio_status.sfx_index].name : "Press X to play";
}
#else
uint32_t audio_perf_now(void) { return 0; }
bool audio_demo_init(void) { return false; }
void audio_demo_service(void) {}
bool audio_scene_play(bool is_music, unsigned index, int gain_q12,
                      uint32_t offset_frames, uint32_t fade_frames)
{
    (void)is_music; (void)index; (void)gain_q12; (void)offset_frames; (void)fade_frames;
    return false;
}
void audio_scene_stop(bool is_music, uint32_t fade_frames) { (void)is_music; (void)fade_frames; }
void audio_scene_pause(bool paused) { (void)paused; }
bool audio_scene_snapshot(AudioSceneSnapshot*s){(void)s;return false;}
bool audio_scene_snapshot_valid(const AudioSceneSnapshot*s){(void)s;return false;}
bool audio_scene_restore(const AudioSceneSnapshot*s){(void)s;return false;}
void audio_scene_volumes(unsigned m,unsigned s){(void)m;(void)s;}
bool audio_ui_prepare(void) { return false; }
void audio_ui_play(AudioUiCue cue) { (void)cue; }
void audio_ui_stop(void) {}
bool audio_scene_play_file(const char *path, uint32_t frames, int gain, uint32_t offset, uint32_t fade)
{ (void)path; (void)frames; (void)gain; (void)offset; (void)fade; return false; }
bool audio_scene_file_position(uint32_t *frame) { (void)frame; return false; }
bool audio_scene_file_timeline(uint32_t *frame) { (void)frame; return false; }
bool audio_scene_file_finished(void) { return false; }
void audio_scene_stop_file(void) {}
void audio_demo_toggle_music(void) {}
void audio_demo_change_music(int direction) { (void)direction; }
void audio_demo_next_sfx(void) {}
const char *audio_demo_music_name(void) { return "unavailable"; }
const char *audio_demo_sfx_name(void) { return "unavailable"; }
#endif
