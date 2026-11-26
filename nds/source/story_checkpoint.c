#include "story_checkpoint.h"
#include "story_gfx.h"
#include "audio_demo.h"
#include "story_text.h"
#include <string.h>

_Static_assert(sizeof(StoryFlowSnapshot)==88,"flow semantic fields");
_Static_assert(sizeof(StoryGfxSnapshot)==324,"gfx semantic fields");
_Static_assert(sizeof(AudioSceneSnapshot)==112,"audio semantic fields");
_Static_assert(sizeof(StoryBackendSnapshot)==76,"backend semantic fields");

#define BASE_PAYLOAD_BYTES 600u
#define VIEW_PAYLOAD_BYTES 612u
#define LAYOUT_PAYLOAD_BYTES 616u
static const uint8_t magic[8]={'Z','A','T','C','K','P','0','1'};
static uint32_t read32(const uint8_t*p){return p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;}
static void write32(uint8_t*p,uint32_t n){p[0]=(uint8_t)n;p[1]=(uint8_t)(n>>8);p[2]=(uint8_t)(n>>16);p[3]=(uint8_t)(n>>24);}
static void put(uint8_t**p,uint32_t v){write32(*p,v);*p+=4;}
static uint32_t get(const uint8_t**p){uint32_t v=read32(*p);*p+=4;return v;}

