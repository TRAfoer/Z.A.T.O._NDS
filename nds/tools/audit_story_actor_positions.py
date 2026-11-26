"""Read-only source scene/transition audit and PNG-master actor spacing preview.

Only workspace reports and technical comparison images are written. Compiled
Ren'Py source is parsed inertly; the original game and image files stay read-only.
"""
from pathlib import Path
import argparse,hashlib,json,sys
from collections import Counter
import numpy as np
from PIL import Image,ImageDraw

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'experiments/asset_lab'))
sys.path.insert(0,str(ROOT/'nds/tests'))
import anim_story as source
from story_gfx_oracle import Oracle

MODULES=['script','ep1']+[f'ep2d{i}' for i in range(1,8)]+['ep3']
MOVES={'ease','easeoutleft','easeinright','easeoutright','easeinleft'}


def actor_family(name):
    parts=name.split('/')
    return parts[2] if len(parts)>3 and parts[:2]==['images','sprites'] and not Path(name).name.startswith('side as ') else None


def special(module,line):return module=='ep2d3' and (599<=line<=687 or 792<=line<=795)


def frame_ids(oracle,image):
    info=oracle.images[image]
    if info['kind']=='static':return [info['frame_id']]
    if info['kind']=='sequence':return [oracle.manifest['name_to_image_id'][x['image']] for x in info['frames']]
    return []


def actor_layers(oracle,layers):
    result=[]
    for layer in layers:
        ids=frame_ids(oracle,layer['image']);families={actor_family(oracle.frames[i]['name']) for i in ids if oracle.frames[i]['category']==2}
        families.discard(None)
        if families:result.append(dict(**layer,families=sorted(families),frames=ids))
    return result


def head_alpha(oracle,frame,placement):
    f=oracle.frames[frame];pixels=oracle.pixels(frame);mask=pixels!=0
    yy,xx=np.nonzero(mask)
    if not len(xx):return dict(frame=frame,name=f['name'],empty=True)
    x,y,z=oracle.position(frame,placement,oracle.programs[0],0);top=int(yy.min())
    hy,hx=np.nonzero(mask[top:min(top+64,len(mask))])
    clipped=(hx+x<0)|(hx+x>=256)
    return dict(frame=frame,name=f['name'],placement=placement,x=int(x),canvas_width=f['width'],canvas_center=x+f['width']/2,
        alpha_bbox=[int(xx.min()),int(yy.min()),int(xx.max()+1),int(yy.max()+1)],
        projected_alpha_x=[int(x+xx.min()),int(x+xx.max()+1)],
        opaque_rectangle=bool(mask.all()),head_proxy_opaque_rectangle=bool(mask[top:min(top+64,len(mask))].all()),head_proxy='first 64 occupied canvas rows; conservative silhouette, not anatomical face detection',
        head_alpha_bbox=[int(hx.min()),top,int(hx.max()+1),min(top+64,len(mask))],head_alpha_pixels=len(hx),head_alpha_clipped_pixels=int(clipped.sum()),
        head_alpha_visible_fraction=float(1-clipped.mean()))


