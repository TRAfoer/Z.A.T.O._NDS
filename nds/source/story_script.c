#include "story_script.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

_Static_assert(sizeof(StoryEvent)==24,"story event ABI");
_Static_assert(sizeof(StoryChapter)==32,"story chapter ABI");
extern void audio_demo_service(void) __attribute__((weak));
static uint32_t u32(const uint8_t*p){return p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;}
static uint32_t crc32(const uint8_t*p,uint32_t n){uint32_t c=~0u;while(n--){c^=*p++;for(unsigned i=0;i<8;i++)c=(c>>1)^(0xedb88320u&-(c&1));}return ~c;}
void story_script_close(StoryScript*s){free(s->data);memset(s,0,sizeof(*s));}
bool story_script_open(StoryScript*s,const char*path)
{
    memset(s,0,sizeof(*s));FILE*f=fopen(path,"rb");
    if(!f){snprintf(s->error,sizeof(s->error),"story script open");return false;}
    if(fseek(f,0,SEEK_END))goto bad_file;
    long size=ftell(f);if(size<64||size>2*1024*1024||fseek(f,0,SEEK_SET))goto bad_file;
    s->data=malloc((size_t)size);if(!s->data)goto bad_file;
    for(uint32_t at=0;at<(uint32_t)size;){
        uint32_t chunk=(uint32_t)size-at;if(chunk>4096)chunk=4096;
        if(fread(s->data+at,1,chunk,f)!=chunk)goto bad_file;
        at+=chunk;if(audio_demo_service)audio_demo_service();
    }
    fclose(f);f=NULL;
    const uint8_t*p=s->data;
    uint32_t n=u32(p+12),nc=u32(p+16),eo=u32(p+24),co=u32(p+28),total=(uint32_t)size;
    if(memcmp(p,"ZSTORY01",8)||u32(p+8)!=1||u32(p+20)!=24||eo!=64||
       u32(p+32)!=total||!n||!nc||nc>64||n>(total-eo)/24||co!=eo+n*24||co>total||
       nc>(total-co)/32||co+nc*32!=total||!u32(p+40)||!u32(p+44)||
       u32(p+48)||u32(p+52)||u32(p+56)||u32(p+60)||crc32(p+64,total-64)!=u32(p+36))goto bad;
    s->count=n;s->chapter_count=nc;s->text_count=u32(p+40);s->source_count=u32(p+44);
    s->events=(const StoryEvent*)(p+eo);s->chapters=(const StoryChapter*)(p+co);
    for(unsigned i=0;i<nc;i++){
        const StoryChapter*c=&s->chapters[i];
        if(!c->name[0]||c->name[15]||c->first_pc>=c->end_pc||c->end_pc>n||
           c->first_pc!=(i?s->chapters[i-1].end_pc:0)||
           c->first_text>s->text_count||c->text_count>s->text_count-c->first_text)goto bad;
    }
    if(s->chapters[nc-1].end_pc!=n||s->events[n-1].op!=ST_END)goto bad;
    for(unsigned i=0;i<n;i++){
        const StoryEvent*e=&s->events[i];
        if(e->op<ST_TEXT||e->op>ST_SPECIAL||e->flags>(e->op==ST_TEXT?7:0))goto bad;
        switch(e->op){
        case ST_TEXT:if(e->a<0||(uint32_t)e->a>=s->text_count||e->b<TR_NONE||e->b>TR_EASEINLEFT||e->c<0||e->d<0)goto bad;break;
        case ST_SHOW:if(e->a<0||e->b<0||e->b>=1024||e->d<0)goto bad;break;
        case ST_HIDE:if(e->a<0||e->a>=1024)goto bad;break;
        case ST_WAIT:if(e->a < -1||e->b<0||e->b>1||(e->a==-1&&e->b))goto bad;break;
        case ST_TRANSITION:if(e->a<TR_NONE||e->a>TR_EASEINLEFT||e->b<0)goto bad;break;
        case ST_MUSIC:case ST_SFX:if(e->a<0||e->b<0||e->c<0||e->d!=(e->op==ST_MUSIC))goto bad;break;
        case ST_STOP_MUSIC:case ST_STOP_SFX:if(e->a<0)goto bad;break;
        case ST_WINDOW:case ST_QUICK:if(e->a<0||e->a>1)goto bad;break;
        case ST_LITERAL:if(e->a<0||(uint32_t)e->a>=s->text_count)goto bad;break;
        case ST_DATE:if(e->a < -1||(e->a>=0&&(uint32_t)e->a>=s->text_count))goto bad;break;
        case ST_UNLOCK:if(e->a<0||e->a>=64||e->b<0||e->b>1)goto bad;break;
        case ST_JUMP:
            if(e->a<0||(uint32_t)e->a>=n||e->b<0||(uint32_t)e->b>=nc||
               (uint32_t)e->a!=s->chapters[e->b].first_pc)goto bad;
            break;
        case ST_END:if(i!=n-1)goto bad;break;
        case ST_SPECIAL:if(e->a < -1||e->a>=32||e->b<0||e->b>4||e->c<0||e->c>1)goto bad;break;
        default:break;
        }
    }
    return true;
bad_file:if(f)fclose(f);
bad:free(s->data);memset(s,0,sizeof(*s));snprintf(s->error,sizeof(s->error),"story script bounds/CRC/command");return false;
}
