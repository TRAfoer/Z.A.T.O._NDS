"""Compile the complete, passive source IR into a closed native instruction set.

Control conversion is useful independently of the graphics/text packs: every
source operation keeps its identity, and unknown syntax is an error, not a noop.
The final package compiler is added alongside the independent subsystem packs.
"""
from __future__ import annotations
import argparse,ast,json,math,re,struct,zlib,hashlib
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TRANSITIONS=dict(none=0,dissolve=1,fade=2,hpunch=3,vpunch=4,ease=5,
                 easeoutleft=6,easeinright=7,easeoutright=8,easeinleft=9)
OPS={name:i+1 for i,name in enumerate('TEXT SHOW HIDE SCENE WAIT TRANSITION MUSIC STOP_MUSIC SFX STOP_SFX WINDOW QUICK CLEAR LITERAL HIDE_LITERAL DATE UNLOCK JUMP END SPECIAL'.split())}
EVENT=struct.Struct('<HHI4i');CHAPTER=struct.Struct('<4I16s');HEADER=struct.Struct('<8s14I')

def ticks(seconds):
    if isinstance(seconds,bool):raise ValueError('Boolean duration')
    try:n=float(seconds)
    except (ValueError,TypeError) as exc:raise ValueError('Nonliteral duration') from exc
    if not math.isfinite(n) or n<0 or n>1_000_000:raise ValueError('Duration range')
    return math.ceil(n*60-1e-8)

def parse_transition(value):
    value='None' if value is None else str(value).strip()
    if value=='None':return dict(kind='transition',type=0,ticks=0)
    if value in TRANSITIONS:
        # Defaults come from the installed, read-only RenPy 00definitions.rpy.
        duration=.275 if value in ('hpunch','vpunch') else 1. if value=='fade' else .5
        return dict(kind='transition',type=TRANSITIONS[value],ticks=ticks(duration))
    match=re.fullmatch(r'(Dissolve|Pause)\s*\(\s*(\d+(?:\.\d*)?|\.\d+)\s*\)',value)
    if match:
        if match[1]=='Pause':return dict(kind='wait',ticks=ticks(match[2]),hard=False)
        return dict(kind='transition',type=1,ticks=ticks(match[2]))
    raise ValueError(f'Unknown transition: {value}')

def parse_control(value):
    value=value.strip()
    if value in ('window show','window hide','nvl show','nvl hide'):
        return dict(op='WINDOW',a=int(value.endswith('show')))
    if value=='nvl clear':return dict(op='CLEAR')
    m=re.fullmatch(r'(show|hide) screen (date[1-7])',value)
    if m:return dict(op='DATE',name=m[2],show=m[1]=='show')
    m=re.fullmatch(r'pause(?:\s+(\d+(?:\.\d*)?|\.\d+)|\s*\(\s*(\d+(?:\.\d*)?|\.\d+)\s*\))?',value)
    if m:return dict(op='WAIT',a=ticks(m[1] or m[2]) if m[1] or m[2] else -1,b=0)
    raise ValueError(f'Unknown control: {value}')

def parse_assignment(value):
    try:tree=ast.parse(value.strip())
    except SyntaxError as exc:raise ValueError('Invalid assignment') from exc
    if len(tree.body)!=1 or not isinstance(tree.body[0],ast.Assign):raise ValueError('Not one assignment')
    assign=tree.body[0]
    if len(assign.targets)!=1 or not isinstance(assign.value,ast.Constant) or type(assign.value.value) is not bool:
        raise ValueError('Not a literal bool assignment')
    target=assign.targets[0];state=int(assign.value.value)
    if isinstance(target,ast.Name) and target.id=='quick_menu':return dict(op='QUICK',a=state)
    if isinstance(target,ast.Attribute) and isinstance(target.value,ast.Name) and target.value.id=='persistent' and \
       (target.attr=='game_completed' or re.fullmatch(r'[A-Za-z_]\w*_unlocked',target.attr)):
        return dict(op='UNLOCK',name=target.attr,a=state)
    raise ValueError('Assignment target outside native whitelist')