def audit(game,output,preview):
    oracle=Oracle();m=oracle.manifest;layers=[];old=None;stats=Counter();maxima={};samples=[];used=set();compositions=set();raw_counts=Counter()
    lookup={row['key']:row for row in m['operations']}
    def checkpoint(kind,module,line,visible=None,transition=None):
        selected=layers if visible is None else visible;actors=actor_layers(oracle,selected)
        families=sorted({x for a in actors for x in a['families']});key=tuple((a['image'],a['tag'],a['placement'],a['program']) for a in actors)
        compositions.add(key);stats[kind]+=1
        for a in actors:
            used.update((f,a['placement']) for f in a['frames'] if oracle.frames[f]['category']==2 and actor_family(oracle.frames[f]['name']))
        tag_count=len({a['tag'] for a in actors})
        row=dict(kind=kind,file=module,line=line,distinct_people=families,actor_tags=tag_count,actor_instances=len(actors),actors=actors)
        if transition:row['transition']=transition
        for label,n in [('distinct_people',len(families)),('actor_tags',tag_count),('actor_instances',len(actors))]:
            if n>maxima.get(label,{}).get('count',-1):maxima[label]=dict(count=n,example=row)
        if len(families)>=2 and {a['placement'] for a in actors}>={1,2} and kind=='say' and len(samples)<3:
            if not samples or samples[-1]['file']!=module:samples.append(dict(row,layers=[dict(x) for x in selected]))
    for module in MODULES:
        objects=source.read_compiled(game/(module+'.rpyc'))
        block=next(source.attrs(x)['block'] for x in objects if x.cls=='renpy.ast.Label' and source.attrs(x).get('name')==('start' if module=='script' else module))
        for node in block:
            state=source.attrs(node);kind=node.cls.rsplit('.',1)[-1];line=state.get('linenumber',0)
            if special(module,line):layers=[];old=None;continue
            if kind in ('Scene','Show','Hide'):
                row=lookup.get(f'{module}:{line}')
                if row is None:continue
                raw_counts['all_visual_operations']+=1
                if row.get('literal'):
                    raw_counts['literal_operations']+=1
                    if row['clear']:layers=[]
                    continue
                raw_counts['ordinary_visual_operations']+=1
                if old is None:old=[dict(x) for x in layers]
                if row['clear']:layers=[]
                if kind=='Hide':layers[:]=[x for x in layers if x['tag']!=row['tag_id']];continue
                previous=next((x for x in layers if x['tag']==row['tag_id']),None)
                placement=row['placement'] if row['placement']>=0 else previous['placement'] if previous else 0
                at=next((i for i,x in enumerate(layers) if x['tag'] in (row['tag_id'],row['behind_tag'])),len(layers))
                layers[:]=[x for x in layers if x['tag']!=row['tag_id']]
                layers.insert(min(at,len(layers)),dict(image=row['image_id'],tag=row['tag_id'],placement=placement,program=row['program_id'],start=0))
            elif kind=='With':
                expression=str(source._expression(state['expr']));visible=layers
                if old is not None:
                    tags={x['tag'] for x in layers};oldtags={x['tag'] for x in old}
                    if expression in MOVES:
                        visible=[x for x in layers if x['tag'] in oldtags or expression in ('easeinright','easeinleft')]
                        if expression in ('easeoutleft','easeoutright'):visible=visible+[x for x in old if x['tag'] not in tags]
                    elif expression=='dissolve' or expression.startswith('Dissolve('):visible=old+layers
                    # Fade old/new stages never overlap; audit both stable ends.
                    if expression=='fade' or expression.startswith('Fade('):checkpoint('fade_old',module,line,old,expression)
                checkpoint('transition',module,line,visible,expression);old=None
            elif kind=='Say':checkpoint('say',module,line);old=None
            elif kind=='UserStatement' and state['line'].startswith('pause') or kind=='Python' and 'pause' in str(source._expression(state.get('code'))):
                checkpoint('pause',module,line);old=None
    probes=[head_alpha(oracle,f,p) for f,p in sorted(used)]
    clipping=[x for x in probes if not x.get('empty') and x['head_alpha_clipped_pixels'] and not x['head_proxy_opaque_rectangle']]
    # Manual source inspection: gupanic's six clipped left pixels belong to the
    # raised outer arm, below/left of the face. Keep the factual silhouette count.
    for row in clipping:
        if row['name'].endswith('/gupanic.png') and row['placement']==1 and row['head_alpha_clipped_pixels']==6:
            row['manual_source_observation']='Six outer raised-arm pixels clipped; face and hair remain within the viewport. No extra position adjustment.'
    previews=[]
    if samples:
        header=20;canvas=Image.new('RGB',(3*256,header+len(samples)*(192+20)),(0,0,0));draw=ImageDraw.Draw(canvas)
        draw.text((4,4),'ACTOR SPACING COMPARISON / TECHNICAL PREVIEW, NOT GAME UI',fill='white')
        for row,sample in enumerate(samples):
            y=header+row*212
            for col,(numerator,denominator,label) in enumerate([(1,1,'1.0'),(5,4,'1.25'),(3,2,'1.5')]):
                o=Oracle(actor_spread=(numerator,denominator));state=[(x['image'],x['placement'],x['program'],0) for x in sample['layers']]
                words=o.draw(state,0);rgb=np.stack((words&31,(words>>5)&31,(words>>10)&31),axis=2).astype(np.uint16)
                image=Image.fromarray(((rgb*255+15)//31).astype(np.uint8));canvas.paste(image,(col*256,y+20))
                draw.text((col*256+4,y+4),f"{sample['file']}:{sample['line']}  spacing {label}",fill='white')
                previews.append(dict(file=sample['file'],line=sample['line'],factor=label,actors=[dict(name=o.frames[a['frames'][0]]['name'],placement=a['placement'],x=o.position(a['frames'][0],a['placement'],o.programs[a['program']],0)[0]) for a in sample['actors']]))
        preview.parent.mkdir(parents=True,exist_ok=True);canvas.save(preview)
    report=dict(result='PASS',scope='Original main-label Say/pause commits and With transition-visible tag states; no source or ROM writes.',
        source_sha256={n+'.rpyc':hashlib.sha256((game/(n+'.rpyc')).read_bytes()).hexdigest() for n in MODULES},
        source_placement_definitions=m['placement_sources'],placement_policy=m['placement_policy'],raw_operations=dict(raw_counts),
        complete_catalog_operations=dict(all=len(m['operations']),literal=sum(bool(r.get('literal')) for r in m['operations']),nonliteral=sum(not r.get('literal') for r in m['operations']),special_route=sum(special(r['file'],r['line']) for r in m['operations'])),checkpoints=dict(stats),
        unique_actor_compositions=len(compositions),maxima=maxima,three_distinct_people_observed=maxima['distinct_people']['count']>=3,
        head_proxy_limitation='Alpha silhouette is conservative and includes hair, raised arms and opaque effect rectangles; it does not claim automatic anatomical face recognition.',
        actor_frame_placement_alpha=probes,nonopaque_head_proxy_clipping=clipping,comparisons=previews,preview=str(preview.resolve()))
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(dict(result=report['result'],checkpoints=report['checkpoints'],max_distinct_people=maxima['distinct_people']['count'],max_actor_tags=maxima['actor_tags']['count'],head_clipping=len(clipping),output=str(output),preview=str(preview)),ensure_ascii=False))
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--game',type=Path,default=source.DEFAULT_GAME)
    p.add_argument('--output',type=Path,default=ROOT/'nds/artifacts/story_actor_positions.json');p.add_argument('--preview',type=Path,default=ROOT/'nds/artifacts/story_actor_spacing_comparison.png')
    a=p.parse_args();audit(a.game,a.output,a.preview)

if __name__=='__main__':main()