static bool voice_syntax(const AudioVoiceSnapshot*v,bool music)
{
    if(v->enabled>1||v->loop>1||(v->flags&~3u))return false;
    if(!v->enabled)return !v->index&&!v->loop&&!v->offset_frames&&!v->gain_q12&&
        !v->fade_step&&!v->fade_target&&!v->fade_total&&!v->fade_left&&
        !v->fade_remainder&&!v->fade_error&&!v->flags;
    if(v->loop!=(unsigned)music||v->gain_q12<0||v->fade_target<0||v->fade_left>v->fade_total)return false;
    if(v->fade_total){if(v->fade_remainder>=v->fade_total||v->fade_error>=v->fade_total)return false;}
    else if(v->fade_left||v->fade_step||v->fade_remainder||v->fade_error||v->gain_q12!=v->fade_target)return false;
    return true;
}
static bool syntax(const StoryCheckpointDecoded*d)
{
    if(d->flow.flags&~STORY_FLOW_SNAPSHOT_FLAGS||d->gfx.count>STORY_SNAPSHOT_LAYERS||
       d->scroll>65535||d->scroll_manual>1||d->scroll_covered>d->flow.value[STORY_FLOW_VISIBLE])return false;
    for(unsigned i=d->gfx.count;i<STORY_SNAPSHOT_LAYERS;i++){
        const StoryGfxLayerSnapshot*l=&d->gfx.layers[i];
        if(l->image||l->tag||l->program||l->placement||l->start)return false;
    }
    if(d->audio.version!=1||d->audio.paused>1||d->audio.master_music>4096||d->audio.master_sfx>4096||
       !voice_syntax(&d->audio.music,true)||!voice_syntax(&d->audio.sfx,false))return false;
    const StoryBackendSnapshot*b=&d->backend;const StoryEvent*e=&b->music;
    if(b->special>1||b->have_music>1||b->group>=32||b->phase>4||b->transition>TR_EASEINLEFT||
       (!b->transition&&b->transition_duration)||e->flags)return false;
    if(b->have_music&&e->op!=ST_MUSIC)return false;
    if(e->op==ST_MUSIC){if(e->a<0||e->b<0||e->c<0||e->d!=1)return false;}
    else if(e->op||e->line||e->a||e->b||e->c||e->d)return false;
    return true;
}
static void write_voice(uint8_t**p,const AudioVoiceSnapshot*v)
{
    put(p,v->enabled);put(p,v->index);put(p,v->loop);put(p,v->offset_frames);
    put(p,(uint32_t)v->gain_q12);put(p,(uint32_t)v->fade_step);put(p,(uint32_t)v->fade_target);
    put(p,v->fade_total);put(p,v->fade_left);put(p,v->fade_remainder);put(p,v->fade_error);put(p,v->flags);
}
static void read_voice(const uint8_t**p,AudioVoiceSnapshot*v)
{
    v->enabled=get(p);v->index=get(p);v->loop=get(p);v->offset_frames=get(p);
    v->gain_q12=(int32_t)get(p);v->fade_step=(int32_t)get(p);v->fade_target=(int32_t)get(p);
    v->fade_total=get(p);v->fade_left=get(p);v->fade_remainder=get(p);v->fade_error=get(p);v->flags=get(p);
}
static void encode(const StoryCheckpointDecoded*d,bool view,uint8_t out[STORY_CHECKPOINT_BYTES])
{
    memset(out,0,STORY_CHECKPOINT_BYTES);memcpy(out,magic,sizeof(magic));
    write32(out+8,view?3:1);write32(out+12,view?LAYOUT_PAYLOAD_BYTES:BASE_PAYLOAD_BYTES);
    uint8_t*p=out+16;
    for(unsigned i=0;i<21;i++)put(&p,d->flow.value[i]);
    put(&p,d->flow.flags);put(&p,d->gfx.count);
    for(unsigned i=0;i<STORY_SNAPSHOT_LAYERS;i++){
        const StoryGfxLayerSnapshot*l=&d->gfx.layers[i];
        put(&p,l->image);put(&p,l->tag);put(&p,l->program);put(&p,(uint32_t)l->placement);put(&p,l->start);
    }
    put(&p,d->audio.version);put(&p,d->audio.paused);put(&p,d->audio.master_music);put(&p,d->audio.master_sfx);
    write_voice(&p,&d->audio.music);write_voice(&p,&d->audio.sfx);
    const StoryBackendSnapshot*b=&d->backend;put(&p,b->special);put(&p,b->group);put(&p,b->phase);
    for(unsigned i=0;i<5;i++)put(&p,b->origin[i]);
    put(&p,b->have_music);put(&p,b->music_tick);put(&p,b->transition);put(&p,b->transition_start);put(&p,b->transition_duration);
    /* StoryEvent's two u16 fields share a word on wire, independently of ABI. */
    put(&p,(uint32_t)b->music.op|((uint32_t)b->music.flags<<16));put(&p,b->music.line);
    put(&p,(uint32_t)b->music.a);put(&p,(uint32_t)b->music.b);put(&p,(uint32_t)b->music.c);put(&p,(uint32_t)b->music.d);
    if(view){put(&p,d->scroll);put(&p,d->scroll_manual);put(&p,d->scroll_covered);put(&p,d->layout_key);}
}
bool story_checkpoint_decode(const uint8_t in[STORY_CHECKPOINT_BYTES],StoryCheckpointDecoded*out)
{
    if(!in||!out||memcmp(in,magic,sizeof(magic)))return false;
    uint32_t version=read32(in+8),bytes=read32(in+12);
    if(!((version==1&&bytes==BASE_PAYLOAD_BYTES)||(version==2&&bytes==VIEW_PAYLOAD_BYTES)||(version==3&&bytes==LAYOUT_PAYLOAD_BYTES)))return false;
    for(unsigned i=16u+bytes;i<STORY_CHECKPOINT_BYTES;i++)if(in[i])return false;
    StoryCheckpointDecoded d={0};const uint8_t*p=in+16;
    for(unsigned i=0;i<21;i++)d.flow.value[i]=get(&p);
    d.flow.flags=get(&p);d.gfx.count=get(&p);
    for(unsigned i=0;i<STORY_SNAPSHOT_LAYERS;i++){
        StoryGfxLayerSnapshot*l=&d.gfx.layers[i];
        l->image=get(&p);l->tag=get(&p);l->program=get(&p);l->placement=(int32_t)get(&p);l->start=get(&p);
    }
    d.audio.version=get(&p);d.audio.paused=get(&p);d.audio.master_music=get(&p);d.audio.master_sfx=get(&p);
    read_voice(&p,&d.audio.music);read_voice(&p,&d.audio.sfx);
    StoryBackendSnapshot*b=&d.backend;b->special=get(&p);b->group=get(&p);b->phase=get(&p);
    for(unsigned i=0;i<5;i++)b->origin[i]=get(&p);
    b->have_music=get(&p);b->music_tick=get(&p);b->transition=get(&p);b->transition_start=get(&p);b->transition_duration=get(&p);
    uint32_t op=get(&p);b->music.op=(uint16_t)op;b->music.flags=(uint16_t)(op>>16);b->music.line=get(&p);
    b->music.a=(int32_t)get(&p);b->music.b=(int32_t)get(&p);b->music.c=(int32_t)get(&p);b->music.d=(int32_t)get(&p);
    if(version>=2){d.scroll=get(&p);d.scroll_manual=get(&p);d.scroll_covered=get(&p);}
    else d.scroll_covered=d.flow.value[STORY_FLOW_VISIBLE];
    if(version==3){d.layout_key=get(&p);if(!d.layout_key)return false;}
    if(p!=in+16u+bytes||!syntax(&d))return false;
    *out=d;return true;
}
static bool source_valid(const StoryFlow*f,const StoryCheckpointDecoded*d)
{
    if(!f||!d||!syntax(d)||!story_flow_snapshot_source_valid(f,&d->flow)||
       !story_gfx_snapshot_valid(&d->gfx)||!audio_scene_snapshot_valid(&d->audio)||d->backend.special)return false;
    uint32_t now=d->flow.value[STORY_FLOW_NOW];const StoryBackendSnapshot*b=&d->backend;
    if(b->transition_duration&&((int32_t)(now-b->transition_start)<0||now-b->transition_start<b->transition_duration))return false;
    if(b->have_music&&(int32_t)(now-b->music_tick)<0)return false;
    for(unsigned i=0;i<5;i++)if((int32_t)(now-b->origin[i])<0)return false;
    for(unsigned i=0;i<d->gfx.count;i++)if((int32_t)(now-d->gfx.layers[i].start)<0)return false;
    return true;
}
bool story_checkpoint_prepare(const StoryFlow*f,const StoryCheckpointDecoded*d,StoryCheckpointDecoded*out)
{
    if(!out||!source_valid(f,d))return false;
    uint32_t key=story_text_layout_key();if(!key)return false;
    StoryCheckpointDecoded result=*d;
    bool reflow=d->layout_key?d->layout_key!=key:!story_text_legacy_layout_compatible(d->flow.value[STORY_FLOW_TEXT_ID]);
    if(reflow){
        unsigned visible=d->flow.value[STORY_FLOW_VISIBLE];
        unsigned anchor=d->scroll_covered<visible?d->scroll_covered+1:(visible?visible:1u);
        if(!story_flow_snapshot_reflow(f,&d->flow,anchor,&result.flow))return false;
        visible=result.flow.value[STORY_FLOW_VISIBLE];
        if(result.scroll_covered>visible)result.scroll_covered=visible;
        result.scroll=story_text_scroll_for_visible(result.flow.value[STORY_FLOW_TEXT_ID],anchor,(result.flow.flags&STORY_FLOW_F_BILINGUAL)!=0);
        result.scroll_manual=0;
    }else if(!story_flow_snapshot_valid(f,&d->flow))return false;
    result.layout_key=key;*out=result;return true;
}
bool story_checkpoint_valid(const StoryFlow*f,const StoryCheckpointDecoded*d)
{StoryCheckpointDecoded prepared;return story_checkpoint_prepare(f,d,&prepared);}
static bool capture_point(const StoryFlow*f,const StoryBackendSnapshot*b,uint32_t scroll,bool manual,uint32_t covered,bool view,uint8_t out[STORY_CHECKPOINT_BYTES])
{
    if(!f||!b||!out)return false;
    StoryCheckpointDecoded d={0};d.backend=*b;d.scroll=scroll;d.scroll_manual=manual;
    d.layout_key=view?story_text_layout_key():0;
    if(!story_flow_snapshot(f,&d.flow)||!story_gfx_snapshot(&d.gfx)||!audio_scene_snapshot(&d.audio))return false;
    d.scroll_covered=view?covered:d.flow.value[STORY_FLOW_VISIBLE];
    if(!story_checkpoint_valid(f,&d))return false;
    encode(&d,view,out);return true;
}
bool story_checkpoint_capture(const StoryFlow*f,const StoryBackendSnapshot*b,uint8_t out[STORY_CHECKPOINT_BYTES])
{return capture_point(f,b,0,false,0,false,out);}
bool story_checkpoint_capture_view(const StoryFlow*f,const StoryBackendSnapshot*b,uint32_t scroll,bool manual,uint32_t covered,uint8_t out[STORY_CHECKPOINT_BYTES])
{return capture_point(f,b,scroll,manual,covered,true,out);}
bool story_checkpoint_restore(StoryFlow*f,const StoryCheckpointDecoded*d)
{
    StoryCheckpointDecoded prepared;if(!story_checkpoint_prepare(f,d,&prepared))return false;d=&prepared;
    AudioSceneSnapshot old_audio;
    if(!audio_scene_snapshot(&old_audio))return false;
    bool owned=!story_gfx_restore_pending();
    if(owned&&!story_gfx_restore_begin())return false;
    if(!audio_scene_restore(&d->audio)||!story_gfx_restore(&d->gfx)||!story_flow_restore(f,&d->flow)){
        if(owned)story_gfx_restore_rollback();
        (void)audio_scene_restore(&old_audio);return false;
    }
    if(owned)story_gfx_restore_commit();
    return true;
}
