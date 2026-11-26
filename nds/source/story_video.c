#include "story_video.h"
#include "audio_demo.h"
#include "asset_pack.h"
#include <nds.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

volatile StoryVideoStatus g_story_video_status;
#ifdef ZATO_NITROFS
#define VIDEO_RATE 22050u
#define VIDEO_PACKET 65536u
#define VIDEO_RAW 36864u
#define VIDEO_BITMAP (256u*192u)
#define VIDEO_KEY 1u
#define VIDEO_XOR 2u
#define VIDEO_TILE 4u
#define VIDEO_RAW_PACKET 8u
#define VIDEO_SKIP 16u
typedef struct {
    char magic[8];
    uint32_t version,width,height,screen_width,screen_height,x,y,fps,frames,duration_us;
    uint32_t colors,bpp,raw_bytes,families,entry_bytes,index_at,palette_at,payload_at,file_bytes,max_packed;
} VideoHeader;
typedef struct { uint32_t offset,bytes,decoded,flags,key,palette,pts,hash,reserved; } VideoFrame;
_Static_assert(sizeof(VideoHeader)==88,"Video header ABI");
_Static_assert(sizeof(VideoFrame)==36,"Video index ABI");
static struct {
    FILE *file;
    VideoHeader h;
    VideoFrame *index;
    uint16_t *palettes;
    uint8_t *raw,*work,*packet,*bitmap;
    uint16_t gpu_palette[256] __attribute__((aligned(32)));
    uint32_t next,prepared,displayed,seek_target,seek_sample,audio_frames,end_sample;
    uint32_t audio_errors,audio_underruns;
    int gain,bg;
    char audio_path[48];
    bool active,audio_started,audio_pending,seeking,paused,finished,displays_ready;
} video;

