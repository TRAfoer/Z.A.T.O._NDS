/* Real store and real files; only failure points are injected. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <io.h>
#else
#include <unistd.h>
#endif
static long write_budget=-1;
static unsigned sync_call,close_call,fail_sync,fail_close;
static bool fail_read,fail_flush,fail_seek,fail_open;
static FILE *written_file;
static unsigned write_close_call,fail_write_close;
static FILE*probe_open(const char*path,const char*mode)
{return fail_open?NULL:fopen(path,mode);}
static int probe_seek(FILE*f,long offset,int origin)
{return fail_seek?-1:fseek(f,offset,origin);}
static int probe_flush(FILE*f){return fail_flush?EOF:fflush(f);}
static size_t probe_write(const void *p,size_t n,size_t count,FILE *f)
{
    size_t bytes=n*count;
    if(write_budget>=0 && bytes>(size_t)write_budget)bytes=(size_t)write_budget;
    size_t written=fwrite(p,1,bytes,f);
    if(written)written_file=f;
    if(write_budget>=0)write_budget-=(long)written;
    return n?written/n:0;
}
static size_t probe_read(void *p,size_t n,size_t count,FILE *f)
{return fail_read?0:fread(p,n,count,f);}
static int probe_close(FILE *f)
{
    bool wrote=f==written_file;if(wrote)written_file=NULL;
    int r=fclose(f);bool bad=++close_call==fail_close;
    if(wrote&&++write_close_call==fail_write_close)bad=true;
    return bad?EOF:r;
}
static int probe_sync(int fd)
{
    if(++sync_call==fail_sync)return -1;
#ifdef _WIN32
    return _commit(fd);
#else
    return fsync(fd);
#endif
}
#define fwrite probe_write
#define fread probe_read
#define fclose probe_close
#define fopen probe_open
#define fseek probe_seek
#define fflush probe_flush
#ifdef _WIN32
#define _commit probe_sync
#else
#define fsync probe_sync
#endif
#include "../source/story_save.c"
#undef fwrite
#undef fread
#undef fclose
#undef fopen
#undef fseek
#undef fflush
#ifdef _WIN32
#undef _commit
#else
#undef fsync
#endif
static uint8_t id[32]={1,2,3,4},checkpoint[768],thumb[3072];
static uint8_t baseline[STORY_SAVE_BYTES],current[STORY_SAVE_BYTES];
static void faults(void)
{write_budget=-1;sync_call=close_call=fail_sync=fail_close=write_close_call=fail_write_close=0;fail_read=fail_flush=fail_seek=fail_open=false;written_file=NULL;}
static void load_file(const char *path,uint8_t *out)
{FILE*f=fopen(path,"rb");assert(f);assert(fread(out,1,STORY_SAVE_BYTES,f)==STORY_SAVE_BYTES);assert(!fclose(f));}
static void put_file(const char *path,const uint8_t *bytes)
{FILE*f=fopen(path,"wb");assert(f);assert(fwrite(bytes,1,STORY_SAVE_BYTES,f)==STORY_SAVE_BYTES);assert(!fclose(f));}
static StorySaveMeta meta(unsigned value)
{StorySaveMeta m={0};m.valid=true;m.unix_time=value;m.play_seconds=123;m.chapter=2;m.text_id=value;m.page=3;m.bilingual=1;strcpy(m.caption,"source caption");return m;}
static void expect_slot(unsigned slot,unsigned value)
{StorySaveMeta m;uint8_t cp[768],tn[3072];assert(story_save_read(slot,&m,cp,tn));assert(m.valid&&m.text_id==value);assert(!memcmp(cp,checkpoint,768));assert(!memcmp(tn,thumb,3072));}
int main(int argc,char **argv)
{
    assert(argc==2);const char *path=argv[1];remove(path);faults();
    memset(checkpoint,0x39,sizeof(checkpoint));memset(thumb,0xa7,sizeof(thumb));
    assert(story_save_open(path,id));assert(story_save_available());
    StorySaveMeta m;assert(story_save_info(0,&m)&&!m.valid);
    StorySaveGlobals*g=story_save_globals();assert(g&&g->music_q12==4096&&g->sfx_q12==4096);
    g->seen[1493]=0x40;g->unlocks[1]=8;g->language=1;g->play_ticks=111;
    assert(story_save_commit_globals());
    m=meta(10);assert(story_save_write(0,&m,checkpoint,thumb));expect_slot(0,10);
    m=meta(20);assert(story_save_write(96,&m,checkpoint,thumb));
    m=meta(30);assert(story_save_write(97,&m,checkpoint,thumb));
    unsigned recent[6];assert(story_save_recent(96,10,recent,6)==2&&recent[0]==97&&recent[1]==96);
    assert(story_save_ring_slot(96,10)==98);
    assert(story_save_delete(97));assert(story_save_info(97,&m)&&!m.valid);
    assert(story_save_recent(96,10,recent,6)==1&&recent[0]==96);
    assert(story_save_ring_slot(96,10)==97);
    story_save_close();assert(!story_save_available());
    assert(story_save_open(path,id));assert(story_save_globals()->seen[1493]==0x40);expect_slot(0,10);
    story_save_close();load_file(path,baseline);
    unsigned cases=0;
    const long cuts[]={0,1,127,255,256,4095,4096,4097,4351,8191,8192};
    for(unsigned i=0;i<sizeof(cuts)/sizeof(*cuts);i++){
        put_file(path,baseline);faults();assert(story_save_open(path,id));
        write_budget=cuts[i];m=meta(40);bool success=story_save_write(0,&m,checkpoint,thumb);
        assert(success==(cuts[i]>=8192));faults();story_save_close();assert(story_save_open(path,id));
        assert(story_save_info(0,&m)&&m.valid);assert(m.text_id==10||m.text_id==40);
        if(cuts[i]>=4096)assert(m.text_id==40);
        story_save_close();cases++;
    }
    for(unsigned operation=1;operation<=2;operation++)for(unsigned kind=0;kind<2;kind++){
        put_file(path,baseline);faults();assert(story_save_open(path,id));faults();
        if(kind)fail_write_close=operation;else fail_sync=operation;
        m=meta(41);assert(!story_save_write(0,&m,checkpoint,thumb));assert(*story_save_error());
        faults();story_save_close();assert(story_save_open(path,id));assert(story_save_info(0,&m)&&m.valid);story_save_close();cases++;
    }
    for(unsigned kind=0;kind<5;kind++){
        put_file(path,baseline);faults();assert(story_save_open(path,id));faults();
        fail_read=kind==0;fail_flush=kind==1;fail_seek=kind==2;fail_open=kind==3;fail_close=kind==4?1:0;
        m=meta(41);assert(!story_save_write(0,&m,checkpoint,thumb)&&*story_save_error());
        faults();story_save_close();assert(story_save_open(path,id));assert(story_save_info(0,&m)&&m.valid);story_save_close();cases++;
    }
    /* A committed journal plus a torn homeslot must repair at open. */
    put_file(path,baseline);faults();assert(story_save_open(path,id));write_budget=4200;m=meta(42);
    assert(!story_save_write(0,&m,checkpoint,thumb));faults();story_save_close();
    assert(story_save_open(path,id));expect_slot(0,42);story_save_close();
    /* A globals write must also drain a pending slot transaction first. */
    put_file(path,baseline);assert(story_save_open(path,id));write_budget=4096;m=meta(45);
    assert(!story_save_write(0,&m,checkpoint,thumb));faults();story_save_globals()->play_ticks=333;
    assert(story_save_commit_globals());expect_slot(0,45);story_save_close();
    /* Failed drain cannot overwrite the previous recovery transaction. */
    put_file(path,baseline);assert(story_save_open(path,id));write_budget=4096;m=meta(43);
    assert(!story_save_write(0,&m,checkpoint,thumb));faults();write_budget=0;m=meta(44);
    assert(!story_save_write(1,&m,checkpoint,thumb));faults();story_save_close();
    assert(story_save_open(path,id));expect_slot(0,43);assert(story_save_info(1,&m)&&!m.valid);story_save_close();
    /* A failed global update always leaves one complete old/new generation. */
    for(unsigned i=0;i<4;i++){
        const long global_cuts[]={0,8191,8192,9000};
        put_file(path,baseline);faults();assert(story_save_open(path,id));
        story_save_globals()->play_ticks=222;write_budget=global_cuts[i];
        assert(story_save_commit_globals()==(global_cuts[i]>=8192));faults();story_save_close();
        assert(story_save_open(path,id));assert(story_save_globals()->play_ticks==111||story_save_globals()->play_ticks==222);
        story_save_close();cases++;
    }
    /* Ring capacity is ten; selection exposes latest six without losing order. */
    put_file(path,baseline);faults();assert(story_save_open(path,id));
    for(unsigned slot=106;slot<116;slot++){m=meta(slot);assert(story_save_write(slot,&m,checkpoint,thumb));}
    assert(story_save_recent(106,10,recent,6)==6);
    for(unsigned n=0;n<6;n++)assert(recent[n]==115-n);
    assert(story_save_ring_slot(106,10)==106);story_save_close();
    /* A valid foreign generation must not be silently replaced even when the
     * other bank happens to match this game. CRC damage alone is recoverable. */
    memcpy(current,baseline,sizeof(current));current[16]^=0x80;
    put32(current+8192-4,crc(current,8192-4));put_file(path,current);
    assert(!story_save_open(path,id)&&!story_save_available());
    /* Identity/corrupt globals/read failure must leave every byte alone. */
    for(unsigned kind=0;kind<3;kind++){
        memcpy(current,baseline,sizeof(current));if(kind==1){current[100]^=1;current[8192+100]^=1;}
        put_file(path,current);faults();uint8_t other[32]={9};fail_read=kind==2;
        assert(!story_save_open(path,kind==0?other:id));assert(!story_save_available()&&*story_save_error());
        faults();load_file(path,baseline+0);assert(!memcmp(current,baseline,sizeof(current)));cases++;
    }
    /* All-zero fixed-size precreated .sav may initialize. */
    memset(current,0,sizeof(current));put_file(path,current);assert(story_save_open(path,id));
    m=meta(7);assert(story_save_write(115,&m,checkpoint,thumb));story_save_close();
    load_file(path,current);current[24576+115*4096+300]^=1;/* discard recovery copies */
    memset(current+16384,0,8192);put_file(path,current);assert(story_save_open(path,id));
    assert(!story_save_info(115,&m)&&*story_save_error());story_save_close();
    printf("PASS story save: fixed512KiB, globals/slots/rings/delete, %u fault/identity cases, journal repair, corrupt!=empty\n",cases);
    return 0;
}
