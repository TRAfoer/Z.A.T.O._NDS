#include "../stubs/nds.h"
#include <assert.h>
#include <string.h>
enum {MODE_5_2D=5,VRAM_B_MAIN_BG_0x06020000=0,VRAM_C_SUB_BG=4,BgType_Bmp8=1,BgSize_B8_256x256=2};
static uint16_t test_bg_palette[256],test_sub_bg_palette[256];
static uint8_t test_bg_pixels[65536],test_sub_bg_pixels[65536];
static struct { int base,shown,pa,pd,x,y,sx,sy,scroll_x,scroll_y; } test_bgs[8];
static unsigned test_sub_mode,test_bank_c,test_sub_init_count;
static unsigned test_vblank,test_dma_count[4],test_dma_vblank[4];
static const void *test_dma_source[4];
#define BG_PALETTE test_bg_palette
#define BG_PALETTE_SUB test_sub_bg_palette
static inline void videoSetMode(unsigned v){(void)v;}
static inline void videoSetModeSub(unsigned v){test_sub_mode=v;}
static inline void vramSetBankB(unsigned v){(void)v;}
static inline void vramSetBankC(unsigned v){test_bank_c=v;}
static inline int test_init(int id,int base){memset(&test_bgs[id],0,sizeof(test_bgs[id]));test_bgs[id].base=base;test_bgs[id].shown=1;test_bgs[id].sx=test_bgs[id].sy=test_bgs[id].pa=test_bgs[id].pd=256;return id;}
static inline int bgInit(int layer,int type,int size,int base,int map){(void)type;(void)size;(void)map;return test_init(layer,base);}
static inline int bgInitSub(int layer,int type,int size,int base,int map){(void)type;(void)size;(void)map;test_sub_init_count++;return test_init(layer+4,base);}
static inline void *bgGetGfxPtr(int layer){assert(layer==3||layer==7);return layer<4?test_bg_pixels:test_sub_bg_pixels;}
static inline void bgHide(int layer){assert(layer>=0&&layer<8);test_bgs[layer].shown=0;}
static inline void bgShow(int layer){assert(layer>=0&&layer<8);test_bgs[layer].shown=1;}
static inline void bgSetPriority(int layer,int p){(void)layer;(void)p;}
static inline void bgSetScale(int layer,int x,int y){test_bgs[layer].sx=x;test_bgs[layer].sy=y;}
static inline void bgSetScroll(int layer,int x,int y){test_bgs[layer].scroll_x=x*256;test_bgs[layer].scroll_y=y*256;}
static inline void bgUpdate(void){
    /* SDK angle0/center0: PA=sx, PD=sy, X=scrollX, Y=scrollY. */
    for(unsigned i=0;i<8;i++){test_bgs[i].pa=test_bgs[i].sx;test_bgs[i].pd=test_bgs[i].sy;test_bgs[i].x=test_bgs[i].scroll_x;test_bgs[i].y=test_bgs[i].scroll_y;}
}
static inline unsigned test_screen_index(unsigned layer,unsigned x,unsigned y){
    unsigned sx=(test_bgs[layer].x+(int)x*test_bgs[layer].pa)>>8,sy=(test_bgs[layer].y+(int)y*test_bgs[layer].pd)>>8;
    assert(sx<256&&sy<256);return (layer<4?test_bg_pixels:test_sub_bg_pixels)[sy*256+sx];
}
static inline void DC_FlushRange(const void *p,unsigned n){(void)p;(void)n;}
static inline void dmaCopy(const void *src,void *dest,unsigned n){
    unsigned i=dest==test_bg_pixels?0:dest==test_sub_bg_pixels?1:dest==test_bg_palette?2:3;
    assert(dest==test_bg_pixels||dest==test_sub_bg_pixels||dest==test_bg_palette||dest==test_sub_bg_palette);
    assert(n==(i<2?49152u:512u));test_dma_count[i]++;test_dma_vblank[i]=test_vblank;test_dma_source[i]=src;memcpy(dest,src,n);
}