static bool video_fail(void)
{
    g_story_video_status.errors++; video.finished=true;
    g_story_video_status.finished=1;
    audio_scene_stop_file();
    return false;
}
static bool video_read(uint32_t offset,void *data,uint32_t bytes)
{
    if(offset>video.h.file_bytes||bytes>video.h.file_bytes-offset||fseek(video.file,offset,SEEK_SET))return false;
    uint8_t *out=data;
    while(bytes){
        unsigned count=bytes>4096?4096:bytes;
        if(fread(out,1,count,video.file)!=count)return false;
        g_story_video_status.bytes+=count;g_story_video_status.reads++;
        out+=count;bytes-=count;
        audio_demo_service();
    }
    return true;
}
static unsigned video_u16(const uint8_t *p){return p[0]|p[1]<<8;}
static bool decode_packet(const VideoFrame *frame)
{
    unsigned bytes=frame->decoded,raw=video.h.raw_bytes;
    if(!video_read(frame->offset,video.packet,frame->bytes))return false;
    if(frame->flags&VIDEO_RAW_PACKET)memcpy(video.work,video.packet,bytes);
    else if(lz10_decode(video.packet,frame->bytes,video.work,VIDEO_PACKET)!=(int)bytes)return false;
    unsigned mode=frame->flags&~VIDEO_RAW_PACKET;
    if(mode==VIDEO_KEY)memcpy(video.raw,video.work,raw);
    else if(mode==VIDEO_XOR){
        uint32_t *dest=(uint32_t*)video.raw,*src=(uint32_t*)video.work;
        for(unsigned i=0;i<raw/4;i++)dest[i]^=src[i];
    }else if(mode==VIDEO_TILE){
        if(bytes<72)return false;
        unsigned cursor=72,tile=raw/576;
        for(unsigned i=0;i<576;i++)if(video.work[i/8]&(1u<<(i&7))){
            if(tile>bytes-cursor)return false;
            memcpy(video.raw+i*tile,video.work+cursor,tile);cursor+=tile;
        }
        if(cursor!=bytes)return false;
    }else if(mode==VIDEO_SKIP){
        unsigned cursor=0,position=0;
        while(position<raw){
            if(bytes-cursor<4)return false;
            unsigned skip=video_u16(video.work+cursor),take=video_u16(video.work+cursor+2);cursor+=4;
            if(skip>raw-position)return false;
            position+=skip;
            if(take>raw-position||take>bytes-cursor||(!skip&&!take))return false;
            for(unsigned i=0;i<take;i++)video.raw[position+i]^=video.work[cursor+i];
            position+=take;cursor+=take;
        }
        if(cursor!=bytes)return false;
    }else return false;
#ifdef ZATO_VIDEO_VERIFY
    if(pixel_hash(video.raw,raw)!=frame->hash)return false;
#endif
    return true;
}
static bool prepare_bitmap(unsigned palette)
{
    unsigned colors=video.h.colors;
    memset(video.bitmap,0,256*24);memset(video.bitmap+256*168,0,256*24);
    for(unsigned ty=0;ty<18;ty++)for(unsigned tx=0;tx<32;tx++){
        const uint8_t *src=video.raw+(ty*32+tx)*(colors==16?32:64);
        uint32_t *dest=(uint32_t*)(video.bitmap+(ty*8+24)*256+tx*8);
        for(unsigned y=0;y<8;y++){
            uint32_t a,b;
            if(colors==16){
                unsigned lo=video_u16(src),hi=video_u16(src+2);src+=4;
                a=(lo&15u)|((lo&0xf0u)<<4)|((lo&0xf00u)<<8)|((lo&0xf000u)<<12);
                b=(hi&15u)|((hi&0xf0u)<<4)|((hi&0xf00u)<<8)|((hi&0xf000u)<<12);
            }else{
                a=((const uint32_t*)src)[0];b=((const uint32_t*)src)[1];src+=8;
                if(colors==48){
                    if(((a|b)&0x80808080u)||((a+0x50505050u)&0x80808080u)||((b+0x50505050u)&0x80808080u))return false;
                }else if((a|b)&(0x01010101u*(256-colors)))return false;
            }
            dest[0]=a+0x01010101u;dest[1]=b+0x01010101u;dest+=64;
        }
    }
    memset(video.gpu_palette,0,sizeof(video.gpu_palette));
    memcpy(video.gpu_palette+1,video.palettes+palette*colors,colors*2);
    DC_FlushRange(video.bitmap,VIDEO_BITMAP);DC_FlushRange(video.gpu_palette,512);
    return true;
}
static bool prepare_frame(void)
{
    if(video.next>=video.h.frames)return true;
    unsigned number=video.next;
    const VideoFrame *frame=&video.index[number];
    if(!decode_packet(frame))return video_fail();
    video.next++;g_story_video_status.decoded++;
    if(video.seeking&&number<video.seek_target)return true;
    if(!prepare_bitmap(frame->palette))return video_fail();
    video.prepared=number;video.seeking=false;g_story_video_status.seeking=0;
    g_story_video_status.source_hash=frame->hash;
    __asm__ volatile("":::"memory");g_story_video_status.ready=1;
    return true;
}
static bool validate_header(uint32_t size)
{
    VideoHeader *h=&video.h;
    if(memcmp(h->magic,"ZATPAL01",8)||h->version!=1||h->width!=256||h->height!=144||
       h->screen_width!=256||h->screen_height!=192||h->x||h->y!=24||
       !h->fps||h->fps>60||!h->duration_us||h->frames>4096||!h->frames||
       (h->colors!=16&&h->colors!=32&&h->colors!=48&&h->colors!=64&&h->colors!=128)||
       h->bpp!=(h->colors==16?4u:8u)||h->raw_bytes!=(h->colors==16?18432u:VIDEO_RAW)||
       h->entry_bytes!=sizeof(VideoFrame)||h->index_at!=sizeof(VideoHeader)||
       h->file_bytes!=size||!h->max_packed||h->max_packed>VIDEO_PACKET)return false;
    if(h->frames!=((uint64_t)h->duration_us*h->fps+999999)/1000000||
       h->families!=(h->frames+h->fps*2-1)/(h->fps*2)||
       h->palette_at!=sizeof(VideoHeader)+h->frames*sizeof(VideoFrame)||
       h->payload_at!=h->palette_at+h->families*h->colors*2||h->payload_at>size)return false;
    return true;
}
static bool validate_index(void)
{
    uint32_t offset=video.h.payload_at,key=0,maximum=0;
    for(unsigned i=0;i<video.h.frames;i++){
        const VideoFrame *f=&video.index[i];unsigned mode=f->flags&~VIDEO_RAW_PACKET;
        if(f->offset!=offset||!f->bytes||f->bytes>video.h.max_packed||f->offset>video.h.file_bytes||
           f->bytes>video.h.file_bytes-f->offset||!f->decoded||f->decoded>VIDEO_PACKET||f->reserved||
           (mode!=VIDEO_KEY&&mode!=VIDEO_XOR&&mode!=VIDEO_TILE&&mode!=VIDEO_SKIP)||
           ((f->flags&VIDEO_RAW_PACKET)&&f->bytes!=f->decoded)||
           ((mode==VIDEO_KEY||mode==VIDEO_XOR)&&f->decoded!=video.h.raw_bytes)||
           f->palette!=i/(video.h.fps*2)||f->pts!=(uint64_t)i*VIDEO_RATE/video.h.fps)return false;
        if(mode==VIDEO_KEY)key=i;
        if(!(i%(video.h.fps*2))&&mode!=VIDEO_KEY)return false;
        if(f->key!=key||i-key>=video.h.fps*2)return false;
        offset+=f->bytes;if(f->bytes>maximum)maximum=f->bytes;
    }
    return offset==video.h.file_bytes&&maximum==video.h.max_packed;
}

