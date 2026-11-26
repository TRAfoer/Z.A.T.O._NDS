#include "scene_gfx.h"
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_GROUPS 32u
#define MAX_BANKS 16u
#define MAX_RESIDENT (2400u*1024u)
#define MAP_TILES 576u
#define MAP_BYTES (MAP_TILES*2u)

typedef struct { uint32_t id, bank, first, count, step, loop, reserved[2]; } Group;
typedef struct { uint32_t offset, bytes, tiles, maps, hash, reserved; } Bank;
static Group groups[MAX_GROUPS];
static Bank banks[MAX_BANKS];
static uint32_t group_count, bank_count, data_offset, read_calls;
static size_t resident;
static unsigned char *data;
static char error_text[96];

#if defined(__GNUC__)
extern void audio_demo_service(void) __attribute__((weak));
#else
extern void audio_demo_service(void);
#endif

static void service_audio(void)
{
    if (audio_demo_service) audio_demo_service();
}

static uint32_t u32(const unsigned char *p)
{
    return p[0] | ((uint32_t)p[1]<<8) | ((uint32_t)p[2]<<16) | ((uint32_t)p[3]<<24);
}

static uint16_t u16(const unsigned char *p)
{
    return p[0] | ((uint16_t)p[1]<<8);
}

static uint32_t fnv(const unsigned char *p, size_t size)
{
    uint32_t hash=2166136261u;
    for (size_t i=0;i<size;++i) {
        hash=(hash^p[i])*16777619u;
        if ((i&4095u)==4095u) service_audio();
    }
    return hash;
}

static bool read_exact(FILE *f, void *output, size_t size)
{
    unsigned char *p=output;
    while (size) {
        size_t n=size>4096u?4096u:size;
        ++read_calls;
        size_t got=fread(p,1,n,f);
        service_audio();
        if (got!=n) return false;
        p+=n;size-=n;
    }
    return true;
}

static const Group *find_group(unsigned id)
{
    if (!data) return NULL;
    for (uint32_t i=0;i<group_count;++i) if (groups[i].id==id) return &groups[i];
    return NULL;
}

void scene_gfx_close(void)
{
    free(data);data=NULL;resident=0;group_count=bank_count=0;data_offset=0;
}

bool scene_gfx_open(const char *path)
{
    unsigned char header[40], record[32];
    FILE *f=NULL;
    const char *failure="invalid scene graphics header";
    scene_gfx_close();read_calls=0;error_text[0]=0;
    if (!path || !(f=fopen(path,"rb"))) { failure="cannot open scene graphics";goto failed; }
    if (fseek(f,0,SEEK_END)) goto failed;
    long file_size=ftell(f);
    if (file_size<40 || (uint64_t)file_size>UINT32_MAX || fseek(f,0,SEEK_SET)) goto failed;
    if (!read_exact(f,header,sizeof(header))) goto failed;
    if (memcmp(header,"ZATOGFX1",8) || u32(header+8)!=1 || u32(header+12)!=256 ||
        u32(header+16)!=144 || u32(header+20)!=24 || u32(header+32)!=(uint32_t)file_size || u32(header+36)) goto failed;
    group_count=u32(header+24);bank_count=u32(header+28);
    if (!group_count || group_count>MAX_GROUPS || !bank_count || bank_count>MAX_BANKS) goto failed;
    data_offset=40u+group_count*32u+bank_count*24u;
    if ((uint32_t)file_size<=data_offset || (uint32_t)file_size-data_offset>MAX_RESIDENT) goto failed;
    for (uint32_t i=0;i<group_count;++i) {
        if (!read_exact(f,record,32)) goto failed;
        groups[i]=(Group){u32(record),u32(record+4),u32(record+8),u32(record+12),
                          u32(record+16),u32(record+20),{u32(record+24),u32(record+28)}};
        const Group *g=&groups[i];
        if (g->bank>=bank_count || !g->count || !g->step || g->loop>1 || g->reserved[0] || g->reserved[1]) goto failed;
        for (uint32_t j=0;j<i;++j) if (groups[j].id==g->id) goto failed;
    }
    uint32_t next=data_offset;
    for (uint32_t i=0;i<bank_count;++i) {
        if (!read_exact(f,record,24)) goto failed;
        banks[i]=(Bank){u32(record),u32(record+4),u32(record+8),u32(record+12),u32(record+16),u32(record+20)};
        const Bank *b=&banks[i];
        uint64_t expected=(uint64_t)b->tiles*128u+(uint64_t)b->maps*MAP_BYTES;
        if (!b->tiles || b->tiles>65535u || !b->maps || b->reserved || b->offset!=next ||
            expected!=b->bytes || (uint64_t)b->offset+b->bytes>(uint32_t)file_size) goto failed;
        next+=b->bytes;
    }
    if (next!=(uint32_t)file_size) goto failed;
    for (uint32_t i=0;i<group_count;++i) {
        const Group *g=&groups[i];
        if ((uint64_t)g->first+g->count>banks[g->bank].maps) goto failed;
    }
    resident=(uint32_t)file_size-data_offset;
    failure="cannot allocate resident scene graphics";
    data=malloc(resident);
    if (!data) goto failed;
    failure="truncated scene graphics payload";
    if (!read_exact(f,data,resident)) goto failed;
    fclose(f);f=NULL;
    failure="scene graphics integrity or tile index mismatch";
    for (uint32_t i=0;i<bank_count;++i) {
        const Bank *b=&banks[i];
        const unsigned char *p=data+b->offset-data_offset;
        if (fnv(p,b->bytes)!=b->hash) goto failed;
        p+=b->tiles*128u;
        for (uint32_t tile=0;tile<b->maps*MAP_TILES;++tile)
            if (u16(p+tile*2u)>=b->tiles) goto failed;
        service_audio();
    }
    return true;
failed:
    if (f) fclose(f);
    scene_gfx_close();snprintf(error_text,sizeof(error_text),"%s",failure);
    return false;
}

