// Host compression/format oracle and ARM9 probe use the existing asset_pack.c LZ10 decoder.
#include "asset_pack.h"
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#include <direct.h>
#else
#include <sys/stat.h>
#endif
#ifdef OP_PALETTE_PROBE
#include <nds.h>
#include <filesystem.h>
#endif

#define PAL_HEADER 88u
#define PAL_ENTRY 36u
#define MAX_PACKET 65536u
#define MAX_RAW 36864u
#define MODE_KEY 1u
#define MODE_XOR 2u
#define MODE_TILE 4u
#define MODE_RAW 8u
#define MODE_SKIP 16u
static uint32_t le32(const uint8_t *p)
{return p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;}
static unsigned le16(const uint8_t *p){return p[0]|p[1]<<8;}

static void make_bitmap8(uint8_t *bitmap,const uint8_t *tiles,unsigned colors)
{
    memset(bitmap,0,256*24);memset(bitmap+256*168,0,256*24);
    unsigned add=colors==256?0:0x01010101u;
    for(unsigned ty=0;ty<18;ty++)for(unsigned tx=0;tx<32;tx++){
        const uint8_t *src=tiles+(ty*32+tx)*(colors==16?32:64);
        uint32_t *dst=(uint32_t*)(bitmap+(ty*8+24)*256+tx*8);
        for(unsigned y=0;y<8;y++){
            if(colors==16){
                unsigned a=src[0]|(unsigned)src[1]<<8,b=src[2]|(unsigned)src[3]<<8;
                dst[0]=((a&15u)|((a&0xf0u)<<4)|((a&0xf00u)<<8)|((a&0xf000u)<<12))+add;
                dst[1]=((b&15u)|((b&0xf0u)<<4)|((b&0xf00u)<<8)|((b&0xf000u)<<12))+add;src+=4;
            }else{
                dst[0]=((const uint32_t*)src)[0]+add;dst[1]=((const uint32_t*)src)[1]+add;src+=8;
            }
            dst+=64;
        }
    }
}
static int make_gpu_palette(uint16_t *gpu,const uint16_t *source,unsigned colors)
{
    memset(gpu,0,512);
    if(colors==256){if(source[0]&0x7fff)return -1;memcpy(gpu,source,512);gpu[0]=0;}
    else memcpy(gpu+1,source,colors*2);
    return 0;
}

static int apply_frame(uint8_t *frame,unsigned raw,const uint8_t *packed,unsigned bytes,
                       unsigned decoded,unsigned flags,uint8_t *work)
{
    if(!raw||raw>MAX_RAW||!decoded||decoded>MAX_PACKET||!bytes||bytes>MAX_PACKET)return -1;
    if(flags&MODE_RAW){if(bytes!=decoded)return -1;memcpy(work,packed,decoded);}
    else if(lz10_decode(packed,bytes,work,MAX_PACKET)!=(int)decoded)return -1;
    unsigned mode=flags&~MODE_RAW;
    if(mode==MODE_KEY){if(decoded!=raw)return -1;memcpy(frame,work,raw);}
    else if(mode==MODE_XOR){if(decoded!=raw)return -1;for(unsigned i=0;i<raw;i++)frame[i]^=work[i];}
    else if(mode==MODE_TILE){
        if(decoded<72)return -1;
        unsigned cursor=72,tile=raw/576;
        for(unsigned i=0;i<576;i++)if(work[i/8]&(1u<<(i&7))){
            if(tile>decoded-cursor)return -1;
            memcpy(frame+i*tile,work+cursor,tile);cursor+=tile;
        }
        if(cursor!=decoded)return -1;
    }else if(mode==MODE_SKIP){
        unsigned cursor=0,position=0;
        while(position<raw){
            if(decoded-cursor<4)return -1;
            unsigned skip=le16(work+cursor),take=le16(work+cursor+2);cursor+=4;
            if(skip>raw-position)return -1;position+=skip;
            if(take>raw-position||take>decoded-cursor||(!skip&&!take))return -1;
            for(unsigned i=0;i<take;i++)frame[position+i]^=work[cursor+i];
            position+=take;cursor+=take;
        }
        if(cursor!=decoded)return -1;
    }else return -1;
    return 0;
}

