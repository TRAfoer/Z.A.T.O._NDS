#ifndef TEST_MAXMOD9_H
#define TEST_MAXMOD9_H
#include <stdbool.h>
#include <stdint.h>
typedef uint32_t mm_word;
typedef void *mm_addr;
typedef int mm_stream_formats;
enum {MM_STREAM_16BIT_STEREO=3, MM_TIMER0=0};
typedef struct {
    unsigned sampling_rate, buffer_length;
    mm_word (*callback)(mm_word, mm_addr, mm_stream_formats);
    int format, timer;
    bool manual;
} mm_stream;
extern mm_stream mock_stream;
static uint32_t mock_stream_position;
static int16_t mock_stream_prefill[2048*2];
static inline bool mmInitNoSoundbank(void) { return true; }
static inline void mmStreamOpen(mm_stream *s) {
    mock_stream = *s; mock_stream_position=0;
    s->callback((s->buffer_length&~15u)-16,mock_stream_prefill,s->format);
}
static inline void mmStreamClose(void) {}
static inline void mmStreamUpdate(void) {}
static inline mm_word mmStreamGetPosition(void) { return mock_stream.manual?mock_stream_position:0; }
static inline void mmStreamVolume(unsigned volume) { (void)volume; }
#endif
