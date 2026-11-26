/* The shipping decoder/callback runs unchanged; only timer MMIO and file paths
 * are supplied by the host. A missing diagnostic is a zero observation so the
 * pre-feature RED exercises a real assertion rather than a link failure. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static volatile uint16_t timer_data[4],timer_control[4];
#define TIMER_DATA(n) timer_data[n]
#define TIMER_CR(n) timer_control[n]
#define TIMER_ENABLE 128u
#define TIMER_DIV_1024 3u
#define BUS_CLOCK 33513982u
static const char *fixture;
static FILE *mapped_open(const char *path,const char *mode)
{
    char local[1024];snprintf(local,sizeof(local),"%s/%s",fixture,path+7);
    assert(!strncmp(path,"nitro:/",7));return fopen(local,mode);
}
static size_t timed_read(void *p,size_t s,size_t n,FILE *f)
{
    if(TIMER_CR(1)&TIMER_ENABLE)TIMER_DATA(1)+=7;
    return fread(p,s,n,f);
}
#define fopen mapped_open
#define fread timed_read
#include "../source/audio_demo.c"
#undef fopen
#undef fread
mm_stream mock_stream;
#ifndef AUDIO_PERF_VERSION
static volatile uint32_t perf_words[18];
static uint32_t audio_perf_now(void){return 0;}
#else
#define perf_words ((volatile uint32_t *)&g_audio_perf_status)
_Static_assert(sizeof(AudioPerfStatus)==18*4,"Independent perf ABI");
#endif
enum {P_MAGIC,P_VERSION,P_HZ,P_DIV,P_SERVICES,P_LAST_SERVICE,P_SERVICE_GAP,P_SERVICE_WORK,
      P_CALLBACKS,P_LAST_CALLBACK,P_CALLBACK_GAP,P_CALLBACK_WORK,P_CALLBACK_FRAMES,
      P_MUSIC_MIN,P_SFX_MIN,P_MUSIC_EMPTY,P_SFX_EMPTY,P_WRAPS};
static void render(unsigned frames)
{
    int16_t output[5000*2];assert(frames<=5000);
    assert(mock_stream.callback(frames,output,MM_STREAM_16BIT_STEREO)==frames);
}
int main(int argc,char **argv)
{
    assert(argc==2);fixture=argv[1];
    assert(sizeof(AudioStatus)==88&&audio_perf_now()==0);
    assert(audio_demo_init());
    assert(perf_words[P_MAGIC]==0x4650415a&&perf_words[P_VERSION]==1);
    assert(perf_words[P_HZ]==33513982&&perf_words[P_DIV]==1024);
    assert(TIMER_CR(1)==(TIMER_ENABLE|TIMER_DIV_1024));
    TIMER_DATA(1)=102;assert(audio_perf_now()==102);
    TIMER_DATA(1)=65000;assert(audio_perf_now()==65000);
    TIMER_DATA(1)=100;assert(audio_perf_now()==65636&&perf_words[P_WRAPS]==1);
    TIMER_DATA(1)=113;assert(audio_perf_now()==65649&&perf_words[P_WRAPS]==1);
    uint32_t initial_callbacks=perf_words[P_CALLBACKS];assert(initial_callbacks==1);
    assert(perf_words[P_CALLBACK_FRAMES]==2032);
    assert(perf_words[P_MUSIC_MIN]==UINT32_MAX&&perf_words[P_SFX_MIN]==UINT32_MAX);
    assert(audio_scene_play(true,0,4096,0,0));
    TIMER_DATA(1)+=10;uint32_t before=audio_perf_now();render(1024);
    assert(perf_words[P_CALLBACKS]==initial_callbacks+1&&perf_words[P_LAST_CALLBACK]==before);
    assert(perf_words[P_MUSIC_MIN]==3072&&perf_words[P_MUSIC_EMPTY]==0);
    TIMER_DATA(1)+=100;audio_demo_service();
    assert(perf_words[P_SERVICES]==1&&perf_words[P_SERVICE_WORK]>0);
    assert(music.write-music.read==4096);
    uint32_t last=perf_words[P_LAST_SERVICE];TIMER_DATA(1)+=300;
    before=audio_perf_now();audio_demo_service();
    assert(perf_words[P_SERVICES]==2&&perf_words[P_SERVICE_GAP]==before-last);
    servicing=true;audio_demo_service();servicing=false;assert(perf_words[P_SERVICES]==2);
    TIMER_DATA(1)+=90;before=audio_perf_now();uint32_t callback_last=perf_words[P_LAST_CALLBACK];
    uint32_t previous_gap=perf_words[P_CALLBACK_GAP];
    render(4100);
    uint32_t gap=before-callback_last;
    assert(perf_words[P_LAST_CALLBACK]==before&&perf_words[P_CALLBACK_GAP]==(gap>previous_gap?gap:previous_gap));
    assert(perf_words[P_CALLBACK_FRAMES]==4100&&perf_words[P_MUSIC_MIN]==0);
    assert(perf_words[P_MUSIC_EMPTY]==4&&g_audio_status.music_underruns==1);
    audio_scene_pause(true);render(99);
    assert(perf_words[P_MUSIC_EMPTY]==4&&g_audio_status.music_underruns==1);
    audio_scene_pause(false);audio_scene_stop(true,0);
    assert(audio_scene_play(false,0,4096,0,0));render(5000);
    assert(perf_words[P_SFX_MIN]==0&&perf_words[P_SFX_EMPTY]==904);
    assert(g_audio_status.sfx_underruns==1);
    audio_scene_stop(false,0);render(20);assert(perf_words[P_SFX_EMPTY]==904);
    puts("audio perf clock/wrap/service/callback/pause/empty: PASS");return 0;
}