def compile_controls(ir,audio):
    if audio.get('diagnostics'):raise ValueError('Audio IR has unresolved diagnostics')
    audio_by_key={(r['chapter'],r['source_event_index']):r for r in audio['cues']}
    bindings={};instructions={};unlocks=set();attached=0;counts=Counter();diagnostics=[]
    for chapter in ir['labels']:
        for e in chapter['events']:
            if not e.get('runtime_eligible',True):continue
            kind=e['kind'];id=e['source_event_id'];result=[]
            location=f"{e['source_file']}:{e['line']}"
            try:
                if kind=='say':
                    if e.get('with_') is not None:
                        attached+=1;parse_transition(e['with_'])
                    continue
                if kind in ('show','hide','scene'):continue
                counts[kind]+=1
                if kind=='command':
                    cue=audio_by_key.get((chapter['label'],e['source_index']))
                    if cue:
                        if cue.get('runtime_requirements'):raise ValueError(f"Audio requirement {cue['runtime_requirements']}")
                        args=cue['native_args'];music=cue['group']=='music'
                        if cue['op']=='stop':r=dict(op='STOP_MUSIC' if music else 'STOP_SFX',a=args['fade_frames'])
                        else:r=dict(op='MUSIC' if music else 'SFX',a=args['index'],b=args['gain_q12'],
                                    c=args['fade_frames'],d=int(cue['loop']))
                        result.append(r);bindings[str(id)]=cue['cue_id']
                    else:result.append(parse_control(e['value']))
                elif kind=='wait':result.append(dict(op='WAIT',a=ticks(e['seconds']) if e['seconds'] is not None else -1,b=int(e['hard'])))
                elif kind=='python':
                    r=parse_assignment(e['value']);result.append(r)
                    if r['op']=='UNLOCK':unlocks.add(r['name'])
                elif kind=='with':
                    r=parse_transition(e['expr'])
                    result.append(dict(op='WAIT',a=r['ticks'],b=0) if r['kind']=='wait' else dict(op='TRANSITION',a=r['type'],b=r['ticks']))
                elif kind=='jump':
                    if e['data'].get('expression'):raise ValueError('Dynamic jump')
                    result.append(dict(op='JUMP',target=e['data']['target']))
                elif kind=='return':
                    if e['data'].get('expression') is not None:raise ValueError('Return expression')
                    result.append(dict(op='END'))
                else:raise ValueError(f'Unknown event kind {kind}')
            except (ValueError,KeyError,TypeError) as exc:
                diagnostics.append(dict(source_event_id=id,location=location,kind=kind,message=str(exc)))
                continue
            instructions[str(id)]=result
    if diagnostics:
        raise ValueError(json.dumps(diagnostics,ensure_ascii=False,indent=2))
    if len(bindings)!=len(audio['cues']):raise ValueError('Audio/source coverage mismatch')
    return dict(version=1,instructions=instructions,counts=dict(counts),audio_bindings=bindings,
                unlocks=sorted(unlocks),attached_transitions=attached,diagnostics=[])

def special_range(e):
    return e['chapter']=='ep2d3' and (599<=e['line']<=687 or 792<=e['line']<=795)

def _group(layers):
    if 'hands' in layers:return 'hands'
    if 'iss' in layers:return 'iss'
    if 'windo' in layers:return 'window'
    if layers & {'ilooked','bodyshop'}:return 'mirror'
    if layers & {'carfix_red','ghost','tface','deface'}:return 'red'
    if 'carfix' in layers:return 'carfix'
    return 'black'

