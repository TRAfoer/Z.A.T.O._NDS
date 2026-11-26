#include "story_history.h"
#include <stdlib.h>
#include <string.h>
#define NONE UINT32_MAX

void story_history_close(StoryHistory*h){
    if(!h)return;
    free(h->starts);free(h->completed);memset(h,0,sizeof(*h));
}
void story_history_clear(StoryHistory*h){
    if(!h||!h->script)return;
    if(h->starts)memset(h->starts,255,(size_t)h->script->count*sizeof(*h->starts));
    if(h->completed)memset(h->completed,255,(size_t)h->script->text_count*sizeof(*h->completed));
}
bool story_history_open(StoryHistory*h,const StoryScript*s){
    if(!h)return false;
    memset(h,0,sizeof(*h));
    if(!s||!s->events||!s->count||!s->text_count)return false;
    h->starts=calloc(s->count,sizeof(*h->starts));
    if(!h->starts)return false;
    h->completed=calloc(s->text_count,sizeof(*h->completed));
    if(!h->completed){story_history_close(h);return false;}
    h->script=s;story_history_clear(h);return true;
}
void story_history_note_event(StoryHistory*h,uint32_t pc,uint32_t now,bool quiet){
    if(!quiet&&h&&h->script&&h->starts&&pc<h->script->count&&h->starts[pc]==NONE)h->starts[pc]=now;
}
void story_history_note_complete(StoryHistory*h,uint32_t pc,uint32_t now){
    if(!h||!h->script||!h->completed||pc>=h->script->count)return;
    const StoryEvent*e=&h->script->events[pc];
    if(e->op==ST_TEXT&&e->a>=0&&(uint32_t)e->a<h->script->text_count&&h->completed[e->a]==NONE)h->completed[e->a]=now;
}
static uint32_t add_ticks(uint32_t a,uint32_t b){return b>UINT32_MAX-a?UINT32_MAX:a+b;}
static uint32_t shift_tick(uint32_t tick,int64_t delta){
    int64_t value=(int64_t)tick+delta;
    return value<0?0:value>UINT32_MAX?UINT32_MAX:(uint32_t)value;
}
typedef struct {
    bool layers[STORY_SNAPSHOT_LAYERS],origins[5],literal,transition;
} Unknown;
/* Unknown source estimates are anchored once, not repeatedly slid along with
 * later reading dwell. Actual or already-anchored timestamps are untouched. */