void story_video_close(void)
{
    if(video.displays_ready)bgHide(video.bg);
    audio_scene_stop_file();
    if(video.file)fclose(video.file);
    free(video.index);free(video.palettes);free(video.raw);free(video.work);free(video.packet);free(video.bitmap);
    memset(&video,0,sizeof(video));g_story_video_status.active=g_story_video_status.ready=0;
}
bool story_video_open(const char *path,const char *audio_path,uint32_t audio_frames,int gain)
{
    if(!path||!*path||strlen(path)>=96||path[0]=='/'||strchr(path,':')||strchr(path,'\\')||strstr(path,"..")||
       !audio_path||!*audio_path||strlen(audio_path)>=sizeof(video.audio_path)||!audio_frames||gain<0)return false;
    story_video_close();memset((void*)&g_story_video_status,0,sizeof(g_story_video_status));
    g_story_video_status.magic=0x4449565a;g_story_video_status.requested_seek=UINT32_MAX;
    char full[112];snprintf(full,sizeof(full),"nitro:/%s",path);video.file=fopen(full,"rb");
    if(!video.file)goto failed;
    if(fseek(video.file,0,SEEK_END))goto failed;
    long size=ftell(video.file);
    if(size<(long)sizeof(VideoHeader)||fseek(video.file,0,SEEK_SET)||
       fread(&video.h,1,sizeof(video.h),video.file)!=sizeof(video.h)||!validate_header(size))goto failed;
    video.index=malloc(video.h.frames*sizeof(VideoFrame));video.palettes=malloc(video.h.families*video.h.colors*2);
    video.raw=malloc(video.h.raw_bytes);video.work=malloc(VIDEO_PACKET);video.packet=malloc(video.h.max_packed);
    video.bitmap=malloc(VIDEO_BITMAP);
    if(!video.index||!video.palettes||!video.raw||!video.work||!video.packet||!video.bitmap||
       !video_read(video.h.index_at,video.index,video.h.frames*sizeof(VideoFrame))||!validate_index()||
       !video_read(video.h.palette_at,video.palettes,video.h.families*video.h.colors*2))goto failed;
    video.audio_frames=audio_frames;video.gain=gain;strcpy(video.audio_path,audio_path);
    video.end_sample=((uint64_t)video.h.duration_us*VIDEO_RATE+999999)/1000000;
    // A standalone file may have encoder tail padding, never missing content.
    if(audio_frames>video.end_sample||video.end_sample-audio_frames>VIDEO_RATE)goto failed;
    audio_scene_stop(true,0);audio_scene_stop(false,0);audio_scene_pause(false);
    video.active=true;video.audio_pending=true;video.displayed=UINT32_MAX;
    g_story_video_status.active=1;g_story_video_status.total_frames=video.h.frames;
    g_story_video_status.fps=video.h.fps;g_story_video_status.colors=video.h.colors;
    if(!prepare_frame())goto failed;
    videoSetMode(MODE_5_2D);vramSetBankB(VRAM_B_MAIN_BG_0x06020000);
    bgHide(2);video.bg=bgInit(3,BgType_Bmp8,BgSize_B8_256x256,8,0);
    bgSetPriority(video.bg,0);bgShow(video.bg);
    videoSetModeSub(MODE_5_2D);vramSetBankC(VRAM_C_SUB_BG);
    /* OP is upper-screen only. Hide all previous lower-screen layers and
     * use a black backdrop; no second bitmap or palette upload is needed. */
    for(unsigned layer=4;layer<8;layer++)bgHide(layer);
    BG_PALETTE_SUB[0]=0;video.displays_ready=true;
    return true;
failed:
    story_video_close();g_story_video_status.errors++;return false;
}
void story_video_service(void)
{
    if(!video.active||video.finished)return;
    audio_demo_service();
    if(g_story_video_status.requested_seek!=UINT32_MAX){
        uint32_t request=g_story_video_status.requested_seek;g_story_video_status.requested_seek=UINT32_MAX;
        story_video_seek(request);
    }
    if(video.audio_pending&&!video.seeking&&video.displayed==video.prepared&&!g_story_video_status.ready){
        audio_scene_pause(video.paused);
        if(!audio_scene_play_file(video.audio_path,video.audio_frames,video.gain,
                                 video.seek_sample>video.audio_frames?video.audio_frames:video.seek_sample,0)){
            video_fail();return;
        }
        video.audio_started=true;video.audio_pending=false;
        video.audio_errors=g_audio_status.errors;video.audio_underruns=g_audio_status.music_underruns;
    }
    if(video.audio_started){
        uint32_t position;
        if(audio_scene_file_timeline(&position))g_story_video_status.audio_frame=position;
        if(g_audio_status.errors!=video.audio_errors||g_audio_status.music_underruns!=video.audio_underruns){video_fail();return;}
        if(!video.paused&&g_story_video_status.audio_frame>=video.end_sample&&video.displayed==video.h.frames-1){
            video.finished=true;g_story_video_status.finished=1;return;
        }
    }
    if(!g_story_video_status.ready)prepare_frame();
}
void story_video_commit(void)
{
    if(!video.active||video.finished||!g_story_video_status.ready||video.seeking)return;
    uint32_t position=g_story_video_status.audio_frame;
    if(video.audio_started){
        audio_scene_file_timeline(&position);g_story_video_status.audio_frame=position;
        if(video.index[video.prepared].pts>position)return;
    }
    bgUpdate();
    dmaCopy(video.bitmap,bgGetGfxPtr(video.bg),VIDEO_BITMAP);dmaCopy(video.gpu_palette,BG_PALETTE,512);
    video.displayed=video.prepared;g_story_video_status.frame=video.displayed;
    if(video.audio_started){
        uint32_t pts=video.index[video.displayed].pts,late=position>pts?position-pts:0;
        if(late>=VIDEO_RATE/video.h.fps)g_story_video_status.late_frames++;
        if(late>g_story_video_status.max_late_samples)g_story_video_status.max_late_samples=late;
    }
    g_story_video_status.displayed++;g_story_video_status.commits++;g_story_video_status.ready=0;
}
void story_video_pause(bool paused)
{
    if(!video.active)return;
    video.paused=paused;g_story_video_status.paused=paused;audio_scene_pause(paused);
}
bool story_video_seek(uint32_t sample)
{
    if(!video.active)return false;
    if(sample>video.end_sample)sample=video.end_sample;
    unsigned frame=(uint64_t)sample*video.h.fps/VIDEO_RATE;if(frame>=video.h.frames)frame=video.h.frames-1;
    audio_scene_stop_file();audio_scene_pause(video.paused);
    video.next=video.index[frame].key;video.seek_target=frame;video.seek_sample=sample;
    video.audio_started=false;video.audio_pending=true;video.seeking=true;video.finished=false;video.displayed=UINT32_MAX;
    g_story_video_status.ready=g_story_video_status.finished=0;g_story_video_status.seeking=1;
    g_story_video_status.audio_frame=sample;
    return true;
}
bool story_video_active(void){return video.active;}
bool story_video_finished(void){return video.finished;}
#else
bool story_video_open(const char *video,const char *audio,uint32_t frames,int gain)
{(void)video;(void)audio;(void)frames;(void)gain;return false;}
void story_video_service(void){}
void story_video_commit(void){}
void story_video_pause(bool paused){(void)paused;}
bool story_video_seek(uint32_t frame){(void)frame;return false;}
void story_video_close(void){}
bool story_video_active(void){return false;}
bool story_video_finished(void){return false;}
#endif
