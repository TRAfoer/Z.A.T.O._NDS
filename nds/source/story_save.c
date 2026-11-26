#include "story_save.h"
#include <errno.h>
#include <stdio.h>
#include <string.h>
#ifdef _WIN32
#include <io.h>
#else
#include <unistd.h>
#endif

#define GLOBAL_BYTES 8192u
#define RECORD_BYTES 4096u
#define JOURNAL_BASE 16384u
#define SLOT_BASE 24576u
#define FORMAT 1u
static struct {
    bool available;
    char path[512],error[128];
    uint8_t identity[32];
    uint32_t seq,global_seq,global_bank;
    StorySaveGlobals globals;
} store;
static uint8_t block[GLOBAL_BYTES],record[RECORD_BYTES],journal[RECORD_BYTES];
static uint32_t u32(const uint8_t*p){return p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;}
static void put32(uint8_t*p,uint32_t n){p[0]=n;p[1]=n>>8;p[2]=n>>16;p[3]=n>>24;}
static uint32_t crc(const uint8_t*p,size_t bytes)
{
    uint32_t value=~0u;
    while(bytes--){value^=*p++;for(unsigned bit=0;bit<8;bit++)value=(value>>1)^(0xedb88320u&-(value&1u));}
    return ~value;
}
static uint32_t record_crc(const uint8_t*p)
{
    uint32_t value=~0u;
    for(unsigned i=0;i<RECORD_BYTES;i++){
        value^=(i>=252&&i<256)?0:p[i];
        for(unsigned bit=0;bit<8;bit++)value=(value>>1)^(0xedb88320u&-(value&1u));
    }
    return ~value;
}
static bool fail(const char*message){snprintf(store.error,sizeof(store.error),"%s",message);return false;}
static bool zero(const uint8_t*p,size_t size){while(size--)if(*p++)return false;return true;}
static bool newer(uint32_t a,uint32_t b){return (int32_t)(a-b)>0;}
static uint32_t next_seq(uint32_t value){return value==UINT32_MAX?1:value+1;}
static bool close_checked(FILE*f,bool good)
{if(fclose(f))return fail("Save close failed");return good;}
static bool sync_checked(FILE*f)
{
    if(fflush(f))return fail("Save flush failed");
#ifdef _WIN32
    if(_commit(fileno(f)))return fail("Save sync failed");
#else
    if(fsync(fileno(f)))return fail("Save sync failed");
#endif
    return true;
}
static bool read_at(uint32_t offset,void*data,size_t size)
{
    FILE*f=fopen(store.path,"rb");if(!f)return fail("Save read open failed");
    bool good=true;
    if(fseek(f,(long)offset,SEEK_SET)||fread(data,1,size,f)!=size)good=fail("Save read failed");
    return close_checked(f,good);
}
static bool write_at(uint32_t offset,const void*data,size_t size)
{
    FILE*f=fopen(store.path,"r+b");if(!f)return fail("Save write open failed");
    bool good=true;
    if(fseek(f,(long)offset,SEEK_SET)||fwrite(data,1,size,f)!=size)good=fail("Save write failed");
    if(good)good=sync_checked(f);
    return close_checked(f,good);
}
static bool global_valid(const uint8_t*p)
{
    return !memcmp(p,"ZATSAV01",8)&&u32(p+8)==FORMAT&&u32(p+12)==STORY_SAVE_BYTES&&
        !memcmp(p+16,store.identity,32)&&u32(p+48)&&u32(p+52)<=1&&u32(p+56)<=4096&&
        u32(p+60)<=4096&&!(u32(p+1568)&~15u)&&u32(p+1572)<=1&&crc(p,GLOBAL_BYTES-4)==u32(p+GLOBAL_BYTES-4);
}
static void globals_decode(const uint8_t*p)
{
    store.global_seq=u32(p+48);store.globals.language=u32(p+52);
    store.globals.music_q12=u32(p+56);store.globals.sfx_q12=u32(p+60);store.globals.play_ticks=u32(p+64);
    memcpy(store.globals.seen,p+68,STORY_SAVE_SEEN_BYTES);
    store.globals.unlocks[0]=u32(p+1564);store.globals.unlocks[1]=u32(p+1568);
    store.globals.chapter_skip=u32(p+1572);
}
static void globals_encode(uint8_t*p,uint32_t seq)
{
    memset(p,0,GLOBAL_BYTES);memcpy(p,"ZATSAV01",8);put32(p+8,FORMAT);put32(p+12,STORY_SAVE_BYTES);
    memcpy(p+16,store.identity,32);put32(p+48,seq);put32(p+52,store.globals.language);
    put32(p+56,store.globals.music_q12);put32(p+60,store.globals.sfx_q12);put32(p+64,store.globals.play_ticks);
    memcpy(p+68,store.globals.seen,STORY_SAVE_SEEN_BYTES);put32(p+1564,store.globals.unlocks[0]);put32(p+1568,store.globals.unlocks[1]);
    put32(p+1572,store.globals.chapter_skip);
    put32(p+GLOBAL_BYTES-4,crc(p,GLOBAL_BYTES-4));
}
static bool record_valid(const uint8_t*p,unsigned expected)
{
    return !memcmp(p,"ZATSLOT1",8)&&u32(p+8)==FORMAT&&!memcmp(p+12,store.identity,32)&&
        u32(p+44)<STORY_SAVE_SLOT_COUNT&&(expected==UINT32_MAX||u32(p+44)==expected)&&u32(p+48)&&
        u32(p+52)<=1&&u32(p+76)<=1&&u32(p+80)==768&&u32(p+84)==3072&&p[183]==0&&
        record_crc(p)==u32(p+252);
}
static void meta_decode(const uint8_t*p,StorySaveMeta*m)
{
    memset(m,0,sizeof(*m));m->valid=u32(p+52)!=0;m->seq=u32(p+48);m->unix_time=u32(p+56);
    m->play_seconds=u32(p+60);m->chapter=u32(p+64);m->text_id=u32(p+68);m->page=u32(p+72);m->bilingual=u32(p+76);
    memcpy(m->caption,p+88,96);
}
static bool drain(void)
{
    /* Never replace a recovery copy until every complete previous transaction
     * has reached its home. Two journals may contain different target slots. */
    for(unsigned bank=0;bank<2;bank++){
        if(!read_at(JOURNAL_BASE+bank*RECORD_BYTES,journal,RECORD_BYTES))return false;
        if(!record_valid(journal,UINT32_MAX))continue; /* incomplete transaction */
        unsigned slot=u32(journal+44);uint32_t seq=u32(journal+48);
        if(!store.seq||newer(seq,store.seq))store.seq=seq;
        if(!read_at(SLOT_BASE+slot*RECORD_BYTES,record,RECORD_BYTES))return false;
        if(record_valid(record,slot)&&!newer(seq,u32(record+48)))continue;
        if(!write_at(SLOT_BASE+slot*RECORD_BYTES,journal,RECORD_BYTES))return false;
    }
    return true;
}
static bool initialize(void)
{
    FILE*f=fopen(store.path,"wb");if(!f)return fail("Save create failed");
    uint8_t zeros[512]={0};bool good=true;
    for(unsigned offset=0;offset<STORY_SAVE_BYTES&&good;offset+=sizeof(zeros))
        if(fwrite(zeros,1,sizeof(zeros),f)!=sizeof(zeros))good=fail("Save allocation failed");
    if(good)good=sync_checked(f);
    if(!close_checked(f,good))return false;
    memset(&store.globals,0,sizeof(store.globals));store.globals.music_q12=store.globals.sfx_q12=4096;
    globals_encode(block,1);
    return write_at(0,block,GLOBAL_BYTES)&&write_at(GLOBAL_BYTES,block,GLOBAL_BYTES);
}
static bool entirely_zero(void)
{
    for(unsigned offset=0;offset<STORY_SAVE_BYTES;offset+=sizeof(block)){
        if(!read_at(offset,block,sizeof(block)))return false;
        if(!zero(block,sizeof(block)))return false;
    }
    return true;
}
bool story_save_open(const char*path,const uint8_t identity[32])
{
    memset(&store,0,sizeof(store));
    /* Preferences remain usable in RAM when no writable SD is mounted. This
     * does not grant persistence: every mutation still checks available. */
    store.globals.music_q12=store.globals.sfx_q12=4096;
    if(!path||!*path||strlen(path)>=sizeof(store.path)||!identity)return fail("Invalid save path/identity");
    strcpy(store.path,path);memcpy(store.identity,identity,32);
    FILE*f=fopen(path,"rb");bool create=false;
    if(!f){if(errno!=ENOENT)return fail("Save open failed");create=true;}
    else{
        bool good=!fseek(f,0,SEEK_END);long size=good?ftell(f):-1;
        if(!close_checked(f,good)||size<0)return fail("Save size read failed");
        if(size==0)create=true;
        else if(size!=STORY_SAVE_BYTES)return fail("Unsupported save length");
    }
    if(create&&!initialize())return false;
    bool found=false;uint32_t best=0;
    for(unsigned bank=0;bank<2;bank++){
        if(!read_at(bank*GLOBAL_BYTES,block,GLOBAL_BYTES))return false;
        if(!memcmp(block,"ZATSAV01",8)&&crc(block,GLOBAL_BYTES-4)==u32(block+GLOBAL_BYTES-4)&&
           (u32(block+8)!=FORMAT||u32(block+12)!=STORY_SAVE_BYTES||memcmp(block+16,store.identity,32)))
            return fail("Foreign or incompatible save generation");
        if(global_valid(block)&&(!found||newer(u32(block+48),best))){
            found=true;best=u32(block+48);globals_decode(block);store.global_bank=bank;
        }
    }
    if(!found){
        if(!entirely_zero())return fail("Foreign, incompatible or corrupt save globals");
        if(!initialize()||!read_at(0,block,GLOBAL_BYTES))return false;
        globals_decode(block);store.global_bank=0;
    }
    if(!drain())return false;
    for(unsigned slot=0;slot<STORY_SAVE_SLOT_COUNT;slot++){
        if(!read_at(SLOT_BASE+slot*RECORD_BYTES,record,RECORD_BYTES))return false;
        if(record_valid(record,slot)&&(!store.seq||newer(u32(record+48),store.seq)))store.seq=u32(record+48);
    }
    store.available=true;store.error[0]=0;return true;
}
void story_save_close(void){store.available=false;}
bool story_save_available(void){return store.available;}
const char*story_save_error(void){return store.error;}
const char*story_save_path(void){return store.path;}
StorySaveGlobals*story_save_globals(void){return &store.globals;}
bool story_save_commit_globals(void)
{
    store.error[0]=0;
    if(!store.available)return fail("Save unavailable");
    if(store.globals.language>1||store.globals.music_q12>4096||store.globals.sfx_q12>4096||store.globals.unlocks[1]&~15u||store.globals.chapter_skip>1)return fail("Invalid persistent settings");
    if(!drain())return false;
    uint32_t seq=next_seq(store.global_seq),bank=store.global_bank^1;
    globals_encode(block,seq);if(!write_at(bank*GLOBAL_BYTES,block,GLOBAL_BYTES))return false;
    store.global_seq=seq;store.global_bank=bank;return true;
}
bool story_save_info(unsigned slot,StorySaveMeta*m)
{
    store.error[0]=0;if(!store.available||slot>=STORY_SAVE_SLOT_COUNT||!m)return fail("Invalid save slot");
    memset(m,0,sizeof(*m));if(!read_at(SLOT_BASE+slot*RECORD_BYTES,record,RECORD_BYTES))return false;
    if(zero(record,RECORD_BYTES))return true;
    if(!record_valid(record,slot))return fail("Save slot CRC/identity invalid");
    meta_decode(record,m);return true;
}
bool story_save_read(unsigned slot,StorySaveMeta*m,uint8_t checkpoint[768],uint8_t thumb[3072])
{
    if(!checkpoint||!thumb||!story_save_info(slot,m))return fail("Save read failed");
    if(!m->valid)return fail("Save slot empty");
    memcpy(checkpoint,record+256,768);memcpy(thumb,record+1024,3072);return true;
}
static bool transaction(unsigned slot,const StorySaveMeta*m,const uint8_t*checkpoint,const uint8_t*thumb,bool valid)
{
    store.error[0]=0;
    if(!store.available||slot>=STORY_SAVE_SLOT_COUNT||!m||m->bilingual>1||!memchr(m->caption,0,96))return fail("Invalid save record");
    if(!drain())return false;
    uint32_t seq=next_seq(store.seq);memset(record,0,RECORD_BYTES);memcpy(record,"ZATSLOT1",8);put32(record+8,FORMAT);
    memcpy(record+12,store.identity,32);put32(record+44,slot);put32(record+48,seq);put32(record+52,valid);
    put32(record+56,m->unix_time);put32(record+60,m->play_seconds);put32(record+64,m->chapter);put32(record+68,m->text_id);
    put32(record+72,m->page);put32(record+76,m->bilingual);put32(record+80,768);put32(record+84,3072);
    size_t caption=strlen(m->caption);memcpy(record+88,m->caption,caption);
    if(valid){memcpy(record+256,checkpoint,768);memcpy(record+1024,thumb,3072);}
    put32(record+252,record_crc(record));
    if(!write_at(JOURNAL_BASE+(seq&1)*RECORD_BYTES,record,RECORD_BYTES))return false;
    store.seq=seq;
    return write_at(SLOT_BASE+slot*RECORD_BYTES,record,RECORD_BYTES);
}
bool story_save_write(unsigned slot,const StorySaveMeta*m,const uint8_t checkpoint[768],const uint8_t thumb[3072])
{if(!checkpoint||!thumb)return fail("Missing save payload");return transaction(slot,m,checkpoint,thumb,true);}
bool story_save_delete(unsigned slot)
{StorySaveMeta m={0};return transaction(slot,&m,NULL,NULL,false);}
unsigned story_save_recent(unsigned first,unsigned count,unsigned*slots,unsigned capacity)
{
    if(first>=STORY_SAVE_SLOT_COUNT||count>STORY_SAVE_SLOT_COUNT-first||(!slots&&capacity)){fail("Invalid save range");return 0;}
    uint32_t seqs[STORY_SAVE_SLOT_COUNT];unsigned found=0;
    for(unsigned slot=first;slot<first+count;slot++){
        StorySaveMeta m;if(!story_save_info(slot,&m))return 0;if(!m.valid)continue;
        unsigned at=found;while(at&&newer(m.seq,seqs[at-1])){seqs[at]=seqs[at-1];if(at<capacity)slots[at]=slots[at-1];at--;}
        seqs[at]=m.seq;if(at<capacity)slots[at]=slot;found++;
    }
    return found<capacity?found:capacity;
}
unsigned story_save_ring_slot(unsigned first,unsigned count)
{
    if(!count||first>=STORY_SAVE_SLOT_COUNT||count>STORY_SAVE_SLOT_COUNT-first){fail("Invalid save range");return UINT32_MAX;}
    unsigned oldest=first;uint32_t seq=0;
    for(unsigned slot=first;slot<first+count;slot++){
        StorySaveMeta m;if(!story_save_info(slot,&m))return UINT32_MAX;if(!m.valid)return slot;
        if(!seq||newer(seq,m.seq)){oldest=slot;seq=m.seq;}
    }
    return oldest;
}
