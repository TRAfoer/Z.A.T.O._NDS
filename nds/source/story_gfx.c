#include "story_gfx.h"
#include "asset_pack.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define MAX_FRAMES 1024u
#define MAX_BANKS 128u
#define MAX_LAYERS 16u
#define NORMAL_LIMIT 1048576u
#define TRANSITION_LIMIT 1572864u
#define Q 65536
#define NONE UINT32_MAX
#if defined(__GNUC__)
typedef uint32_t GfxWord __attribute__((may_alias));
#else
typedef uint32_t GfxWord;
#endif
typedef struct {int32_t kind,data,count,flags,vx0,vx1,vy0,vy1,start,seed,reserved[2];} Image;
typedef struct {uint32_t frame,duration,transition,reserved;} Sequence;
typedef struct {int32_t alpha,blend,z0,z1,duration,ax,ay,px,py,flip,yalign,flags;} Program;
typedef struct {int32_t source_width,fw,fh,cx,cy,sh,sy,reserved;} Geometry;
_Static_assert(sizeof(Image)==48,"story image layout");
_Static_assert(sizeof(Sequence)==16,"story sequence layout");
_Static_assert(sizeof(Program)==48,"story program layout");
_Static_assert(sizeof(Geometry)==32,"story geometry layout");
_Static_assert(sizeof(PackHeader)==40 && sizeof(PackBank)==16 && sizeof(PackFrame)==196,"frozen assets layout");
typedef struct {uint8_t *data;unsigned refs;} CachedBank;
typedef struct {uint16_t *map;unsigned refs;} CachedFrame;
typedef struct {unsigned image,tag,program;int placement;uint32_t start;bool loaded;} Layer;
_Static_assert(sizeof(Layer)==24,"unchanged layer ABI");
typedef struct {unsigned frame,old,mix;} Sample;
typedef struct {int32_t x,y,z;} Transform;
typedef struct {int64_t xbase;int32_t vx,vy;uint32_t delay,travel;} ParticleConstant;
_Static_assert(sizeof(ParticleConstant)==24,"particle constant cache layout");
static ParticleConstant **particle_constants;
static uint32_t hash32(uint32_t v);
static FILE *assets;
static PackHeader ah;
static uint32_t asset_bytes,io_calls,image_count,sequence_count,program_count,state_version;
static size_t resident;
static uint8_t *index_data,*catalog;
static const PackBank *banks;
static const PackFrame *frames;
static const Image *images;
static const Sequence *sequences;
static const Program *programs;
static const Geometry *geometries;
static CachedBank bc[MAX_BANKS];
static CachedFrame fc[MAX_FRAMES];
static Layer layers[MAX_LAYERS],old_layers[MAX_LAYERS];
static unsigned count,old_count,move_kind;
static uint32_t move_start,move_duration;
static bool replay,batch_valid,moving;
/* One value-keyed component table serves identity indexed multiply layers.
 * Ordinary BSS, outside ITCM; no decoded image or per-frame allocation. */
