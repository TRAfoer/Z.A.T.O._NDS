#include "story_text.h"
#include "asset_pack.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>

#define HEAD 136u
#define CMD 10u
#define NONE UINT32_MAX
#if defined(__arm__)
#define HOT __attribute__((target("arm"),noinline))
#else
#define HOT
#endif
typedef uint32_t Word __attribute__((__may_alias__));
typedef struct {uint8_t *data;uint32_t id,size;} TextSlot;
static FILE *file;
static uint8_t *shared,*raw_buffer,*packed_buffer;
static uint32_t h[32],reads;
static TextSlot slots[2];
static size_t resident,peak;
typedef struct {
    unsigned page,y,cn0,cn1,en0,en1,cn_h,en_h,en_y,height,pitch,wide_y;
} ScrollRow;
typedef struct {ScrollRow*rows;unsigned count,capacity;bool ready,paired;} ScrollLayout;
static ScrollLayout scroll_layouts[2][2];
static const char *error_message="not loaded";
static void (*service_hook)(void);
extern void audio_demo_service(void) __attribute__((weak));
static uint16_t u16(const uint8_t*p){return p[0]|((uint16_t)p[1]<<8);}
static uint32_t u32(const uint8_t*p){return u16(p)|((uint32_t)u16(p+2)<<16);}
static void w16(uint8_t*p,uint16_t n){p[0]=(uint8_t)n;p[1]=(uint8_t)(n>>8);}
static void w32(uint8_t*p,uint32_t n){w16(p,(uint16_t)n);w16(p+2,(uint16_t)(n>>16));}
static void service(void);
static uint32_t crc(const uint8_t*p,uint32_t n,int header){
    /* Reflected CRC-32, two nibbles per byte. 64 read-only bytes replace the
     * eight bit rounds on every shared-font/record byte; wire CRC is unchanged. */
    static const uint32_t nibble[16]={
        0x00000000u,0x1db71064u,0x3b6e20c8u,0x26d930acu,
        0x76dc4190u,0x6b6b51f4u,0x4db26158u,0x5005713cu,
        0xedb88320u,0xf00f9344u,0xd6d6a3e8u,0xcb61b38cu,
        0x9b64c2b0u,0x86d3d2d4u,0xa00ae278u,0xbdbdf21cu
    };
    uint32_t c=~0u;for(uint32_t i=0;i<n;i++){
        if(!(i&511u))service();
        uint32_t zero=(uint32_t)(header!=0)&(uint32_t)(i-16u<4u);
        c^=p[i]&(zero-1u);
        c=(c>>4)^nibble[c&15u];c=(c>>4)^nibble[c&15u];
    }return ~c;
}
static void service(void){if(service_hook)service_hook();else if(audio_demo_service)audio_demo_service();}
void story_text_set_service(void(*hook)(void)){service_hook=hook;}
static void*allocate(size_t n){void*p=malloc(n?n:1);if(p){resident+=n;if(resident>peak)peak=resident;}return p;}
static void clear_scroll(unsigned slot){
    for(unsigned bi=0;bi<2;bi++){
        ScrollLayout*l=&scroll_layouts[slot][bi];
        if(l->rows){free(l->rows);resident-=l->capacity*sizeof(*l->rows);}
        *l=(ScrollLayout){0};
    }
}
static int read_at(uint32_t at,void*out,uint32_t n){
    service();if(fseek(file,(long)at,SEEK_SET))return 0;
    uint8_t*p=out;while(n){uint32_t chunk=n>4096?4096:n;reads++;if(fread(p,1,chunk,file)!=chunk)return 0;p+=chunk;n-=chunk;service();}return 1;
}
static int inside(uint32_t total,uint32_t at,uint32_t n){return at<=total&&n<=total-at;}
void story_text_close(void){
    for(unsigned i=0;i<2;i++)clear_scroll(i);
    if(file)fclose(file);
    file=NULL;free(shared);free(raw_buffer);free(packed_buffer);shared=raw_buffer=packed_buffer=NULL;
    for(unsigned i=0;i<2;i++){free(slots[i].data);slots[i]=(TextSlot){NULL,NONE,0};}
    memset(h,0,sizeof(h));resident=peak=0;reads=0;
}
const char*story_text_error(void){return error_message;}
unsigned story_text_count(void){return shared?h[3]:0;}
size_t story_text_resident_bytes(void){return resident;}
size_t story_text_peak_bytes(void){return peak;}
uint32_t story_text_read_calls(void){return reads;}
static TextSlot*cached(unsigned id){if(!shared||id>=h[3])return NULL;for(unsigned i=0;i<2;i++)if(slots[i].id==id)return &slots[i];return NULL;}
const SceneTextInfo*story_text_info(unsigned id){TextSlot*s=cached(id);return s?(const SceneTextInfo*)s->data:NULL;}
static uint32_t field(const TextSlot*s,unsigned n){return u32(s->data+n*4);}
int story_text_is_poem(unsigned id){TextSlot*s=cached(id);return s&&field(s,16)==4;}
static unsigned glyph_index(unsigned id){return h[29]==4?id&0x3fffu:id;}
static unsigned glyph_height(const uint8_t*g){unsigned n=u16(g+6);return h[29]==4?n&255u:n;}
static unsigned glyph_advance(const uint8_t*g){return h[29]==4?u16(g+6)>>8:u16(g+4);}
uint32_t story_text_layout_key(void){return shared?(h[2]?h[2]:1u):0;}
int story_text_legacy_layout_compatible(unsigned id){
    TextSlot*s=cached(id);if(!s)return 0;
    if(h[31]&&!field(s,16))return 0;
    if(h[29]!=4)return 1;
    const unsigned starts[]={11,13,18},counts[]={12,14,19};
    for(unsigned plane=0;plane<3;plane++){
        unsigned n=field(s,counts[plane]),at=field(s,starts[plane]);
        for(unsigned i=0;i<n;i++){
            unsigned tagged=u16(s->data+at+i*CMD);if(tagged==65535)continue;
            const uint8_t*g=shared+h[9]+glyph_index(tagged)*8;unsigned height=glyph_height(g);
            if(height!=8&&height!=12)return 0;
        }
    }
    return 1;
}
static int command_bounds(unsigned id,unsigned x,unsigned y){
    if(id==65535)return y<192;
    unsigned index=glyph_index(id);if(index>=h[5])return 0;
    const uint8_t*g=shared+h[9]+index*8;
    unsigned shadow=h[29]==4&&(id&0x8000),underline=h[29]==4&&(id&0x4000);
    unsigned width=u16(g+4),advance=glyph_advance(g);if(underline&&advance>width)width=advance;
    return x+width+(shadow?1:0)<=256&&y+glyph_height(g)+(shadow||underline?1:0)<=192;
}
const SceneTextPause*story_text_pause(unsigned id,unsigned i){TextSlot*s=cached(id);return s&&i<field(s,3)?(const SceneTextPause*)(s->data+field(s,10)+i*12):NULL;}

