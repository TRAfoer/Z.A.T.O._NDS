#include "scene_script.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
_Static_assert(sizeof(SceneEvent)==24,"scene event layout");
_Static_assert(sizeof(SceneCheckpoint)==28,"scene checkpoint layout");
static uint32_t u32(const uint8_t*p){return p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;}
static uint32_t crc32(const uint8_t*p,uint32_t n){uint32_t v=~0u;while(n--){v^=*p++;for(unsigned b=0;b<8;b++)v=(v>>1)^(0xedb88320u&-(v&1));}return ~v;}
void scene_script_close(SceneScript*s){free(s->data);memset(s,0,sizeof(*s));}
bool scene_script_open(SceneScript*s,const char*path){
    memset(s,0,sizeof(*s));FILE*f=fopen(path,"rb");if(!f){snprintf(s->error,sizeof(s->error),"script open");return false;}
    fseek(f,0,SEEK_END);long n=ftell(f);rewind(f);
    if(n<40||n>1024*1024){fclose(f);snprintf(s->error,sizeof(s->error),"script size");return false;}
    s->data=malloc((size_t)n);if(!s->data||fread(s->data,1,(size_t)n,f)!=(size_t)n){fclose(f);goto bad;}fclose(f);
    const uint8_t*p=s->data;uint32_t count=u32(p+12),cp=u32(p+16),eo=u32(p+24),co=u32(p+28);
    if(memcmp(p,"ZSCENE01",8)||u32(p+8)!=1||u32(p+20)!=24||u32(p+32)!=(uint32_t)n||
       eo<40||eo%4||co%4||eo>(uint32_t)n||count>((uint32_t)n-eo)/24||co<eo+count*24||co>(uint32_t)n||
       !cp||cp>32||cp>((uint32_t)n-co)/28||crc32(p+40,(uint32_t)n-40)!=u32(p+36))goto bad;
    s->count=count;s->checkpoint_count=cp;s->events=(const SceneEvent*)(p+eo);s->checkpoints=(const SceneCheckpoint*)(p+co);
    if(!count||s->events[count-1].op!=SC_END)goto bad;
    for(unsigned i=0;i<count;i++){
        const SceneEvent*e=&s->events[i];
        if(e->op<SC_TEXT||e->op>SC_END||e->flags>(e->op==SC_TEXT?1:0)||e->a<0)goto bad;
        if(e->op==SC_GFX&&(e->b<0||e->b>4||e->c<0||e->c>1))goto bad;
        if((e->op==SC_MUSIC||e->op==SC_SFX)&&(e->b<0||e->b>16384||e->c<0))goto bad;
        if(e->op==SC_WAIT&&(e->b<0||e->b>1))goto bad;
        if(e->op==SC_WINDOW&&e->a>1)goto bad;
    }
    for(unsigned i=0;i<cp;i++)if(s->checkpoints[i].pc>=count||s->checkpoints[i].name[23])goto bad;
    return true;
bad:free(s->data);memset(s,0,sizeof(*s));snprintf(s->error,sizeof(s->error),"script layout/CRC");return false;
}