static union {uint8_t component[16][3][32];uint16_t pair[17][16];} multiply_lut;
static uint16_t multiply_palette[16];
static uint16_t multiply_background[16];
static unsigned multiply_alpha;
static unsigned multiply_kind; /*0 invalid,1 component table,2 bottom/top pair. */
static struct {
    bool pending,batch,moving;
    unsigned count,old_count,kind;
    uint32_t start,duration,version;
    Layer layers[MAX_LAYERS],old_layers[MAX_LAYERS];
} restore_backup;
static char error_text[112];
#if defined(__GNUC__)
extern void audio_demo_service(void) __attribute__((weak));
#else
extern void audio_demo_service(void);
#endif
static void service(void){if(audio_demo_service)audio_demo_service();}
static bool fail(const char*s){snprintf(error_text,sizeof(error_text),"%s",s);return false;}
static uint32_t r32(const uint8_t*p){return p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;}
static void *allocate(size_t n){size_t limit=batch_valid?TRANSITION_LIMIT:NORMAL_LIMIT;if(!n||resident>limit||n>limit-resident){fail("graphics heap budget");return NULL;}void*p=malloc(n);if(!p){fail("graphics allocation");return NULL;}resident+=n;return p;}
static void release(void*p,size_t n){if(p){free(p);resident-=n;}}
static bool cache_particles(void){
    particle_constants=allocate(image_count*sizeof(*particle_constants));
    if(!particle_constants)return false;
    memset(particle_constants,0,image_count*sizeof(*particle_constants));
    for(unsigned n=0;n<image_count;n++){
        const Image*i=&images[n];if(i->kind!=2)continue;
        ParticleConstant*items=allocate((unsigned)i->count*sizeof(*items));
        if(!items)return false;
        particle_constants[n]=items;
        for(unsigned at=0;at<(unsigned)i->count;at++){
            uint32_t h=hash32((uint32_t)i->seed+at*0x9e3779b9u);
            uint32_t delay=at&&i->start?h%((unsigned)i->start*60u+1u):0;
            int32_t vx=i->vx0+(int32_t)(((int64_t)i->vx1-i->vx0)*(hash32(h)&65535)/65535);
            int32_t vy=i->vy0+(int32_t)(((int64_t)i->vy1-i->vy0)*(hash32(h+1)&65535)/65535);
            if(!vy)vy=13107;
            uint32_t travel=(uint32_t)((164ll*Q*60)/(vy<0?-(int64_t)vy:vy));
            int64_t xdist=(int64_t)vx*travel/60;
            int64_t xmin=xdist>0?-xdist:0,xmax=256ll*Q+(xdist>0?xdist:0);
            int64_t span=xmax-xmin;unsigned fraction=hash32(h+2)&65535;
            /* Equivalent nonnegative interpolation without a potentially
             * overflowing span*fraction for adversarial velocity ranges. */
            int64_t xbase=xmin+(span/65535)*fraction+(span%65535)*fraction/65535;
            uint64_t max_product=(uint64_t)(vx<0?-(int64_t)vx:vx)*(travel?travel-1:0);
            if(max_product>INT32_MAX)delay|=0x80000000u;
            items[at]=(ParticleConstant){xbase,vx,vy,delay,travel?travel:1};
            if(!(at&15))service();
        }
    }
    return true;
}
static uint32_t crc(const uint8_t*p,size_t n){uint32_t v=~0u;for(size_t i=0;i<n;i++){v^=p[i];for(unsigned b=0;b<8;b++)v=(v>>1)^(0xedb88320u&-(v&1));if((i&4095u)==4095u)service();}return ~v;}
static bool read_at(FILE*f,uint32_t length,uint32_t offset,void*destination,uint32_t n){
    if(offset>length||n>length-offset)return fail("graphics file range");
    if(fseek(f,offset,SEEK_SET))return fail("graphics seek");
    uint8_t*p=destination;while(n){unsigned take=n>4096?4096:n;io_calls++;size_t got=fread(p,1,take,f);service();if(got!=take)return fail("graphics short read");p+=take;n-=take;}return true;
}
typedef struct {uint32_t offset,remaining,cursor,filled;uint8_t buffer[1024];} Reader;
static int read_byte(Reader*r){if(r->cursor==r->filled){if(!r->remaining)return -1;r->filled=r->remaining>1024?1024:r->remaining;if(!read_at(assets,asset_bytes,r->offset,r->buffer,r->filled))return -1;r->offset+=r->filled;r->remaining-=r->filled;r->cursor=0;}return r->buffer[r->cursor++];}
static bool inflate(uint32_t offset,uint32_t packed,uint8_t*dst,uint32_t raw){
    if(!raw||raw>262144||!packed||packed>300000||offset>asset_bytes||packed>asset_bytes-offset)return fail("asset LZ range");
    Reader r={.offset=offset,.remaining=packed};int h[4];for(unsigned i=0;i<4;i++)h[i]=read_byte(&r);
    if(h[0]!=16||h[1]<0||h[2]<0||h[3]<0||(uint32_t)(h[1]|h[2]<<8|h[3]<<16)!=raw)return fail("asset LZ header");
    uint32_t out=0;while(out<raw){int flags=read_byte(&r);if(flags<0)return fail("asset LZ flags");for(unsigned bit=128;bit&&out<raw;bit>>=1){int a=read_byte(&r);if(a<0)return fail("asset LZ literal");if(flags&bit){int b=read_byte(&r);if(b<0)return fail("asset LZ match");unsigned token=(a<<8)|b,n=(token>>12)+3,d=(token&4095)+1;if(d>out||n>raw-out)return fail("asset LZ bounds");while(n--){dst[out]=dst[out-d];out++;}}else dst[out++]=(uint8_t)a;}}return true;
}
static size_t frame_storage_bytes(const PackFrame*f){return f->map_raw+(f->format==2?(size_t)f->height*4u:0u);}
static void cache_frame_spans(unsigned id){
    const PackFrame*f=&frames[id];if(f->format!=2)return;
    uint16_t*bounds=fc[id].map+f->map_raw/2;
    const uint8_t*data=bc[f->bank].data;
    for(unsigned y=0;y<f->height;y++){
        unsigned left=f->width,right=0;
        const uint16_t*map=fc[id].map+(y>>3)*f->cols;
        for(unsigned x=0;x<f->width;x++){
            const uint8_t*tile=data+map[x>>3]*192u;
            /* Source alpha0..3 quantizes to zero; raw4 already contributes. */
            if(tile[128u+(y&7u)*8u+(x&7u)]>=4u){if(left==f->width)left=x;right=x+1;}
        }
        bounds[y*2]=(uint16_t)(right?left:0);bounds[y*2+1]=(uint16_t)right;
        if(!(y&7u))service();
    }
}
static bool acquire_frame(unsigned id){
    if(id>=ah.frames)return fail("frame ID");
    CachedFrame*c=&fc[id];if(c->refs){c->refs++;return true;}
    const PackFrame*f=&frames[id];CachedBank*b=&bc[f->bank];const PackBank*record=&banks[f->bank];
    if(!b->data){b->data=allocate(record->raw);if(!b->data)return false;if(!inflate(record->offset,record->packed,b->data,record->raw)){release(b->data,record->raw);b->data=NULL;return false;}}
    c->map=allocate(frame_storage_bytes(f));if(!c->map){if(!b->refs){release(b->data,record->raw);b->data=NULL;}return false;}
    if(!inflate(f->map_offset,f->map_bytes,(uint8_t*)c->map,f->map_raw))goto bad;
    for(unsigned i=0;i<f->map_raw/2;i++)if(c->map[i]>=record->raw/record->tile_bytes){fail("asset tile index");goto bad;}
    cache_frame_spans(id);
    c->refs=1;b->refs++;return true;
bad:release(c->map,frame_storage_bytes(f));c->map=NULL;if(!b->refs){release(b->data,record->raw);b->data=NULL;}return false;
}
static void release_frame(unsigned id){CachedFrame*c=&fc[id];if(!c->refs||--c->refs)return;const PackFrame*f=&frames[id];CachedBank*b=&bc[f->bank];release(c->map,frame_storage_bytes(f));c->map=NULL;if(!--b->refs){release(b->data,banks[f->bank].raw);b->data=NULL;}}
static unsigned dependency(const Image*i,unsigned at){return i->kind==1?sequences[i->data+at].frame:(unsigned)i->data;}
static unsigned dependencies(const Image*i){return i->kind==1?(unsigned)i->count:1;}
static void release_image(unsigned id){const Image*i=&images[id];for(unsigned j=0;j<dependencies(i);j++)release_frame(dependency(i,j));}
static bool acquire_image(unsigned id){const Image*i=&images[id];if(i->kind==3)return fail("effect requires scene_gfx route");unsigned at=0;for(;at<dependencies(i);at++)if(!acquire_frame(dependency(i,at))){while(at)release_frame(dependency(i,--at));return false;}return true;}
static bool retain_image(unsigned id){
    const Image*i=&images[id];
    for(unsigned at=0;at<dependencies(i);at++)if(!fc[dependency(i,at)].refs)return false;
    for(unsigned at=0;at<dependencies(i);at++)fc[dependency(i,at)].refs++;
    return true;
}
static void release_snapshot(void){for(unsigned i=0;i<old_count;i++)if(old_layers[i].loaded)release_image(old_layers[i].image);old_count=0;batch_valid=moving=false;}
bool story_gfx_restore_pending(void){return restore_backup.pending;}
bool story_gfx_restore_begin(void){
    if(!catalog||replay||restore_backup.pending)return fail("graphics restore transaction unavailable");
    unsigned current=0,old=0;
    for(;current<count;current++)if(layers[current].loaded&&!retain_image(layers[current].image))goto bad;
    for(;old<old_count;old++)if(old_layers[old].loaded&&!retain_image(old_layers[old].image))goto bad;
    restore_backup.pending=true;restore_backup.batch=batch_valid;restore_backup.moving=moving;
    restore_backup.count=count;restore_backup.old_count=old_count;restore_backup.kind=move_kind;
    restore_backup.start=move_start;restore_backup.duration=move_duration;restore_backup.version=state_version;
    memcpy(restore_backup.layers,layers,sizeof(layers));memcpy(restore_backup.old_layers,old_layers,sizeof(old_layers));
    return true;
bad:
    while(old){old--;if(old_layers[old].loaded)release_image(old_layers[old].image);}
    while(current){current--;if(layers[current].loaded)release_image(layers[current].image);}
    return fail("graphics restore lost resident dependency");
}
void story_gfx_restore_commit(void){
    if(!restore_backup.pending)return;
    for(unsigned i=0;i<restore_backup.count;i++)if(restore_backup.layers[i].loaded)release_image(restore_backup.layers[i].image);
    for(unsigned i=0;i<restore_backup.old_count;i++)if(restore_backup.old_layers[i].loaded)release_image(restore_backup.old_layers[i].image);
    memset(&restore_backup,0,sizeof(restore_backup));
}
void story_gfx_restore_rollback(void){
    if(!restore_backup.pending)return;
    release_snapshot();
    for(unsigned i=0;i<count;i++)if(layers[i].loaded)release_image(layers[i].image);
    memcpy(layers,restore_backup.layers,sizeof(layers));memcpy(old_layers,restore_backup.old_layers,sizeof(old_layers));
    count=restore_backup.count;old_count=restore_backup.old_count;move_kind=restore_backup.kind;
    move_start=restore_backup.start;move_duration=restore_backup.duration;state_version=restore_backup.version;
    batch_valid=restore_backup.batch;moving=restore_backup.moving;
    /* The backup's retained refs become the restored lists' owner refs. */
    memset(&restore_backup,0,sizeof(restore_backup));
}
void story_gfx_clear(void){if(!batch_valid||moving)release_snapshot();for(unsigned i=0;i<count;i++)if(layers[i].loaded)release_image(layers[i].image);count=0;state_version++;}
void story_gfx_close(void){
    story_gfx_restore_commit();
    if(images){release_snapshot();story_gfx_clear();}
    if(particle_constants){for(unsigned i=0;i<image_count;i++)free(particle_constants[i]);free(particle_constants);particle_constants=NULL;}
    for(unsigned i=0;i<MAX_FRAMES;i++)free(fc[i].map);
    for(unsigned i=0;i<MAX_BANKS;i++)free(bc[i].data);
    free(index_data);free(catalog);if(assets)fclose(assets);assets=NULL;index_data=catalog=NULL;banks=NULL;frames=NULL;images=NULL;sequences=NULL;programs=NULL;geometries=NULL;
    memset(fc,0,sizeof(fc));memset(bc,0,sizeof(bc));memset(&ah,0,sizeof(ah));resident=0;count=old_count=0;image_count=sequence_count=program_count=0;replay=batch_valid=moving=false;
}
bool story_gfx_open(const char *assets_path,const char *catalog_path){
    story_gfx_close();error_text[0]=0;io_calls=0;state_version=1;FILE*cfile=NULL;uint8_t h[64];
    if(!assets_path||!catalog_path||!(assets=fopen(assets_path,"rb"))){fail("open assets");goto bad;}
    if(fseek(assets,0,SEEK_END))goto bad;
    long n=ftell(assets);if(n<40||(uint64_t)n>UINT32_MAX){fail("assets length");goto bad;}asset_bytes=(uint32_t)n;
    if(!read_at(assets,asset_bytes,0,&ah,40))goto bad;
    if(memcmp(ah.magic,"ZATONDP1",8)||ah.version!=1||!ah.frames||ah.frames>MAX_FRAMES||!ah.banks||ah.banks>MAX_BANKS||ah.frame_size!=196||ah.stages>ah.frames||ah.actors>ah.frames){fail("assets header");goto bad;}
    uint32_t index_size=ah.banks*16u+ah.frames*196u+(ah.stages+ah.actors)*2u;
    if(index_size>asset_bytes-40){fail("assets index size");goto bad;}index_data=allocate(index_size);if(!index_data||!read_at(assets,asset_bytes,40,index_data,index_size))goto bad;
    banks=(const PackBank*)index_data;frames=(const PackFrame*)(index_data+ah.banks*16u);
    for(unsigned i=0;i<ah.banks;i++){const PackBank*b=&banks[i];if(!b->raw||b->raw>262144||(b->tile_bytes!=40&&b->tile_bytes!=128&&b->tile_bytes!=192)||b->raw%b->tile_bytes||b->offset>asset_bytes||b->packed>asset_bytes-b->offset){fail("assets bank shape");goto bad;}}
    for(unsigned i=0;i<ah.frames;i++){const PackFrame*f=&frames[i];if(f->bank>=ah.banks||!f->width||!f->height||f->width>1024||f->height>2048||f->cols!=(f->width+7)/8||f->rows!=(f->height+7)/8||f->map_raw!=(uint32_t)f->cols*f->rows*2||f->map_raw>32768||f->format>2||banks[f->bank].tile_bytes!=(f->format==2?192u:f->format==1?40u:128u)){fail("assets frame shape");goto bad;}}
    cfile=fopen(catalog_path,"rb");if(!cfile){fail("open story catalog");goto bad;}if(fseek(cfile,0,SEEK_END))goto bad;n=ftell(cfile);
    if(n<64||n>262144||!read_at(cfile,(uint32_t)n,0,h,64)){fail("catalog length");goto bad;}
    image_count=r32(h+16);sequence_count=r32(h+20);program_count=r32(h+24);uint32_t fcount=r32(h+28),io=r32(h+32),so=r32(h+36),po=r32(h+40),go=r32(h+44);
    if(memcmp(h,"ZSTGFX01",8)||r32(h+8)!=1||r32(h+12)!=(uint32_t)n||!image_count||image_count>2048||sequence_count>1024||!program_count||program_count>256||fcount!=ah.frames||io!=64||so!=io+image_count*48u||po!=so+sequence_count*16u||go!=po+program_count*48u||go+fcount*32u!=(uint32_t)n||r32(h+48)!=asset_bytes||r32(h+56)||r32(h+60)){fail("catalog layout");goto bad;}
    catalog=allocate((size_t)n);if(!catalog||!read_at(cfile,(uint32_t)n,0,catalog,(uint32_t)n))goto bad;fclose(cfile);cfile=NULL;
    if(crc(catalog+64,(size_t)n-64)!=r32(catalog+52)){fail("catalog CRC");goto bad;}
    images=(const Image*)(catalog+io);sequences=(const Sequence*)(catalog+so);programs=(const Program*)(catalog+po);geometries=(const Geometry*)(catalog+go);
    for(unsigned i=0;i<image_count;i++){const Image*x=&images[i];if(x->kind<0||x->kind>3||x->data<0||x->reserved[0]||x->reserved[1]){fail("catalog image");goto bad;}
        if(x->kind==0&&(x->data>=(int)fcount||x->count!=1)){fail("catalog static");goto bad;}
        if(x->kind==1&&(x->count<=0||x->data>(int)sequence_count||x->count>(int)sequence_count-x->data||x->flags<0||x->flags>1)){fail("catalog sequence range");goto bad;}
        if(x->kind==2&&(x->data>=(int)fcount||x->count<1||x->count>256||x->start<0||x->start>300||x->vx0>x->vx1||x->vy0>x->vy1)){fail("catalog particle");goto bad;}}
    for(unsigned i=0;i<sequence_count;i++)if(sequences[i].frame>=fcount||sequences[i].duration>36000||sequences[i].transition>36000||sequences[i].reserved){fail("catalog sequence frame");goto bad;}
    for(unsigned i=0;i<image_count;i++)if(images[i].kind==1){
        const Image*x=&images[i];
        for(int j=0;j<x->count;j++)if(!sequences[x->data+j].duration&&(x->flags||j+1<x->count)){
            fail("zero sequence duration before final hold");goto bad;
        }
    }
    for(unsigned i=0;i<program_count;i++){const Program*p=&programs[i];if(p->alpha<0||p->alpha>4096||p->blend<0||p->blend>2||p->z0<4096||p->z1<4096||p->z0>8*Q||p->z1>8*Q||p->duration<0||p->duration>36000||(p->flip!=1&&p->flip!=-1)||p->flags<0||p->flags>7||p->ax< -4*Q||p->ax>4*Q||p->ay< -4*Q||p->ay>4*Q||p->px< -4*Q||p->px>4*Q||p->py< -4*Q||p->py>4*Q){fail("catalog program");goto bad;}}
    for(unsigned i=0;i<fcount;i++){const Geometry*g=&geometries[i];if(g->source_width<1||g->source_width>8192||g->fw<1||g->fh<1||g->sh<1||g->reserved){fail("catalog geometry");goto bad;}}
    if(!cache_particles())goto bad;
    return true;
bad:if(cfile)fclose(cfile);if(!error_text[0])fail("graphics file error");story_gfx_close();return false;
}
bool story_gfx_hide(unsigned tag){for(unsigned i=0;i<count;i++)if(layers[i].tag==tag){if(layers[i].loaded)release_image(layers[i].image);memmove(layers+i,layers+i+1,(count-i-1)*sizeof(*layers));count--;state_version++;break;}return catalog!=NULL;}
bool story_gfx_show(unsigned image,unsigned tag,int placement,unsigned program,int behind,uint32_t ticks){
    if(!catalog||image>=image_count||program>=program_count||placement< -1||placement>2||behind< -1||tag>65534)return fail("show arguments");
    if(images[image].kind==3)return fail("effect requires scene_gfx route");
    unsigned insertion=count;int previous=-1;
    for(unsigned i=0;i<count;i++){if(layers[i].tag==tag)previous=(int)i;if(insertion==count&&(layers[i].tag==tag||(behind>=0&&layers[i].tag==(unsigned)behind)))insertion=i;}
    int place=placement>=0?placement:previous>=0?layers[previous].placement:0;if(previous>=0)story_gfx_hide(tag);
    if(count>=MAX_LAYERS)return fail("too many story layers");
    if(!replay&&!acquire_image(image))return false;
    if(insertion>count)insertion=count;
    memmove(layers+insertion+1,layers+insertion,(count-insertion)*sizeof(*layers));layers[insertion]=(Layer){image,tag,program,place,ticks,!replay};count++;state_version++;return true;
}
void story_gfx_set_replay(bool value){replay=value;if(value)release_snapshot();}
bool story_gfx_sync(void){if(!catalog||replay)return fail("sync while closed/replaying");for(unsigned i=0;i<count;i++)if(!layers[i].loaded){if(!acquire_image(layers[i].image))return false;layers[i].loaded=true;}return true;}
void story_gfx_begin_batch(uint32_t ticks){(void)ticks;if(replay||!catalog)return;release_snapshot();batch_valid=true;old_count=count;memcpy(old_layers,layers,count*sizeof(*layers));for(unsigned i=0;i<old_count;i++)if(old_layers[i].loaded&&!acquire_image(old_layers[i].image))old_layers[i].loaded=false;}
bool story_gfx_move_transition(unsigned kind,uint32_t ticks,uint32_t duration){if(!catalog||kind<5||kind>9)return fail("move kind");if(replay)return true;if(!batch_valid)return fail("move without batch snapshot");move_kind=kind;move_start=ticks;move_duration=duration;moving=duration>0;state_version++;if(!moving)release_snapshot();return true;}
bool story_gfx_end_batch(void){if(!moving)release_snapshot();return moving||resident<=NORMAL_LIMIT?true:fail("normal graphics exceeds steady heap");}
bool story_gfx_snapshot_valid(const StoryGfxSnapshot*s){
    if(!catalog||!s||s->count>MAX_LAYERS)return false;
    for(unsigned i=0;i<STORY_SNAPSHOT_LAYERS;i++){
        const StoryGfxLayerSnapshot*l=&s->layers[i];
        if(i>=s->count){if(l->image||l->tag||l->program||l->placement||l->start)return false;continue;}
        if(l->image>=image_count||images[l->image].kind==3||l->program>=program_count||
           l->tag>65534||l->placement<0||l->placement>2)return false;
        for(unsigned j=0;j<i;j++)if(s->layers[j].tag==l->tag)return false;
    }
    /* Check the target's steady resource union, not the currently loaded set.
     * A syntactically valid set of sixteen IDs can otherwise exceed the heap. */
    size_t needed=resident;
    for(unsigned i=0;i<ah.banks;i++)if(bc[i].data)needed-=banks[i].raw;
    for(unsigned i=0;i<ah.frames;i++)if(fc[i].map)needed-=frame_storage_bytes(&frames[i]);
    uint32_t seen_frames[MAX_FRAMES/32]={0},seen_banks[MAX_BANKS/32]={0};
    for(unsigned i=0;i<s->count;i++){
        const Image*image=&images[s->layers[i].image];
        for(unsigned j=0;j<dependencies(image);j++){
            unsigned id=dependency(image,j),bank=frames[id].bank;
            if(!(seen_frames[id/32]&(1u<<(id&31)))){
                seen_frames[id/32]|=1u<<(id&31);needed+=frame_storage_bytes(&frames[id]);
            }
            if(!(seen_banks[bank/32]&(1u<<(bank&31)))){
                seen_banks[bank/32]|=1u<<(bank&31);needed+=banks[bank].raw;
            }
            if(needed>NORMAL_LIMIT)return false;
        }
    }
    if(needed>NORMAL_LIMIT)return false;
    return true;
}
bool story_gfx_snapshot(StoryGfxSnapshot*out){
    if(!out||!catalog||replay||batch_valid||moving||restore_backup.pending)return false;
    StoryGfxSnapshot s={0};s.count=count;
    for(unsigned i=0;i<count;i++){
        const Layer*l=&layers[i];if(!l->loaded)return false;
        s.layers[i]=(StoryGfxLayerSnapshot){l->image,l->tag,l->program,l->placement,l->start};
    }
    if(!story_gfx_snapshot_valid(&s))return false;
    *out=s;return true;
}
bool story_gfx_restore(const StoryGfxSnapshot*s){
    if(!story_gfx_snapshot_valid(s)||replay||(!restore_backup.pending&&(batch_valid||moving)))return false;
    unsigned acquired=0;
    /* Keep the old scene until destination dependencies are ready, bounded by
     * the existing transition budget for this temporary resource union. */
    bool was_batch=batch_valid;batch_valid=true;
    for(;acquired<s->count;acquired++)if(!acquire_image(s->layers[acquired].image))break;
    batch_valid=was_batch;
    if(acquired!=s->count){while(acquired)release_image(s->layers[--acquired].image);return false;}
    release_snapshot();
    for(unsigned i=0;i<count;i++)if(layers[i].loaded)release_image(layers[i].image);
    memset(layers,0,sizeof(layers));count=s->count;
    for(unsigned i=0;i<count;i++){
        const StoryGfxLayerSnapshot*l=&s->layers[i];
        layers[i]=(Layer){l->image,l->tag,l->program,l->placement,l->start,true};
    }
    move_kind=move_start=move_duration=0;state_version++;return true;
}
const char *story_gfx_error(void){return error_text;}
size_t story_gfx_resident_bytes(void){return resident;}
uint32_t story_gfx_read_calls(void){return io_calls;}
unsigned story_gfx_layer_count(void){return count;}
/* Render and presentation keys share exactly the same quantized parameters. */
static const uint32_t ease_lut[257]={0,402,804,1206,1608,2010,2412,2814,3216,3617,4019,4420,4821,5222,5623,6023,6424,6824,7224,7623,8022,8421,8820,9218,9616,10014,10411,10808,11204,11600,11996,12391,12785,13180,13573,13966,14359,14751,15143,15534,15924,16314,16703,17091,17479,17867,18253,18639,19024,19409,19792,20175,20557,20939,21320,21699,22078,22457,22834,23210,23586,23961,24335,24708,25080,25451,25821,26190,26558,26925,27291,27656,28020,28383,28745,29106,29466,29824,30182,30538,30893,31248,31600,31952,32303,32652,33000,33347,33692,34037,34380,34721,35062,35401,35738,36075,36410,36744,37076,37407,37736,38064,38391,38716,39040,39362,39683,40002,40320,40636,40951,41264,41576,41886,42194,42501,42806,43110,43412,43713,44011,44308,44604,44898,45190,45480,45769,46056,46341,46624,46906,47186,47464,47741,48015,48288,48559,48828,49095,49361,49624,49886,50146,50404,50660,50914,51166,51417,51665,51911,52156,52398,52639,52878,53114,53349,53581,53812,54040,54267,54491,54714,54934,55152,55368,55582,55794,56004,56212,56418,56621,56823,57022,57219,57414,57607,57798,57986,58172,58356,58538,58718,58896,59071,59244,59415,59583,59750,59914,60075,60235,60392,60547,60700,60851,60999,61145,61288,61429,61568,61705,61839,61971,62101,62228,62353,62476,62596,62714,62830,62943,63054,63162,63268,63372,63473,63572,63668,63763,63854,63944,64031,64115,64197,64277,64354,64429,64501,64571,64639,64704,64766,64827,64884,64940,64993,65043,65091,65137,65180,65220,65259,65294,65328,65358,65387,65413,65436,65457,65476,65492,65505,65516,65525,65531,65535,65536};
static uint32_t ease(uint32_t t){
    if(t>=65536)return 65536;
    unsigned at=t>>8,f=t&255;
    return (ease_lut[at]*(256-f)+ease_lut[at+1]*f+128)>>8;
}
static int floor_div(int64_t v,int d){return v>=0?(int)(v/d):-(int)((-v+d-1)/d);}
static Sample sample(const Layer*l,uint32_t ticks){
    const Image*i=&images[l->image];if(i->kind!=1)return (Sample){(unsigned)i->data,NONE,4096};
    uint32_t elapsed=ticks-l->start,period=0;
    for(int j=0;j<i->count;j++)period+=sequences[i->data+j].duration;
    bool repeated=i->flags&&period&&elapsed>=period;
    uint32_t phase=i->flags&&period?elapsed%period:elapsed,begin=0;unsigned at=0;
    for(;at+1<(unsigned)i->count;at++){uint32_t d=sequences[i->data+at].duration;if(!d||phase<begin+d)break;begin+=d;}
    const Sequence*s=&sequences[i->data+at];
    unsigned old=at?sequences[i->data+at-1].frame:repeated?sequences[i->data+i->count-1].frame:NONE;
    uint32_t local=phase-begin;unsigned mix=4096;
    if(old!=NONE&&s->transition&&local<s->transition)mix=(local*16u/s->transition)*256u;else old=NONE;
    return (Sample){s->frame,old,mix};
}
static Transform transform(const Layer*l,unsigned id,uint32_t ticks){
    const PackFrame*f=&frames[id];const Geometry*g=&geometries[id];const Program*p=&programs[l->program];
    uint32_t elapsed=ticks-l->start;int32_t z=p->z0;
    if(p->duration){uint32_t t=elapsed>=(unsigned)p->duration?65536:(uint32_t)((uint64_t)elapsed*65536u/p->duration);z+=(int32_t)((int64_t)(p->z1-p->z0)*ease(t)/65536);}
    z=(z+32)&~63;
    int x=f->x;
    if(l->placement==1)x=floor_div(1280ll+g->source_width-10ll*f->width,20);
    else if(l->placement==2)x=floor_div(3840ll-g->source_width-10ll*f->width,20);
    if(f->category==2&&(l->placement==1||l->placement==2)&&!(p->flags&1)){
        /* Spread only the already rounded ordinary actor placement. Use the
         * packed frame's own center as pivot, including odd-width canvases. */
        x=f->x+floor_div(((int64_t)x-f->x)*3,2);
        int minimum=floor_div(97ll-f->width,2),maximum=floor_div(416ll-f->width,2);
        if(x<minimum)x=minimum;
        if(x>maximum)x=maximum;
    }
    int64_t ox=(int64_t)x*Q+(int64_t)f->width*(Q-z)/2;
    int64_t oy=(int64_t)f->y*Q+(int64_t)f->height*(Q-z)/2;
    if(p->flags&1){
        ox=(int64_t)p->px*256+((int64_t)g->cx-(int64_t)p->ax*g->fw/Q)*z/Q;
        oy=g->sy+(int64_t)p->py*g->sh/Q+((int64_t)g->cy-(int64_t)p->ay*g->fh/Q)*z/Q;
    }
    if(p->flags&2)oy=((int64_t)192*Q-(int64_t)f->height*z)*p->yalign/Q;
    if(p->flags&4){
        static const int8_t xs[5]={-1,1,1,-1,0},ys[5]={1,-1,-1,1,0};
        unsigned at=elapsed%15/3,part=elapsed%3;
        int px=at?xs[at-1]:0,py=at?ys[at-1]:0;
        ox+=(px*(3-part)+xs[at]*(int)part)*13107/3;
        oy+=(py*(3-part)+ys[at]*(int)part)*13107/3;
    }
    return (Transform){(int32_t)ox,(int32_t)oy,z};
}
#if defined(__arm__) && defined(__GNUC__)
#define PIXEL_INLINE static inline __attribute__((always_inline,target("arm")))
#else
#define PIXEL_INLINE static inline
#endif
PIXEL_INLINE uint16_t pixel(unsigned id,int x,int y){
    const PackFrame*f=&frames[id];if(x<0||y<0||x>=f->width||y>=f->height)return 0;
    const PackBank*b=&banks[f->bank];
    unsigned index=fc[id].map[(y>>3)*f->cols+(x>>3)],pos=((y&7)<<3)|(x&7);
    const uint8_t*t=bc[f->bank].data+index*b->tile_bytes;
    if(!f->format)return ((const uint16_t*)t)[pos];
    if(!(t[32+(pos>>3)]&(1u<<(pos&7))))return 0;
    return f->palette[(t[pos>>1]>>((pos&1)*4))&15]|0x8000;
}
/* Soft masks keep straight RGB15 and their source alpha in the same tile.
 * Return Q5 opacity beside the word, with no expanded bitmap allocation. */
