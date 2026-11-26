// Shipping mixer and IMA decoder; fake only filesystem mount and hardware time.
#include <assert.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
static const char *root;
static int in_callback;
static FILE *mounted_open(const char *path,const char *mode)
{
    assert(!in_callback);char local[1024];
    if(!strncmp(path,"nitro:/",7)){snprintf(local,sizeof(local),"%s/%s",root,path+7);return fopen(local,mode);}
    return fopen(path,mode);
}
static int checked_close(FILE *file){assert(!in_callback);return fclose(file);}
#define fopen mounted_open
#define fclose checked_close
#include "../source/audio_demo.c"
#undef fopen
#undef fclose
mm_stream mock_stream;

static void mix(unsigned count)
{
    int16_t out[4096*2];assert(count<=4096);in_callback=1;
    assert(mock_stream.callback(count,out,MM_STREAM_16BIT_STEREO)==count);in_callback=0;
}
static void position(unsigned hardware,unsigned expected,bool done)
{
    mock_stream_position=hardware;uint32_t frame=UINT32_MAX;
    assert(audio_scene_file_position(&frame));
    if(frame!=expected){fprintf(stderr,"hardware %u audible source %u, expected %u\n",hardware,frame,expected);abort();}
    assert(audio_scene_file_finished()==done);
}
int main(int argc,char **argv)
{
    assert(argc==2||argc==3);root=argv[1];assert(audio_demo_init());
    if(argc==3){
        FILE *pcm=fopen(argv[2],"rb");assert(pcm);
        assert(audio_scene_play_file("video/actual.wav",1283072,4096,0,0));
        int16_t expected[4096],actual[4096];
        assert(fread(expected,4,2032,pcm)==2032);
        assert(!memcmp(expected,mock_stream_prefill,2032*4));
        for(unsigned frame=2032;frame<1283072;){
            unsigned count=1283072-frame;if(count>256)count=256;
            mock_stream_position=frame;audio_demo_service();
            assert(fread(expected,4,count,pcm)==count);
            in_callback=1;assert(mock_stream.callback(count,actual,MM_STREAM_16BIT_STEREO)==count);in_callback=0;
            assert(!memcmp(expected,actual,count*4));frame+=count;
        }
        position(1283072,1283072,true);
        assert(!g_audio_status.errors&&!g_audio_status.music_underruns);
        audio_scene_stop_file();fclose(pcm);free(entries);
        puts("PASS file PCM: complete 1,283,072 stereo frames vs FFmpeg, padded final block excluded");return 0;
    }
    assert(!mock_stream.manual);assert(sizeof(AudioStatus)==88);
    audio_scene_pause(true);
    assert(audio_scene_play_file("video/clip.wav",6000,4096,0,0));
    assert(!audio_scene_file_position(&(uint32_t){0}));
    audio_scene_pause(false);mix(100);mock_stream_position=2031;
    assert(!audio_scene_file_position(&(uint32_t){0}));position(2032,0,false);position(2050,18,false);
    audio_scene_stop_file();
    // 2032 frames are initially mixed by the real Maxmod open, but none audible.
    assert(audio_scene_play_file("video/clip.wav",6000,4096,0,0));
    assert(mock_stream.manual && g_audio_status.music_frames==2032);
    position(0,0,false);position(50,50,false);position(2032,2032,false);
    // Pausing creates a queued output gap; already queued source is still heard.
    audio_scene_pause(true);mix(100);position(2000,2000,false);
    position(2082,2032,false);position(2132,2032,false);
    audio_scene_pause(false);audio_demo_service();mix(500);
    position(2200,2100,false);position(2632,2532,false);
    // A second gap must not double-subtract the first after it is retired.
    audio_scene_pause(true);mix(10);audio_scene_pause(false);mix(10);
    position(2647,2537,false);
    // New file seek flushes old queued sound; exact block-internal seek.
    assert(audio_scene_play_file("video/clip.wav",6000,2048,504,0));
    position(0,504,false);position(31,535,false);
    assert(music.ring[0]==12000 && music.ring[1]==-8000);
    audio_scene_stop_file();assert(!mock_stream.manual);
    assert(!audio_scene_file_position(&(uint32_t){0}) && !audio_scene_file_finished());
    // True frame count, not the final padded IMA block, determines completion.
    assert(audio_scene_play_file("video/clip.wav",6000,4096,5998,0));
    position(0,5998,false);position(1,5999,false);position(2,6000,true);position(500,6000,true);
    audio_demo_service();assert(!music.file);
    audio_scene_stop_file();
    assert(!audio_scene_play_file("video/clip.wav",999999,4096,0,0));
    assert(!audio_scene_play_file("../clip.wav",6000,4096,0,0));
    assert(!audio_scene_play_file("video/bad.wav",6000,4096,0,0));
    assert(!audio_scene_play_file("video/clip.wav",6000,-1,0,0));
    // Switching back to normal catalog playback restores the original auto API.
    assert(audio_scene_play_file("video/clip.wav",6000,4096,0,0));
    assert(audio_scene_play(true,0,4096,0,0));assert(!mock_stream.manual);
    assert(!g_audio_status.music_underruns && !g_audio_status.sfx_underruns);
    voice_stop(&music);voice_stop(&sfx);free(entries);
    puts("PASS audio file: stereo IMA, real prefill, audible hardware clock, queued pause, seek, exact EOF, legacy auto restore");
}
