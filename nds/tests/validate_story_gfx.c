#include "story_gfx.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static unsigned services;
void audio_demo_service(void){++services;}
static uint32_t r32(FILE*f){unsigned char b[4];assert(fread(b,1,4,f)==4);return b[0]|(uint32_t)b[1]<<8|(uint32_t)b[2]<<16|(uint32_t)b[3]<<24;}
int main(int argc,char **argv){
    assert(argc==4);
    if(!strcmp(argv[1],"reject")){
        assert(!story_gfx_open(argv[2],argv[3]));assert(!story_gfx_resident_bytes());return 0;
    }
    if(!story_gfx_open(argv[1],argv[2])){fprintf(stderr,"open: %s\n",story_gfx_error());return 1;}
    FILE*f=fopen(argv[3],"rb");assert(f);char magic[8];assert(fread(magic,1,8,f)==8&&!memcmp(magic,"STORYTST",8));unsigned n=r32(f),draws=0;
    uint16_t*screen_base=malloc(98306),*gold=malloc(98304);assert(screen_base&&gold);
    uint16_t*screen=screen_base+(getenv("STORY_GFX_UNALIGNED")?1:0);uint32_t previous=0;size_t peak=0;bool replaying=false;
    for(unsigned i=0;i<n;++i){
        uint32_t op=r32(f),tick=r32(f);int32_t a=(int32_t)r32(f),b=(int32_t)r32(f),c=(int32_t)r32(f),d=(int32_t)r32(f),e=(int32_t)r32(f),flag=(int32_t)r32(f);
        bool ok=true;uint32_t prior_reads=story_gfx_read_calls();
        if(op==0)story_gfx_clear();
        else if(op==1)ok=story_gfx_show(a,b,c,d,e,tick);
        else if(op==2)ok=story_gfx_hide(a);
        else if(op==4){assert(!story_gfx_show(a,b,c,d,e,tick));continue;}
        else if(op==6)story_gfx_begin_batch(tick);
        else if(op==7)ok=story_gfx_move_transition(a,tick,b);
        else if(op==8)ok=story_gfx_end_batch();
        else if(op==9){replaying=a!=0;story_gfx_set_replay(replaying);}
        else if(op==10)ok=story_gfx_sync();
        else if(op==11){uint32_t reads=story_gfx_read_calls();(void)story_gfx_version(tick);assert(story_gfx_read_calls()==reads);}
        else if(op==3){
            assert(fread(gold,1,98304,f)==98304);uint32_t reads=story_gfx_read_calls(),v=story_gfx_version(tick);
            assert(v==story_gfx_version(tick));if(flag==1)assert(v==previous);if(flag==-1)assert(v!=previous);previous=v;
            unsigned prior_services=services;
            ok=story_gfx_draw(tick,screen);assert(story_gfx_read_calls()==reads);draws++;
            /* A full-screen CPU render must cooperate with streaming audio,
             * even when every graphics bank/map is already in RAM. */
            assert(services>prior_services);
            if(ok&&memcmp(gold,screen,98304)){for(unsigned p=0;p<49152;++p)if(gold[p]!=screen[p]){fprintf(stderr,"case%u tick%u pixel %u,%u got%04x expected%04x\n",i,tick,p%256,p/256,screen[p],gold[p]);break;}return 1;}
        }
        if(!ok){fprintf(stderr,"case%u op%u: %s\n",i,op,story_gfx_error());return 1;}
        if(replaying)assert(story_gfx_read_calls()==prior_reads);
        size_t resident=story_gfx_resident_bytes();assert(resident<=1536u*1024u);if(resident>peak)peak=resident;
    }
    assert(services);assert(!story_gfx_show(0xffffffffu,0,0,0,-1,0));
    story_gfx_clear();assert(!story_gfx_layer_count());story_gfx_close();assert(!story_gfx_resident_bytes());
    printf("PASS normal gfx: %u actions, %u pixel goldens; peak heap %lu; draw/version reads0\n",n,draws,(unsigned long)peak);
    free(screen_base);free(gold);fclose(f);return 0;
}