PIXEL_INLINE uint32_t soft_pixel(unsigned id,int x,int y){
    const PackFrame*f=&frames[id];
    if(f->format!=2){uint16_t c=pixel(id,x,y);return c|((uint32_t)(c>>15)*32u<<16);}
    if(x<0||y<0||x>=f->width||y>=f->height)return 0;
    unsigned index=fc[id].map[(y>>3)*f->cols+(x>>3)],pos=((y&7)<<3)|(x&7);
    const uint8_t*t=bc[f->bank].data+index*192u;
    return ((const uint16_t*)t)[pos]|(((unsigned)t[128+pos]+4u)>>3<<16);
}
PIXEL_INLINE uint16_t over_soft(uint16_t dest,uint16_t color,unsigned a){
    if(!a)return dest;
    if(a==32)return color;
    unsigned rb=((color&0x7c1fu)*a+(dest&0x7c1fu)*(32-a)+0x4010u)>>5;
    unsigned green=((color&0x03e0u)*a+(dest&0x03e0u)*(32-a)+0x0200u)>>5;
    return (uint16_t)(0x8000|(rb&0x7c1f)|(green&0x03e0));
}
PIXEL_INLINE uint16_t blend_soft(uint16_t dest,uint32_t color,uint32_t old,unsigned mix,const Program*p){
    unsigned ca=((color>>16)*(unsigned)p->alpha+16)>>5;
    unsigned oa=((old>>16)*(unsigned)p->alpha+16)>>5;
    unsigned alpha=(ca*mix+oa*(4096-mix)+2048)>>12;
    if(!alpha)return dest;
    unsigned result=0x8000;
    for(unsigned shift=0;shift<15;shift+=5){
        unsigned c=(color>>shift)&31,o=(old>>shift)&31,d=(dest>>shift)&31;
        unsigned prem=(ca*c*mix+oa*o*(4096-mix)+2048)>>12,out;
        if(p->blend==1){out=d+((prem+2048)>>12);if(out>31)out=31;}
        else if(p->blend==2){unsigned factor=(31u*(4096-alpha)+prem+15)/31;out=(d*factor+2048)>>12;}
        else out=(d*(4096-alpha)+prem+2048)>>12;
        result|=out<<shift;
    }
    return (uint16_t)result;
}
PIXEL_INLINE uint16_t blend(uint16_t dest,uint16_t color,uint16_t old,unsigned mix,const Program*p){
    if(p->blend==0&&p->alpha==4096){
        if(mix==4096||mix==0){uint16_t value=mix?color:old;return value&0x8000?value:dest;}
        if(!(color&0x8000))color=dest;
        if(!(old&0x8000))old=dest;
        /* RGB15's red/blue fields have enough gap to interpolate together.
         * Two integer multiplies replace six per-channel multiplies/divides. */
        int k=(int)(mix>>8),rb0=old&0x7c1f,g0=old&0x03e0;
        int rb=(rb0<<4)+((int)(color&0x7c1f)-rb0)*k+0x2008;
        int green=(g0<<4)+((int)(color&0x03e0)-g0)*k+0x100;
        return (uint16_t)(0x8000|((rb>>4)&0x7c1f)|((green>>4)&0x03e0));
    }
    unsigned ca=color>>15,oa=old>>15,coverage=ca*mix+oa*(4096-mix);
    if(!coverage||!p->alpha)return dest;
    unsigned alpha=(coverage*(unsigned)p->alpha+2048)>>12,result=0x8000;
    for(unsigned shift=0;shift<15;shift+=5){
        unsigned c=(color>>shift)&31,o=(old>>shift)&31,d=(dest>>shift)&31;
        unsigned prem=(ca*c*mix+oa*o*(4096-mix))*(unsigned)p->alpha/4096,out;
        if(p->blend==1){out=d+((prem+2048)>>12);if(out>31)out=31;}
        else if(p->blend==2){unsigned factor=(31u*(4096-alpha)+prem+15)/31;out=(d*factor+2048)>>12;}
        else out=(d*(4096-alpha)+prem+2048)>>12;
        result|=out<<shift;
    }
    return (uint16_t)result;
}
#if defined(__arm__) && defined(__GNUC__)
#define DRAW_FAST __attribute__((section(".itcm"),target("arm"),optimize("O3"),long_call))
#else
#define DRAW_FAST
#endif
static void cache_multiply(const uint16_t*palette,unsigned alpha){
    if(multiply_kind==1&&multiply_alpha==alpha&&!memcmp(multiply_palette,palette,sizeof(multiply_palette)))return;
    for(unsigned index=0;index<16;index++){
        for(unsigned channel=0;channel<3;channel++){
            unsigned color=(palette[index]>>(channel*5))&31u;
            unsigned factor=(31u*(4096u-alpha)+color*alpha+15u)/31u;
            for(unsigned dest=0;dest<32;dest++)multiply_lut.component[index][channel][dest]=(uint8_t)((dest*factor+2048u)>>12);
        }
        if(!(index&3u))service();
    }
    memcpy(multiply_palette,palette,sizeof(multiply_palette));multiply_alpha=alpha;multiply_kind=1;
}
PIXEL_INLINE uint16_t multiply_index(uint16_t dest,unsigned index){
    const uint8_t(*table)[32]=multiply_lut.component[index];
    return (uint16_t)(0x8000u|table[0][dest&31u]|((unsigned)table[1][(dest>>5)&31u]<<5)|((unsigned)table[2][(dest>>10)&31u]<<10));
}
/* Same two rounding stages as blend(), but palette/global-alpha work is done
 * once per key and the map is resolved once per8-pixel source tile row. */