def compile_story(ir,controls,text,gfx,special):
    """All source nodes map to instructions or explicit promoted/coalesced rows.

    text: record_count, literal_event_ids[str(source_event_id)], date_ids.
    gfx: show_keys[chapter:line], tag_to_id. Special group IDs come from the
    frozen, independently verified compositor pack, never image-name guesses.
    """
    result=[];chapters=[];coverage=[];special_on=False;layers=set();last_group=None
    literal_ids=text.get('literal_event_ids',text.get('literal_source_event_id_to_id',{}))
    date_ids=text.get('date_ids',text.get('date_name_to_id',{}))
    groups=special['groups'];gids={g['name']:g['id'] for g in groups} if isinstance(groups,list) else {k:v['id'] for k,v in groups.items()}
    unlock_ids={name:i for i,name in enumerate(controls['unlocks'])}
    def emit(name,e,**args):
        row=dict(name=name,op=OPS[name],flags=0,line=e['line'],a=0,b=0,c=0,d=0,
                 source_event_id=e['source_event_id'],chapter=e['chapter'])
        row.update(args);result.append(row)
    def group(e):
        nonlocal last_group
        name=_group(layers)
        if name!=last_group:
            phase={'red':1,'hands':1,'mirror':2,'iss':3,'window':4}.get(name,0)
            reset=int(bool(phase) and not(last_group=='hands' and name=='red'))
            emit('SPECIAL',e,a=gids[name],b=phase,c=reset);last_group=name
    for chapter in ir['labels']:
        first_pc=len(result);text_ids=[]
        for e in chapter['events']:
            before=len(result);id=e['source_event_id'];kind=e['kind'];status='compiled'
            if not e.get('runtime_eligible',True):
                coverage.append(dict(source_event_id=id,status='promoted_init' if kind=='init' else 'unreachable_after_jump',pc=before,end_pc=before));continue
            use_special=special_range(e)
            if use_special and not special_on:
                special_on=True;layers.clear();last_group=None;group(e)
            elif not use_special and special_on:
                emit('SPECIAL',e,a=-1);special_on=False;last_group=None
            if kind=='say':
                tid=e['global_text_id'];text_ids.append(tid)
                tr=parse_transition(e.get('with_'))
                if tr['kind']!='transition':raise ValueError('Say attached Pause is unsupported')
                mode={'a':1,'n':2,'centered':3}.get(e['display']['who'],0)
                emit('TEXT',e,a=tid,b=tr['type'],c=tr['ticks'],d=e['display']['duration_ticks'],
                     flags=int(e['display']['automatic'] or not e.get('interact',True))|(mode<<1))
            elif kind in ('show','hide','scene'):
                if e.get('image')=='text':
                    if kind=='hide':emit('HIDE_LITERAL',e)
                    else:emit('LITERAL',e,a=int(literal_ids[str(id)]))
                elif use_special:
                    if kind=='scene':layers.clear()
                    if kind=='hide':layers.discard(e['image'])
                    else:layers.add(e['image'])
                    group(e)
                    if len(result)==before:status='precomposed_atomic_layer'
                else:
                    if kind=='scene':emit('SCENE',e)
                    if kind=='hide':emit('HIDE',e,a=gfx['tag_to_id'][e['image'].split()[0]])
                    else:
                        key=f"{chapter['module']}:{e['line']}"
                        g=gfx['show_keys'][key]
                        if g.get('dispatch',g.get('kind'))=='special':raise ValueError(f'Unrouted special image {key}')
                        pos=int(g['placement'])&0xffff;behind=int(g['behind_tag'])&0xffff
                        packed=pos|(behind<<16)
                        if packed>=0x80000000:packed-=0x100000000
                        emit('SHOW',e,a=g['image_id'],b=g['tag_id'],c=packed,d=g['program_id'])
            else:
                for raw in controls['instructions'][str(id)]:
                    r=dict(raw);op=r.pop('op')
                    if op=='UNLOCK':r=dict(a=unlock_ids[r['name']],b=r['a'])
                    elif op=='DATE':r=dict(a=date_ids[r['name']] if r['show'] else -1)
                    emit(op,e,**r)
            coverage.append(dict(source_event_id=id,status=status,pc=before,end_pc=len(result)))
        if special_on:raise ValueError('Special route crosses a chapter boundary')
        chapters.append(dict(name=chapter['label'],first_pc=first_pc,end_pc=len(result),
                             first_text=min(text_ids),text_count=len(text_ids)))
    by_label={c['name']:i for i,c in enumerate(chapters)}
    for row in result:
        if row['name']=='JUMP':
            index=by_label[row.pop('target')];row['a']=chapters[index]['first_pc'];row['b']=index
    if len([e for e in result if e['name']=='TEXT'])!=11951:raise ValueError('Full-story text coverage')
    if len(coverage)!=ir['summary']['event_count']:raise ValueError('Source coverage cardinality')
    return result,chapters,coverage

def encode(events,chapters,text_count,source_count):
    payload=b''.join(EVENT.pack(e['op'],e['flags'],e['line'],e['a'],e['b'],e['c'],e['d']) for e in events)
    co=HEADER.size+len(payload)
    payload+=b''.join(CHAPTER.pack(c['first_pc'],c['end_pc'],c['first_text'],c['text_count'],c['name'].encode().ljust(16,b'\0')) for c in chapters)
    return HEADER.pack(b'ZSTORY01',1,len(events),len(chapters),24,HEADER.size,co,HEADER.size+len(payload),zlib.crc32(payload),text_count,source_count,0,0,0,0)+payload

def main():
    p=argparse.ArgumentParser();p.add_argument('--controls-only',action='store_true');args=p.parse_args()
    ir=json.loads((ROOT/'artifacts/full_story_ir.json').read_text(encoding='utf-8'))
    audio=json.loads((ROOT/'artifacts/full_story_audio_cues.json').read_text(encoding='utf-8'))
    result=compile_controls(ir,audio)
    (ROOT/'artifacts/story_control_ir.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f"PASS closed controls: {len(result['instructions'])} source nodes, {len(result['audio_bindings'])} audio, {len(result['unlocks'])} unlocks, {result['attached_transitions']} attached transitions")
    if not args.controls_only:
        text=json.loads((ROOT/'artifacts/story_text_manifest.json').read_text(encoding='utf-8'))
        gfx=json.loads((ROOT/'artifacts/story_gfx_manifest.json').read_text(encoding='utf-8'))
        special=json.loads((ROOT/'artifacts/scene_gfx_manifest.json').read_text(encoding='utf-8'))
        events,chapters,coverage=compile_story(ir,result,text,gfx,special)
        data=encode(events,chapters,text['record_count'],ir['summary']['runtime_event_count'])
        path=ROOT/'nitrofs/story/script.bin';path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        report=dict(events=events,chapters=chapters,coverage=coverage,unlocks=result['unlocks'],bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        (ROOT/'artifacts/story_script_manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(f'PASS full native script: {len(events)} instructions, {len(chapters)} chapters, {len(data)}B')

if __name__=='__main__':main()
