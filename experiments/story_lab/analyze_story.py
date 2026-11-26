"""Read-only full-mainline text IR. This is data, not an executable Ren'Py VM.

Compiled scripts pass only through the repository's inert opcode simulator.
Python, init blocks, expressions and jump targets remain strings/symbolic data.
No original game file or frozen native scene package is ever written here.
"""
from pathlib import Path
from collections import Counter
import argparse
import ast
import hashlib
import json
import math
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'experiments/asset_lab'))
import anim_story as source
from anim_player import ScenePlayer,RichText

MODULES=('script','ep1',*(f'ep2d{i}' for i in range(1,8)),'ep3')


def ticks(seconds):return max(0,math.ceil(seconds*60-1e-8))
def canonical_sha(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()


def style_runs(glyphs):
    runs=[]
    for index,g in enumerate(glyphs):
        style=dict(color=list(g['color']),bold=g['bold'],italic=g['italic'],scale=g['scale'])
        if runs and all(runs[-1][key]==value for key,value in style.items()):runs[-1]['end']=index+1
        else:runs.append(dict(start=index,end=index+1,**style))
    return runs


def character_definitions(objects,speakers):
    definitions=[]
    for node in objects:
        state=source.attrs(node)
        if node.cls!='renpy.ast.Define' or state.get('varname') not in speakers:continue
        code=source._expression(state.get('code'))
        row=dict(variable=state['varname'],source_file=state['filename'],source_line=state['linenumber'],expression=code)
        try:
            value=ast.parse(code,mode='eval').body
            if isinstance(value,ast.Call) and isinstance(value.func,ast.Name) and value.func.id=='Character':
                if value.args and isinstance(value.args[0],ast.Constant) and isinstance(value.args[0].value,str):row['static_name']=value.args[0].value
                row['keyword_expressions']={k.arg:ast.get_source_segment(code,k.value) for k in value.keywords if k.arg}
        except (SyntaxError,TypeError):pass
        definitions.append(row)
    return definitions


def build_ir(game=source.DEFAULT_GAME):
    game=Path(game)
    labels=[];hashes={};say_count=0;event_count=0;with_counts=Counter();kinds=Counter();speakers=Counter()
    missing=[];text_diagnostics=Counter();jumps=[];all_objects=[];a_attrs=Counter()
    definitions=[];text_index=[];source_index={};runtime_count=unreachable_count=0
    cn_glyphs=en_glyphs=0
    player=ScenePlayer({'events':[]})
    for chapter_index,module in enumerate(MODULES):
        label_name='start' if module=='script' else module
        compiled=game/(module+'.rpyc');translation=game/'tl/chinese'/(module+'.rpy')
        for path in (compiled,translation):hashes['game/'+path.relative_to(game).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
        objects=source.read_compiled(compiled);all_objects.extend(objects)
        label_node=next(node for node in objects if node.cls=='renpy.ast.Label' and source.attrs(node).get('name')==label_name)
        block=source.attrs(label_node)['block']
        translated=source.parse_translations(translation.read_text(encoding='utf-8-sig'))
        matches=source._match_translations(block,translated)
        events=[];chapter_says=0;after_jump=False
        for index,node in enumerate(block):
            event=source._event(node,index)
            event.update(event_id=event_count,source_event_id=event_count,label=label_name,chapter=label_name,chapter_index=chapter_index,source_line=event['line'])
            event['source_key']=f'{event["source_file"]}:{event["line"]}:{index}'
            source_index.setdefault(f'{event["source_file"]}:{event["line"]}',[]).append(event_count)
            event['unreachable_after_jump']=after_jump
            event['runtime_eligible']=not after_jump and event['kind']!='init'
            if after_jump:unreachable_count+=1
            if event['runtime_eligible']:
                event['runtime_event_id']=runtime_count;runtime_count+=1
            event_count+=1;kinds[event['kind']]+=1
            if event['kind']=='init':
                ids=[]
                for child_index,child in enumerate(source.attrs(node).get('block',[])):
                    definition=source._event(child,child_index)
                    definition.update(definition_id=len(definitions),parent_source_event_id=event['source_event_id'],chapter=label_name,priority=source.attrs(node).get('priority'))
                    ids.append(len(definitions));definitions.append(definition)
                event['definition_ids']=ids
                event.get('data',{}).pop('block',None)
            elif event['kind']=='say':
                row=matches.get(index)
                if row:
                    for key in ('text','translation_id','translation_line','translation_source_line','translation_source_file'):event[key]=row[key]
                    if 'original_with_' in row:event['original_with_']=row['original_with_']
                    if 'translated_with_' in row:event['translated_with_']=row['translated_with_']
                else:missing.append(dict(file=event['source_file'],line=event['line'],source_index=index))
                event['say_id']=event['global_text_id']=say_count;say_count+=1;chapter_says+=1
                text_index.append(dict(global_text_id=event['global_text_id'],source_event_id=event['source_event_id'],chapter=label_name,source_file=event['source_file'],source_line=event['line'],translation_id=event['translation_id']))
                speakers[str(event['who'])]+=1
                if 'with_' in event:with_counts[str(event['with_'])]+=1
                if event['text'] is not None:
                    prior=player.display['raw'] if event['who']=='extend' and player.display else player.nvl_raw+'\n' if event['who']=='n' and player.nvl_raw else ''
                    new_start=len(RichText(prior).glyphs)
                    player._apply(event,replay=True)
                    display=player.display;plan=display['text'];english=RichText(display['original'],base_cps=0)
                    event['display']=dict(who=display['who'],attributes=display['attributes'],raw=display['raw'],original=display['original'],plain=plan.text,english_plain=english.text,
                        continuation=bool(prior),new_char_start=new_start,char_count=len(plan.glyphs),english_char_count=len(english.glyphs),duration_ticks=ticks(plan.duration),automatic=plan.automatic,
                        timeline_ticks=[ticks(t) for t in plan.times],pauses=[dict(start_tick=ticks(p['start']),end_tick=ticks(p['end']),glyphs=p['glyphs']) for p in plan.pauses],style_runs=style_runs(plan.glyphs))
                    cn_glyphs+=len(plan.glyphs);en_glyphs+=len(english.glyphs)
                    text_diagnostics.update(plan.diagnostics)
                    if display['who']=='a':a_attrs[' '.join(display['attributes'])]+=1
            elif event['kind']=='command' and event['value']=='nvl clear':player._apply(event,replay=True)
            elif event['kind']=='scene':
                # RenPy SceneLists.clear calls shown.predict_scene(master),
                # clearing hidden side-image attributes as well as shown tags.
                player._apply(event,replay=True);player.asya_attributes=[]
            elif event['kind']=='jump':
                state=source.attrs(node)
                event.update(target=source._expression(state['target']),expression=bool(state.get('expression')))
                jumps.append(dict(label=label_name,event_id=event['event_id'],source_file=event['source_file'],source_line=event['line'],target=event['target'],expression=event['expression']))
                if not event['expression']:after_jump=True
            events.append(event)
        labels.append(dict(module=module,label=label_name,source_file=source.attrs(label_node)['filename'],source_line=source.attrs(label_node)['linenumber'],event_count=len(events),say_count=chapter_says,events=events))
    label_names={label['label'] for label in labels}
    static_edges={edge['label']:edge['target'] for edge in jumps if not edge['expression']}
    chain=[];current='start'
    while current in label_names and current not in chain:
        chain.append(current)
        if current not in static_edges:break
        current=static_edges[current]
    frozen=source.build_story(game)
    frozen_says=[event for event in frozen['events'] if event['kind']=='say']
    result=dict(version=1,kind='passive_mainline_story_ir',method='inert pickletools opcode simulation; ast.literal_eval for strings only; no game pickle/Python/ATL execution',
        summary=dict(label_count=len(labels),event_count=event_count,runtime_event_count=runtime_count,promoted_definition_count=len(definitions),unreachable_event_count=unreachable_count,say_count=say_count,translated_count=say_count-len(missing),missing_translations=missing,event_kinds=dict(kinds),say_with_transitions=dict(sorted(with_counts.items())),source_speakers=dict(speakers),resolved_asya_attributes=dict(a_attrs),cumulative_chinese_glyphs=cn_glyphs,cumulative_english_glyphs=en_glyphs,richtext_diagnostics=dict(text_diagnostics)),
        flow=dict(entry='start',mainline_chain=chain,static_jumps=jumps,unresolved_static_targets=[edge for edge in jumps if not edge['expression'] and edge['target'] not in label_names],note='Only static AST Jump edges are resolved. Init/Python/user statements remain inert data and still need explicit engine support.'),
        context_semantics=dict(across_labels='One continuous passive dialogue/NVL/attribute context; Jump itself does not reset it.',scene='Scene clears master-layer image attributes, including the hidden Asya side-image tag; ep2d3 scenes603/605 therefore clear attrs before608.',nvl='Only authored nvl clear resets accumulated NVL.',source_evidence=['renpy/character.py resolve_say_attributes/handle_say_attributes and side-image attribute assignment','renpy/display/core.py SceneLists.clear → shown.predict_scene(layer)','renpy/display/image.py ShownImageInfo.predict_scene and choose_image','renpy/exports.py get_side_image']),
        speaker_definitions=character_definitions(all_objects,{speaker for speaker in speakers if speaker not in ('None','extend')}),
        source_sha256=hashes,frozen_ep2d3=dict(canonical_json_sha256=canonical_sha(frozen),say_count=len(frozen_says),translation_ids=[event['translation_id'] for event in frozen_says],source_sha256=frozen['source_sha256']),promoted_definitions=definitions,source_index=source_index,text_index=text_index,labels=labels)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game',type=Path,default=source.DEFAULT_GAME)
    parser.add_argument('--output',type=Path,default=ROOT/'nds/artifacts/full_story_ir.json')
    args=parser.parse_args()
    result=build_ir(args.game)
    if result['summary']['missing_translations']:raise ValueError(result['summary']['missing_translations'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(output=str(args.output),bytes=args.output.stat().st_size,summary=result['summary'],mainline_chain=result['flow']['mainline_chain'],frozen_ep2d3_sha256=result['frozen_ep2d3']['canonical_json_sha256']),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
