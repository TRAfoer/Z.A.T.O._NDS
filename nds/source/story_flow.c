#include "story_flow.h"
#include <string.h>
_Static_assert(sizeof(StoryFlow)==sizeof(void*)*3+104,"unchanged flow ABI");

static const SceneTextInfo*info(const StoryFlow*f){return f->ops->info(f->context,f->text_id);}
static void fail(StoryFlow*f){f->failed=f->done=true;}
unsigned story_flow_pages(const StoryFlow*f){const SceneTextInfo*t=info(f);return t?(f->bilingual?t->pages_bi:t->pages_cn):1;}
static unsigned limit(const StoryFlow*f){return f->ops->limit(f->context,f->text_id,f->page,f->bilingual);}
static const SceneTextPause*pause(const StoryFlow*f){return f->revealed?NULL:f->ops->pause(f->context,f->text_id,f->pause_index);}
static uint32_t first_tick(StoryFlow*f,unsigned count)
{
    const SceneTextInfo*t=info(f);uint32_t lo=0,hi=t?t->duration_ticks:0;
    while(lo<hi){uint32_t mid=lo+(hi-lo)/2;if(f->ops->visible(f->context,f->text_id,mid)>=count)hi=mid;else lo=mid+1;}
    return lo;
}
static unsigned cap(StoryFlow*f,unsigned raw)
{
    const SceneTextPause*p=pause(f);if(p&&raw>p->glyphs)raw=p->glyphs;
    unsigned n=limit(f);if(raw>n)raw=n;
    return raw;
}
void story_flow_refresh(StoryFlow*f)
{
    if(!f->blocked)return;
    const SceneTextInfo*t=info(f);if(!t){fail(f);return;}
    f->visible=cap(f,f->revealed?t->char_count:f->ops->visible(f->context,f->text_id,f->local));
}
void story_flow_mark_presented(StoryFlow*f)
{
    if(f->blocked){f->presented=true;f->presented_local=f->local;f->presented_visible=f->visible;}
}
static void end(StoryFlow*f)
{
    const SceneTextInfo*t=info(f);if(t&&t->mode==3)f->text_active=false;
    f->blocked=f->waiting=f->hard=f->forever=false;
}
static bool start_text(StoryFlow*f,const StoryEvent*e)
{
    f->text_id=e->a;
    if(!f->ops->select_text(f->context,f->text_id,0))return false;
    const SceneTextInfo*t=info(f);if(!t)return false;
    f->text_active=f->blocked=true;f->source_auto=(e->flags&1)!=0;
    f->local=f->pause_index=0;f->revealed=f->page_hold=f->presented=f->scroll_ready=false;f->page_ready=STORY_NONE;
    f->page=t->continuation?f->ops->page(f->context,f->text_id,t->new_char_start+1,f->bilingual):0;
    if(t->mode!=3)f->window=true;
    story_flow_refresh(f);return !f->failed;
}
static void execute(StoryFlow*f,const StoryEvent*e)
{
    f->line=e->line;f->commands++;
    if(f->ops->event&&!f->ops->event(f->context,e,f->now,f->quiet)){fail(f);return;}
    switch(e->op){
    case ST_TEXT:
        if(f->quiet){
            f->text_id=e->a;unsigned mode=(e->flags>>1)&3;f->text_active=mode!=3;
            if(mode!=3)f->window=true;
            if(e->flags&1)f->now+=e->d;
            if(e->b!=TR_NONE)f->now+=e->c;
        }else if(!start_text(f,e))fail(f);
        break;
    case ST_WAIT:
        if(f->quiet){if(e->a>=0)f->now+=e->a;}
        else{f->waiting=true;f->hard=e->b;f->forever=e->a<0;f->wait_started=f->now;f->wait_end=f->now+(e->a<0?0:e->a);}
        break;
    case ST_TRANSITION:
        if(f->quiet)f->now+=e->b;
        else if(e->b){f->waiting=f->hard=true;f->forever=false;f->wait_started=f->now;f->wait_end=f->now+e->b;}
        break;
    case ST_WINDOW:f->window=e->a;break;
    case ST_QUICK:f->quick_menu=e->a;break;
    case ST_CLEAR:f->text_id=STORY_NONE;f->text_active=false;break;
    case ST_LITERAL:{
        f->literal_id=e->a;f->literal_start=f->now;uint64_t duration=0;
        for(unsigned at=f->pc;at<f->script->count;at++){
            const StoryEvent*q=&f->script->events[at];
            if(q->op==ST_HIDE_LITERAL||q->op==ST_LITERAL||q->op==ST_TEXT||q->op==ST_END)break;
            if(q->op==ST_WAIT&&q->a>=0)duration+=q->a;
            if(q->op==ST_TRANSITION)duration+=q->b;
        }
        f->literal_duration=duration>UINT32_MAX?UINT32_MAX:(duration?duration:60);break;
    }
    case ST_HIDE_LITERAL:f->literal_id=STORY_NONE;break;
    case ST_DATE:f->date_id=e->a<0?STORY_NONE:(unsigned)e->a;break;
    case ST_SCENE:f->literal_id=STORY_NONE;break;
    case ST_UNLOCK:{uint32_t mask=1u<<(e->a&31);if(e->b)f->unlocks[e->a>>5]|=mask;else f->unlocks[e->a>>5]&=~mask;break;}
    case ST_JUMP:f->chapter=e->b;if(!f->quiet)f->pc=e->a;break;
    case ST_END:if(!f->quiet)f->done=true;break;
    default:break;
    }
}
static void pump(StoryFlow*f)
{
    if(f->ops->batch)f->ops->batch(f->context,true,f->now,f->quiet);
    unsigned budget=0;
    while(!f->blocked&&!f->waiting&&!f->done&&!f->failed){
        if(f->pc>=f->script->count||budget++==2048){fail(f);break;}
        const StoryEvent*e=&f->script->events[f->pc++];execute(f,e);
    }
    if(f->ops->batch)f->ops->batch(f->context,false,f->now,f->quiet);
}
static void reset(StoryFlow*f)
{
    const StoryScript*s=f->script;const StoryFlowOps*ops=f->ops;void*c=f->context;
    bool bi=f->bilingual,automatic=f->automatic,skip=f->skip,scrolling=f->scrolling;
    memset(f,0,sizeof(*f));f->script=s;f->ops=ops;f->context=c;
    f->bilingual=bi;f->automatic=automatic;f->skip=skip;f->scrolling=scrolling;
    f->text_id=f->literal_id=f->date_id=f->page_ready=STORY_NONE;f->window=false;f->quick_menu=true;
    if(ops->reset)ops->reset(c);
}
bool story_flow_init(StoryFlow*f,const StoryScript*s,const StoryFlowOps*ops,void*c)
{
    memset(f,0,sizeof(*f));f->script=s;f->ops=ops;f->context=c;
    if(!s||!s->count||!s->chapter_count||!ops||!ops->select_text||!ops->info||!ops->pause||!ops->visible||!ops->page||!ops->limit)return false;
    reset(f);pump(f);return !f->failed;
}
bool story_flow_seek(StoryFlow*f,unsigned chapter)
{
    if(chapter>=f->script->chapter_count)return false;
    reset(f);f->quiet=true;
    uint32_t target=f->script->chapters[chapter].first_pc;
    if(f->ops->batch)f->ops->batch(f->context,true,0,true);
    while(f->pc<target&&!f->failed){const StoryEvent*e=&f->script->events[f->pc++];execute(f,e);}
    if(f->ops->batch)f->ops->batch(f->context,false,f->now,true);
    f->quiet=false;f->chapter=chapter;
    if(f->failed)return false;
    if(f->text_id!=STORY_NONE){
        if(!f->ops->select_text(f->context,f->text_id,0)){fail(f);return false;}
        const SceneTextInfo*t=info(f);if(!t){fail(f);return false;}
        f->visible=t->char_count;f->local=t->duration_ticks;f->page=f->ops->page(f->context,f->text_id,f->visible,f->bilingual);
    }
    pump(f);return !f->failed;
}
void story_flow_tick(StoryFlow*f,uint32_t delta)
{
    if(f->paused||f->done||f->failed)return;
    f->now+=delta;
    if(f->waiting){if(!f->forever&&f->now>=f->wait_end&&f->now>f->wait_started){end(f);pump(f);}return;}
    if(!f->blocked)return;
    const SceneTextInfo*t=info(f);if(!t){fail(f);return;}
    bool scroll=f->scrolling&&t->mode!=3;
    if(!f->page_hold&&!f->revealed)f->local+=delta;
    story_flow_refresh(f);unsigned last=limit(f);
    const SceneTextPause*p=pause(f);
    if(f->page+1<story_flow_pages(f)&&f->visible>=last&&(!p||p->glyphs>last)){
        if(!f->source_auto){uint32_t stamp=first_tick(f,last);if(f->local>stamp)f->local=stamp;}
        f->page_hold=true;
        if(f->page_ready==STORY_NONE)f->page_ready=f->now;
        /* A fast authored batch can span pages at one tick. Submit its tail
         * on this page before moving, even for {nw}; keep the authored clock
         * while the additional physical page commit is pending. */
        if(f->presented&&f->presented_visible>=last&&(!scroll||f->scroll_ready)&&
           (scroll||f->source_auto||(f->automatic&&f->now-f->page_ready>=(f->skip?4u:90u)))){
            f->page++;f->page_hold=false;f->page_ready=STORY_NONE;f->presented=f->scroll_ready=false;
            story_flow_refresh(f);
        }
        return;
    }
    if(p){
        if(f->local>=p->end_tick&&f->presented&&(!scroll||f->scroll_ready)&&f->presented_local>=p->end_tick&&f->presented_visible>=p->glyphs){
            f->local=p->end_tick;f->pause_index++;f->presented=f->scroll_ready=false;
        }
        return;
    }
    if(f->visible>=t->char_count&&f->local>=t->duration_ticks){
        if(f->page_ready==STORY_NONE)f->page_ready=f->now;
        if((f->source_auto||f->automatic)&&f->presented&&(!scroll||f->scroll_ready)&&f->presented_visible>=t->char_count&&f->presented_local>=t->duration_ticks&&
           (f->source_auto||f->now-f->page_ready>=(f->skip?4u:90u))){end(f);pump(f);}
    }
}
void story_flow_advance(StoryFlow*f)
{
    if(f->paused||f->done||f->failed)return;
    if(f->waiting){if(!f->hard){end(f);pump(f);}return;}
    if(!f->blocked)return;
    const SceneTextInfo*t=info(f);if(!t){fail(f);return;}
    bool scroll=f->scrolling&&t->mode!=3,ready=!scroll||(f->presented&&f->scroll_ready);
    unsigned last=limit(f);const SceneTextPause*p=pause(f);
    if(p&&p->glyphs<=last){
        if(f->local<p->start_tick)f->local=p->start_tick;
        else{if(!ready||(scroll&&f->presented_visible<p->glyphs))return;f->local=p->end_tick;f->pause_index++;}
        f->presented=f->scroll_ready=false;
    }else if(f->visible<last){f->local=first_tick(f,last);f->presented=f->scroll_ready=false;}
    else if(f->page+1<story_flow_pages(f)){
        if(scroll)return; /* Runtime coverage + tick continues this reveal chunk. */
        f->page++;f->page_hold=false;f->page_ready=STORY_NONE;f->presented=f->scroll_ready=false;
    }else if(f->local<t->duration_ticks){if(!ready)return;f->local=t->duration_ticks;f->revealed=true;f->presented=f->scroll_ready=false;}
    else{if(!ready||(scroll&&f->presented_visible<t->char_count))return;end(f);pump(f);}
    story_flow_refresh(f);
}
void story_flow_toggle_language(StoryFlow*f)
{
    unsigned anchor=f->visible;f->bilingual=!f->bilingual;
    f->page=f->blocked?f->ops->page(f->context,f->text_id,anchor?anchor:1,f->bilingual):0;
    f->page_hold=false;f->page_ready=STORY_NONE;f->presented=f->scroll_ready=false;
    story_flow_refresh(f);
}

