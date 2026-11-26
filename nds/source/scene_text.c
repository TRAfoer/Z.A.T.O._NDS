#include "scene_text.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define HEADER 104u
#define RECORD 72u
#define CMD 10u
#if defined(__arm__)
#define TEXT_ARM __attribute__((target("arm"), noinline))
#else
#define TEXT_ARM
#endif
typedef uint32_t TextWord __attribute__((__may_alias__));
static uint8_t *pack;
static uint32_t h[24];
static const char *error_message = "not loaded";
static uint16_t r16(const uint8_t *p) { return p[0] | ((uint16_t)p[1] << 8); }
static uint32_t r32(const uint8_t *p) { return r16(p) | ((uint32_t)r16(p+2) << 16); }
static uint32_t field(unsigned id, unsigned f) { return r32(pack+h[5]+id*RECORD+f*4); }
static int range(uint32_t at, uint32_t bytes) { return at <= h[1] && bytes <= h[1]-at; }
static uint32_t crc(const uint8_t *p, size_t n) {
    uint32_t c=~0u;
    for(size_t i=0;i<n;i++) {
        c ^= (i>=16 && i<20) ? 0 : p[i];
        for(unsigned j=0;j<8;j++) c=(c>>1)^(0xedb88320u & (0u-(c&1u)));
    }
    return ~c;
}
void scene_text_close(void) { free(pack); pack=NULL; memset(h,0,sizeof(h)); }
const char *scene_text_error(void) { return error_message; }
unsigned scene_text_count(void) { return pack ? h[3] : 0; }
const SceneTextInfo *scene_text_info(unsigned id) {
    return pack && id<h[3] ? (const SceneTextInfo *)(pack+h[5]+id*RECORD) : NULL;
}
const SceneTextPause *scene_text_pause(unsigned id,unsigned index) {
    if(!scene_text_info(id) || index>=field(id,3)) return NULL;
    return (const SceneTextPause *)(pack+h[8]+(field(id,10)+index)*12);
}
int scene_text_open(const char *path) {
    FILE *f; long size; int ok=0;
    scene_text_close(); error_message="open"; f=fopen(path,"rb"); if(!f)return 0;
    if(fseek(f,0,SEEK_END) || (size=ftell(f))<(long)HEADER || size>2*1024*1024) goto done;
    rewind(f); pack=malloc((size_t)size); error_message="allocation"; if(!pack)goto done;
    error_message="read"; if(fread(pack,1,(size_t)size,f)!=(size_t)size)goto done;
    error_message="header"; if(memcmp(pack,"ZATSTX01",8))goto done;
    for(unsigned i=0;i<24;i++)h[i]=r32(pack+8+i*4);
    if(h[0]!=1 || h[1]!=(uint32_t)size || h[3]>4096 || !h[3] || !h[4] || h[4]>=65535)goto done;
    error_message="CRC"; if(crc(pack,(size_t)size)!=h[2])goto done;
    error_message="table bounds";
    if(h[5]!=HEADER || !range(h[5],h[3]*RECORD) || h[6]!=h[5]+h[3]*RECORD ||
       !range(h[6],h[4]*8) || h[7]<h[6]+h[4]*8 || h[8]<h[7] || h[9]<h[8] ||
       h[10]<h[9] || !range(h[10],h[11]) || h[18]<h[10]+h[11] ||
       !range(h[18],h[19]) || h[18]+h[19]!=h[1] || (h[8]&3) || h[19]!=7200)goto done;
    if((h[8]-h[7])%2 || (h[9]-h[8])%12 || (h[10]-h[9])%CMD)goto done;
    unsigned times=(h[8]-h[7])/2, pauses=(h[9]-h[8])/12, cmds=(h[10]-h[9])/CMD;
    for(unsigned i=12;i<18;i+=2)if(h[i]>cmds || h[i+1]>cmds-h[i])goto done;
    if(h[16]+h[17]>cmds || cmds-(h[16]+h[17])<11 || h[20]>cmds || h[21]>cmds-h[20] ||
       h[22]>cmds || h[23]!=95 || h[23]>cmds-h[22])goto done;
    for(unsigned i=0;i<h[4];i++) {
        const uint8_t *g=pack+h[6]+i*8; unsigned w=r16(g+4),height=r16(g+6),at=r32(g);
        if(!w || !height || w>64 || height>64 || at>h[11] || (w*height+7)/8>h[11]-at)goto done;
    }
    for(unsigned i=0;i<cmds;i++) {
        const uint8_t *c=pack+h[9]+i*CMD; unsigned g=r16(c);
        if(g!=65535 && g>=h[4])goto done;
        if(c[3]>=192)goto done;
        if(g!=65535) {
            const uint8_t *desc=pack+h[6]+g*8;
            if(c[2]+r16(desc+4)>256 || c[3]+r16(desc+6)>192)goto done;
        }
    }
    for(unsigned group=0;group<2;group++) {
        unsigned at=group?h[22]:h[16]+h[17],n=group?95:11;
        for(unsigned j=0;j<n;j++) {
            const uint8_t *c=pack+h[9]+(at+j)*CMD;
            if(r16(c)>=h[4] || !c[9] || c[9]>64)goto done;
        }
    }
    for(unsigned i=0;i<h[3];i++) {
        unsigned count=field(i,1),t=field(i,9),p=field(i,10),cn=field(i,11),en=field(i,13);
        if(t>times || count>times-t || p>pauses || field(i,3)>pauses-p ||
           cn>cmds || field(i,12)>cmds-cn || en>cmds || field(i,14)>cmds-en ||
           !field(i,4) || !field(i,5) || field(i,4)>255 || field(i,5)>255 ||
           field(i,6)>3 || field(i,8)>count || field(i,15)>1 || field(i,16)>1)goto done;
        unsigned prev=0;
        for(unsigned j=0;j<count;j++) { unsigned tick=r16(pack+h[7]+(t+j)*2); if(tick<prev)goto done; prev=tick; }
        for(unsigned j=0;j<field(i,3);j++) {
            const SceneTextPause *v=scene_text_pause(i,j);
            if(v->start_tick>v->end_tick || v->glyphs>count || v->end_tick>field(i,2))goto done;
        }
        for(unsigned kind=0;kind<2;kind++) {
            unsigned start=kind?en:cn,n=field(i,kind?14:12),pages=field(i,kind?5:4);
            for(unsigned j=0;j<n;j++) {
                const uint8_t *c=pack+h[9]+(start+j)*CMD;
                if(c[8]>=pages || (!kind && r16(c+6)>=count))goto done;
            }
        }
    }
    error_message=""; ok=1;
done:
    fclose(f); if(!ok)scene_text_close(); return ok;
}
unsigned scene_text_visible(unsigned id,uint32_t ticks) {
    if(!scene_text_info(id))return 0;
    unsigned low=0,high=field(id,1),base=field(id,9);
    while(low<high) { unsigned mid=low+(high-low)/2; if(r16(pack+h[7]+(base+mid)*2)<=ticks)low=mid+1;else high=mid; }
    return low;
}
unsigned scene_text_page(unsigned id,unsigned visible,int bilingual) {
    if(!scene_text_info(id))return 0;
    unsigned page=0,count=field(id,12),at=field(id,11);
    for(unsigned i=0;i<count;i++) { const uint8_t *c=pack+h[9]+(at+i)*CMD; if(r16(c+6)<visible)page=c[8]; }
    if(bilingual && visible>=field(id,1) && field(id,14)) {
        const uint8_t *c=pack+h[9]+(field(id,13)+field(id,14)-1)*CMD;page=c[8];
    }
    return page;
}
unsigned scene_text_page_limit(unsigned id,unsigned page,int bilingual) {
    if(!scene_text_info(id))return 0;
    unsigned limit=field(id,1),pages=field(id,bilingual?5:4);
    if(page>=pages)return limit;
    unsigned count=field(id,12),at=field(id,11);
    for(unsigned i=0;i<count;i++) {
        const uint8_t *c=pack+h[9]+(at+i)*CMD;
        if(c[8]>page) { limit=r16(c+6);break; }
    }
    return limit;
}
#define ROW8(mask,dst,color) do { \
    if((mask)&128){(dst)[0]=(color);} if((mask)&64){(dst)[1]=(color);} \
    if((mask)&32){(dst)[2]=(color);} if((mask)&16){(dst)[3]=(color);} \
    if((mask)&8){(dst)[4]=(color);} if((mask)&4){(dst)[5]=(color);} \
    if((mask)&2){(dst)[6]=(color);} if((mask)&1){(dst)[7]=(color);} \
} while(0)
#define ROW12(mask,dst,color) do { \
    ROW8((mask)>>4,dst,color); \
    if((mask)&8){(dst)[8]=(color);} if((mask)&4){(dst)[9]=(color);} \
    if((mask)&2){(dst)[10]=(color);} if((mask)&1){(dst)[11]=(color);} \
} while(0)
static TEXT_ARM void draw_commands(unsigned at,unsigned count,unsigned visible,unsigned page,uint16_t *screen) {
    for(unsigned i=0;i<count;i++) {
        const uint8_t *c=pack+h[9]+(at+i)*CMD;unsigned id=r16(c);
        if(id==65535 || c[8]!=page || r16(c+6)>=visible)continue;
        const uint8_t *g=pack+h[6]+id*8,*bits=pack+h[10]+r32(g);
        unsigned w=r16(g+4),height=r16(g+6),x=c[2],y=c[3];uint16_t color=r16(c+4);
        /* Command bounds were checked once at load. The two native font sizes
           avoid per-pixel bounds, multiply, bit-index and loop branches. ARM
           mode emits conditional halfword stores for these small ink masks. */
        uint16_t *dst=screen+y*256+x;
        if(w==12 && height==12) {
            for(unsigned row=0;row<6;row++) {
                unsigned a=((unsigned)bits[0]<<4)|(bits[1]>>4),b=((unsigned)(bits[1]&15)<<8)|bits[2];
                ROW12(a,dst,color);ROW12(b,dst+256,color);dst+=512;bits+=3;
            }
        }else if(w==8 && height==8) {
            for(unsigned row=0;row<8;row++) {unsigned ink=*bits++;ROW8(ink,dst,color);dst+=256;}
        }else for(unsigned yy=0;yy<height;yy++)for(unsigned xx=0;xx<w;xx++) {
            unsigned bit=yy*w+xx;if(bits[bit>>3]&(0x80>>(bit&7)))screen[(y+yy)*256+x+xx]=color;
        }
    }
}
static TEXT_ARM void fill_black(uint16_t *screen) {
    if((uintptr_t)screen&3u) {
        for(unsigned i=0;i<256*192;i++)screen[i]=0x8000;
        return;
    }
    TextWord *out=(TextWord *)screen;
    for(unsigned i=0;i<256*192/2;i+=8) {
        out[0]=out[1]=out[2]=out[3]=0x80008000u;
        out[4]=out[5]=out[6]=out[7]=0x80008000u;out+=8;
    }
}
void scene_text_draw_lower(unsigned id,unsigned visible,unsigned page,int bilingual,int window,int quick_menu,uint16_t *screen) {
    if(!screen)return;
    fill_black(screen);
    if(!pack)return;
    int valid=scene_text_info(id)!=NULL,portrait=valid && field(id,15);
    if(window) {
        for(unsigned x=4;x<=251;x++)screen[10*256+x]=screen[190*256+x]=0xffff;
        for(unsigned y=10;y<=190;y++)screen[y*256+4]=screen[y*256+251]=0xffff;
        /* The canvas is already black; the name strip erases only the newly
           drawn top frame line. Other19 rows would write black over black. */
        for(unsigned x=7;x<=(portrait?190u:248u);x++)screen[10*256+x]=0x8000;
        unsigned header=valid && field(id,6)==1 ? (portrait?14:20) : 12;
        draw_commands(h[header],h[header+1],UINT32_MAX,0,screen);
        if(valid && !field(id,16)) {
            unsigned pages=field(id,bilingual?5:4);if(page>=pages)page=pages-1;
            draw_commands(field(id,11),field(id,12),visible,page,screen);
            if(bilingual && visible>=field(id,1))draw_commands(field(id,13),field(id,14),UINT32_MAX,page,screen);
            if(portrait)for(unsigned y=0;y<60;y++)for(unsigned x=0;x<60;x++)screen[y*256+196+x]=r16(pack+h[18]+(y*60+x)*2);
            /* Page counters are packed with the footer digits and drawn below. */
            if(pages>1) {
                /* The last static command group contains no text-dependent state.
                   Digit masks are found via the small digit command directory. */
                unsigned xpos=226;
                char label[16];snprintf(label,sizeof(label),"%u/%u",page+1,pages);
                for(const char *s=label;*s;s++) {
                    unsigned digit=(unsigned)(*s=='/'?10:*s-'0');
                    const uint8_t *d=pack+h[9]+(h[16]+h[17]+digit)*CMD;
                    uint8_t copy[CMD];memcpy(copy,d,CMD);copy[2]=(uint8_t)xpos;copy[3]=169;
                    /* Emit directly, preserving one common glyph loop. */
                    unsigned gid=r16(copy);const uint8_t *g=pack+h[6]+gid*8,*bits=pack+h[10]+r32(g);
                    unsigned w=r16(g+4),height=r16(g+6);
                    for(unsigned yy=0;yy<height && yy+169<192;yy++)for(unsigned xx=0;xx<w && xpos+xx<256;xx++) {
                        unsigned b=yy*w+xx;if(bits[b>>3]&(0x80>>(b&7)))screen[(169+yy)*256+xpos+xx]=r16(copy+4);
                    }
                    xpos+=copy[9];
                }
            }
        }
    }
    if(quick_menu)draw_commands(h[16],h[17],UINT32_MAX,0,screen);
}
void scene_text_draw_upper(unsigned id,unsigned visible,unsigned page,uint16_t *screen) {
    if(!screen || !scene_text_info(id) || !field(id,16))return;
    unsigned pages=field(id,4);if(page>=pages)page=pages-1;
    draw_commands(field(id,11),field(id,12),visible,page,screen);
}
void scene_text_draw_ascii(uint16_t *screen,int x,int y,const char *text,uint16_t color) {
    if(!screen || !pack || !text)return;
    int origin=x;
    for(const unsigned char *s=(const unsigned char *)text;*s;s++) {
        if(*s=='\n'){x=origin;y+=11;continue;}
        unsigned ch=*s>=32 && *s<=126 ? *s : '?';
        const uint8_t *c=pack+h[9]+(h[22]+ch-32)*CMD;
        const uint8_t *g=pack+h[6]+r16(c)*8,*bits=pack+h[10]+r32(g);
        unsigned w=r16(g+4),height=r16(g+6);
        for(unsigned yy=0;yy<height;yy++)for(unsigned xx=0;xx<w;xx++) {
            int dx=x+(int)xx,dy=y+(int)yy;unsigned b=yy*w+xx;
            if(dx>=0 && dx<256 && dy>=0 && dy<192 && (bits[b>>3]&(0x80>>(b&7))))screen[dy*256+dx]=color;
        }
        x+=c[9];
    }
}
