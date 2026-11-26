#include "asset_pack.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>

static unsigned validate(AssetPack *pack, const char *gold_path)
{
    FILE *gold=fopen(gold_path,"rb");assert(gold);
    char magic[8];uint32_t count;
    assert(fread(magic,1,8,gold)==8 && !memcmp(magic,"ZATAGOLD",8));
    assert(fread(&count,4,1,gold)==1 && count==pack->header.frames);
    BankCache cache={.id=-1};
    uint16_t *pixels=malloc(256000*2),*expected=malloc(256000*2);
    uint16_t *screen=malloc(256*192*2);assert(pixels&&expected&&screen);
    for(unsigned id=0;id<count;id++) {
        uint32_t header[3];assert(fread(header,4,3,gold)==3);
        const PackFrame *f=&pack->frames[id];
        assert(header[0]==id && header[1]==f->width && header[2]==f->height);
        unsigned bytes=(unsigned)f->width*f->height*2;
        assert(bytes<=256000*2 && fread(expected,1,bytes,gold)==bytes);
        if(!pack_decode(pack,&cache,id,pixels,256000)) {
            fprintf(stderr,"Frame %u: %s\n",id,pack->error);exit(1);
        }
        assert(!memcmp(pixels,expected,bytes));
        assert(pack_draw(pack,&cache,id,screen,NULL));
        for(int y=0;y<192;y++)for(int x=0;x<256;x++) {
            int sx=x-f->x,sy=y-f->y;
            uint16_t want=sx>=0&&sy>=0&&sx<f->width&&sy<f->height?expected[sy*f->width+sx]:0;
            assert(screen[y*256+x]==want);
        }
    }
    assert(fgetc(gold)==EOF);fclose(gold);
    free(screen);free(expected);free(pixels);bank_cache_clear(&cache);
    return count;
}

static bool reject(AssetPack *pack)
{
    BankCache cache={.id=-1};uint16_t *pixels=malloc(256000*2);assert(pixels);
    bool failed=false;
    for(unsigned i=0;i<pack->header.frames;i++)
        if(!pack_decode(pack,&cache,i,pixels,256000)){failed=true;break;}
    free(pixels);bank_cache_clear(&cache);return failed;
}

int main(int argc,char **argv)
{
    assert(argc==3);
    AssetPack pack;
    if(!strcmp(argv[1],"reject")) {
        bool bad=!pack_open(&pack,argv[2]);
        if(!bad)bad=reject(&pack);
        pack_close(&pack);assert(bad);
        FILE *file=fopen(argv[2],"rb");assert(file);
        assert(!fseek(file,0,SEEK_END));long bytes=ftell(file);assert(bytes>=0);
        assert(!fseek(file,0,SEEK_SET));uint8_t *memory=calloc(1,bytes?bytes:4);assert(memory);
        assert(fread(memory,1,bytes,file)==(size_t)bytes);fclose(file);
        bad=!pack_open_memory(&pack,memory,(uint32_t)bytes);
        if(!bad)bad=reject(&pack);
        pack_close(&pack);free(memory);assert(bad);
        puts("PASS corrupt package rejected by file and memory backends");return 0;
    }
    assert(pack_open(&pack,argv[1]));unsigned count=validate(&pack,argv[2]);pack_close(&pack);
    FILE *file=fopen(argv[1],"rb");assert(file);assert(!fseek(file,0,SEEK_END));
    long bytes=ftell(file);assert(bytes>0 && !fseek(file,0,SEEK_SET));
    uint8_t *memory=malloc(bytes);assert(memory && fread(memory,1,bytes,file)==(size_t)bytes);fclose(file);
    assert(pack_open_memory(&pack,memory,(uint32_t)bytes));assert(validate(&pack,argv[2])==count);
    assert(pack_try_file(&pack,argv[1]));assert(validate(&pack,argv[2])==count);
    pack_use_memory(&pack);pack_close(&pack);free(memory);
    printf("PASS %u frames: independent master pixels and viewports, file/memory/shared-index backends\n",count);
    return 0;
}