DRAW_FAST static void draw_indexed_multiply(unsigned id,int ox,int oy,unsigned alpha,uint16_t*screen,int clip_top,int clip_bottom){
    if(!alpha)return;
    const PackFrame*f=&frames[id];
    int x0=ox<0?0:ox,x1=ox+f->width,y0=oy<clip_top?clip_top:oy,y1=oy+f->height;
    if(x1>256)x1=256;
    if(y1>clip_bottom)y1=clip_bottom;
    if(x0>=x1||y0>=y1)return;
    cache_multiply(f->palette,alpha);
    const uint8_t*data=bc[f->bank].data;
    for(int y=y0;y<y1;y++){
        unsigned sy=(unsigned)(y-oy),row=sy&7u;
        const uint16_t*map=fc[id].map+(sy>>3)*f->cols;
        uint16_t*dest=screen+y*256+x0;
        for(int x=x0;x<x1;){
            unsigned sx=(unsigned)(x-ox),at=sx&7u,n=8u-at;
            if(n>(unsigned)(x1-x))n=(unsigned)(x1-x);
            const uint8_t*tile=data+map[sx>>3]*40u,*src=tile+row*4u;
            unsigned mask=tile[32u+row];
            if(mask==255u&&n==8u){
                dest[0]=multiply_index(dest[0],src[0]&15u);dest[1]=multiply_index(dest[1],src[0]>>4);
                dest[2]=multiply_index(dest[2],src[1]&15u);dest[3]=multiply_index(dest[3],src[1]>>4);
                dest[4]=multiply_index(dest[4],src[2]&15u);dest[5]=multiply_index(dest[5],src[2]>>4);
                dest[6]=multiply_index(dest[6],src[3]&15u);dest[7]=multiply_index(dest[7],src[3]>>4);
            }else if(mask)for(unsigned k=0;k<n;k++){
                unsigned pos=at+k;
                if(mask&(1u<<pos))dest[k]=multiply_index(dest[k],(src[pos>>1]>>((pos&1u)*4u))&15u);
            }
            x+=(int)n;dest+=n;
        }
        if(!(y&7))service();
    }
}
/* Most story pictures and every particle have an integer translation and no
 * scale/blend. Resolve one map entry per tile row instead of per output pixel.
 * Keep the transparent mask: an opaque black image must still cover old tags. */