int story_text_open(const char*path){
    uint8_t header[HEAD];long length;
    story_text_close();error_message="open";file=fopen(path,"rb");if(!file)return 0;
    error_message="size";if(fseek(file,0,SEEK_END)||(length=ftell(file))<(long)HEAD||length>=64000000)goto bad;
    error_message="header read";if(!read_at(0,header,HEAD))goto bad;
    error_message="header";if(memcmp(header,"ZATSTX02",8))goto bad;
    for(unsigned i=0;i<32;i++)h[i]=u32(header+8+i*4);
    if(h[0]!=2||h[1]!=(uint32_t)length||!h[3]||h[3]>100000||h[4]>h[3]||!h[5]||h[5]>=65535||
       !h[6]||h[6]>1024||h[7]>128||h[8]!=HEAD||h[22]>1024*1024||h[22]>h[1]||
       !h[23]||h[23]>65535||!h[24]||h[24]>131072||!h[25]||h[25]>32767||(h[29]!=3&&h[29]!=4)||h[30]!=12||h[31]>1||
       (h[29]==4&&h[5]>=16384))goto bad;
    if(!inside(h[22],h[8],h[3]*12)||h[9]!=h[8]+h[3]*12||!inside(h[22],h[9],h[5]*8)||
       h[10]!=h[9]+h[5]*8||!inside(h[22],h[10],h[11])||h[12]<h[10]+h[11]||h[14]>h[22]/CMD||
       !inside(h[22],h[12],h[6]*8)||h[13]!=h[12]+h[6]*8||!inside(h[22],h[13],h[14]*CMD)||
       h[20]<h[13]+h[14]*CMD||h[21]!=h[7]*7200||!inside(h[22],h[20],h[21])||h[22]!=h[20]+h[21]||
       h[15]>h[14]||h[16]>h[14]-h[15]||h[17]>h[14]||h[18]!=95||h[18]>h[14]-h[17]||h[19]>h[14]||11>h[14]-h[19])goto bad;
    error_message="shared allocation";shared=allocate(h[22]);if(!shared)goto bad;
    memcpy(shared,header,HEAD);error_message="shared read";if(!read_at(HEAD,shared+HEAD,h[22]-HEAD))goto bad;
    error_message="shared CRC";if(crc(shared,h[22],1)!=h[2])goto bad;
    error_message="shared bounds";
    uint32_t previous=h[22];
    for(unsigned i=0;i<h[3];i++){
        if(!(i&127u))service();
        const uint8_t*d=shared+h[8]+i*12;unsigned at=u32(d),packed=u16(d+4)&32767,raw=u16(d+6);
        if(at<previous||!packed||packed>h[25]||raw<38||raw>h[23]||!inside(h[1],at,packed))goto bad;
        previous=at+packed;
    }
    if(previous!=h[1])goto bad;
    for(unsigned i=0;i<h[5];i++){
        if(!(i&127u))service();
        const uint8_t*g=shared+h[9]+i*8;unsigned w=u16(g+4),height=glyph_height(g),at=u32(g),advance=glyph_advance(g);
        if(!w||!height||w>64||height>64||!advance||advance>64||!inside(h[11],at,(w*height+7)/8))goto bad;
    }
    for(unsigned i=0;i<h[14];i++){
        if(!(i&127u))service();
        const uint8_t*c=shared+h[13]+i*CMD;unsigned id=u16(c);
        /* Static UI commands are plain glyph IDs; style flags belong to the
         * checked body streams, not the dynamic ASCII clipping directory. */
        if(id>=h[5]||!command_bounds(id,c[2],c[3]))goto bad;
    }
    for(unsigned i=0;i<h[6];i++){const uint8_t*d=shared+h[12]+i*8;unsigned at=u32(d),n=u32(d+4);if(at>h[14]||n>h[14]-at)goto bad;}
    for(unsigned i=0;i<95;i++)if(!shared[h[13]+(h[17]+i)*CMD+9])goto bad;
    for(unsigned i=0;i<11;i++)if(!shared[h[13]+(h[19]+i)*CMD+9])goto bad;
    error_message="cache allocation";
    raw_buffer=allocate(h[23]);packed_buffer=allocate(h[25]);
    for(unsigned i=0;i<2;i++){slots[i].data=allocate(h[24]);slots[i].id=NONE;}
    if(!raw_buffer||!packed_buffer||!slots[0].data||!slots[1].data)goto bad;
    error_message="";return 1;
bad:story_text_close();return 0;
}

