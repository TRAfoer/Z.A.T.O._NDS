#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include "../source/audio_demo.h"
static const char *root;
static uint32_t cursor;
static unsigned audio_starts;
static bool audio_active,paused;
volatile AudioStatus g_audio_status;
static FILE *mounted_open(const char *path,const char *mode)
{char local[1024];assert(!strncmp(path,"nitro:/",7));snprintf(local,sizeof(local),"%s/%s",root,path+7);return fopen(local,mode);}
#define fopen mounted_open
#include "../source/story_video.c"
#undef fopen
void audio_demo_service(void){}
bool audio_scene_play_file(const char *p,uint32_t n,int gain,uint32_t offset,uint32_t fade)
{assert(!strcmp(p,"sound.wav")&&n==66000&&gain==2048&&!fade);assert(test_dma_count[0]&&test_dma_count[0]==test_dma_count[2]&&!test_dma_count[1]&&!test_dma_count[3]);audio_starts++;cursor=offset;audio_active=true;return true;}
bool audio_scene_file_position(uint32_t *frame){if(!audio_active)return false;*frame=cursor;return true;}
bool audio_scene_file_timeline(uint32_t *frame){return audio_scene_file_position(frame);}
bool audio_scene_file_finished(void){return cursor>=66000;}
void audio_scene_stop_file(void){audio_active=false;}
void audio_scene_stop(bool m,uint32_t fade){(void)m;assert(!fade);}
void audio_scene_pause(bool p){paused=p;}
static void commit(void){test_vblank++;story_video_commit();}
static void check_screens(unsigned frame){
    assert(test_dma_count[0]==test_dma_count[2]&&!test_dma_count[1]&&!test_dma_count[3]);
    assert(test_dma_vblank[0]==test_dma_vblank[2]);
    assert(test_dma_source[0]==video.bitmap&&test_dma_source[2]==video.gpu_palette);
    assert(test_bgs[3].pa==256&&test_bgs[3].pd==256&&!test_bgs[3].x&&!test_bgs[3].y);
    for(unsigned layer=4;layer<8;layer++)assert(!test_bgs[layer].shown);
    for(unsigned y=0;y<192;y++)for(unsigned x=0;x<256;x++){
        unsigned expected=(y<24||y>=168)?0:(x+3*(y-24)+frame)%g_story_video_status.colors+1;
        assert(test_screen_index(3,x,y)==expected);
    }
    assert(!test_bg_palette[0]&&!test_sub_bg_palette[0]);
    if(frame>=4)assert(test_bg_palette[1]==0x801f);
}

int main(int argc,char **argv)
{
    assert(argc==2||argc==3);root=argv[1];
    if(argc==3){assert(!story_video_open("movie.zpv","sound.wav",66000,2048));assert(!story_video_active()&&!test_sub_init_count);return 0;}
    assert(!story_video_open("bad.zpv","sound.wav",66000,2048));
    assert(!story_video_active());
    for(unsigned layer=4;layer<8;layer++)test_bgs[layer].shown=1;
    test_sub_bg_palette[0]=0x7fff;assert(story_video_open("movie.zpv","sound.wav",66000,2048));
    assert(story_video_active()&&!audio_active);
    assert(test_sub_init_count==0&&test_sub_mode==MODE_5_2D&&test_bank_c==VRAM_C_SUB_BG);
    assert(test_bgs[3].shown&&test_bgs[3].base==8);
    commit();assert(g_story_video_status.frame==0);check_screens(0);
    story_video_service();assert(audio_active&&audio_starts==1);
    for(unsigned frame=1;frame<6;frame++){
        for(unsigned i=0;i<4&&!g_story_video_status.ready;i++)story_video_service();
        assert(g_story_video_status.ready);
        unsigned count=test_dma_count[0];cursor=frame*11025-1;commit();assert(g_story_video_status.frame==frame-1&&test_dma_count[0]==count&&test_dma_count[1]==0);
        cursor++;commit();assert(g_story_video_status.frame==frame);check_screens(frame);assert(audio_starts==1);
    }
    story_video_pause(true);assert(paused&&g_story_video_status.paused);
    assert(story_video_seek(22050));
    for(unsigned i=0;i<10&&g_story_video_status.seeking;i++)story_video_service();
    assert(g_story_video_status.ready);commit();check_screens(2);
    assert(g_story_video_status.frame==2);story_video_service();assert(cursor==22050&&paused&&audio_starts==2);
    unsigned count=test_dma_count[0],saved_frame=g_story_video_status.frame;
    for(unsigned i=0;i<3;i++){story_video_service();commit();assert(g_story_video_status.frame==saved_frame&&test_dma_count[0]==count&&test_dma_count[1]==0&&audio_starts==2);}
    story_video_pause(false);assert(!paused);
    assert(story_video_seek(66150));
    for(unsigned i=0;i<10&&g_story_video_status.seeking;i++)story_video_service();
    commit();check_screens(5);story_video_service();assert(audio_starts==3);cursor=66150;story_video_service();
    assert(story_video_finished()&&!g_story_video_status.errors);
    story_video_close();assert(!story_video_active()&&!audio_active&&!test_bgs[3].shown&&!test_bgs[7].shown);
    count=test_dma_count[0];story_video_service();commit();assert(test_dma_count[0]==count&&test_dma_count[1]==0);
    assert(story_video_open("movie.zpv","sound.wav",66000,2048));commit();check_screens(0);story_video_service();assert(audio_starts==4);
    story_video_close();assert(!test_bgs[3].shown&&!test_bgs[7].shown&&!audio_active);
    puts("PASS story video: one decode/audio clock, Main bitmap/palette same-VBlank DMA, Main borders/Sub black, palettes, paused seek, skip/close, EOF");
}