#ifndef OP_PALETTE_PROBE
static unsigned encode_lz(const uint8_t *data,unsigned size,uint8_t *out)
{
    static int32_t head[8192],previous[4096];
    memset(head,255,sizeof(head));memset(previous,255,sizeof(previous));
    out[0]=0x10;out[1]=size;out[2]=size>>8;out[3]=size>>16;
    unsigned position=0,written=4;
    while(position<size){
        unsigned flag_at=written++;out[flag_at]=0;
        for(unsigned bit=0x80;bit&&position<size;bit>>=1){
            unsigned best=0,distance=0;
            if(position+2<size){
                unsigned hash=((data[position]*251u+data[position+1])*251u+data[position+2])&8191;
                int32_t candidate=head[hash];unsigned probes=0,limit=size-position;
                if(limit>18)limit=18;
                while(candidate>=0&&(unsigned)candidate<position&&position-(unsigned)candidate<=4096&&probes++<64){
                    unsigned count=0;
                    while(count<limit&&data[candidate+count]==data[position+count])count++;
                    if(count>=3&&count>best){best=count;distance=position-candidate;if(best==limit)break;}
                    int32_t next=previous[candidate&4095];if(next>=candidate)break;candidate=next;
                }
            }
            unsigned step=best>=3?best:1;
            if(best>=3){unsigned token=((best-3)<<12)|(distance-1);out[flag_at]|=bit;out[written++]=token>>8;out[written++]=token;}
            else out[written++]=data[position];
            for(unsigned i=0;i<step;i++)if(position+i+2<size){
                unsigned at=position+i,hash=((data[at]*251u+data[at+1])*251u+data[at+2])&8191;
                previous[at&4095]=head[hash];head[hash]=at;
            }
            position+=step;
        }
    }
    return written;
}

