#ifndef ZATO_STORY_VIDEO_H
#define ZATO_STORY_VIDEO_H
#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint32_t magic,active,ready,frame,decoded,displayed,total_frames,fps,colors;
    uint32_t audio_frame,paused,seeking,finished,errors,late_frames,max_late_samples;
    uint32_t bytes,reads,commits,source_hash,requested_seek;
} StoryVideoStatus;
extern volatile StoryVideoStatus g_story_video_status;
// Main-thread operations. Filenames are NitroFS-relative. Full bounded index
// validation happens before display setup; source/payload reads never run in IRQ.
bool story_video_open(const char *video_path,const char *audio_path,
                      uint32_t true_audio_frames,int gain_q12);
void story_video_service(void);
void story_video_pause(bool paused);
bool story_video_seek(uint32_t audio_sample);
void story_video_close(void);
bool story_video_active(void);
bool story_video_finished(void);
// Call once per VBlank, after service. Only cache-flushed palette/bitmap DMA and
// state publication occur here. Main BG3/bank B keeps the full picture and
// black bars; the lower display stays black with all its BG layers hidden.
// Only the upper display receives movie bitmap/palette DMA. Close hides BG3;
// caller restores its ordinary Bmp16 scene setup afterward. No HUD.
void story_video_commit(void);
#endif
