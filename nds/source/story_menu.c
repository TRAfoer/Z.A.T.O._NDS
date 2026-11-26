#include "story_menu.h"
#include "credits_badge.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define HEADER 104u
#define WHITE 0xffffu
#define GRAY 0xc631u
#define GOLD 0xb31bu
#define BLACK 0x8000u
#define INVALID UINT32_MAX
static uint8_t *data;
static uint32_t h[24],reads;
static const char *error="not loaded";
static unsigned badge_row=INVALID;
static unsigned text(uint16_t*p,const uint8_t*value,unsigned n,int x,int y,unsigned size,uint16_t color,int width,unsigned skip,unsigned rows);
static unsigned find_badge_row(void);
extern void audio_demo_service(void) __attribute__((weak));
static void service(void){if(audio_demo_service)audio_demo_service();}
static unsigned u16(const uint8_t*p){return p[0]|(unsigned)p[1]<<8;}
static uint32_t u32(const uint8_t*p){return u16(p)|(uint32_t)u16(p+2)<<16;}
static bool fail(const char*s){error=s;return false;}
static uint32_t checksum(const uint8_t*p,unsigned n){uint32_t c=~0u;for(unsigned i=0;i<n;i++){c^=i>=16&&i<20?0:p[i];for(unsigned b=0;b<8;b++)c=(c>>1)^(0xedb88320u&(0u-(c&1)));if(!(i&4095))service();}return ~c;}
static unsigned utf8(const uint8_t **cursor,const uint8_t*end){
    const uint8_t*p=*cursor;if(p>=end)return INVALID;unsigned c=*p++,n,min;
    if(c<128){*cursor=p;return c;}
    if(c>=0xc2&&c<=0xdf){n=1;min=128;c&=31;}else if(c>=0xe0&&c<=0xef){n=2;min=2048;c&=15;}else if(c>=0xf0&&c<=0xf4){n=3;min=65536;c&=7;}else return INVALID;
    if((size_t)(end-p)<n)return INVALID;
    for(unsigned i=0;i<n;i++){if((p[i]&0xc0)!=0x80)return INVALID;c=(c<<6)|(p[i]&63);}p+=n;
    if(c<min||c>0x10ffff||(c>=0xd800&&c<=0xdfff))return INVALID;
    *cursor=p;return c;
}
static const uint8_t *glyph(unsigned cp,unsigned size){
    unsigned lo=0,hi=h[3];
    while(lo<hi){unsigned mid=lo+(hi-lo)/2;const uint8_t*g=data+h[4]+mid*12;unsigned font=g[11],code=u32(g);if(font<size||(font==size&&code<cp))lo=mid+1;else hi=mid;}
    if(lo<h[3]){const uint8_t*g=data+h[4]+lo*12;if(g[11]==size&&u32(g)==cp)return g;}
    return size==8?glyph(cp,12):NULL;
}
void story_menu_close(void){free(data);data=NULL;memset(h,0,sizeof(h));reads=0;badge_row=INVALID;}
const char*story_menu_error(void){return error;}
size_t story_menu_resident_bytes(void){return data?h[1]:0;}
uint32_t story_menu_read_calls(void){return reads;}
const uint8_t*story_menu_identity(void){return data?data+h[19]:NULL;}
bool story_menu_open(const char*path){
    story_menu_close();error="open menu";FILE*f=path?fopen(path,"rb"):NULL;if(!f)return false;
    long length;if(fseek(f,0,SEEK_END)||(length=ftell(f))<(long)HEADER||length>524288||fseek(f,0,SEEK_SET)){fclose(f);return fail("menu size");}
    data=malloc((size_t)length);if(!data){fclose(f);return fail("menu allocation");}
    unsigned at=0;while(at<(unsigned)length){unsigned n=(unsigned)length-at;if(n>4096)n=4096;reads++;if(fread(data+at,1,n,f)!=n){fclose(f);fail("menu read");goto bad;}at+=n;service();}fclose(f);
    if(memcmp(data,"ZSTMENU1",8)){fail("menu magic");goto bad;}for(unsigned i=0;i<24;i++)h[i]=u32(data+8+i*4);
    if(h[0]!=1||h[1]!=(unsigned)length||!h[3]||h[3]>4096||h[4]!=HEADER||h[5]!=h[4]+h[3]*12||h[6]>65536||
       !h[7]||h[7]>512||h[8]!=((h[5]+h[6]+3)&~3u)||h[9]!=h[8]+h[7]*8||h[10]>32768||h[11]!=((h[9]+h[10]+3)&~3u)||
       h[12]!=h[11]+256*184*2||h[13]!=h[12]+256*184*2||h[14]!=h[13]+75*192*2||h[15]!=6||h[16]!=6||
       h[23]!=h[14]+72||h[17]!=h[23]+64*36*2||h[18]>128||h[19]!=h[17]+h[18]*8||h[1]!=h[19]+32||h[20]>=h[7]||h[21]>=h[7]||h[22]!=49||h[22]>h[7]){fail("menu directory");goto bad;}
    if(checksum(data,h[1])!=h[2]){fail("menu CRC");goto bad;}
    unsigned previous_font=0,previous_cp=0;
    for(unsigned i=0;i<h[3];i++){
        const uint8_t*g=data+h[4]+i*12;unsigned cp=u32(g),offset=u32(g+4),w=g[8],height=g[9],advance=g[10],font=g[11];
        if((font!=8&&font!=12)||w!=font||height!=font||!advance||advance>16||cp<32||cp>0x10ffff||(cp>=0xd800&&cp<=0xdfff)||
           font<previous_font||(font==previous_font&&cp<=previous_cp)||offset>h[6]||(w*height+7)/8>h[6]-offset){fail("menu glyph bounds");goto bad;}
        previous_font=font;previous_cp=cp;
    }
    for(unsigned i=0;i<h[7];i++){
        const uint8_t*d=data+h[8]+i*8;unsigned offset=u32(d),n=u32(d+4);if(offset>h[10]||n>h[10]-offset){fail("menu string bounds");goto bad;}
        const uint8_t*p=data+h[9]+offset,*end=p+n;
        while(p<end){unsigned cp=utf8(&p,end);if(cp==INVALID||!cp||(cp!='\n'&&!glyph(cp,12))){fail("menu string glyph");goto bad;}}
    }
    for(unsigned i=0;i<h[18];i++){const uint8_t*r=data+h[17]+i*8;if(u16(r)>=36||u16(r+2)>=1024||u16(r+4)>=h[7]||u16(r+6)){fail("menu gallery bounds");goto bad;}}
    for(unsigned i=h[11];i<h[17];i+=2){unsigned w=u16(data+i);if((i<h[12]||(i>=h[13]&&i<h[14])||i>=h[23])?!(w&0x8000):(w&&!(w&0x8000))){fail("menu image pixels");goto bad;}}
    badge_row=find_badge_row();error="";return true;
bad:story_menu_close();return false;
}
unsigned story_menu_gallery_count(void){return data?h[18]:0;}
bool story_menu_gallery(unsigned index,unsigned*bit,unsigned*frame){if(!data||index>=h[18])return false;const uint8_t*r=data+h[17]+index*8;if(bit)*bit=u16(r);if(frame)*frame=u16(r+2);return true;}
static bool unlocked(const StoryMenuState*s,unsigned index){unsigned bit;if(!story_menu_gallery(index,&bit,NULL))return false;return bit<32?(s->unlock_low>>bit)&1:(s->unlock_high>>(bit-32))&1;}
void story_menu_init(StoryMenuState*s,StoryMenuMode mode,bool in_game){if(!s)return;memset(s,0,sizeof(*s));s->mode=mode;s->return_mode=in_game?STORY_MENU_GAME:STORY_MENU_TITLE;s->page=1;s->in_game=in_game;s->music_volume=s->sfx_volume=100;for(unsigned i=0;i<6;i++)s->slots[i].slot_id=i;}
static void hold_ui(StoryMenuState*s){if(s->mode!=STORY_MENU_CONFIRM&&s->mode!=STORY_MENU_MESSAGE){s->return_mode=s->mode;s->return_selection=s->selection;s->return_focus=s->focus;}}
static void restore_ui(StoryMenuState*s){s->mode=s->return_mode;s->selection=s->return_selection;s->focus=s->return_focus;}
void story_menu_confirm(StoryMenuState*s,StoryMenuPrompt prompt,StoryMenuActionKind action,int value){if(!s)return;hold_ui(s);s->mode=STORY_MENU_CONFIRM;s->prompt=prompt;s->confirm_action=(StoryMenuAction){action,value};s->selection=1;}
void story_menu_message(StoryMenuState*s,StoryMenuPrompt prompt,const char*detail){if(!s)return;hold_ui(s);s->mode=STORY_MENU_MESSAGE;s->prompt=prompt;snprintf(s->message,sizeof(s->message),"%s",detail?detail:"");}
static StoryMenuAction action(StoryMenuActionKind kind,int value){return (StoryMenuAction){kind,value};}
static bool hit(StoryMenuInput in,unsigned x,unsigned y,unsigned w,unsigned height){return in.touch&&in.x>=x&&in.x-x<w&&in.y>=y&&in.y-y<height;}
static unsigned text_rows(const StoryMenuState*s){
    if(!data||!s||(s->mode!=STORY_MENU_CREDITS&&s->mode!=STORY_MENU_CONTROLS))return 0;
    unsigned id=s->mode==STORY_MENU_CREDITS?h[20]:h[21];const uint8_t*d=data+h[8]+id*8;
    return text(NULL,data+h[9]+u32(d),u32(d+4),12,0,12,0,232,0,0);
}
unsigned story_menu_text_page_count(const StoryMenuState*s){unsigned rows=text_rows(s),capacity=s&&s->game_preview?10:20;return rows?(rows+capacity-1)/capacity:0;}
bool story_menu_bar_hit(unsigned x,unsigned y){return x>=4&&x<252&&y>=STORY_MENU_QUICK_COLLAPSED_Y&&y<192;}
static void quick_page(StoryMenuState*s,int delta){int n=(int)s->quick_page+delta;if(n<0)n=0;if(n>2)n=2;s->quick_page=(unsigned)n;s->selection=0;}
static StoryMenuAction quick_action(unsigned at){
    static const StoryMenuActionKind kinds[10]={STORY_MENU_OPEN_HISTORY,STORY_MENU_OPEN_HISTORY,STORY_MENU_TOGGLE_SKIP,STORY_MENU_TOGGLE_AUTO,STORY_MENU_TOGGLE_LANGUAGE,STORY_MENU_OPEN_SAVE,STORY_MENU_QUICK_SAVE,STORY_MENU_QUICK_LOAD,STORY_MENU_OPEN_SETTINGS,STORY_MENU_OPEN_DEBUG};
    return at<10?action(kinds[at],at==1?1:0):action(STORY_MENU_NONE,0);
}
static StoryMenuAction quick_input(StoryMenuState*s,StoryMenuInput in){
    unsigned keys=in.down,allowed=STORY_MENU_KEY_A|STORY_MENU_KEY_B|STORY_MENU_KEY_SELECT|STORY_MENU_KEY_LEFT|STORY_MENU_KEY_RIGHT|STORY_MENU_KEY_L|STORY_MENU_KEY_R|STORY_MENU_KEY_TOUCH;
    if(keys&(STORY_MENU_KEY_B|STORY_MENU_KEY_SELECT)||(keys&~allowed))return action(STORY_MENU_CLOSE,0);
    if(s->quick_page>2)s->quick_page=2;
    unsigned count=s->quick_page==2?2:4;if(s->selection>=count)s->selection=0;
    if(in.touch){
        if(!hit(in,4,STORY_MENU_QUICK_Y,248,2*STORY_MENU_QUICK_ROW_HEIGHT))return action(STORY_MENU_CLOSE,0);
        if(in.y<STORY_MENU_QUICK_COLLAPSED_Y){unsigned local=(in.x-4)/62;if(local>=count)return action(STORY_MENU_NONE,0);s->selection=local;return quick_action(s->quick_page*4+local);}
        if(in.x<66)quick_page(s,-1);else if(in.x>=190)quick_page(s,1);
        return action(STORY_MENU_NONE,0);
    }
    if(keys&STORY_MENU_KEY_L){quick_page(s,-1);return action(STORY_MENU_NONE,0);}if(keys&STORY_MENU_KEY_R){quick_page(s,1);return action(STORY_MENU_NONE,0);}
    unsigned item=s->quick_page*4+s->selection;
    if(keys&STORY_MENU_KEY_LEFT)item=(item+9)%10;
    if(keys&STORY_MENU_KEY_RIGHT)item=(item+1)%10;
    s->quick_page=item/4;s->selection=item%4;
    return keys&STORY_MENU_KEY_A?quick_action(s->quick_page*4+s->selection):action(STORY_MENU_NONE,0);
}
static StoryMenuAction activate(StoryMenuState*s,StoryMenuActionKind kind,int value){
    if(kind==STORY_MENU_RETURN_TITLE||kind==STORY_MENU_QUIT){story_menu_confirm(s,kind==STORY_MENU_QUIT?STORY_MENU_PROMPT_QUIT:STORY_MENU_PROMPT_TITLE,kind,value);return action(STORY_MENU_NONE,0);}return action(kind,value);
}
static unsigned page_order(unsigned page){return page==0?0:page==17?1:page<=16?page+1:2;}
static unsigned page_value(unsigned order){return order==0?0:order==1?17:order-1;}
static StoryMenuAction change_page(StoryMenuState*s,int delta,unsigned page){unsigned next=page;if(delta){int n=(int)page_order(s->page)+delta;if(n<0)n=0;if(n>17)n=17;next=page_value((unsigned)n);}if(next==s->page)return action(STORY_MENU_NONE,0);s->page=next;s->selection=s->focus=0;return action(STORY_MENU_PAGE_CHANGED,(int)next);}
static StoryMenuAction slot_action(StoryMenuState*s,bool deleting){
    if(s->selection>=6)return action(STORY_MENU_NONE,0);
    StoryMenuSlot*slot=&s->slots[s->selection];
    if(deleting){if(slot->status==STORY_MENU_SLOT_EMPTY)return action(STORY_MENU_NONE,0);story_menu_confirm(s,STORY_MENU_PROMPT_DELETE,STORY_MENU_DELETE_SLOT,(int)slot->slot_id);return action(STORY_MENU_NONE,0);}
    if(slot->status==STORY_MENU_SLOT_INVALID){story_menu_message(s,STORY_MENU_PROMPT_ERROR,"Corrupt save. Delete explicitly before replacing.");return action(STORY_MENU_NONE,0);}
    if(s->mode==STORY_MENU_SAVE){if(!s->in_game||s->page==0){story_menu_message(s,STORY_MENU_PROMPT_ERROR,"This page is read-only.");return action(STORY_MENU_NONE,0);}if(slot->status==STORY_MENU_SLOT_VALID){story_menu_confirm(s,STORY_MENU_PROMPT_OVERWRITE,STORY_MENU_SAVE_SLOT,(int)slot->slot_id);return action(STORY_MENU_NONE,0);}return action(STORY_MENU_SAVE_SLOT,(int)slot->slot_id);}
    if(slot->status!=STORY_MENU_SLOT_VALID)return action(STORY_MENU_NONE,0);
    if(s->in_game){story_menu_confirm(s,STORY_MENU_PROMPT_LOAD,STORY_MENU_LOAD_SLOT,(int)slot->slot_id);return action(STORY_MENU_NONE,0);}return action(STORY_MENU_LOAD_SLOT,(int)slot->slot_id);
}
StoryMenuAction story_menu_input(StoryMenuState*s,StoryMenuInput in){
    if(!s)return action(STORY_MENU_NONE,0);
    unsigned k=in.down;
    if(s->mode==STORY_MENU_QUICK)return quick_input(s,in);
    if(s->mode==STORY_MENU_CONFIRM){if(k&STORY_MENU_KEY_B){restore_ui(s);return action(STORY_MENU_CANCEL,0);}if(k&(STORY_MENU_KEY_LEFT|STORY_MENU_KEY_RIGHT|STORY_MENU_KEY_UP|STORY_MENU_KEY_DOWN))s->selection^=1;
        if(hit(in,52,100,64,36)){s->selection=0;k|=STORY_MENU_KEY_A;}if(hit(in,152,100,64,36)){s->selection=1;k|=STORY_MENU_KEY_A;}
        if(k&STORY_MENU_KEY_A){bool yes=s->selection==0;restore_ui(s);return yes?s->confirm_action:action(STORY_MENU_CANCEL,0);}return action(STORY_MENU_NONE,0);}
    if(s->mode==STORY_MENU_MESSAGE){if(k&(STORY_MENU_KEY_A|STORY_MENU_KEY_B)||in.touch){restore_ui(s);return action(STORY_MENU_CANCEL,0);}return action(STORY_MENU_NONE,0);}
    if(k&STORY_MENU_KEY_B||hit(in,208,0,48,22))return action(s->mode==STORY_MENU_GAME?STORY_MENU_RESUME:STORY_MENU_BACK,0);
    if(s->mode==STORY_MENU_TITLE||s->mode==STORY_MENU_GAME){
        static const StoryMenuActionKind title[7]={STORY_MENU_NEW_GAME,STORY_MENU_OPEN_LOAD,STORY_MENU_OPEN_GALLERY,STORY_MENU_OPEN_SETTINGS,STORY_MENU_OPEN_CREDITS,STORY_MENU_OPEN_CONTROLS,STORY_MENU_QUIT};
        static const StoryMenuActionKind game[9]={STORY_MENU_RESUME,STORY_MENU_OPEN_HISTORY,STORY_MENU_OPEN_SAVE,STORY_MENU_OPEN_LOAD,STORY_MENU_OPEN_SETTINGS,STORY_MENU_RETURN_TITLE,STORY_MENU_OPEN_CREDITS,STORY_MENU_OPEN_CONTROLS,STORY_MENU_QUIT};
        unsigned n=s->mode==STORY_MENU_TITLE?7:9,top=s->mode==STORY_MENU_TITLE?16:10,step=s->mode==STORY_MENU_TITLE?22:19;
        if(s->selection>=n)s->selection=0;
        if(k&STORY_MENU_KEY_UP)s->selection=(s->selection+n-1)%n;
        if(k&STORY_MENU_KEY_DOWN)s->selection=(s->selection+1)%n;
        if(hit(in,8,top,240,step*n)){s->selection=(in.y-top)/step;k|=STORY_MENU_KEY_A;}
        return k&STORY_MENU_KEY_A?activate(s,s->mode==STORY_MENU_TITLE?title[s->selection]:game[s->selection],0):action(STORY_MENU_NONE,0);
    }
    if(s->mode==STORY_MENU_SAVE||s->mode==STORY_MENU_LOAD){
        if(k&STORY_MENU_KEY_L)return change_page(s,-1,0);
        if(k&STORY_MENU_KEY_R)return change_page(s,1,0);
        if(hit(in,4,20,247,16)){unsigned i=(in.x-4)/19;if(i==0)return change_page(s,-1,0);if(i==12)return change_page(s,1,0);return change_page(s,0,i==1?0:i==2?17:i-2);}
        for(unsigned i=0;i<6;i++)if(hit(in,8+(i%3)*83,38+(i/3)*68,74,62)){s->selection=i;s->focus=0;return slot_action(s,false);}
        if(s->focus){if(k&STORY_MENU_KEY_LEFT)s->selection=s->selection?s->selection-1:12;if(k&STORY_MENU_KEY_RIGHT)s->selection=(s->selection+1)%13;if(k&STORY_MENU_KEY_DOWN){s->focus=s->selection=0;return action(STORY_MENU_NONE,0);}if(k&STORY_MENU_KEY_A){unsigned i=s->selection;if(i==0)return change_page(s,-1,0);if(i==12)return change_page(s,1,0);return change_page(s,0,i==1?0:i==2?17:i-2);}return action(STORY_MENU_NONE,0);}
        if(s->selection>=6)s->selection=0;
        if(k&STORY_MENU_KEY_LEFT)s->selection=s->selection%3?s->selection-1:s->selection+2;
        if(k&STORY_MENU_KEY_RIGHT)s->selection=s->selection%3<2?s->selection+1:s->selection-2;
        if(k&STORY_MENU_KEY_UP){if(s->selection>=3)s->selection-=3;else{s->focus=1;s->selection=s->page==0?1:s->page==17?2:s->page<=9?s->page+2:12;}}
        if(k&STORY_MENU_KEY_DOWN)s->selection=s->selection<3?s->selection+3:s->selection-3;
        if(k&STORY_MENU_KEY_Y)return slot_action(s,true);
        if(k&STORY_MENU_KEY_A)return slot_action(s,false);
        return action(STORY_MENU_NONE,0);
    }
    if(s->mode==STORY_MENU_SETTINGS){if(s->selection>3)s->selection=0;if(k&STORY_MENU_KEY_UP)s->selection=(s->selection+3)%4;if(k&STORY_MENU_KEY_DOWN)s->selection=(s->selection+1)%4;
        bool changed=false;for(unsigned i=0;i<4;i++)if(hit(in,12,42+i*32,232,28)){s->selection=i;if(!i){s->bilingual=!s->bilingual;changed=true;}else if(i==3){s->chapter_skip=!s->chapter_skip;changed=true;}else{unsigned value=in.x<=80?0:in.x>=232?100:(in.x-80)*100/152;if(i==1)s->music_volume=value;else s->sfx_volume=value;changed=true;}}
        if(k&(STORY_MENU_KEY_LEFT|STORY_MENU_KEY_RIGHT|STORY_MENU_KEY_A)){if(!s->selection){s->bilingual=!s->bilingual;changed=true;}else if(s->selection==3){s->chapter_skip=!s->chapter_skip;changed=true;}else{unsigned*v=s->selection==1?&s->music_volume:&s->sfx_volume;if(*v>100)*v=100;if(k&STORY_MENU_KEY_LEFT)*v=*v<10?0:*v-10;else *v=*v>90?100:*v+10;changed=true;}}
        return action(changed?STORY_MENU_SETTINGS_CHANGED:STORY_MENU_NONE,(int)s->selection);
    }
    if(s->mode==STORY_MENU_GALLERY){unsigned n=story_menu_gallery_count();if(!n)return action(STORY_MENU_NONE,0);unsigned prior=s->selection;if(prior>=n)s->selection=0;
        if(k&STORY_MENU_KEY_LEFT)s->selection=s->selection?s->selection-1:n-1;
        if(k&STORY_MENU_KEY_RIGHT)s->selection=(s->selection+1)%n;
        if(k&STORY_MENU_KEY_UP)s->selection=s->selection>=3?s->selection-3:0;
        if(k&STORY_MENU_KEY_DOWN)s->selection=s->selection+3<n?s->selection+3:n-1;
        if(k&STORY_MENU_KEY_L)s->selection=s->selection>=6?s->selection-6:0;
        if(k&STORY_MENU_KEY_R)s->selection=s->selection+6<n?s->selection+6:n-1;
        for(unsigned i=0;i<6;i++)if(hit(in,8+(i%3)*83,38+(i/3)*68,74,62)){unsigned next=s->selection/6*6+i;if(next<n){s->selection=next;k|=STORY_MENU_KEY_A;}}
        if(s->selection!=prior)s->gallery_preview=NULL;
        if((s->selection!=prior||k&STORY_MENU_KEY_A)&&unlocked(s,s->selection))return action(STORY_MENU_GALLERY_SELECT,(int)s->selection);
        return action(STORY_MENU_NONE,0);
    }
    if(s->mode==STORY_MENU_CREDITS||s->mode==STORY_MENU_CONTROLS){unsigned capacity=s->game_preview?10:20,rows=text_rows(s),maximum=rows>capacity?rows-capacity:0;
        unsigned delta=k&(STORY_MENU_KEY_L|STORY_MENU_KEY_R|STORY_MENU_KEY_LEFT|STORY_MENU_KEY_RIGHT)?capacity:1;
        bool previous=(k&(STORY_MENU_KEY_UP|STORY_MENU_KEY_L|STORY_MENU_KEY_LEFT))!=0,next=(k&(STORY_MENU_KEY_DOWN|STORY_MENU_KEY_R|STORY_MENU_KEY_RIGHT))!=0;
        if(hit(in,4,172,80,20)){previous=true;delta=capacity;}else if(hit(in,172,172,80,20)){next=true;delta=capacity;}
        if(previous)s->scroll=s->scroll>delta?s->scroll-delta:0;
        if(next)s->scroll=s->scroll+delta<maximum?s->scroll+delta:maximum;
        if(s->scroll>maximum)s->scroll=maximum;}
    return action(STORY_MENU_NONE,0);
}
static void fill(uint16_t*p,uint16_t color){for(unsigned i=0;i<49152;i++)p[i]=color;}
static void rect(uint16_t*p,int x,int y,int w,int height,uint16_t color){for(int yy=y;yy<y+height;yy++)if(yy>=0&&yy<192)for(int xx=x;xx<x+w;xx++)if(xx>=0&&xx<256)p[yy*256+xx]=color;}
static void box(uint16_t*p,int x,int y,int w,int height,uint16_t color){rect(p,x,y,w,1,color);rect(p,x,y+height-1,w,1,color);rect(p,x,y,1,height,color);rect(p,x+w-1,y,1,height,color);}
static void paint(uint16_t*p,const uint8_t*g,int x,int y,uint16_t color){const uint8_t*bits=data+h[5]+u32(g+4);for(unsigned yy=0;yy<g[9];yy++)for(unsigned xx=0;xx<g[8];xx++){unsigned bit=yy*g[8]+xx;if(bits[bit>>3]&(128>>(bit&7)))rect(p,x+(int)xx,y+(int)yy,1,1,color);}}
static unsigned text(uint16_t*p,const uint8_t*value,unsigned n,int x,int y,unsigned size,uint16_t color,int width,unsigned skip,unsigned rows){
    const uint8_t*end=value+n;int at=x;unsigned line=0;
    while(value<end){unsigned cp=utf8(&value,end);if(cp==INVALID)break;const uint8_t*g=cp=='\n'?NULL:glyph(cp,size);if(!g&&cp!='\n')g=glyph('?',size);if(cp=='\n'||(g&&at>x&&at+g[10]>x+width)){at=x;line++;if(cp=='\n')continue;}
        if(g){if(line>=skip&&line-skip<rows)paint(p,g,at,y+(int)(line-skip)*14,color);at+=g[10];}}
    return line+1;
}
static unsigned find_badge_row(void){
    /* Four reserved blank rows keep the native image in the same scrolling
     * coordinate space as the surrounding text, without a new pack format. */
    static const uint8_t key[]="\nNDS\xe7\xa7\xbb\xe6\xa4\x8d\n\n\n\n\n";
    const uint8_t*d=data+h[8]+h[20]*8,*value=data+h[9]+u32(d);unsigned n=u32(d+4),length=sizeof(key)-1;
    if(n<length)return INVALID;
    for(unsigned i=0;i<=n-length;i++)if(!memcmp(value+i,key,length))
        return text(NULL,value,i+length-5,12,0,12,0,232,0,0);
    return INVALID;
}
static void draw_credits_badge(uint16_t*p,unsigned skip){
    if(badge_row==INVALID)return;
    int top=30+((int)badge_row-(int)skip)*14+4;
    if(top>=170||top+CREDITS_BADGE_HEIGHT<=30)return;
    for(int sy=top<30?30-top:0;sy<CREDITS_BADGE_HEIGHT&&top+sy<170;sy++){
        for(unsigned sx=0;sx<CREDITS_BADGE_WIDTH;sx++){
            unsigned bit=(unsigned)sy*CREDITS_BADGE_WIDTH+sx;
            if(credits_badge_bits[bit>>3]&(128u>>(bit&7u)))
                p[(top+sy)*256+(256-CREDITS_BADGE_WIDTH)/2+sx]=WHITE;
        }
    }
}
static void label(uint16_t*p,unsigned id,int x,int y,unsigned size,uint16_t color){if(id>=h[7])return;const uint8_t*d=data+h[8]+id*8;text(p,data+h[9]+u32(d),u32(d+4),x,y,size,color,256-x,0,1);}
static void ascii(uint16_t*p,const char*s,int x,int y,unsigned size,uint16_t color){unsigned n=0;while(s[n]&&n<128)n++;text(p,(const uint8_t*)s,n,x,y,size,color,244-x,0,8);}
static unsigned label_width(unsigned id,unsigned size){
    const uint8_t*d=data+h[8]+id*8,*p=data+h[9]+u32(d),*end=p+u32(d+4);unsigned width=0;
    while(p<end){unsigned cp=utf8(&p,end);const uint8_t*g=glyph(cp,size);if(g)width+=g[10];}return width;
}
bool story_menu_draw_bar(const StoryMenuState*s,bool expanded,uint16_t*lower){
    if(!data||!lower||(expanded&&!s))return fail("quick bar arguments");
    unsigned y=expanded?STORY_MENU_QUICK_Y:STORY_MENU_QUICK_COLLAPSED_Y,height=expanded?2*STORY_MENU_QUICK_ROW_HEIGHT:STORY_MENU_QUICK_ROW_HEIGHT;
    unsigned item_text=STORY_MENU_QUICK_Y+STORY_MENU_QUICK_TEXT_PADDING,nav_text=STORY_MENU_QUICK_COLLAPSED_Y+STORY_MENU_QUICK_TEXT_PADDING;
    rect(lower,4,(int)y,248,(int)height,BLACK);box(lower,4,(int)y,248,(int)height,WHITE);
    if(!expanded){label(lower,45,10,(int)nav_text,12,WHITE);ascii(lower,"SELECT",208,(int)nav_text,12,GRAY);service();return true;}
    static const unsigned labels[10]={7,8,41,42,20,9,15,43,3,44};unsigned page=s->quick_page<3?s->quick_page:2;
    rect(lower,4,STORY_MENU_QUICK_COLLAPSED_Y,248,1,WHITE);
    for(unsigned i=1;i<4;i++)rect(lower,4+(int)i*62,STORY_MENU_QUICK_Y+1,1,STORY_MENU_QUICK_ROW_HEIGHT-1,GRAY);
    for(unsigned i=0;i<4;i++){unsigned at=page*4+i;if(at>=10)continue;unsigned width=label_width(labels[at],12);label(lower,labels[at],4+(int)i*62+(62-(int)width)/2,(int)item_text,12,s->selection==i?WHITE:GRAY);}
    ascii(lower,"<",10,(int)nav_text,12,page?WHITE:GRAY);ascii(lower,">",240,(int)nav_text,12,page<2?WHITE:GRAY);char counter[12];snprintf(counter,sizeof(counter),"%u/3",page+1);ascii(lower,counter,119,(int)nav_text,12,GRAY);service();return true;
}
static void dim_background(uint16_t*destination,const uint16_t*source){for(unsigned i=0;i<49152;i++){unsigned v=source[i];destination[i]=(uint16_t)(0x8000|((v&31)>>2)|((((v>>5)&31)>>2)<<5)|((((v>>10)&31)>>2)<<10));if(!(i&4095))service();}}
static uint32_t random32(uint32_t n){n^=n>>16;n*=0x7feb352d;n^=n>>15;n*=0x846ca68b;return n^(n>>16);}
static void title_upper(uint16_t*upper,uint32_t ticks){
    ticks&=~3u;
    memcpy(upper+4*256,data+h[11],256*184*2);
    for(unsigned i=0;i<50;i++){
        uint32_t r=random32(0x5a41544fu+i*0x9e3779b9u);unsigned depth=r%10+1;int speed=15-(int)depth;
        unsigned border=(r>>8)%101,yborder=50+(r>>20)%351,period=((720+yborder)*600u+50u*(unsigned)speed-1)/(50u*(unsigned)speed);
        if(ticks<i)continue;
        unsigned age=(ticks-i)%period;
        int sx=(int)(random32(r+1)%(1281+2*border))-(int)border,wind=(int)(random32(r)&255)-128;
        int x=((sx-280)*256)/1000+(int)((int64_t)wind*speed*age/30000);
        int y=4+(int)((-(int64_t)yborder*600+50ll*speed*age)*184/(600*720));
        unsigned side=(66-depth*6)/10;if(!side)side=1;
        if(x< -6||x>=256||y< -6||y>=188)continue;
        for(unsigned yy=0;yy<side;yy++)for(unsigned xx=0;xx<side;xx++){unsigned sample=u16(data+h[14]+((yy*6/side)*6+xx*6/side)*2);int dx=x+(int)xx,dy=y+(int)yy;if(!sample||dx<0||dx>=256||dy<4||dy>=188)continue;unsigned a=11-depth,dest=upper[dy*256+dx],out=0x8000;for(unsigned shift=0;shift<15;shift+=5)out|=((((sample>>shift)&31)*a+((dest>>shift)&31)*(10-a)+5)/10)<<shift;upper[dy*256+dx]=(uint16_t)out;}
    }
    for(unsigned i=0;i<256*184;i++){uint16_t value=(uint16_t)u16(data+h[12]+i*2);if(value)upper[4*256+i]=value;}
}
static uint16_t rgb332(unsigned value){return (uint16_t)(0x8000|((((value>>5)&7)*31+3)/7)|((((value>>2)&7)*31+3)/7)<<5|(((value&3)*31+1)/3)<<10);}
static void thumbnail(uint16_t*p,const uint8_t*thumb,unsigned x,unsigned y,unsigned width,unsigned height){if(!thumb)return;for(unsigned yy=0;yy<height;yy++)for(unsigned xx=0;xx<width;xx++)p[(y+yy)*256+x+xx]=rgb332(thumb[(yy*48/height)*64+xx*64/width]);}
static void empty_thumbnail(uint16_t*p,unsigned x,unsigned y,unsigned width,unsigned height){for(unsigned yy=0;yy<height;yy++)for(unsigned xx=0;xx<width;xx++)p[(y+yy)*256+x+xx]=(uint16_t)u16(data+h[23]+((yy*36/height)*64+xx*64/width)*2);}
static void time_label(char*out,size_t n,uint32_t unix_time,bool full){
    if(!unix_time){snprintf(out,n,"----/--/-- --:--");return;}
    unsigned z=unix_time/86400+719468,era=z/146097,doe=z-era*146097,yoe=(doe-doe/1460+doe/36524-doe/146096)/365,y=yoe+era*400,doy=doe-(365*yoe+yoe/4-yoe/100),mp=(5*doy+2)/153,d=doy-(153*mp+2)/5+1,m=mp<10?mp+3:mp-9;y+=m<=2;
    unsigned hour=(unsigned)(unix_time/3600%24),minute=(unsigned)(unix_time/60%60);
    if(full)snprintf(out,n,"%04u/%02u/%02u %02u:%02u",y,m,d,hour,minute);else snprintf(out,n,"%02u/%02u %02u:%02u",m,d,hour,minute);
}
static void page_label(uint16_t*p,unsigned page,int x,int y){if(page==0)label(p,14,x,y,8,WHITE);else if(page==17)label(p,15,x,y,8,WHITE);else{char buffer[40];snprintf(buffer,sizeof(buffer),"第 %u 页",page);ascii(p,buffer,x,y,8,WHITE);}}
static void top_return(uint16_t*p,unsigned id){label(p,id,12,5,12,WHITE);label(p,7,220,5,12,GRAY);}
static void selected_slot(const StoryMenuState*s,uint16_t*upper){
    const StoryMenuSlot*slot=&s->slots[s->selection<6?s->selection:0];box(upper,31,9,194,146,WHITE);
    if(slot->status==STORY_MENU_SLOT_VALID&&slot->thumbnail)thumbnail(upper,slot->thumbnail,32,10,192,144);else if(slot->status==STORY_MENU_SLOT_EMPTY)empty_thumbnail(upper,32,28,192,108);else label(upper,slot->status==STORY_MENU_SLOT_INVALID?17:24,96,74,8,GRAY);
    page_label(upper,s->page,12,160);char buffer[80];snprintf(buffer,sizeof(buffer),"章节 %u  行 %u",(unsigned)slot->chapter+1,(unsigned)slot->text_id);ascii(upper,buffer,92,160,8,WHITE);
    time_label(buffer,sizeof(buffer),slot->unix_time,true);ascii(upper,buffer,12,176,8,GRAY);
}
static unsigned prompt_label(StoryMenuPrompt prompt){switch(prompt){case STORY_MENU_PROMPT_OVERWRITE:return 25;case STORY_MENU_PROMPT_LOAD:return 26;case STORY_MENU_PROMPT_DELETE:return 27;case STORY_MENU_PROMPT_TITLE:return 28;case STORY_MENU_PROMPT_QUIT:return 29;case STORY_MENU_PROMPT_SAVED:return 30;case STORY_MENU_PROMPT_SHUTDOWN:return 32;default:return 31;}}
bool story_menu_draw(const StoryMenuState*s,uint32_t ticks,uint16_t*upper,uint16_t*lower){
    if(!data||!s||!upper||!lower||s->mode>STORY_MENU_QUICK)return fail("menu draw arguments");
    if(s->mode==STORY_MENU_QUICK){if(s->game_preview)memcpy(upper,s->game_preview,98304);if(s->game_lower_preview)memcpy(lower,s->game_lower_preview,98304);return story_menu_draw_bar(s,true,lower);}
    if(s->mode==STORY_MENU_CONFIRM||s->mode==STORY_MENU_MESSAGE){StoryMenuState base=*s;base.mode=s->return_mode==STORY_MENU_CONFIRM||s->return_mode==STORY_MENU_MESSAGE?STORY_MENU_TITLE:s->return_mode;base.selection=s->return_selection;base.focus=s->return_focus;if(!story_menu_draw(&base,ticks,upper,lower))return false;fill(lower,BLACK);box(lower,12,48,232,108,WHITE);const uint8_t*d=data+h[8]+prompt_label(s->prompt)*8;text(lower,data+h[9]+u32(d),u32(d+4),24,66,12,WHITE,208,0,3);
        if(s->mode==STORY_MENU_CONFIRM){label(lower,11,76,116,12,s->selection==0?WHITE:GRAY);label(lower,12,176,116,12,s->selection==1?WHITE:GRAY);}else{unsigned n=0;while(n<sizeof(s->message)&&s->message[n])n++;text(lower,(const uint8_t*)s->message,n,24,108,8,GRAY,208,0,3);}service();return true;}
    fill(upper,BLACK);fill(lower,BLACK);
    if(s->mode==STORY_MENU_TITLE||s->mode==STORY_MENU_GAME){if(s->mode==STORY_MENU_GAME&&s->game_preview)memcpy(upper,s->game_preview,98304);else title_upper(upper,ticks);if(s->mode==STORY_MENU_GAME&&s->game_lower_preview)dim_background(lower,s->game_lower_preview);for(unsigned y=0;y<192;y++)memcpy(lower+y*256,data+h[13]+y*75*2,150);
        static const unsigned game[9]={7,8,9,1,3,10,4,5,6};unsigned n=s->mode==STORY_MENU_TITLE?7:9,step=s->mode==STORY_MENU_TITLE?22:19,top=s->mode==STORY_MENU_TITLE?20:14;
        for(unsigned i=0;i<n;i++)label(lower,s->mode==STORY_MENU_TITLE?i:game[i],24,(int)(top+i*step),12,s->selection==i?WHITE:GRAY);
        if(s->mode==STORY_MENU_TITLE)ascii(lower,"Ferry // Nopanamaman.  2025",84,180,8,GRAY);
    }else if(s->mode==STORY_MENU_SAVE||s->mode==STORY_MENU_LOAD){selected_slot(s,upper);top_return(lower,s->mode==STORY_MENU_SAVE?9:1);
        for(unsigned i=0;i<13;i++){unsigned p=i==1?0:i==2?17:i>=3&&i<=11?i-2:INVALID;char b[4];if(i==0)strcpy(b,"<");else if(i==12)strcpy(b,">");else if(i==1)strcpy(b,"A");else if(i==2)strcpy(b,"Q");else snprintf(b,sizeof(b),"%u",i-2);ascii(lower,b,9+(int)i*19,24,8,s->focus?(s->selection==i?WHITE:GRAY):(s->page==p?WHITE:GRAY));}
        for(unsigned i=0;i<6;i++){unsigned x=8+(i%3)*83,y=38+(i/3)*68;const StoryMenuSlot*slot=&s->slots[i];box(lower,(int)x,(int)y,74,62,!s->focus&&s->selection==i?WHITE:GRAY);if(slot->status==STORY_MENU_SLOT_VALID&&slot->thumbnail)thumbnail(lower,slot->thumbnail,x+13,y+4,48,36);else if(slot->status==STORY_MENU_SLOT_EMPTY)empty_thumbnail(lower,x+5,y+4,64,36);else label(lower,slot->status==STORY_MENU_SLOT_INVALID?17:24,(int)x+18,(int)y+17,8,GRAY);char b[40];if(slot->status==STORY_MENU_SLOT_VALID){time_label(b,sizeof(b),slot->unix_time,false);ascii(lower,b,(int)x+4,(int)y+42,8,GRAY);}if(s->page==0||s->page==17)snprintf(b,sizeof(b),"%c-%u",s->page==0?'A':'Q',i+1);else snprintf(b,sizeof(b),"%u-%u",s->page,i+1);ascii(lower,b,(int)x+4,(int)y+52,8,GRAY);}
        label(lower,37,12,179,8,GRAY);
    }else if(s->mode==STORY_MENU_SETTINGS){if(s->game_preview)memcpy(upper,s->game_preview,98304);else title_upper(upper,ticks);top_return(lower,3);for(unsigned i=0;i<4;i++){unsigned y=48+i*32;label(lower,i==3?46:21+i,24,(int)y,12,s->selection==i?WHITE:GRAY);if(!i)label(lower,s->bilingual?20:19,108,(int)y,12,WHITE);else if(i==3)label(lower,s->chapter_skip?47:48,108,(int)y,12,WHITE);else{unsigned value=i==1?s->music_volume:s->sfx_volume;if(value>100)value=100;box(lower,80,(int)y+3,153,10,GRAY);rect(lower,81,(int)y+4,(int)(value*151/100),8,WHITE);}}}
    else if(s->mode==STORY_MENU_GALLERY){top_return(lower,2);unsigned n=story_menu_gallery_count(),selected=s->selection<n?s->selection:0;if(s->gallery_preview&&unlocked(s,selected))memcpy(upper,s->gallery_preview,98304);else label(upper,18,104,88,8,GRAY);for(unsigned i=0;i<6;i++){unsigned at=selected/6*6+i,x=8+(i%3)*83,y=38+(i/3)*68;if(at>=n)continue;box(lower,(int)x,(int)y,74,62,at==selected?WHITE:GRAY);if(unlocked(s,at)){const uint8_t*r=data+h[17]+at*8;label(lower,u16(r+4),(int)x+4,(int)y+22,8,WHITE);}else label(lower,18,(int)x+12,(int)y+22,8,GRAY);}char b[24];snprintf(b,sizeof(b),"%u / %u",selected+1,n);ascii(lower,b,108,22,8,GRAY);label(lower,38,12,179,8,GRAY);}
    else{unsigned id=s->mode==STORY_MENU_CREDITS?h[20]:h[21],name=s->mode==STORY_MENU_CREDITS?4:5;top_return(lower,name);const uint8_t*d=data+h[8]+id*8;unsigned rows=text_rows(s),capacity=s->game_preview?10:20,maximum=rows>capacity?rows-capacity:0,start=s->scroll<maximum?s->scroll:maximum;
        if(s->game_preview){memcpy(upper,s->game_preview,98304);text(lower,data+h[9]+u32(d),u32(d+4),12,30,12,WHITE,232,start,10);}
        else{top_return(upper,name);text(upper,data+h[9]+u32(d),u32(d+4),12,30,12,WHITE,232,start,10);text(lower,data+h[9]+u32(d),u32(d+4),12,30,12,WHITE,232,start+10,10);}
        if(s->mode==STORY_MENU_CREDITS){if(s->game_preview)draw_credits_badge(lower,start);else{draw_credits_badge(upper,start);draw_credits_badge(lower,start+10);}}
        unsigned total=story_menu_text_page_count(s),current=1+(start+capacity-1)/capacity;if(current>total)current=total;
        char b[32];snprintf(b,sizeof(b),"%u / %u",current,total);ascii(lower,b,108,177,12,GRAY);ascii(lower,"<",12,177,12,start?WHITE:GRAY);ascii(lower,">",238,177,12,start<maximum?WHITE:GRAY);}
    service();return true;
}
static uint32_t hash_word(uint32_t a,uint32_t b){return (a^b)*16777619u;}
uint32_t story_menu_version(const StoryMenuState*s,uint32_t ticks){
    if(!data||!s)return 0;
    const unsigned fields[]={s->mode,s->return_mode,s->selection,s->page,s->focus,s->scroll,s->in_game,s->bilingual,s->music_volume,s->sfx_volume,s->unlock_low,s->unlock_high,s->prompt,s->confirm_action.kind,(unsigned)s->confirm_action.value,s->return_selection,s->return_focus,s->quick_page,s->chapter_skip};
    uint32_t version=2166136261u;for(unsigned i=0;i<sizeof(fields)/sizeof(*fields);i++)version=hash_word(version,fields[i]);
    for(unsigned i=0;i<6;i++){const StoryMenuSlot*p=&s->slots[i];version=hash_word(version,p->slot_id);version=hash_word(version,p->status);version=hash_word(version,p->unix_time);version=hash_word(version,p->chapter);version=hash_word(version,p->text_id);version=hash_word(version,p->page);version=hash_word(version,(uint32_t)(uintptr_t)p->thumbnail);}
    version=hash_word(version,(uint32_t)(uintptr_t)s->gallery_preview);version=hash_word(version,(uint32_t)(uintptr_t)s->game_preview);
    version=hash_word(version,(uint32_t)(uintptr_t)s->game_lower_preview);
    for(unsigned i=0;i<sizeof(s->message)&&s->message[i];i++)version=hash_word(version,(uint8_t)s->message[i]);
    StoryMenuMode mode=s->mode;if(mode==STORY_MENU_CONFIRM||mode==STORY_MENU_MESSAGE)mode=s->return_mode;
    if(mode==STORY_MENU_TITLE||((mode==STORY_MENU_GAME||mode==STORY_MENU_SETTINGS)&&!s->game_preview))version=hash_word(version,ticks/4);
    return version?version:1;
}