static int expand_commands(const uint8_t*src,uint32_t bytes,uint8_t*dst,unsigned count,unsigned first,unsigned pages,uint16_t color){
    if(count>65535||first>65535-count||count>(UINT32_MAX-2)/5||bytes<count*5+2)return 0;
    unsigned runs=u16(src+count*5);if(runs>(bytes-count*5-2)/6||bytes!=count*5+2+runs*6)return 0;
    for(unsigned i=0;i<count;i++){
        if(!(i&63u))service();
        unsigned gid=u16(src+i*2),x=src[count*2+i],y=src[count*3+i],page=src[count*4+i];
        if(page>=pages||!command_bounds(gid,x,y))return 0;
        uint8_t*c=dst+i*CMD;w16(c,(uint16_t)gid);c[2]=(uint8_t)x;c[3]=(uint8_t)y;w16(c+4,color);w16(c+6,(uint16_t)(first+i));c[8]=(uint8_t)page;c[9]=0;
    }
    unsigned previous=0;
    for(unsigned i=0;i<runs;i++){
        if(!(i&63u))service();
        const uint8_t*r=src+count*5+2+i*6;unsigned at=u16(r),n=u16(r+2),ink=u16(r+4);
        if(!n||at<previous||at>count||n>count-at||!(ink&0x8000))return 0;
        for(unsigned j=0;j<n;j++){
            if(!(j&127u))service();
            w16(dst+(at+j)*CMD+4,(uint16_t)ink);
        }
        previous=at+n;
    }
    return 1;
}
static int expand_record(TextSlot*s,unsigned raw_bytes){
    unsigned modern=h[29]==4,wire_head=modern?48:36,prefix=modern?96:72;
    uint32_t f[24]={0};if(raw_bytes<wire_head+2)return 0;for(unsigned i=0;i<(modern?24u:18u);i++)f[i]=u16(raw_buffer+i*2);
    if(!f[1]||f[1]!=f[12]||!f[4]||f[4]>255||!f[5]||f[5]>255||f[6]>3||f[7]>1||f[8]>f[1]||f[15]>h[7]||f[16]>4||f[17]>=h[6]||f[9]!=wire_head)return 0;
    if(f[16]==4&&(!modern||f[4]!=f[5]||f[6]!=3||!f[14]||f[2]||f[3]||f[7]||f[8]||f[15]))return 0;
    unsigned runs=u16(raw_buffer+wire_head);if(runs>(raw_bytes-wire_head-2)/4||f[10]!=wire_head+2+runs*4||f[3]>(UINT32_MAX-f[10])/12||f[11]!=f[10]+f[3]*12||f[13]<f[11]||f[13]>raw_bytes)return 0;
    unsigned paired=modern&&f[19],en_end=raw_bytes;
    if(modern){
        if(f[23])return 0;
        if(paired){
            if(f[16]||f[19]!=f[12]||f[21]!=f[14]||f[18]<f[13]||f[20]<f[18]||f[22]<f[20]||f[22]>raw_bytes||
               f[22]-f[20]!=f[21]*2||raw_bytes-f[22]!=f[5]*2)return 0;
            en_end=f[18];
        }else for(unsigned i=18;i<24;i++)if(f[i])return 0;
    }
    unsigned pause_at=prefix+((f[1]*2+3)&~3u),cn_at=pause_at+f[3]*12;
    if(pause_at>h[24]||cn_at<pause_at||cn_at>h[24]||f[12]>(h[24]-cn_at)/CMD)return 0;
    unsigned en_at=cn_at+f[12]*CMD;if(en_at>h[24]||f[14]>(h[24]-en_at)/CMD)return 0;
    unsigned bi_at=en_at+f[14]*CMD,threshold_at=bi_at,limits_at=bi_at,expanded=bi_at;
    if(paired){
        if(f[19]>(h[24]-bi_at)/CMD)return 0;
        threshold_at=bi_at+f[19]*CMD;
        if(f[21]>(h[24]-threshold_at)/2)return 0;
        limits_at=threshold_at+f[21]*2;
        if(f[5]>(h[24]-limits_at)/2)return 0;
        expanded=limits_at+f[5]*2;
    }
    for(unsigned i=0;i<18;i++)w32(s->data+i*4,f[i]);
    if(modern)memset(s->data+72,0,24);
    memset(s->data+prefix,0,pause_at-prefix);
    unsigned cursor=0,clock=0;
    for(unsigned i=0;i<runs;i++){
        if(!(i&63u))service();
        const uint8_t*r=raw_buffer+wire_head+2+i*4;unsigned n=u16(r),delta=u16(r+2);
        if(!n||n>f[1]-cursor)return 0;
        for(unsigned j=0;j<n;j++){
            if(!(j&127u))service();
            if(delta>65535-clock)return 0;
            clock+=delta;w16(s->data+prefix+(cursor++)*2,(uint16_t)clock);
        }
    }
    if(cursor!=f[1]||clock>f[2])return 0;
    memcpy(s->data+pause_at,raw_buffer+f[10],f[3]*12);
    for(unsigned i=0;i<f[3];i++){
        const uint8_t*p=s->data+pause_at+i*12;if(u32(p)>u32(p+4)||u32(p+4)>f[2]||u32(p+8)>f[1])return 0;
    }
    if(!expand_commands(raw_buffer+f[11],f[13]-f[11],s->data+cn_at,f[12],0,f[4],0xffff)||
       !expand_commands(raw_buffer+f[13],en_end-f[13],s->data+en_at,f[14],f[12],f[5],f[16]==2||f[16]==4?0xffff:0xca52))return 0;
    if(paired){
        if(!expand_commands(raw_buffer+f[18],f[20]-f[18],s->data+bi_at,f[19],0,f[5],0xffff))return 0;
        memcpy(s->data+threshold_at,raw_buffer+f[20],f[21]*2);memcpy(s->data+limits_at,raw_buffer+f[22],f[5]*2);
        unsigned previous=0;
        for(unsigned i=0;i<f[5];i++){unsigned cap=u16(s->data+limits_at+i*2);if(cap<previous||cap>f[1])return 0;previous=cap;}
        if(previous!=f[1])return 0;
        previous=0;
        for(unsigned i=0;i<f[21];i++){
            if(!(i&127u))service();
            unsigned value=u16(s->data+threshold_at+i*2),page=s->data[en_at+i*CMD+8];
            if(value<previous||value>u16(s->data+limits_at+page*2))return 0;
            previous=value;
        }
        for(unsigned i=0;i<f[19];i++){
            if(!(i&127u))service();
            if(i+1>u16(s->data+limits_at+s->data[bi_at+i*CMD+8]*2))return 0;
        }
        w32(s->data+18*4,bi_at);w32(s->data+19*4,f[19]);w32(s->data+20*4,threshold_at);
        w32(s->data+21*4,f[21]);w32(s->data+22*4,limits_at);
    }
    w32(s->data+9*4,prefix);w32(s->data+10*4,pause_at);w32(s->data+11*4,cn_at);w32(s->data+13*4,en_at);s->size=expanded;return 1;
}
int story_text_select(unsigned id,unsigned slot){
    if(!shared||id>=h[3]||slot>1){error_message="record id/slot";return 0;}
    if(slots[slot].id==id){error_message="";return 1;}
    clear_scroll(slot);
    TextSlot*other=cached(id);if(other){memcpy(slots[slot].data,other->data,other->size);slots[slot].size=other->size;slots[slot].id=id;error_message="";return 1;}
    const uint8_t*d=shared+h[8]+id*12;unsigned at=u32(d),flags=u16(d+4),packed=flags&32767,raw=u16(d+6);
    error_message="record read";if(!read_at(at,packed_buffer,packed))return 0;
    error_message="record codec";
    if(flags&32768){if(packed!=raw)return 0;memcpy(raw_buffer,packed_buffer,raw);}
    else if(lz10_decode(packed_buffer,packed,raw_buffer,raw)!=(int)raw)return 0;
    service();
    error_message="record CRC";if(crc(raw_buffer,raw,0)!=u32(d+8))return 0;
    slots[slot].id=NONE;error_message="record bounds";if(!expand_record(&slots[slot],raw))return 0;
    slots[slot].id=id;error_message="";return 1;
}
unsigned story_text_visible(unsigned id,uint32_t ticks){
    TextSlot*s=cached(id);if(!s)return 0;unsigned low=0,high=field(s,1),at=field(s,9);
    while(low<high){unsigned mid=low+(high-low)/2;if(u16(s->data+at+mid*2)<=ticks)low=mid+1;else high=mid;}return low;
}
unsigned story_text_page(unsigned id,unsigned visible,int bilingual){
    TextSlot*s=cached(id);if(!s)return 0;unsigned page=0,n=field(s,12),at=field(s,11);
    unsigned paired=bilingual&&h[29]==4&&field(s,19);
    if(paired){n=field(s,19);at=field(s,18);}
    for(unsigned i=0;i<n;i++){const uint8_t*c=s->data+at+i*CMD;if(u16(c+6)<visible)page=c[8];}
    if(!paired&&bilingual&&visible>=field(s,1)&&field(s,14))page=s->data[field(s,13)+(field(s,14)-1)*CMD+8];
    return page;
}
unsigned story_text_page_limit(unsigned id,unsigned page,int bilingual){
    TextSlot*s=cached(id);if(!s)return 0;unsigned limit=field(s,1);if(page>=field(s,bilingual?5:4))return limit;
    if(bilingual&&h[29]==4&&field(s,19))return u16(s->data+field(s,22)+page*2);
    for(unsigned i=0;i<field(s,12);i++){const uint8_t*c=s->data+field(s,11)+i*CMD;if(c[8]>page){limit=u16(c+6);break;}}return limit;
}
unsigned story_text_english_visible(unsigned id,unsigned visible,unsigned page){
    TextSlot*s=cached(id);if(!s)return 0;unsigned count=0;
    const uint8_t*thresholds=h[29]==4&&field(s,19)?s->data+field(s,20):NULL;
    for(unsigned i=0;i<field(s,14);i++){
        const uint8_t*c=s->data+field(s,13)+i*CMD;
        if(c[8]==page&&u16(c)!=65535&&(thresholds?u16(thresholds+i*2)<=visible:visible>=field(s,1)))count++;
    }
    return count;
}