static void anchor(StoryHistoryView*v,Unknown*u,int64_t delta){
    for(unsigned i=0;i<v->gfx.count;i++)if(u->layers[i])v->gfx.layers[i].start=shift_tick(v->gfx.layers[i].start,delta);
    for(unsigned i=0;i<5;i++)if(u->origins[i])v->backend.origin[i]=shift_tick(v->backend.origin[i],delta);
    if(u->literal&&v->literal_id!=NONE)v->literal_start=shift_tick(v->literal_start,delta);
    if(u->transition)v->backend.transition_start=shift_tick(v->backend.transition_start,delta);
    memset(u,0,sizeof(*u));
}
static void hide_tag(StoryHistoryView*v,Unknown*u,unsigned tag){
    for(unsigned i=0;i<v->gfx.count;i++)if(v->gfx.layers[i].tag==tag){
        unsigned n=--v->gfx.count-i;
        memmove(v->gfx.layers+i,v->gfx.layers+i+1,n*sizeof(v->gfx.layers[0]));
        memmove(u->layers+i,u->layers+i+1,n*sizeof(u->layers[0]));
        memset(v->gfx.layers+v->gfx.count,0,sizeof(v->gfx.layers[0]));u->layers[v->gfx.count]=false;break;
    }
}
static bool show_layer(StoryHistoryView*v,Unknown*u,const StoryEvent*e,uint32_t now,bool known){
    int place=(int16_t)((uint32_t)e->c&65535u),behind=(int16_t)((uint32_t)e->c>>16);
    if(e->a<0||e->b<0||e->b>65534||e->d<0||place< -1||place>2||behind< -1)return false;
    unsigned insertion=v->gfx.count;int previous=-1;
    for(unsigned i=0;i<v->gfx.count;i++){
        if(v->gfx.layers[i].tag==(unsigned)e->b)previous=(int)i;
        if(insertion==v->gfx.count&&(v->gfx.layers[i].tag==(unsigned)e->b||(behind>=0&&v->gfx.layers[i].tag==(unsigned)behind)))insertion=i;
    }
    if(place<0)place=previous>=0?v->gfx.layers[previous].placement:0;
    if(previous>=0)hide_tag(v,u,(unsigned)e->b);
    if(v->gfx.count>=STORY_SNAPSHOT_LAYERS)return false;
    if(insertion>v->gfx.count)insertion=v->gfx.count;
    unsigned n=v->gfx.count-insertion;
    memmove(v->gfx.layers+insertion+1,v->gfx.layers+insertion,n*sizeof(v->gfx.layers[0]));
    memmove(u->layers+insertion+1,u->layers+insertion,n*sizeof(u->layers[0]));
    v->gfx.layers[insertion]=(StoryGfxLayerSnapshot){.image=(unsigned)e->a,.tag=(unsigned)e->b,.program=(unsigned)e->d,.placement=place,.start=now};
    u->layers[insertion]=!known;v->gfx.count++;return true;
}
static uint32_t literal_duration(const StoryScript*s,uint32_t pc){
    uint64_t duration=0;
    for(uint32_t at=pc+1;at<s->count;at++){
        const StoryEvent*e=&s->events[at];
        if(e->op==ST_HIDE_LITERAL||e->op==ST_LITERAL||e->op==ST_TEXT||e->op==ST_END)break;
        if(e->op==ST_WAIT&&e->a>=0)duration+=(uint32_t)e->a;
        if(e->op==ST_TRANSITION&&e->b>=0)duration+=(uint32_t)e->b;
    }
    return duration>UINT32_MAX?UINT32_MAX:duration?(uint32_t)duration:60;
}
bool story_history_build(const StoryHistory*h,uint32_t target,StoryHistoryView*out){
    if(!h||!h->script||!h->starts||!h->completed||!out||target>=h->script->count)return false;
    const StoryScript*s=h->script;const StoryEvent*last=&s->events[target];
    if(last->op!=ST_TEXT||last->a<0||(uint32_t)last->a>=s->text_count)return false;
    StoryHistoryView v={.literal_id=NONE,.date_id=NONE};Unknown unknown={0};uint32_t clock=0;
    for(uint32_t pc=0;pc<=target;pc++){
        const StoryEvent*e=&s->events[pc];bool known=h->starts[pc]!=NONE;
        uint32_t now=known?h->starts[pc]:clock;
        if(known)anchor(&v,&unknown,(int64_t)now-clock);
        clock=now;
        switch(e->op){
        case ST_SHOW:if(!show_layer(&v,&unknown,e,now,known))return false;break;
        case ST_HIDE:if(e->a<0)return false;hide_tag(&v,&unknown,(unsigned)e->a);break;
        case ST_SCENE:memset(&v.gfx,0,sizeof(v.gfx));memset(unknown.layers,0,sizeof(unknown.layers));v.literal_id=NONE;unknown.literal=false;break;
        case ST_SPECIAL:
            if(e->a<0){v.backend.special=0;break;}
            if(e->b<0||e->b>=5)return false;
            if(!v.backend.special){memset(&v.gfx,0,sizeof(v.gfx));memset(unknown.layers,0,sizeof(unknown.layers));}
            v.backend.special=1;v.backend.group=(unsigned)e->a;v.backend.phase=(unsigned)e->b;
            if(e->c){v.backend.origin[e->b]=now;unknown.origins[e->b]=!known;}
            break;
        case ST_LITERAL:
            if(e->a<0)return false;
            v.literal_id=(unsigned)e->a;v.literal_start=now;v.literal_duration=literal_duration(s,pc);unknown.literal=!known;break;
        case ST_HIDE_LITERAL:v.literal_id=NONE;unknown.literal=false;break;
        case ST_DATE:v.date_id=e->a<0?NONE:(unsigned)e->a;break;
        case ST_TRANSITION:
            if(e->a<0||e->b<0)return false;
            v.backend.transition=(unsigned)e->a;v.backend.transition_start=now;v.backend.transition_duration=(unsigned)e->b;unknown.transition=!known;
            clock=add_ticks(clock,(uint32_t)e->b);break;
        case ST_WAIT:if(e->a>=0)clock=add_ticks(clock,(uint32_t)e->a);break;
        case ST_TEXT:
            if(e->a<0||(uint32_t)e->a>=s->text_count||e->d<0||e->c<0)return false;
            if(e->b!=TR_NONE){v.backend.transition=(unsigned)e->b;v.backend.transition_start=now;v.backend.transition_duration=(unsigned)e->c;unknown.transition=!known;}
            clock=add_ticks(clock,(uint32_t)e->d);
            if(pc==target){
                v.now=h->completed[e->a]!=NONE?h->completed[e->a]:clock;
                if(h->completed[e->a]!=NONE&&!known)anchor(&v,&unknown,(int64_t)v.now-clock);
            }
            break;
        /* Audio, unlocks, window, quick-menu and jumps have no scene/time
         * side effects here. The project uses a linear source target_pc. */
        default:break;
        }
    }
    *out=v;return true;
}
