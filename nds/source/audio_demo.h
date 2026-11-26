#ifndef ZATO_AUDIO_DEMO_H
#define ZATO_AUDIO_DEMO_H
#include <stdbool.h>
#include <stdint.h>
#include "story_state_types.h"

typedef struct {
    uint32_t magic, initialized, rate, music_count, sfx_count, music_index, sfx_index;
    uint32_t music_playing, sfx_playing, music_starts, sfx_starts, callbacks;
    uint32_t music_underruns, sfx_underruns, errors, music_frames, sfx_frames;
    uint32_t music_loops, sfx_finishes, ui_version, nonzero_samples, output_peak;
} AudioStatus;
extern volatile AudioStatus g_audio_status;
#define AUDIO_PERF_VERSION 1u
/* Independent diagnostics: all durations are Timer1 ticks, each 1024 ARM9 bus
 * cycles (33513982 Hz). Max work includes any interrupt preemption. Min ready
 * is sampled after callbacks while a source is enabled and not paused;
 * UINT32_MAX means no such sample. Empty counts exclude pause and natural EOF.
 * The extended clock must be read at least once per 16-bit wrap (~2.00 s), as
 * normal audio callbacks do. A longer full IRQ stall can undercount its time. */
typedef struct {
    uint32_t magic, version, clock_hz, timer_div;
    uint32_t service_calls, last_service, max_service_gap, max_service_work;
    uint32_t callback_calls, last_callback, max_callback_gap, max_callback_work, max_callback_frames;
    uint32_t music_min_ready, sfx_min_ready, music_empty_frames, sfx_empty_frames;
    uint32_t timer_wraps;
} AudioPerfStatus;
extern volatile AudioPerfStatus g_audio_perf_status;
/* Safe from main or IRQ; returns zero until the audio timer is initialized.
 * Uses Timer1 only. Maxmod keeps Timer0; Timer2/3 are not changed. */
uint32_t audio_perf_now(void);
bool audio_demo_init(void);
void audio_demo_service(void);
// Scene indices are group-local manifest indices; gain is the final Q12 gain.
// Music offsets count total elapsed frames across intro and loop repetitions.
// Linear fades use the starting gain on the first frame and reach the target
// after fade_frames output frames. Pause freezes both source and fade clocks.
bool audio_scene_play(bool is_music, unsigned index, int gain_q12,
                      uint32_t offset_frames, uint32_t fade_frames);
void audio_scene_stop(bool is_music, uint32_t fade_frames);
void audio_scene_pause(bool paused);
#define AUDIO_SCENE_SNAPSHOT_VERSION 1u
#define AUDIO_SCENE_FADE_DOWN 1u
#define AUDIO_SCENE_FADE_STOP 2u
/* Ordinary catalog voices only; file/movie mode cannot be checkpointed here.
 * Capture after pause. Offsets follow consumed source samples, not flow ticks
 * or diagnostic mixed-frame counters. Restore always remains paused. */
bool audio_scene_snapshot(AudioSceneSnapshot *snapshot);
bool audio_scene_snapshot_valid(const AudioSceneSnapshot *snapshot);
bool audio_scene_restore(const AudioSceneSnapshot *snapshot);
void audio_scene_volumes(unsigned music_q12,unsigned sfx_q12);
typedef enum {
    AUDIO_UI_MOVE, AUDIO_UI_CONFIRM, AUDIO_UI_BACK, AUDIO_UI_ERROR
} AudioUiCue;
/* Optional main-thread preload of the existing button catalog sound. No new
 * payloads. Play/stop perform no I/O and never consume ordinary scene voices.
 * MOVE starts immediately at source frame 2205, at 3/4 speed (nearest) and
 * Q12 gain 512. Other cues retain their original start/rate/gains. A new cue
 * replaces the single UI voice; there is no delayed queue.
 * UI sounds follow master_sfx even while the scene is paused. Movie/file mode
 * rejects preparation/play; restore and file playback stop an active cue. */
bool audio_ui_prepare(void);
void audio_ui_play(AudioUiCue cue);
void audio_ui_stop(void);
// Exclusive, nonlooping stereo IMA soundtrack outside the approved catalog.
// path is a NitroFS-relative filename; true_frames excludes padded IMA samples.
// The movie stream is manual so Maxmod's hardware timer supplies an audible
// cursor. Queued pauses become effective when those samples reach the output.
bool audio_scene_play_file(const char *path, uint32_t true_frames, int gain_q12,
                           uint32_t offset_frames, uint32_t fade_frames);
bool audio_scene_file_position(uint32_t *frame);
// The same audible clock continues through the final silent container tail.
bool audio_scene_file_timeline(uint32_t *frame);
bool audio_scene_file_finished(void);
void audio_scene_stop_file(void);
void audio_demo_toggle_music(void);
void audio_demo_change_music(int direction);
void audio_demo_next_sfx(void);
const char *audio_demo_music_name(void);
const char *audio_demo_sfx_name(void);
#endif