#define ROW8(m,d,c) do{if((m)&128){(d)[0]=(c);}if((m)&64){(d)[1]=(c);}if((m)&32){(d)[2]=(c);}if((m)&16){(d)[3]=(c);}if((m)&8){(d)[4]=(c);}if((m)&4){(d)[5]=(c);}if((m)&2){(d)[6]=(c);}if((m)&1){(d)[7]=(c);}}while(0)
#define ROW12(m,d,c) do{ROW8((m)>>4,d,c);if((m)&8){(d)[8]=(c);}if((m)&4){(d)[9]=(c);}if((m)&2){(d)[10]=(c);}if((m)&1){(d)[11]=(c);}}while(0)
static HOT void paint_mask(unsigned id,unsigned x,unsigned y,uint16_t color,uint16_t*screen){
    const uint8_t*g=shared+h[9]+id*8,*bits=shared+h[10]+u32(g);unsigned w=u16(g+4),height=glyph_height(g);uint16_t*dst=screen+y*256+x;
    if(w==12&&height==12){for(unsigned row=0;row<6;row++){unsigned a=((unsigned)bits[0]<<4)|(bits[1]>>4),b=((unsigned)(bits[1]&15)<<8)|bits[2];ROW12(a,dst,color);ROW12(b,dst+256,color);dst+=512;bits+=3;}}
    else if(w==8&&height==8){for(unsigned row=0;row<8;row++){unsigned ink=*bits++;ROW8(ink,dst,color);dst+=256;}}
    else for(unsigned yy=0;yy<height;yy++){
        if(!(yy&3u))service();
        for(unsigned xx=0;xx<w;xx++){unsigned bit=yy*w+xx;if(bits[bit>>3]&(0x80>>(bit&7)))dst[yy*256+xx]=color;}
    }
}
static HOT void draw_reveal(const uint8_t*commands,unsigned n,unsigned visible,unsigned page,const uint8_t*thresholds,uint16_t*screen){
    for(unsigned i=0;i<n;i++){
        if(!(i&31u))service();
        const uint8_t*c=commands+i*CMD;unsigned tagged=u16(c);
        if(tagged==65535||c[8]!=page||(thresholds?u16(thresholds+i*2)>visible:u16(c+6)>=visible))continue;
        unsigned id=glyph_index(tagged);const uint8_t*g=shared+h[9]+id*8;
        unsigned shadow=h[29]==4&&(tagged&0x8000),underline=h[29]==4&&(tagged&0x4000);uint16_t color=u16(c+4);
        if(shadow)paint_mask(id,c[2]+1,c[3]+1,0xb18c,screen);
        paint_mask(id,c[2],c[3],color,screen);
        if(underline)for(unsigned x=0;x<glyph_advance(g);x++)screen[(c[3]+glyph_height(g))*256+c[2]+x]=color;
    }
}
static void draw(const uint8_t*commands,unsigned n,unsigned visible,unsigned page,uint16_t*screen){draw_reveal(commands,n,visible,page,NULL,screen);}
static HOT void black(uint16_t*screen){
    if((uintptr_t)screen&3u){for(unsigned i=0;i<256*192;i++)screen[i]=0x8000;return;}
    Word*out=(Word*)screen;for(unsigned i=0;i<256*192/2;i+=8){out[0]=out[1]=out[2]=out[3]=0x80008000u;out[4]=out[5]=out[6]=out[7]=0x80008000u;out+=8;}
}
static void clipped_glyph(unsigned id,int x,int y,uint16_t color,uint16_t*screen){
    const uint8_t*g=shared+h[9]+id*8,*bits=shared+h[10]+u32(g);unsigned w=u16(g+4),height=glyph_height(g);
    for(unsigned yy=0;yy<height;yy++)for(unsigned xx=0;xx<w;xx++){int dx=x+(int)xx,dy=y+(int)yy;unsigned b=yy*w+xx;if(dx>=0&&dx<256&&dy>=0&&dy<192&&(bits[b>>3]&(0x80>>(b&7))))screen[dy*256+dx]=color;}
}
static void lower_frame(unsigned id,unsigned visible,unsigned page,int bilingual,int window,int quick_menu,int body,uint16_t*screen){
    if(!screen)return;
    black(screen);service();if(!shared)return;TextSlot*s=cached(id);unsigned portrait=s?field(s,15):0;
    if(window){
        for(unsigned x=4;x<=251;x++)screen[10*256+x]=screen[190*256+x]=0xffff;
        for(unsigned y=10;y<=190;y++)screen[y*256+4]=screen[y*256+251]=0xffff;
        const uint8_t*header=shared+h[12]+(s?field(s,17):0)*8;
        const uint8_t*name=shared+h[13]+u32(header)*CMD;unsigned name_count=u32(header+4);
        if(h[29]==4){
            if(portrait)for(unsigned x=191;x<=251;x++)screen[10*256+x]=0x8000;
            if(name_count){
                unsigned first=256,last=0;
                for(unsigned i=0;i<name_count;i++){
                    const uint8_t*c=name+i*CMD,*g=shared+h[9]+u16(c)*8;
                    if(c[2]<first)first=c[2];
                    unsigned end=c[2]+glyph_advance(g);if(end>last)last=end;
                }
                unsigned from=first>2?first-2:0,to=last+2;if(to>252)to=252;
                for(unsigned x=from;x<to;x++)screen[10*256+x]=0x8000;
            }
        }else for(unsigned x=7;x<=(portrait?190u:248u);x++)screen[10*256+x]=0x8000;
        draw(name,name_count,NONE,0,screen);
        if(s&&!field(s,16)){
            unsigned pages=field(s,bilingual?5:4);if(page>=pages)page=pages-1;
            unsigned paired=bilingual&&h[29]==4&&field(s,19);
            if(body){
                draw(s->data+field(s,paired?18:11),field(s,paired?19:12),visible,page,screen);
                if(paired)draw_reveal(s->data+field(s,13),field(s,14),visible,page,s->data+field(s,20),screen);
                else if(bilingual&&visible>=field(s,1))draw(s->data+field(s,13),field(s,14),NONE,page,screen);
            }
            if(portrait)for(unsigned y=0;y<60;y++)for(unsigned x=0;x<60;x++)screen[y*256+196+x]=u16(shared+h[20]+(portrait-1)*7200+(y*60+x)*2);
            service();
            if(body&&pages>1){char label[16];snprintf(label,sizeof(label),"%u/%u",page+1,pages);int x=226;for(const char*p=label;*p;p++){unsigned digit=*p=='/'?10:(unsigned)(*p-'0');const uint8_t*c=shared+h[13]+(h[19]+digit)*CMD;clipped_glyph(u16(c),x,169,u16(c+4),screen);x+=c[9];}}
        }
    }
    if(quick_menu)draw(shared+h[13]+h[15]*CMD,h[16],NONE,0,screen);
}
void story_text_draw_lower(unsigned id,unsigned visible,unsigned page,int bilingual,int window,int quick_menu,uint16_t*screen){
    lower_frame(id,visible,page,bilingual,window,quick_menu,1,screen);
}
void story_text_draw_lower_poem(unsigned id,unsigned page,int window,int quick_menu,uint16_t*screen){
    lower_frame(id,0,page,0,window,quick_menu,0,screen);
    TextSlot*s=cached(id);if(!screen||!window||!s||field(s,16)!=4)return;
    unsigned pages=field(s,4);if(page>=pages)page=pages-1;
    draw(s->data+field(s,11),field(s,12),NONE,page,screen);
}

