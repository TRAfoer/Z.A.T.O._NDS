/* Host validator: independently written raw golden frames, not pack hashes. */
#include "scene_gfx.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned services;
void audio_demo_service(void) { ++services; }
static uint32_t read32(FILE *f)
{
    unsigned char b[4];
    assert(fread(b, 1, 4, f) == 4);
    return b[0] | ((uint32_t)b[1]<<8) | ((uint32_t)b[2]<<16) | ((uint32_t)b[3]<<24);
}
int main(int argc, char **argv)
{
    uint16_t *screen = malloc(256u*192u*2u);
    uint16_t *gold = malloc(256u*192u*2u);
    unsigned char *half_storage=malloc(256u*192u*2u+4u);
    uint16_t *half_aligned=(uint16_t*)(half_storage+2u);
    assert(screen && gold && half_storage && argc >= 3);
    if (!strcmp(argv[1], "reject")) {
        if (scene_gfx_open(argv[2])) { fprintf(stderr,"accepted invalid pack\n"); return 1; }
        assert(scene_gfx_resident_bytes() == 0);
        assert(!scene_gfx_draw(0,0,screen));
        puts(scene_gfx_error());
        free(screen); free(gold); free(half_storage); return 0;
    }
    if (!scene_gfx_open(argv[1])) {
        fprintf(stderr,"open failed: %s\n",scene_gfx_error()); return 1;
    }
    assert(services > 0);
    assert(scene_gfx_resident_bytes() <= 2400u*1024u);
    uint32_t reads = scene_gfx_read_calls();
    assert(reads && !scene_gfx_select(0x7fffffffu));
    FILE *f = fopen(argv[2],"rb"); assert(f);
    char magic[8]; assert(fread(magic,1,8,f)==8 && !memcmp(magic,"ZATOGOLD",8));
    uint32_t count = read32(f);
    for (uint32_t i=0; i<count; ++i) {
        uint32_t group=read32(f), ticks=read32(f), frame=read32(f);
        assert(fread(gold,2,256u*192u,f)==256u*192u);
        assert(scene_gfx_select(group));
        assert(scene_gfx_frame(group,ticks)==frame);
        assert(scene_gfx_draw(group,ticks,screen));
        assert(scene_gfx_draw(group,ticks,half_aligned));
        assert(!memcmp(half_aligned,gold,256u*192u*2u));
        if (memcmp(screen,gold,256u*192u*2u)) {
            for (unsigned p=0;p<256u*192u;++p) if(screen[p]!=gold[p]) {
                fprintf(stderr,"pixel mismatch item %u group %u tick %u x%u y%u got%04x expected%04x\n",
                        i,group,ticks,p%256,p/256,screen[p],gold[p]); break;
            }
            return 1;
        }
        assert(scene_gfx_read_calls()==reads);
    }
    assert(!scene_gfx_draw(0xffffffffu,0,screen));
    assert(!scene_gfx_draw(0,0,NULL));
    fclose(f); scene_gfx_close();
    assert(scene_gfx_resident_bytes()==0);
    assert(!scene_gfx_select(0));
    printf("PASS %u independent pixel/time goldens; resident load services=%u; draw/select reads=0\n",count,services);
    free(screen); free(gold); free(half_storage); return 0;
}