DRAW_FAST static void draw_plain(unsigned id,int ox,int oy,uint16_t*screen,int clip_top,int clip_bottom){
    const PackFrame*f=&frames[id];const PackBank*b=&banks[f->bank];
    int x0=ox<0?0:ox,x1=ox+f->width,y0=oy<clip_top?clip_top:oy,y1=oy+f->height;
    if(x1>256)x1=256;
    if(y1>clip_bottom)y1=clip_bottom;
    if(x0>=x1||y0>=y1)return;
    const uint8_t*data=bc[f->bank].data;
    const uint16_t*bounds=f->format==2?fc[id].map+f->map_raw/2:NULL;
    for(int y=y0;y<y1;y++){
        unsigned sy=(unsigned)(y-oy),row=sy&7;
        const uint16_t*map=fc[id].map+(sy>>3)*f->cols;
        int left=x0,right=x1;
        if(bounds){int from=ox+bounds[sy*2],to=ox+bounds[sy*2+1];if(from>left)left=from;if(to<right)right=to;}
        if(left>=right)left=right=x0;
        uint16_t*dest=screen+y*256+left;
        for(int x=left;x<right;){
            unsigned sx=(unsigned)(x-ox),at=sx&7,n=8-at;
            if(n>(unsigned)(right-x))n=(unsigned)(right-x);
            const uint8_t*tile=data+map[sx>>3]*b->tile_bytes;
            if(f->format==2){
                const uint16_t*src=(const uint16_t*)tile+row*8+at;
                const uint8_t*alpha=tile+128+row*8+at;
                for(unsigned k=0;k<n;k++)dest[k]=over_soft(dest[k],src[k],((unsigned)alpha[k]+4)>>3);
            }else if(f->format==1){
                unsigned mask=tile[32+row];
                const uint8_t*src=tile+row*4;
                if(mask==255&&n==8){
                    const uint16_t*pal=f->palette;
                    dest[0]=pal[src[0]&15]|0x8000;dest[1]=pal[src[0]>>4]|0x8000;
                    dest[2]=pal[src[1]&15]|0x8000;dest[3]=pal[src[1]>>4]|0x8000;
                    dest[4]=pal[src[2]&15]|0x8000;dest[5]=pal[src[2]>>4]|0x8000;
                    dest[6]=pal[src[3]&15]|0x8000;dest[7]=pal[src[3]>>4]|0x8000;
                }else if(mask)for(unsigned k=0;k<n;k++){
                    unsigned pos=at+k;
                    if(mask&(1u<<pos))dest[k]=f->palette[(src[pos>>1]>>((pos&1)*4))&15]|0x8000;
                }
            }else{
                const uint16_t*src=(const uint16_t*)tile+row*8+at;
                if(n==8&&!((uintptr_t)dest&3)){
                    const GfxWord*words=(const GfxWord*)src;
                    uint32_t a=words[0],c=words[1],d=words[2],e=words[3];
                    if((a&c&d&e&0x80008000u)==0x80008000u){
                        GfxWord*out=(GfxWord*)dest;out[0]=a;out[1]=c;out[2]=d;out[3]=e;
                    }else for(unsigned k=0;k<n;k++)if(src[k]&0x8000)dest[k]=src[k];
                }else for(unsigned k=0;k<n;k++)if(src[k]&0x8000)dest[k]=src[k];
            }
            x+=(int)n;dest+=n;
        }
        if(!(y&15))service();
    }
}
PIXEL_INLINE uint16_t crossfade_q9(uint16_t dest,uint16_t color,uint16_t old,unsigned ca,unsigned oa){
    unsigned da=512u-ca-oa;
    /* Each R/B lane is16bits wide. The weighted channel plus round is at
     * most31*512+256=16128, so neither lane can carry into its neighbour. */
    uint32_t rb=((dest&31u)|((dest&0x7c00u)<<6))*da+
                ((color&31u)|((color&0x7c00u)<<6))*ca+
                ((old&31u)|((old&0x7c00u)<<6))*oa+0x01000100u;
    uint32_t green=(dest&0x03e0u)*da+(color&0x03e0u)*ca+(old&0x03e0u)*oa+0x2000u;
    return (uint16_t)(0x8000u|((rb>>9)&31u)|((rb>>15)&0x7c00u)|((green>>9)&0x03e0u));
}
/* Q5 source alpha and integer Q4 sequence mix reduce the exact full-global
 * Q12 blend to nearest/512. Both source maps are resolved once per tile row. */
