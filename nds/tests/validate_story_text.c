#include "story_text.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned service_calls;
void audio_demo_service(void){service_calls++;}
static void need(int ok,const char*why){if(!ok){fprintf(stderr,"FAIL: %s\n",why);exit(2);}}
static uint32_t crc(const void*data,size_t n){
    static uint32_t table[256];static int ready;
    if(!ready){for(unsigned i=0;i<256;i++){uint32_t c=i;for(unsigned b=0;b<8;b++)c=(c>>1)^(0xedb88320u&(0u-(c&1u)));table[i]=c;}ready=1;}
    uint32_t c=~0u;const uint8_t*p=data;while(n--)c=(c>>8)^table[(c^*p++)&255];return ~c;
}
int main(int argc,char**argv){
    if(argc==8&&!strcmp(argv[1],"--probe")){
        if(!story_text_open(argv[2])){fprintf(stderr,"REJECT open: %s\n",story_text_error());return 3;}
        unsigned id=(unsigned)strtoul(argv[3],NULL,0),visible=(unsigned)strtoul(argv[4],NULL,0);
        unsigned page=(unsigned)strtoul(argv[5],NULL,0),bi=(unsigned)strtoul(argv[6],NULL,0);
        need(story_text_select(id,0),story_text_error());unsigned reads=story_text_read_calls();
        uint16_t screen[256*192];story_text_draw_lower(id,visible,page,bi,1,0,screen);
        need(story_text_read_calls()==reads,"probe getter/draw did I/O");
        FILE*out=fopen(argv[7],"wb");need(out!=NULL,"probe output");
        need(fwrite(screen,sizeof(screen),1,out)==1,"probe pixels");fclose(out);
        printf("PASS probe id=%u visible=%u page=%u bilingual=%u reads=%u\n",id,visible,page,bi,reads);
        story_text_close();return 0;
    }
    need(argc==2||argc==4,"pack [cases output]");
    if(!story_text_open(argv[1])){fprintf(stderr,"REJECT open: %s\n",story_text_error());return 3;}
    need(story_text_count()==11975,"11951 Say+17 literal+7 date");
    need(!story_text_info(0)&&!story_text_info(11974)&&!story_text_visible(0,0),"getters must not implicitly load");
    need(!story_text_info(0xffffffffu)&&!story_text_pause(0xffffffffu,0),"NONE must not alias an unfilled cache slot");
    if(!story_text_select(0,0)){fprintf(stderr,"REJECT select: %s\n",story_text_error());return 3;}
    SceneTextInfo pinned=*story_text_info(0);
    need(!story_text_info(0xffffffffu),"NONE must remain invalid with only the live slot filled");
    unsigned previous=0xffffffffu;
    for(unsigned id=0;id<story_text_count();id++){
        if(!story_text_select(id,1)){fprintf(stderr,"REJECT select%u: %s\n",id,story_text_error());return 3;}
        const SceneTextInfo*t=story_text_info(id);need(t&&t->char_count&&story_text_visible(id,0xffffffffu)==t->char_count,"full reveal");
        need(!memcmp(story_text_info(0),&pinned,sizeof(pinned)),"review slot replaced pinned live text");
        if(previous!=0xffffffffu&&previous!=0&&previous!=id)need(!story_text_info(previous),"evicted record getter must be NULL");
        need(!story_text_pause(id,t->pause_count),"invalid pause");
        need(story_text_page_limit(id,t->pages_bi-1,1)==t->char_count,"last page cap");previous=id;
    }
    unsigned before=story_text_read_calls();need(!story_text_select(0xffffffffu,1)&&!story_text_select(0,2),"invalid id/slot");need(story_text_read_calls()==before,"invalid select did I/O");
    unsigned rendered=0,metadata=0;
    if(argc==4){
        FILE*input=fopen(argv[2],"rb"),*output=fopen(argv[3],"wb");need(input&&output,"case files");
        uint32_t request[6],canary[256*192/2+2];
        while(fread(request,sizeof(request),1,input)==1){
            unsigned id=request[0];
            if(id!=0xffffffffu)need(story_text_select(id,1),story_text_error());
            unsigned reads=story_text_read_calls();uint32_t result[4];
            if(request[5]==3){const SceneTextInfo*t=story_text_info(id);need(t!=NULL,"metadata id");result[0]=crc(t,sizeof(*t));metadata++;}
            else if(request[5]==4){result[0]=story_text_page_limit(id,request[2],request[3]);metadata++;}
            else if(request[5]==6){result[0]=0;metadata++;}
            else{
                canary[0]=canary[256*192/2+1]=0xdefacedu;uint16_t*screen=(uint16_t*)(canary+1);
                for(unsigned i=0;i<256*192;i++)screen[i]=(uint16_t)(0x8000|(i*37&32767));
                if(request[5]==1)story_text_draw_upper_bilingual(id,request[1],request[2],request[3],screen);
                else if(request[5]==2)story_text_draw_date(id,screen);
                else if(request[5]==5)story_text_draw_ascii(screen,-2,188,"ASCII 0z!",0xffff);
                else if(request[5]==7)story_text_draw_lower_poem(id,request[2],request[4]&1,request[4]&2,screen);
                else story_text_draw_lower(id,request[1],request[2],request[3],request[4]&1,request[4]&2,screen);
                need(canary[0]==0xdefacedu&&canary[256*192/2+1]==0xdefacedu,"draw canaries");result[0]=crc(screen,256*192*2);rendered++;
            }
            result[1]=story_text_page(id,request[1],request[3]);result[2]=story_text_visible(id,request[4]>>8);
            result[3]=story_text_english_visible(id,request[1],request[2]);
            need(story_text_read_calls()==reads,"getter/draw must not perform I/O");need(fwrite(result,sizeof(result),1,output)==1,"case output");
        }
        fclose(input);fclose(output);
    }
    need(service_calls>=story_text_read_calls(),"every bounded read must service audio");
    printf("PASS story text:11975 records, %u pixel cases, %u metadata cases, resident=%u peak=%u reads=%u services=%u getter_draw_reads=0\n",rendered,metadata,(unsigned)story_text_resident_bytes(),(unsigned)story_text_peak_bytes(),story_text_read_calls(),service_calls);
    story_text_close();need(!story_text_count()&&!story_text_info(0),"close");return 0;
}
