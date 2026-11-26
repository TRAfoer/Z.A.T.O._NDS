/* Independent before/after storage proof through the production C decoder. */
#include "asset_pack.h"
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <inttypes.h>

_Static_assert(offsetof(PackFrame,width)==16,"Only four storage fields may change");
typedef struct {uint64_t frames,pixels,zero,partial,opaque;} Stats;
static void quoted(const char*s){
    putchar('"');for(;*s;s++){unsigned char c=(unsigned char)*s;if(c=='"'||c=='\\')putchar('\\');if(c>=32)putchar(c);else printf("\\u%04x",c);}putchar('"');
}
static int failure(const char*phase,int frame,const char*error){
    printf("{\"result\":\"FAIL\",\"phase\":");quoted(phase);
    printf(",\"frame\":%d,\"error\":",frame);quoted(error);puts("}");return 2;
}
int main(int argc,char**argv){
    if(argc!=3)return failure("arguments",-1,"Expected before after");
    AssetPack before={0},after={0};BankCache bc={.id=-1},ac={.id=-1};
    if(!pack_open(&before,argv[1]))return failure("before_open",-1,before.error);
    if(!pack_open(&after,argv[2]))return failure("after_open",-1,after.error);
    PackHeader expected=before.header;expected.banks=after.header.banks;
    if(memcmp(&expected,&after.header,sizeof(expected)))return failure("header",-1,"Non-storage header changed");
    if(memcmp(before.stages,after.stages,before.header.stages*sizeof(*before.stages)))return failure("stage_list",-1,"Stage frame IDs changed");
    if(memcmp(before.actors,after.actors,before.header.actors*sizeof(*before.actors)))return failure("actor_list",-1,"Actor frame IDs changed");
    Stats stats[3]={{0}};uint64_t pixels=0;unsigned checked=0;
    for(unsigned i=0;i<before.header.frames;i++){
        const PackFrame*f=&before.frames[i],*g=&after.frames[i];
        if(memcmp((const unsigned char*)f+16,(const unsigned char*)g+16,sizeof(*f)-16))return failure("frame_metadata",(int)i,"frame[4:] geometry/category/format/hash/palette/name changed");
        uint64_t count64=(uint64_t)f->width*f->height;
        if(!count64||count64>16777216||f->format>2)return failure("frame_size",(int)i,"Invalid frame geometry or format");
        uint32_t count=(uint32_t)count64;
        uint16_t *rgb0=malloc((size_t)count*2),*rgb1=malloc((size_t)count*2);
        uint8_t *alpha0=malloc(count),*alpha1=malloc(count);
        if(!rgb0||!rgb1||!alpha0||!alpha1)return failure("allocation",(int)i,"Pixel buffer allocation failed");
        if(!pack_decode_alpha(&before,&bc,i,rgb0,alpha0,count))return failure("before_decode",(int)i,before.error);
        if(!pack_decode_alpha(&after,&ac,i,rgb1,alpha1,count))return failure("after_decode",(int)i,after.error);
        if(memcmp(rgb0,rgb1,(size_t)count*2))return failure("RGB15_compare",(int)i,"Decoded RGB15 bytes differ");
        if(memcmp(alpha0,alpha1,count))return failure("alpha8_compare",(int)i,"Decoded alpha8 bytes differ");
        Stats*s=&stats[f->format];s->frames++;s->pixels+=count;
        for(uint32_t j=0;j<count;j++){if(!alpha0[j])s->zero++;else if(alpha0[j]==255)s->opaque++;else s->partial++;}
        pixels+=count;checked++;
        free(rgb0);free(rgb1);free(alpha0);free(alpha1);
    }
    printf("{\"result\":\"PASS\",\"frames\":%u,\"frames_checked\":%u,\"total_pixels\":%" PRIu64 ",\"RGB15_bytes_compared\":%" PRIu64 ",\"alpha8_bytes_compared\":%" PRIu64 ",",
        before.header.frames,checked,pixels,pixels*2,pixels);
    printf("\"before_banks\":%u,\"after_banks\":%u,\"stages\":%u,\"actors\":%u,\"frame_metadata_bytes_compared\":%zu,",
        before.header.banks,after.header.banks,before.header.stages,before.header.actors,(size_t)checked*(sizeof(PackFrame)-16));
    printf("\"header_semantics_equal\":true,\"frame_metadata_equal\":true,\"stage_list_equal\":true,\"actor_list_equal\":true,\"RGB15_byte_differences\":0,\"alpha8_byte_differences\":0,\"formats\":[");
    for(unsigned i=0;i<3;i++)printf("%s{\"format\":%u,\"frames\":%" PRIu64 ",\"pixels\":%" PRIu64 ",\"alpha_zero\":%" PRIu64 ",\"alpha_partial\":%" PRIu64 ",\"alpha_opaque\":%" PRIu64 "}",i?",":"",i,stats[i].frames,stats[i].pixels,stats[i].zero,stats[i].partial,stats[i].opaque);
    puts("]}");bank_cache_clear(&bc);bank_cache_clear(&ac);pack_close(&before);pack_close(&after);return 0;
}