DRAW_FAST static void draw_soft_crossfade(unsigned current,unsigned old,int ox,int oy,unsigned mix,uint16_t*screen,int clip_top,int clip_bottom){
    const PackFrame*f=&frames[current],*of=&frames[old];
    int x0=ox<0?0:ox,x1=ox+f->width,y0=oy<clip_top?clip_top:oy,y1=oy+f->height;
    if(x1>256)x1=256;
    if(y1>clip_bottom)y1=clip_bottom;
    if(x0>=x1||y0>=y1)return;
    const uint8_t*data=bc[f->bank].data,*old_data=bc[of->bank].data;
    const uint16_t*bounds=fc[current].map+f->map_raw/2,*old_bounds=fc[old].map+of->map_raw/2;
    unsigned weight=mix>>8,old_weight=16u-weight;
    for(int y=y0;y<y1;y++){
        unsigned sy=(unsigned)(y-oy),row=sy&7u;
        const uint16_t*map=fc[current].map+(sy>>3)*f->cols,*old_map=fc[old].map+(sy>>3)*of->cols;
        unsigned sl=bounds[sy*2],sr=bounds[sy*2+1],ol=old_bounds[sy*2],orr=old_bounds[sy*2+1];
        if(!weight){sl=ol;sr=orr;}
        else if(old_weight&&ol<orr){if(sl>=sr){sl=ol;sr=orr;}else{if(ol<sl)sl=ol;if(orr>sr)sr=orr;}}
        int left=x0,right=x1,from=ox+(int)sl,to=ox+(int)sr;
        if(from>left)left=from;
        if(to<right)right=to;
        if(left>=right)left=right=x0;
        uint16_t*dest=screen+y*256+left;
        for(int x=left;x<right;){
            unsigned sx=(unsigned)(x-ox),at=sx&7u,n=8u-at;
            if(n>(unsigned)(right-x))n=(unsigned)(right-x);
            const uint8_t*tile=data+map[sx>>3]*192u,*old_tile=old_data+old_map[sx>>3]*192u;
            const uint8_t*alpha=tile+128u+row*8u+at,*old_alpha=old_tile+128u+row*8u+at;
            const uint16_t*src=(const uint16_t*)tile+row*8u+at,*old_src=(const uint16_t*)old_tile+row*8u+at;
            for(unsigned k=0;k<n;k++){
                unsigned ca=(((unsigned)alpha[k]+4u)>>3)*weight,oa=(((unsigned)old_alpha[k]+4u)>>3)*old_weight;
                if(!(ca|oa))continue;
                /* Read straight RGB only after the zero-coverage test. */
                uint16_t color=ca?src[k]:0,old_color=oa?old_src[k]:0;
                dest[k]=crossfade_q9(dest[k],color,old_color,ca,oa);
            }
            x+=(int)n;dest+=n;
        }
        if(!(y&7))service();
    }
}
DRAW_FAST static void draw_layer(const Layer*l,Sample s,Transform t,uint16_t*screen,int clip_top,int clip_bottom){
    const PackFrame*f=&frames[s.frame];const Program*p=&programs[l->program];
    bool soft=f->format==2||(s.old!=NONE&&frames[s.old].format==2);
    if(f->format==1&&t.z==Q&&!(t.x&(Q-1))&&!(t.y&(Q-1))&&p->flip==1&&p->blend==2&&s.old==NONE&&s.mix==4096){
        draw_indexed_multiply(s.frame,t.x/Q,t.y/Q,(unsigned)p->alpha,screen,clip_top,clip_bottom);return;
    }
    if(f->format==2&&s.old!=NONE&&frames[s.old].format==2&&frames[s.old].width==f->width&&frames[s.old].height==f->height&&
       t.z==Q&&!(t.x&(Q-1))&&!(t.y&(Q-1))&&p->flip==1&&p->blend==0&&p->alpha==4096&&s.mix<=4096&&!(s.mix&255u)){
        draw_soft_crossfade(s.frame,s.old,t.x/Q,t.y/Q,s.mix,screen,clip_top,clip_bottom);return;
    }
    if(t.z==Q&&!(t.x&(Q-1))&&!(t.y&(Q-1))&&p->flip==1&&p->blend==0&&p->alpha==4096&&s.old==NONE){
        draw_plain(s.frame,t.x/Q,t.y/Q,screen,clip_top,clip_bottom);return;
    }
    int x0=floor_div(t.x,Q),y0=floor_div(t.y,Q);
    int x1=floor_div((int64_t)t.x+(int64_t)f->width*t.z+Q-1,Q);
    int y1=floor_div((int64_t)t.y+(int64_t)f->height*t.z+Q-1,Q);
    if(x0<0)x0=0;
    if(x1>256)x1=256;
    if(y0<clip_top)y0=clip_top;
    if(y1>clip_bottom)y1=clip_bottom;
    if(x0>=x1||y0>=y1)return;
    /* Exact rational stepping avoids inverse-scale drift at texel boundaries
     * (e.g. .75 zoom must sample source x118, never x117.999 from truncated 1/z).
     * Only the two initial coordinates divide; inner pixels use add/carry. */
    int64_t nx=(int64_t)x0*Q+Q/2-t.x,ny=(int64_t)y0*Q+Q/2-t.y;
    int sx0=floor_div(nx,t.z),yy=floor_div(ny,t.z);
    int xr0=(int)(nx-(int64_t)sx0*t.z),yr=(int)(ny-(int64_t)yy*t.z);
    int step=Q/t.z,remainder=Q%t.z;
    for(int y=y0;y<y1;y++){
        int sx=sx0,xr=xr0;uint16_t*dest=screen+y*256+x0;
        for(int x=x0;x<x1;x++,dest++){
            int xx=p->flip<0?f->width-1-sx:sx;
            if(soft){
                uint32_t c=soft_pixel(s.frame,xx,yy),o=s.old!=NONE?soft_pixel(s.old,xx,yy):0;
                *dest=blend_soft(*dest,c,o,s.mix,p);
            }else{
                uint16_t c=pixel(s.frame,xx,yy),o=s.old!=NONE?pixel(s.old,xx,yy):0;
                *dest=blend(*dest,c,o,s.mix,p);
            }
            sx+=step;xr+=remainder;if(xr>=t.z){xr-=t.z;sx++;}
        }
        yy+=step;yr+=remainder;if(yr>=t.z){yr-=t.z;yy++;}
        if(!(y&7))service();
    }
}
static uint32_t hash32(uint32_t v){v^=v>>16;v*=0x7feb352du;v^=v>>15;v*=0x846ca68bu;return v^(v>>16);}
DRAW_FAST static bool particle_position(const Image*i,unsigned at,uint32_t elapsed,int*x,int*y){
    const ParticleConstant*c=&particle_constants[i-images][at];
    uint32_t delay=c->delay&0x7fffffffu;
    if(elapsed<delay)return false;
    uint32_t age=(elapsed-delay)%c->travel;
    /* |vy|*age is bounded by the 164px trajectory *60*Q (<2^30).
     * All seven source particle programs also fit vx*age in signed32;
     * arbitrary legal catalogs retain the exact signed64 fallback. */
    int64_t dx=c->delay&0x80000000u?(int64_t)c->vx*age/60:(int32_t)(c->vx*(int32_t)age)/60;
    int32_t dy=c->vy*(int32_t)age/60;
    int64_t xpos=c->xbase+dx;
    int32_t ypos=(c->vy>0?-10*Q:154*Q)+dy;
    int64_t px=xpos>>16;
    /* Offscreen positions stay offscreen, while the later Q16 translation
     * remains representable even for malicious catalog velocities. */
    int left=-(int)frames[i->data].width;
    *x=px<left?left-1:px>256?257:(int)px;*y=24+(ypos>>16);return true;
}
typedef struct {Layer layer;Sample sample;Transform transform;} RenderLayer;
static RenderLayer render_layers[MAX_LAYERS*2];
static unsigned render_count;
#if defined(__arm__) && defined(__GNUC__)
__attribute__((target("arm"),optimize("O3"),noinline))
#endif
static void cache_index_pair(const uint16_t*background,const uint16_t*top,unsigned alpha){
    if(multiply_kind==2&&multiply_alpha==alpha&&!memcmp(multiply_palette,top,sizeof(multiply_palette))&&
       !memcmp(multiply_background,background,sizeof(multiply_background)))return;
    uint16_t factors[16][3];
    for(unsigned fg=0;fg<16;fg++){
        for(unsigned channel=0;channel<3;channel++){
            unsigned c=(top[fg]>>(channel*5))&31u;
            factors[fg][channel]=(uint16_t)((31u*(4096u-alpha)+c*alpha+15u)/31u);
        }
    }
    for(unsigned bg=0;bg<17;bg++){
        unsigned color=bg<16?background[bg]:0,red=color&31u,green=(color>>5)&31u,blue=(color>>10)&31u;
        for(unsigned fg=0;fg<16;fg++){
            unsigned r=(red*factors[fg][0]+2048u)>>12,g=(green*factors[fg][1]+2048u)>>12,b=(blue*factors[fg][2]+2048u)>>12;
            multiply_lut.pair[bg][fg]=(uint16_t)(0x8000u|r|(g<<5)|(b<<10));
        }
        if(!(bg&3u))service();
    }
    memcpy(multiply_palette,top,sizeof(multiply_palette));memcpy(multiply_background,background,sizeof(multiply_background));
    multiply_alpha=alpha;multiply_kind=2;
}
/* This pair is the entire stack immediately after clear_screen. A transparent
 * background therefore means black, represented by palette sentinel16. */