typedef struct{uint8_t *data;unsigned size,count,raw,colors,fps,palettes,palette_at,payload;const uint8_t *index;} PalFile;
static int open_pack(PalFile *p,const char *path)
{
    memset(p,0,sizeof(*p));FILE *f=fopen(path,"rb");if(!f)return -1;
    if(fseek(f,0,SEEK_END)){fclose(f);return -1;}long size=ftell(f);rewind(f);
    if(size<(long)PAL_HEADER||(unsigned long)size>0xffffffffu){fclose(f);return -1;}
    p->data=malloc(size);p->size=size;
    if(!p->data||fread(p->data,1,size,f)!=(size_t)size){fclose(f);return -1;}fclose(f);
    if(memcmp(p->data,"ZATPAL01",8))return -1;
    unsigned h[20];for(unsigned i=0;i<20;i++)h[i]=le32(p->data+8+i*4);
    p->fps=h[7];p->count=h[8];p->colors=h[10];p->raw=h[12];p->palettes=h[13];
    p->palette_at=h[16];p->payload=h[17];p->index=p->data+PAL_HEADER;
    if(h[0]!=1||h[1]!=256||h[2]!=144||h[3]!=256||h[4]!=192||h[5]!=0||h[6]!=24||
       !p->fps||p->fps>60||!p->count||p->count>4096||!h[9]||
       p->count!=((uint64_t)h[9]*p->fps+999999)/1000000||
       (p->colors<16||p->colors>256)||h[11]!=(p->colors==16?4:8)||
       p->raw!=(p->colors==16?18432u:36864u)||p->palettes!=(p->count+p->fps*2-1)/(p->fps*2)||
       h[14]!=PAL_ENTRY||h[15]!=PAL_HEADER||p->palette_at!=PAL_HEADER+p->count*PAL_ENTRY||
       p->payload!=p->palette_at+p->palettes*p->colors*2||h[18]!=p->size||
       !h[19]||h[19]>MAX_PACKET||p->payload>p->size)return -1;
    if(p->colors==256)for(unsigned i=0;i<p->palettes;i++)if(le16(p->data+p->palette_at+i*p->colors*2)&0x7fff)return -1;
    unsigned next=p->payload,key=0,last_palette=0,maximum=0;
    for(unsigned i=0;i<p->count;i++){
        const uint8_t*r=p->index+i*PAL_ENTRY;
        unsigned at=le32(r),bytes=le32(r+4),decoded=le32(r+8),flags=le32(r+12),nearest=le32(r+16),pal=le32(r+20);
        unsigned mode=flags&~MODE_RAW;
        if(at!=next||at>p->size||!bytes||bytes>MAX_PACKET||bytes>p->size-at||
           !decoded||decoded>MAX_PACKET||pal>=p->palettes||pal!=i/(p->fps*2)||
           le32(r+24)!=(uint64_t)i*22050/p->fps||le32(r+32)||
           (mode!=MODE_KEY&&mode!=MODE_XOR&&mode!=MODE_TILE&&mode!=MODE_SKIP))return -1;
        if(mode==MODE_KEY)key=i;
        if((i==0||pal!=last_palette)&&mode!=MODE_KEY)return -1;
        if(nearest!=key||i-key>=p->fps*2)return -1;
        if(bytes>maximum)maximum=bytes;
        last_palette=pal;next+=bytes;
    }
    if(next!=p->size||maximum!=h[19])return -1;
    return 0;
}
static int decode_at(const PalFile*p,unsigned i,uint8_t*frame,uint8_t*work)
{
    const uint8_t*r=p->index+i*PAL_ENTRY;
    if(apply_frame(frame,p->raw,p->data+le32(r),le32(r+4),le32(r+8),le32(r+12),work))return -1;
    if(p->colors>16&&p->colors<256)for(unsigned at=0;at<p->raw;at++)if(frame[at]>=p->colors)return -1;
    return pixel_hash(frame,p->raw)==le32(r+28)?0:-1;
}
static int ensure_dir(const char*path)
{
#ifdef _WIN32
    return _mkdir(path)&&errno!=EEXIST?-1:0;
#else
    return mkdir(path,0700)&&errno!=EEXIST?-1:0;
#endif
}
int main(int argc,char**argv)
{
#ifdef _WIN32
    _setmode(_fileno(stdin),_O_BINARY);_setmode(_fileno(stdout),_O_BINARY);
#endif
    static uint8_t raw[MAX_PACKET],encoded[MAX_PACKET+MAX_PACKET/8+8],decoded[MAX_PACKET],bitmap[256*192];
    static uint16_t gpu_palette[256];
    if(argc==2&&!strcmp(argv[1],"--encode")){
        uint32_t size;
        while(fread(&size,4,1,stdin)==1&&size){
            if(size>MAX_PACKET||fread(raw,1,size,stdin)!=size)return 2;
            uint32_t bytes=encode_lz(raw,size,encoded),hash=pixel_hash(raw,size);
            if(lz10_decode(encoded,bytes,decoded,MAX_PACKET)!=(int)size||memcmp(raw,decoded,size))return 3;
            fwrite(&bytes,4,1,stdout);fwrite(&hash,4,1,stdout);fwrite(encoded,1,bytes,stdout);fflush(stdout);
        }return 0;
    }
    if(argc<4)return 4;
    PalFile p;if(open_pack(&p,argv[2]))return 5;
    if(!strcmp(argv[1],"--basis")){
        if(argc!=5)return 6;unsigned target=strtoul(argv[3],0,10);if(target>=p.count)return 7;
        unsigned key=le32(p.index+target*PAL_ENTRY+16);memset(raw,0,p.raw);
        for(unsigned i=key;i<target;i++)if(decode_at(&p,i,raw,decoded))return 8;
        FILE*f=fopen(argv[4],"wb");if(!f||fwrite(raw,1,p.raw,f)!=p.raw)return 9;fclose(f);free(p.data);return 0;
    }
    if(strcmp(argv[1],"--verify")||ensure_dir(argv[3]))return 10;
    memset(raw,0,p.raw);unsigned mode_counts[17]={0};
    for(unsigned i=0;i<p.count;i++){
        if(decode_at(&p,i,raw,decoded))return 11;
        mode_counts[le32(p.index+i*PAL_ENTRY+12)&~MODE_RAW]++;
        if(i==0||i==p.count/4||i==p.count/2||i==p.count*3/4||i+1==p.count){
            char path[1024];snprintf(path,sizeof(path),"%s/%05u.tiles",argv[3],i);FILE*f=fopen(path,"wb");
            if(!f||fwrite(raw,1,p.raw,f)!=p.raw)return 12;fclose(f);
            snprintf(path,sizeof(path),"%s/%05u.pal",argv[3],i);f=fopen(path,"wb");
            unsigned pal=le32(p.index+i*PAL_ENTRY+20);if(!f||fwrite(p.data+p.palette_at+pal*p.colors*2,2,p.colors,f)!=p.colors)return 13;fclose(f);
            make_bitmap8(bitmap,raw,p.colors);
            if(make_gpu_palette(gpu_palette,(const uint16_t*)(p.data+p.palette_at+pal*p.colors*2),p.colors))return 15;
            snprintf(path,sizeof(path),"%s/%05u.bmp8",argv[3],i);f=fopen(path,"wb");if(!f||fwrite(bitmap,1,256*192,f)!=256*192)return 16;fclose(f);
            snprintf(path,sizeof(path),"%s/%05u.gpu_pal",argv[3],i);f=fopen(path,"wb");if(!f||fwrite(gpu_palette,2,256,f)!=256)return 17;fclose(f);
        }
    }
    unsigned seeks=0;
    for(unsigned target=0;target<p.count;target++){
        unsigned phase=target%(p.fps*2);
        if(p.count>16&&target&&target+1!=p.count&&phase>1&&phase+1<p.fps*2&&target!=p.count/2)continue;
        unsigned key=le32(p.index+target*PAL_ENTRY+16);memset(raw,0,p.raw);
        for(unsigned i=key;i<=target;i++)if(decode_at(&p,i,raw,decoded))return 14;
        seeks++;
    }
    printf("{\"frames\":%u,\"seek_checks\":%u,\"colors\":%u,\"raw_bytes\":%u,\"key\":%u,\"xor\":%u,\"tile\":%u,\"skip\":%u}\n",
        p.count,seeks,p.colors,p.raw,mode_counts[1],mode_counts[2],mode_counts[4],mode_counts[16]);
    free(p.data);return 0;
}
#else
typedef struct {
    uint32_t magic,ready,cases,completed,errors,bus_clock,timer_divider,calibration_ticks;
    uint32_t total_ticks,max_ticks,frame_buffer_bytes,work_buffer_bytes,packed_buffer_bytes;
} PalProbeStatus;
typedef struct {
    uint32_t fps,colors,frame,pass,bytes,flags,decode_ticks,read_ticks,raw_bytes,work_bytes;
} PalProbeSample;
volatile PalProbeStatus g_pal_probe_status;
volatile PalProbeSample g_pal_probe_samples[256];
static uint8_t pal_frame[MAX_RAW] __attribute__((aligned(32)));
static uint8_t pal_work[MAX_PACKET] __attribute__((aligned(32)));
static uint8_t pal_packed[MAX_PACKET] __attribute__((aligned(32)));
static uint16_t pal_colors[256] __attribute__((aligned(32)));
static uint16_t gpu_colors[256] __attribute__((aligned(32)));
static uint8_t pal_bitmap[256*192] __attribute__((aligned(32)));
static uint32_t timer_ticks(void)
{
    uint16_t high,low,check;
    do{high=TIMER_DATA(1);low=TIMER_DATA(0);check=TIMER_DATA(1);}while(high!=check);
    return (uint32_t)high<<16|low;
}
static int read_case(unsigned i,const char*suffix,void*buffer,unsigned bytes)
{
    char path[64];snprintf(path,sizeof(path),"nitro:/probe/%03u.%s",i,suffix);
    FILE*f=fopen(path,"rb");if(!f)return -1;
    size_t got=fread(buffer,1,bytes,f);fclose(f);return got==bytes?0:-1;
}
int main(void)
{
    consoleDemoInit();lcdMainOnTop();videoSetMode(MODE_5_2D);vramSetBankB(VRAM_B_MAIN_BG_0x06020000);
    int bg=bgInit(3,BgType_Bmp8,BgSize_B8_256x256,8,0);
    g_pal_probe_status.magic=0x4250565a;g_pal_probe_status.bus_clock=BUS_CLOCK;g_pal_probe_status.timer_divider=64;
    g_pal_probe_status.frame_buffer_bytes=MAX_RAW;g_pal_probe_status.work_buffer_bytes=MAX_PACKET;g_pal_probe_status.packed_buffer_bytes=MAX_PACKET;
    if(!nitroFSInit(NULL)){g_pal_probe_status.errors++;goto finished;}
    FILE*index=fopen("nitro:/probe/index.bin","rb");uint8_t header[16],records[128*40];
    if(!index||fread(header,1,16,index)!=16||memcmp(header,"ZPLPRB01",8)||le32(header+12)!=40){g_pal_probe_status.errors++;goto finished;}
    unsigned count=le32(header+8);
    if(!count||count>128||fread(records,40,count,index)!=count){g_pal_probe_status.errors++;goto finished;}
    fclose(index);g_pal_probe_status.cases=count*2;
    TIMER_CR(0)=TIMER_CR(1)=0;TIMER_DATA(0)=TIMER_DATA(1)=0;
    TIMER_CR(1)=TIMER_ENABLE|TIMER_CASCADE;TIMER_CR(0)=TIMER_ENABLE|TIMER_DIV_64;
    swiWaitForVBlank();uint32_t start=timer_ticks();for(unsigned i=0;i<60;i++)swiWaitForVBlank();
    g_pal_probe_status.calibration_ticks=timer_ticks()-start;
    for(unsigned pass=0;pass<2;pass++)for(unsigned i=0;i<count;i++){
        const uint8_t*r=records+i*40;
        PalProbeSample sample={.fps=le32(r),.colors=le32(r+4),.frame=le32(r+8),.pass=pass,.bytes=le32(r+12),.flags=le32(r+20),.raw_bytes=le32(r+32),.work_bytes=le32(r+16)};
        unsigned decoded=le32(r+16),hash=le32(r+24),palette_bytes=le32(r+28);
        if(sample.colors<16||sample.colors>256||sample.bytes>MAX_PACKET||decoded>MAX_PACKET||sample.raw_bytes>MAX_RAW||palette_bytes>512||
           read_case(i,"base",pal_frame,sample.raw_bytes)||read_case(i,"pal",pal_colors,palette_bytes)){g_pal_probe_status.errors++;goto finished;}
        start=timer_ticks();int read_error=read_case(i,"bin",pal_packed,sample.bytes);sample.read_ticks=timer_ticks()-start;
        if(read_error){g_pal_probe_status.errors++;goto finished;}
        swiWaitForVBlank();start=timer_ticks();
        int result=apply_frame(pal_frame,sample.raw_bytes,pal_packed,sample.bytes,decoded,sample.flags,pal_work);
        make_bitmap8(pal_bitmap,pal_frame,sample.colors);result|=make_gpu_palette(gpu_colors,pal_colors,sample.colors);
        DC_FlushRange(pal_bitmap,sizeof(pal_bitmap));DC_FlushRange(gpu_colors,sizeof(gpu_colors));
        dmaCopy(pal_bitmap,bgGetGfxPtr(bg),sizeof(pal_bitmap));dmaCopy(gpu_colors,BG_PALETTE,sizeof(gpu_colors));
        sample.decode_ticks=timer_ticks()-start;
        if(result||pixel_hash(pal_frame,sample.raw_bytes)!=hash){g_pal_probe_status.errors++;goto finished;}
        g_pal_probe_samples[pass*count+i]=sample;g_pal_probe_status.completed++;
        g_pal_probe_status.total_ticks+=sample.decode_ticks;if(sample.decode_ticks>g_pal_probe_status.max_ticks)g_pal_probe_status.max_ticks=sample.decode_ticks;
        printf("\x1b[0;0HZATO PALETTE VIDEO PROBE\n%lu/%lu frames\n%lu colors / %lu fps\nDecode+VRAM %lu ticks\nNative LZ10 + tile delta\nNo audio in this probe",
            (unsigned long)g_pal_probe_status.completed,(unsigned long)g_pal_probe_status.cases,
            (unsigned long)sample.colors,(unsigned long)sample.fps,(unsigned long)sample.decode_ticks);
    }
#ifdef OP_PREVIEW_CASE
    {
        unsigned i=OP_PREVIEW_CASE;const uint8_t*r=records+i*40;
        unsigned colors=le32(r+4),bytes=le32(r+12),decoded=le32(r+16),flags=le32(r+20),palette_bytes=le32(r+28),raw=le32(r+32);
        if(read_case(i,"base",pal_frame,raw)||read_case(i,"pal",pal_colors,palette_bytes)||read_case(i,"bin",pal_packed,bytes)||
           apply_frame(pal_frame,raw,pal_packed,bytes,decoded,flags,pal_work)){g_pal_probe_status.errors++;goto finished;}
        make_bitmap8(pal_bitmap,pal_frame,colors);
        if(make_gpu_palette(gpu_colors,pal_colors,colors)){g_pal_probe_status.errors++;goto finished;}
        DC_FlushRange(pal_bitmap,sizeof(pal_bitmap));DC_FlushRange(gpu_colors,sizeof(gpu_colors));swiWaitForVBlank();
        dmaCopy(pal_bitmap,bgGetGfxPtr(bg),sizeof(pal_bitmap));dmaCopy(gpu_colors,BG_PALETTE,sizeof(gpu_colors));
    }
#endif
finished:g_pal_probe_status.ready=1;while(1)swiWaitForVBlank();
}
#endif
