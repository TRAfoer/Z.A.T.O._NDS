#include "scene_text.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void need(int ok,const char *message) { if(!ok){fprintf(stderr,"FAIL: %s\n",message);exit(2);} }
static uint32_t crc(const uint16_t *screen) {
    uint32_t c=~0u;
    for(unsigned i=0;i<256*192;i++)for(unsigned byte=0;byte<2;byte++) {
        c^=(screen[i]>>(byte*8))&255;
        for(unsigned j=0;j<8;j++)c=(c>>1)^(0xedb88320u&(0u-(c&1u)));
    }
    return ~c;
}
int main(int argc,char **argv) {
    need(argc==2 || argc==4,"pack [cases output]");
    if(!scene_text_open(argv[1])) { fprintf(stderr,"REJECT: %s\n",scene_text_error());return 3; }
    need(scene_text_count()==279,"278 say plus stage literal");
    need(!scene_text_info(0xffffffffu) && !scene_text_pause(0xffffffffu,0) && !scene_text_page_limit(0xffffffffu,0,0),"invalid id");
    unsigned all_pauses=0;
    for(unsigned id=0;id<scene_text_count();id++) {
        const SceneTextInfo *info=scene_text_info(id);
        need(info && info->char_count && scene_text_visible(id,0xffffffffu)==info->char_count,"full reveal");
        need(!scene_text_pause(id,info->pause_count),"invalid pause index");
        need(scene_text_page_limit(id,info->pages_cn-1,0)==info->char_count &&
             scene_text_page_limit(id,info->pages_bi-1,1)==info->char_count &&
             scene_text_page_limit(id,0xffffffffu,1)==info->char_count,"final page caps");
        unsigned previous=0;
        for(unsigned t=0;t<=info->duration_ticks;t++) {
            unsigned visible=scene_text_visible(id,t);need(visible>=previous && visible<=info->char_count,"monotonic visible");previous=visible;
        }
        for(unsigned p=0;p<info->pause_count;p++) {
            const SceneTextPause *pause=scene_text_pause(id,p);
            need(pause && pause->start_tick<=pause->end_tick && pause->glyphs<=info->char_count,"pause contract");all_pauses++;
        }
    }
    if(argc==4) {
        FILE *input=fopen(argv[2],"rb"),*output=fopen(argv[3],"wb");need(input && output,"case streams");
        uint32_t request[6],canary[256*192/2+2];unsigned frames=0,probes=0,page_probes=0;
        while(fread(request,sizeof(request),1,input)==1) {
            if(request[5]>=3) {
                uint32_t result[3]={request[5]==4?scene_text_page_limit(request[0],request[2],request[3]):0,
                    scene_text_page(request[0],request[1],request[3]),scene_text_visible(request[0],request[4]>>8)};
                need(fwrite(result,sizeof(result),1,output)==1,"write metadata result");
                if(request[5]==4)page_probes++;else probes++;
                continue;
            }
            canary[0]=canary[256*192/2+1]=0xdefacedu;uint16_t *screen=(uint16_t *)(canary+1);
            for(unsigned i=0;i<256*192;i++)screen[i]=(uint16_t)(0x8000|(i*37&32767));
            if(request[5]==2)scene_text_draw_ascii(screen,-2,188,"ASCII 0z!",0xffff);
            else if(request[5])scene_text_draw_upper(request[0],request[1],request[2],screen);
            else scene_text_draw_lower(request[0],request[1],request[2],request[3],request[4]&1,request[4]&2,screen);
            need(canary[0]==0xdefacedu && canary[256*192/2+1]==0xdefacedu,"draw bounds canaries");
            uint32_t result[3]={crc(screen),scene_text_page(request[0],request[1],request[3]),scene_text_visible(request[0],request[4]>>8)};
            need(fwrite(result,sizeof(result),1,output)==1,"write case result");frames++;
        }
        fclose(input);fclose(output);printf("PASS native scene text: %u records, %u pauses, %u render cases, %u timing probes, %u page probes\n",scene_text_count(),all_pauses,frames,probes,page_probes);
    }
    scene_text_close();need(!scene_text_count(),"close");return 0;
}