static unsigned command_height(const uint8_t*c){
    unsigned tagged=u16(c);if(tagged==65535)return 0;
    return glyph_height(shared+h[9]+glyph_index(tagged)*8)+(h[29]==4&&!!(tagged&0xc000));
}
static unsigned command_width(const uint8_t*c){
    unsigned tagged=u16(c);if(tagged==65535)return 0;
    const uint8_t*g=shared+h[9]+glyph_index(tagged)*8;unsigned w=u16(g+4);
    if(h[29]==4&&(tagged&0x8000))w++;
    if(h[29]==4&&(tagged&0x4000)&&glyph_advance(g)>w)w=glyph_advance(g);
    return w;
}
static bool command_ink(const uint8_t*c){
    unsigned tagged=u16(c);if(tagged==65535)return false;
    if(h[29]==4&&(tagged&0x4000))return true;
    const uint8_t*g=shared+h[9]+glyph_index(tagged)*8,*bits=shared+h[10]+u32(g);
    unsigned bytes=(u16(g+4)*glyph_height(g)+7)/8;
    for(unsigned i=0;i<bytes;i++)if(bits[i])return true;
    return false;
}
static ScrollRow*new_row(ScrollLayout*l,unsigned page,unsigned y){
    for(unsigned i=0;i<l->count;i++)if(l->rows[i].page==page&&l->rows[i].y==y)return &l->rows[i];
    if(l->count==l->capacity)return NULL;
    ScrollRow*r=&l->rows[l->count++];*r=(ScrollRow){.page=page,.y=y,.cn0=NONE,.en0=NONE,.en_y=y+14,.wide_y=NONE};return r;
}
static unsigned groups(const uint8_t*commands,unsigned n){
    unsigned count=0,page=NONE,y=NONE;
    for(unsigned i=0;i<n;i++){const uint8_t*c=commands+i*CMD;if(c[8]!=page||c[3]!=y){count++;page=c[8];y=c[3];}}
    return count;
}
static int row_order(const void*a,const void*b){
    const ScrollRow*x=a,*y=b;if(x->page!=y->page)return x->page<y->page?-1:1;return x->y<y->y?-1:x->y>y->y;
}
static ScrollLayout*scroll_layout(TextSlot*s,int bilingual){
    if(!s||field(s,16))return NULL;
    unsigned bi=bilingual!=0;ScrollLayout*l=&scroll_layouts[s-slots][bi];if(l->ready)return l;
    l->paired=bi&&h[29]==4&&field(s,19);
    const uint8_t*cn=s->data+field(s,l->paired?18:11),*en=s->data+field(s,13);
    unsigned nc=field(s,l->paired?19:12),ne=bi?field(s,14):0;
    l->capacity=groups(cn,nc)+groups(en,ne);
    if(!l->capacity){l->ready=true;return l;}
    l->rows=allocate(l->capacity*sizeof(*l->rows));if(!l->rows){l->capacity=0;error_message="scroll row allocation";return NULL;}
    for(unsigned i=0;i<nc;i++){
        const uint8_t*c=cn+i*CMD;ScrollRow*r=new_row(l,c[8],c[3]);if(!r)goto bad;
        if(r->cn0==NONE)r->cn0=i;
        r->cn1=i+1;
        unsigned height=command_height(c);if(height>r->cn_h)r->cn_h=height;
        if(!(i&63u))service();
    }
    for(unsigned i=0;i<ne;i++){
        const uint8_t*c=en+i*CMD;ScrollRow*r=NULL;
        for(unsigned j=0;j<l->count;j++){
            ScrollRow*q=&l->rows[j];if(q->page!=c[8])continue;
            unsigned off=q->cn_h+1;if(off<14)off=14;
            if((q->en0!=NONE&&q->en_y==c[3])||(l->paired&&q->cn0!=NONE&&q->y+off==c[3])){r=q;break;}
        }
        if(!r){if(l->paired&&c[3]<14)goto bad;r=new_row(l,c[8],l->paired?c[3]-14:c[3]);if(!r)goto bad;}
        if(r->en0==NONE)r->en0=i;
        r->en1=i+1;r->en_y=c[3];
        unsigned height=command_height(c);if(height>r->en_h)r->en_h=height;
        if(!(i&63u))service();
    }
    service();qsort(l->rows,l->count,sizeof(*l->rows),row_order);service();
    for(unsigned i=0;i<l->count;i++){
        ScrollRow*r=&l->rows[i];unsigned ch=r->cn_h?r->cn_h:12,eh=r->en_h?r->en_h:8;
        r->height=r->cn0!=NONE?ch:0;
        if(l->paired){unsigned bottom=r->en_y-r->y+eh;if(bottom>r->height)r->height=bottom;}
        else if(r->en0!=NONE&&eh>r->height)r->height=eh;
        r->pitch=r->height+2;unsigned nominal=l->paired?25:15;if(r->pitch<nominal)r->pitch=nominal;
        if(i+1<l->count&&l->rows[i+1].page==r->page){unsigned distance=l->rows[i+1].y-r->y;if(distance<r->height)goto bad;r->pitch=distance;}
        if(r->cn0!=NONE)for(unsigned j=r->cn0;j<r->cn1;j++){
            const uint8_t*c=cn+j*CMD;if(command_ink(c)&&c[2]+command_width(c)>190)r->wide_y=0;
        }
        if(r->en0!=NONE)for(unsigned j=r->en0;j<r->en1;j++){
            const uint8_t*c=en+j*CMD;unsigned off=c[3]-r->y;
            if(command_ink(c)&&c[2]+command_width(c)>190&&(r->wide_y==NONE||off<r->wide_y))r->wide_y=off;
        }
        service();
    }
    l->ready=true;return l;
bad:
    free(l->rows);resident-=l->capacity*sizeof(*l->rows);*l=(ScrollLayout){0};error_message="scroll row geometry";return NULL;
}
static bool english_revealed(TextSlot*s,ScrollLayout*l,unsigned index,unsigned visible){
    return l->paired?u16(s->data+field(s,20)+index*2)<=visible:visible>=field(s,1);
}
static bool row_revealed(TextSlot*s,ScrollLayout*l,const ScrollRow*r,unsigned visible){
    if(r->cn0!=NONE&&r->cn0<visible)return true;
    if(r->en0!=NONE)for(unsigned i=r->en0;i<r->en1;i++)if(english_revealed(s,l,i,visible))return true;
    return false;
}
static unsigned layout_max(TextSlot*s,ScrollLayout*l,unsigned visible){
    unsigned max=0;for(unsigned i=0;i<l->count;i++)if(row_revealed(s,l,&l->rows[i],visible))max=i;return max;
}
static unsigned place_row(TextSlot*s,const ScrollRow*r,unsigned y){
    if(field(s,15)&&r->wide_y!=NONE&&y+r->wide_y<60)y=60-r->wide_y;
    return y;
}
static bool row_in_view(TextSlot*s,ScrollLayout*l,unsigned origin,unsigned target){
    if(origin>target)return false;
    unsigned y=h[31]?30:35;
    for(unsigned i=origin;i<=target;i++){ScrollRow*r=&l->rows[i];y=place_row(s,r,y);if(y+r->height>174)return false;if(i==target)return true;y+=r->pitch;}
    return false;
}
unsigned story_text_scroll_max(unsigned id,unsigned visible,int bilingual){
    TextSlot*s=cached(id);ScrollLayout*l=scroll_layout(s,bilingual);return l?layout_max(s,l,visible):0;
}
unsigned story_text_scroll_for_visible(unsigned id,unsigned visible,int bilingual){
    TextSlot*s=cached(id);ScrollLayout*l=scroll_layout(s,bilingual);if(!l||!l->count)return 0;
    unsigned target=0;for(unsigned i=0;i<l->count;i++)if(l->rows[i].cn0!=NONE&&l->rows[i].cn0<visible)target=i;
    for(unsigned i=0;i<=target;i++)if(row_in_view(s,l,i,target))return i;
    return target;
}
unsigned story_text_scroll_follow(unsigned id,unsigned visible,int bilingual,unsigned scroll,unsigned covered_cn){
    TextSlot*s=cached(id);ScrollLayout*l=scroll_layout(s,bilingual);if(!l||!l->count)return 0;
    unsigned max=layout_max(s,l,visible);if(scroll>max)scroll=max;
    unsigned target=NONE;
    if(covered_cn<visible)for(unsigned i=0;i<l->count;i++){
        ScrollRow*r=&l->rows[i];if(r->cn0!=NONE&&r->cn1>covered_cn&&r->cn0<visible){target=i;break;}
    }
    if(target==NONE){for(unsigned i=0;i<l->count;i++)if(l->rows[i].cn0!=NONE&&l->rows[i].cn0<visible)target=i;}
    if(target==NONE||row_in_view(s,l,scroll,target))return scroll;
    unsigned first=scroll<target?scroll+1:0;for(unsigned i=first;i<=target;i++)if(row_in_view(s,l,i,target))return i;
    return target;
}
static void range(unsigned index,unsigned*first,unsigned*end){if(!*end||index<*first)*first=index;if(index+1>*end)*end=index+1;}
static void scroll_glyph(const uint8_t*c,unsigned y,uint16_t*screen){
    unsigned tagged=u16(c);if(tagged==65535)return;
    unsigned id=glyph_index(tagged);const uint8_t*g=shared+h[9]+id*8;uint16_t color=u16(c+4);
    if(h[29]==4&&(tagged&0x8000))paint_mask(id,c[2]+1,y+1,0xb18c,screen);
    paint_mask(id,c[2],y,color,screen);
    if(h[29]==4&&(tagged&0x4000))for(unsigned x=0;x<glyph_advance(g);x++)screen[(y+glyph_height(g))*256+c[2]+x]=color;
}
void story_text_draw_lower_scroll(unsigned id,unsigned visible,unsigned scroll,int bilingual,int window,int quick_menu,uint16_t*screen,StoryTextViewport*out){
    StoryTextViewport v={0};if(out)*out=v;if(!screen)return;
    lower_frame(id,0,0,bilingual,window,quick_menu,0,screen);
    TextSlot*s=cached(id);ScrollLayout*l=scroll_layout(s,bilingual);if(!l||!l->count)return;
    v.max_scroll=layout_max(s,l,visible);v.scroll=scroll>v.max_scroll?v.max_scroll:scroll;if(!window){if(out)*out=v;return;}
    const uint8_t*cn=s->data+field(s,l->paired?18:11),*en=s->data+field(s,13);unsigned y=h[31]?30:35;
    for(unsigned row=v.scroll;row<l->count;row++){
        ScrollRow*r=&l->rows[row];if(!row_revealed(s,l,r,visible))break;
        y=place_row(s,r,y);if(y+r->height>174)break;
        if(r->cn0!=NONE)for(unsigned i=r->cn0;i<r->cn1&&i<visible;i++){scroll_glyph(cn+i*CMD,y,screen);range(i,&v.cn_first,&v.cn_end);}
        if(r->en0!=NONE)for(unsigned i=r->en0;i<r->en1;i++)if(english_revealed(s,l,i,visible)){
            scroll_glyph(en+i*CMD,y+r->en_y-r->y,screen);range(field(s,1)+i,&v.en_first,&v.en_end);
        }
        y+=r->pitch;
        service();
    }
    if(out)*out=v;
}
void story_text_draw_upper_bilingual(unsigned id,unsigned visible,unsigned page,int bilingual,uint16_t*screen){
    TextSlot*s=cached(id);if(!screen||!s||!field(s,16)||field(s,16)==3)return;
    if(field(s,16)==4){
        unsigned pages=field(s,5);if(page>=pages)page=pages-1;
        draw(s->data+field(s,13),field(s,14),NONE,page,screen);return;
    }
    unsigned original=bilingual&&field(s,16)==2&&field(s,14),pages=field(s,original?5:4);if(page>=pages)page=pages-1;
    draw(s->data+field(s,original?13:11),field(s,original?14:12),original?(visible>=field(s,1)?NONE:0):visible,page,screen);
}
void story_text_draw_upper(unsigned id,unsigned visible,unsigned page,uint16_t*screen){story_text_draw_upper_bilingual(id,visible,page,0,screen);}
void story_text_draw_date(unsigned id,uint16_t*screen){TextSlot*s=cached(id);if(screen&&s&&field(s,16)==3)draw(s->data+field(s,11),field(s,12),NONE,0,screen);}
void story_text_draw_ascii(uint16_t*screen,int x,int y,const char*text,uint16_t color){
    if(!screen||!shared||!text)return;
    int origin=x;
    for(const unsigned char*p=(const unsigned char*)text;*p;p++){if(*p=='\n'){x=origin;y+=11;continue;}unsigned ch=*p>=32&&*p<=126?*p:'?';const uint8_t*c=shared+h[13]+(h[17]+ch-32)*CMD;clipped_glyph(u16(c),x,y,color,screen);x+=c[9];}
}