DRAW_FAST static bool draw_index_pair(uint16_t*screen){
    if(moving||render_count!=2)return false;
    const RenderLayer*a=&render_layers[0],*b=&render_layers[1];
    if(!a->layer.loaded||!b->layer.loaded||images[a->layer.image].kind==2||images[b->layer.image].kind==2||
       a->sample.old!=NONE||b->sample.old!=NONE||a->sample.mix!=4096||b->sample.mix!=4096)return false;
    const PackFrame*bg=&frames[a->sample.frame],*fg=&frames[b->sample.frame];
    const Program*bp=&programs[a->layer.program],*fp=&programs[b->layer.program];
    if(bg->format!=1||fg->format!=1||bg->width!=fg->width||bg->height!=fg->height||
       bp->blend!=0||bp->alpha!=4096||bp->flip!=1||fp->blend!=2||fp->flip!=1||
       a->transform.z!=Q||b->transform.z!=Q||a->transform.x!=b->transform.x||a->transform.y!=b->transform.y||
       (a->transform.x&(Q-1))||(a->transform.y&(Q-1)))return false;
    int ox=a->transform.x/Q,oy=a->transform.y/Q,x0=ox<0?0:ox,x1=ox+bg->width,y0=oy<0?0:oy,y1=oy+bg->height;
    if(x1>256)x1=256;
    if(y1>192)y1=192;
    if(x0>=x1||y0>=y1)return true;
    cache_index_pair(bg->palette,fg->palette,(unsigned)fp->alpha);
    const uint8_t*bd=bc[bg->bank].data,*fd=bc[fg->bank].data;
    for(int y=y0;y<y1;y++){
        unsigned sy=(unsigned)(y-oy),row=sy&7u;
        const uint16_t*bm=fc[a->sample.frame].map+(sy>>3)*bg->cols,*fm=fc[b->sample.frame].map+(sy>>3)*fg->cols;
        uint16_t*dest=screen+y*256+x0;
        for(int x=x0;x<x1;){
            unsigned sx=(unsigned)(x-ox),at=sx&7u,n=8u-at;
            if(n>(unsigned)(x1-x))n=(unsigned)(x1-x);
            const uint8_t*bt=bd+bm[sx>>3]*40u,*ft=fd+fm[sx>>3]*40u,*bs=bt+row*4u,*fs=ft+row*4u;
            unsigned bmask=bt[32u+row],fmask=ft[32u+row];
            if(bmask==255u&&fmask==255u&&!at&&n==8u){
                unsigned b0=bs[0],f0=fs[0],b1=bs[1],f1=fs[1],b2=bs[2],f2=fs[2],b3=bs[3],f3=fs[3];
                dest[0]=multiply_lut.pair[b0&15u][f0&15u];dest[1]=multiply_lut.pair[b0>>4][f0>>4];
                dest[2]=multiply_lut.pair[b1&15u][f1&15u];dest[3]=multiply_lut.pair[b1>>4][f1>>4];
                dest[4]=multiply_lut.pair[b2&15u][f2&15u];dest[5]=multiply_lut.pair[b2>>4][f2>>4];
                dest[6]=multiply_lut.pair[b3&15u][f3&15u];dest[7]=multiply_lut.pair[b3>>4][f3>>4];
            }else for(unsigned k=0;k<n;k++){
                unsigned pos=at+k,bi=(bs[pos>>1]>>((pos&1u)*4u))&15u,fi=(fs[pos>>1]>>((pos&1u)*4u))&15u;
                unsigned background=bmask&(1u<<pos)?bi:16u;
                dest[k]=fmask&(1u<<pos)?multiply_lut.pair[background][fi]:(background<16?(uint16_t)(bg->palette[background]|0x8000u):0x8000u);
            }
            x+=(int)n;dest+=n;
        }
        if(!(y&7))service();
    }
    return true;
}
static int find_tag(const Layer*items,unsigned n,unsigned tag){for(unsigned i=0;i<n;i++)if(items[i].tag==tag)return (int)i;return -1;}
static bool visited(unsigned tag,const unsigned*tags,unsigned n){for(unsigned i=0;i<n;i++)if(tags[i]==tag)return true;return false;}
static void append_render(const Layer*l,uint32_t ticks,Transform *override){
    if(render_count>=MAX_LAYERS*2)return;
    Sample s=sample(l,ticks);Transform t=override?*override:transform(l,s.frame,ticks);
    render_layers[render_count++]=(RenderLayer){*l,s,t};
}
static int32_t lerp(int32_t a,int32_t b,uint32_t t){return a+(int32_t)((int64_t)(b-a)*t/Q);}
static void prepare_layers(uint32_t ticks){
    if(moving&&ticks-move_start>=move_duration){release_snapshot();state_version++;}
    render_count=0;
    if(!moving){for(unsigned i=0;i<count;i++)append_render(&layers[i],ticks,NULL);return;}
    uint32_t t=(uint32_t)((uint64_t)(ticks-move_start)*Q/move_duration);
    uint32_t sin=ease(t),warp=(uint32_t)(((uint64_t)sin*sin+Q/2)/Q);
    unsigned oi=0,ni=0,nvisited=0,tags[MAX_LAYERS*2];
    while(oi<old_count||ni<count){
        if(oi<old_count){
            const Layer*old=&old_layers[oi];
            if(visited(old->tag,tags,nvisited)){oi++;continue;}
            if(find_tag(layers,count,old->tag)<0){
                if(move_kind==6||move_kind==8){
                    Sample s=sample(old,ticks);Transform from=transform(old,s.frame,ticks),to=from;
                    to.x=move_kind==6?-(int32_t)((int64_t)frames[s.frame].width*from.z):256*Q;
                    from.x=lerp(from.x,to.x,Q-ease(Q-t));
                    append_render(old,ticks,&from);
                }
                tags[nvisited++]=old->tag;oi++;continue;
            }
        }
        if(ni>=count){oi++;continue;}
        const Layer*next=&layers[ni++];int previous=find_tag(old_layers,old_count,next->tag);
        if(previous>=0){
            Sample s=sample(next,ticks),os=sample(&old_layers[previous],ticks);
            Transform dest=transform(next,s.frame,ticks),from=transform(&old_layers[previous],os.frame,ticks);
            dest.x=lerp(from.x,dest.x,warp);dest.y=lerp(from.y,dest.y,warp);
            append_render(next,ticks,&dest);
        }else if(move_kind==7||move_kind==9){
            Sample s=sample(next,ticks);Transform dest=transform(next,s.frame,ticks),from=dest;
            from.x=move_kind==9?-(int32_t)((int64_t)frames[s.frame].width*dest.z):256*Q;
            dest.x=lerp(from.x,dest.x,sin);append_render(next,ticks,&dest);
        }
        tags[nvisited++]=next->tag;
    }
}
static uint32_t hash_value(uint32_t hash,uint32_t value){return (hash^value)*16777619u;}
uint32_t story_gfx_version(uint32_t ticks){
    if(!catalog)return 0;
    prepare_layers(ticks);
    uint32_t hash=hash_value(2166136261u,state_version);
    for(unsigned j=0;j<render_count;j++){
        const RenderLayer*r=&render_layers[j];const Layer*l=&r->layer;const Image*i=&images[l->image];
        if(i->kind==2){
            for(unsigned p=0;p<(unsigned)i->count;p++){
                int x,y;
                if(particle_position(i,p,ticks-l->start,&x,&y)&&x<256&&x+frames[i->data].width>0&&y<168&&y+frames[i->data].height>24){
                    hash=hash_value(hash,(unsigned)x);hash=hash_value(hash,(unsigned)y);
                }
            }
        }else{
            hash=hash_value(hash,r->sample.frame);hash=hash_value(hash,r->sample.old);hash=hash_value(hash,r->sample.mix);
            hash=hash_value(hash,r->transform.x);hash=hash_value(hash,r->transform.y);hash=hash_value(hash,r->transform.z);
        }
    }
    return hash?hash:1;
}
DRAW_FAST static void clear_screen(uint16_t*screen){
    if((uintptr_t)screen&3){for(unsigned i=0;i<256u*192u;i++)screen[i]=0x8000;return;}
    GfxWord*d=(GfxWord*)screen;
    for(unsigned i=0;i<256u*192u/2;i+=8){
        d[i]=0x80008000u;d[i+1]=0x80008000u;d[i+2]=0x80008000u;d[i+3]=0x80008000u;
        d[i+4]=0x80008000u;d[i+5]=0x80008000u;d[i+6]=0x80008000u;d[i+7]=0x80008000u;
    }
}
bool story_gfx_draw(uint32_t ticks,uint16_t*screen){
    if(!catalog||!screen||replay)return fail("draw while closed/replaying");
    prepare_layers(ticks);
    clear_screen(screen);
    service();
    if(draw_index_pair(screen)){service();return true;}
    for(unsigned j=0;j<render_count;j++){
        const RenderLayer*r=&render_layers[j];const Layer*l=&r->layer;const Image*i=&images[l->image];
        if(!l->loaded)return fail("draw requires sync");
        if(!j&&i->kind!=2&&r->sample.old==NONE&&frames[r->sample.frame].format==1){
            /* A verified all-black palette on the very first layer cannot
             * change the cleared black target, regardless of its A1 mask.
             * Later black layers must still cover the pictures under them. */
            unsigned colors=0;for(unsigned k=0;k<16;k++)colors|=frames[r->sample.frame].palette[k]&0x7fff;
            if(!colors)continue;
        }
        if(i->kind==2){
            for(unsigned p=0;p<(unsigned)i->count;p++){
                int x,y;
                if(particle_position(i,p,ticks-l->start,&x,&y))
                    draw_layer(l,(Sample){(unsigned)i->data,NONE,4096},(Transform){x*Q,y*Q,Q},screen,24,168);
                if(!(p&15))service();
            }
        }else draw_layer(l,r->sample,r->transform,screen,0,192);
        service();
    }
    return true;
}