bool scene_gfx_select(unsigned group)
{
    return find_group(group)!=NULL;
}

unsigned scene_gfx_frame(unsigned group, uint32_t ticks)
{
    const Group *g=find_group(group);
    if (!g) return UINT_MAX;
    uint32_t frame=ticks/g->step;
    return g->loop?frame%g->count:(frame<g->count?frame:g->count-1);
}

#if defined(__GNUC__) && (!defined(__BYTE_ORDER__) || __BYTE_ORDER__ != __ORDER_BIG_ENDIAN__)
/* Source tiles and maps are 4-byte aligned in the single malloc payload.
 * may_alias permits four pixels per store into the public uint16_t buffer.
 * The small ARM/ITCM loop avoids 4608 out-of-line Thumb memcpy calls per frame;
 * keep loaders/checksums and all data in normal RAM. */
typedef uint32_t GfxWord __attribute__((may_alias));
#if defined(__arm__)
__attribute__((section(".itcm"),target("arm"),optimize("O3"),long_call))
#endif
static void draw_fast(const unsigned char *restrict tiles,
                      const unsigned char *restrict map,uint16_t *restrict screen)
{
    GfxWord *top=(GfxWord*)screen,*bottom=(GfxWord*)(screen+256u*168u);
    for (unsigned i=0;i<256u*24u/2u;i+=4u) {
        top[i]=top[i+1]=top[i+2]=top[i+3]=0x80008000u;
        bottom[i]=bottom[i+1]=bottom[i+2]=bottom[i+3]=0x80008000u;
    }
    const uint16_t *indices=(const uint16_t*)map;
    for (unsigned ty=0;ty<18;++ty) {
        for (unsigned tx=0;tx<32;++tx) {
            const GfxWord *restrict tile=(const GfxWord*)(tiles+indices[ty*32u+tx]*128u);
            GfxWord *restrict destination=(GfxWord*)(screen+(ty*8u+24u)*256u+tx*8u);
            for (unsigned y=0;y<8;++y) {
                uint32_t a=tile[0],b=tile[1],c=tile[2],d=tile[3];
                destination[0]=a;destination[1]=b;destination[2]=c;destination[3]=d;
                destination+=128;tile+=4;
            }
        }
    }
}
#endif

bool scene_gfx_draw(unsigned group, uint32_t ticks, uint16_t *screen)
{
    const Group *g=find_group(group);
    if (!g || !screen) return false;
    const Bank *b=&banks[g->bank];
    const unsigned char *tiles=data+b->offset-data_offset;
    const unsigned char *map=tiles+b->tiles*128u+(g->first+scene_gfx_frame(group,ticks))*MAP_BYTES;
#if defined(__GNUC__) && (!defined(__BYTE_ORDER__) || __BYTE_ORDER__ != __ORDER_BIG_ENDIAN__)
    if (!((uintptr_t)screen&3u)) {
        draw_fast(tiles,map,screen);
        return true;
    }
#endif
    for (unsigned i=0;i<256u*24u;++i) screen[i]=screen[256u*168u+i]=0x8000;
    for (unsigned ty=0;ty<18;++ty) {
        for (unsigned tx=0;tx<32;++tx) {
            const unsigned char *tile=tiles+u16(map+(ty*32u+tx)*2u)*128u;
            uint16_t *destination=screen+(ty*8u+24u)*256u+tx*8u;
            for (unsigned y=0;y<8;++y) {
#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
                for (unsigned x=0;x<8;++x) destination[x]=u16(tile+x*2u);
#else
                memcpy(destination,tile,16);
#endif
                destination+=256;tile+=16;
            }
        }
    }
    return true;
}

const char *scene_gfx_error(void) { return error_text; }
size_t scene_gfx_resident_bytes(void) { return resident; }
uint32_t scene_gfx_read_calls(void) { return read_calls; }