#define SNAPSHOT_VALUES(X) \
 X(pc,0) X(now,1) X(chapter,2) X(line,3) X(commands,4) \
 X(text_id,5) X(literal_id,6) X(date_id,7) X(local,8) X(visible,9) \
 X(page,10) X(pause_index,11) X(page_ready,12) X(wait_started,13) X(wait_end,14) \
 X(presented_local,15) X(presented_visible,16) X(literal_start,17) X(literal_duration,18) \
 X(unlocks[0],19) X(unlocks[1],20)
#define SNAPSHOT_BOOLS(X) \
 X(window,0) X(quick_menu,1) X(blocked,2) X(waiting,3) X(hard,4) X(forever,5) \
 X(done,6) X(failed,7) X(quiet,8) X(text_active,9) X(source_auto,10) X(revealed,11) \
 X(page_hold,12) X(presented,13) X(paused,14) X(bilingual,15) X(automatic,16) X(skip,17) X(scrolling,18)

static void snapshot_read(const StoryFlow*base,const StoryFlowSnapshot*s,StoryFlow*out)
{
    *out=*base;
#define READ_VALUE(field,index) out->field=s->value[index];
    SNAPSHOT_VALUES(READ_VALUE)
#undef READ_VALUE
#define READ_BOOL(field,index) out->field=(s->flags&(1u<<index))!=0;
    SNAPSHOT_BOOLS(READ_BOOL)
#undef READ_BOOL
}
static bool record_was_shown(const StoryFlow*f,unsigned id,unsigned op)
{
    for(unsigned i=0;i<f->pc;i++)if(f->script->events[i].op==op&&(unsigned)f->script->events[i].a==id)return true;
    return false;
}
static bool snapshot_valid(const StoryFlow*base,const StoryFlowSnapshot*s,bool geometry)
{
    if(!base||!s||!base->script||!base->ops||!base->ops->select_text||!base->ops->info||
       !base->ops->pause||!base->ops->visible||!base->ops->limit||!base->ops->page||
       (s->flags&~STORY_FLOW_SNAPSHOT_FLAGS))return false;
    StoryFlow f;snapshot_read(base,s,&f);const StoryScript*script=f.script;
    if(!script->events||!script->chapters||!f.pc||f.pc>script->count||f.chapter>=script->chapter_count||
       f.commands!=f.pc||!f.blocked||!f.text_active||!f.window||!f.presented||f.waiting||f.hard||f.forever||
       f.done||f.failed||f.quiet||f.presented_local!=f.local||f.presented_visible!=f.visible||
       (f.unlocks[1]&~15u))return false;
    const StoryChapter*c=&script->chapters[f.chapter];const StoryEvent*e=&script->events[f.pc-1];
    if(f.pc-1<c->first_pc||f.pc>c->end_pc||e->op!=ST_TEXT||e->a<0||
       (unsigned)e->a!=f.text_id||f.text_id>=script->text_count||f.line!=e->line||
       f.source_auto!=((e->flags&1)!=0))return false;
    if(f.literal_id!=STORY_NONE&&(f.literal_id>=script->text_count||!f.literal_duration||
       !record_was_shown(&f,f.literal_id,ST_LITERAL)))return false;
    if(f.date_id!=STORY_NONE&&(f.date_id>=script->text_count||!record_was_shown(&f,f.date_id,ST_DATE)))return false;
    const SceneTextInfo*t=f.ops->info(f.context,f.text_id);
    if(!t){if(!f.ops->select_text(f.context,f.text_id,1))return false;t=f.ops->info(f.context,f.text_id);}
    if(!t||t->mode>=3||t->mode!=((e->flags>>1)&3)||t->source_line!=f.line||
       e->d<0||(unsigned)e->d!=t->duration_ticks||f.pause_index>t->pause_count||
       f.visible>t->char_count||f.page>=255||(geometry&&f.page>=(f.bilingual?t->pages_bi:t->pages_cn)))return false;
    if(f.pause_index){
        const SceneTextPause*previous=f.ops->pause(f.context,f.text_id,f.pause_index-1);
        if(!previous||f.local<previous->end_tick||f.visible<previous->glyphs)return false;
    }
    unsigned last=geometry?f.ops->limit(f.context,f.text_id,f.page,f.bilingual):t->char_count;
    if(last>t->char_count)return false;
    const SceneTextPause*p=f.revealed?NULL:f.ops->pause(f.context,f.text_id,f.pause_index);
    if(!f.revealed&&f.pause_index<t->pause_count&&!p)return false;
    if(p&&(p->start_tick>p->end_tick||p->glyphs>t->char_count))return false;
    unsigned raw=f.revealed?t->char_count:f.ops->visible(f.context,f.text_id,f.local);
    if(raw>t->char_count)return false;
    if(p&&raw>p->glyphs)raw=p->glyphs;
    if(raw>last)raw=last;
    if(geometry?raw!=f.visible:f.visible>raw)return false;
    if(f.revealed&&(f.local<t->duration_ticks||f.pause_index!=t->pause_count))return false;
    if(f.page_hold&&(f.page_ready==STORY_NONE||(geometry&&(f.page+1>=(f.bilingual?t->pages_bi:t->pages_cn)||f.visible<last||
       (p&&p->glyphs<=last)))))return false;
    if(f.page_ready!=STORY_NONE&&(int32_t)(f.now-f.page_ready)<0)return false;
    return true;
}
bool story_flow_snapshot_valid(const StoryFlow*base,const StoryFlowSnapshot*s){return snapshot_valid(base,s,true);}
bool story_flow_snapshot_source_valid(const StoryFlow*base,const StoryFlowSnapshot*s){return snapshot_valid(base,s,false);}
bool story_flow_snapshot_reflow(const StoryFlow*base,const StoryFlowSnapshot*s,unsigned anchor,StoryFlowSnapshot*out)
{
    if(!out||!story_flow_snapshot_source_valid(base,s)||!anchor||anchor>(s->value[STORY_FLOW_VISIBLE]?s->value[STORY_FLOW_VISIBLE]:1u))return false;
    StoryFlow f;snapshot_read(base,s,&f);
    f.page=f.ops->page(f.context,f.text_id,anchor,f.bilingual);
    f.page_hold=false;f.page_ready=STORY_NONE;
    story_flow_refresh(&f);
    /* Staging metadata only: original ACK equality was checked above. The
     * live restore below always clears ACK before any new view can be saved. */
    f.presented_local=f.local;f.presented_visible=f.visible;
    StoryFlowSnapshot result={{0},0};
#define REFLOW_VALUE(field,index) result.value[index]=f.field;
    SNAPSHOT_VALUES(REFLOW_VALUE)
#undef REFLOW_VALUE
#define REFLOW_BOOL(field,index) if(f.field)result.flags|=1u<<index;
    SNAPSHOT_BOOLS(REFLOW_BOOL)
#undef REFLOW_BOOL
    if(!story_flow_snapshot_valid(base,&result))return false;
    *out=result;return true;
}
bool story_flow_snapshot(const StoryFlow*f,StoryFlowSnapshot*out)
{
    if(!f||!out)return false;
    StoryFlowSnapshot s={{0},0};
#define WRITE_VALUE(field,index) s.value[index]=f->field;
    SNAPSHOT_VALUES(WRITE_VALUE)
#undef WRITE_VALUE
#define WRITE_BOOL(field,index) if(f->field)s.flags|=1u<<index;
    SNAPSHOT_BOOLS(WRITE_BOOL)
#undef WRITE_BOOL
    if(!story_flow_snapshot_valid(f,&s))return false;
    *out=s;return true;
}
bool story_flow_restore(StoryFlow*f,const StoryFlowSnapshot*s)
{
    if(!story_flow_snapshot_valid(f,s))return false;
    if(!f->ops->select_text(f->context,s->value[STORY_FLOW_TEXT_ID],0))return false;
    StoryFlow restored;snapshot_read(f,s,&restored);
    restored.presented=restored.scroll_ready=false;restored.presented_local=restored.presented_visible=0;
    *f=restored;return true;
}
#undef SNAPSHOT_VALUES
#undef SNAPSHOT_BOOLS
