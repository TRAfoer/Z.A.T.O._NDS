"""Compile a closed, inert ordinary-story graphics catalog over frozen assets.zat.

No images/audio are rewritten. Source basename names, game Position overrides,
13 ATL definitions, 7 SnowBlossom definitions and 27 inline programs are data.
Eight reviewed ep2d3 definitions deliberately dispatch to the separate scene pack.
"""
from pathlib import Path
import argparse,ast,hashlib,json,math,re,struct,sys,zlib

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'experiments/asset_lab'))
import anim_story as story

HEADER=struct.Struct('<8s14I');IMAGE=struct.Struct('<12i');SEQ=struct.Struct('<4I')
PROGRAM=struct.Struct('<12i');GEOMETRY=struct.Struct('<8i')
SPECIAL={'hands','crack','windo','ghost','tface','deface','ilooked','iss'}
NAMES=['script','ep1']+[f'ep2d{i}' for i in range(1,8)]+['ep3']
DEFAULT=(4096,0,65536,65536,0,32768,32768,32768,32768,1,-1,0)

def q(value):return round(float(value)*65536)
def placement_x(frame,source_width,placement):
    if placement==0:return frame['x']
    base=((1280+source_width if placement==1 else 3840-source_width)-10*frame['width'])//20
    if frame['category']!=2:return base
    spread=frame['x']+(base-frame['x'])*3//2
    return max((97-frame['width'])//2,min((416-frame['width'])//2,spread))
def fnv(value):
    h=2166136261
    for b in value:h=((h^b)*16777619)&0xffffffff
    return h

def program(props,at):
    known={'alpha','blend','zoom','zoom_easein','transitions','anchor','pos','xzoom','yalign'}
    if set(props)-known:raise ValueError(f'unknown graphics properties: {set(props)-known}')
    if set(at)-{'left','right','center','shaking'}:raise ValueError(f'unknown transforms: {at}')
    alpha=round(float(props.get('alpha',1))*4096)
    blend={'normal':0,'add':1,'multiply':2}[props.get('blend','normal')]
    zoom=float(props.get('zoom',1));tween=props.get('zoom_easein')
    zfrom=q(tween['from'] if tween else zoom);zto=q(tween['to'] if tween else zoom)
    duration=round(float(tween['duration'])*60) if tween else 0
    anchor=props.get('anchor',(.5,.5));pos=props.get('pos',(.5,.5))
    if any(not isinstance(v,(float,int)) for v in (*anchor,*pos)):raise ValueError('non-numeric anchor/position')
    flags=int('anchor' in props or 'pos' in props)
    if 'yalign' in props:flags|=2
    if 'shaking' in at:flags|=4
    flip=int(props.get('xzoom',1))
    if flip not in (-1,1) or not 0<=alpha<=4096 or min(zfrom,zto)<=0:raise ValueError('invalid closed transform')
    return (alpha,blend,zfrom,zto,duration,*map(q,anchor),*map(q,pos),flip,q(props['yalign']) if 'yalign' in props else -1,flags)

def build(game,assets,output,manifest,asset_manifest=None):
    frozen=json.loads((ROOT/'nds/artifacts/assets_manifest.json').read_text(encoding='utf8'))
    metadata=Path(asset_manifest) if asset_manifest else ROOT/'nds/artifacts/assets_manifest.json'
    selected=json.loads(metadata.read_text(encoding='utf8'))
    assets_bytes=assets.read_bytes()
    if hashlib.sha256(assets_bytes).hexdigest()!=selected['package_sha256']:raise ValueError('asset package hash mismatch')
    if len(selected['frames'])!=len(frozen['frames']):raise ValueError('asset frame count drift')
    identity=('id','name','width','height','x','y','category')
    from convert_story_soft_alpha import SOFT_ASSETS
    for before,after in zip(frozen['frames'],selected['frames']):
        if any(before[key]!=after[key] for key in identity):raise ValueError('asset frame ID/name/geometry/category drift')
        if before['format']!=after['format'] and not (after['id'] in SOFT_ASSETS and after['name']==SOFT_ASSETS[after['id']] and before['format']==(1 if after['id']==150 else 0) and after['format']==2):
            raise ValueError('unapproved asset alpha format change')
    if asset_manifest and selected['package_sha256']!=frozen['package_sha256'] and selected.get('source_package_sha256')!=frozen['package_sha256']:
        raise ValueError('replacement asset source hash mismatch')
    source=json.loads((ROOT/'experiments/asset_lab/out/full_asset_audit/report.json').read_text(encoding='utf8'))
    dimensions={x['asset']:x['source_size'] for x in source['assets']}
    frames=selected['frames'];paths={x['name']:x['id'] for x in frames};byname={}
    images=[];geometries=[];image_info=[]
    for f in frames:
        i=f['id'];images.append((0,i,1,0,0,0,0,0,0,0,0,0))
        byname[f['name'].lower()]=i
        if f['name'].startswith('images/'):byname[Path(f['name']).stem.lower()]=i
        sw,sh=dimensions[f['name']]
        soft_actor=f['format']==2 and f['category']==2
        scale=2 if soft_actor else 4 if f['master'] else 2 if f['category']==2 else 5
        fw,fh=sw/scale,sh/scale
        sceneh=192 if soft_actor else fh if f['master'] else 192 if f['category']==2 else 144
        geometries.append((sw,q(fw),q(fh),q(fw/2+f['x']-128),q(fh/2+f['y']-96),q(sceneh),q((192-sceneh)/2),0))
        image_info.append(dict(id=i,kind='static',name=f['name'],frame_id=i,source_width=sw,
                               center_x=f['x'],left_x=placement_x(f,sw,1),right_x=placement_x(f,sw,2),
                               source_projection_left_x=math.floor(64+sw/20-f['width']/2),
                               source_projection_right_x=math.floor(192-sw/20-f['width']/2)))
    all_objects={n:story.read_compiled(game/(n+'.rpyc')) for n in NAMES}
    definitions=[];sequences=[]
    for n,objects in all_objects.items():
        for node in objects:
            if node.cls!='renpy.ast.Image':continue
            state=story.attrs(node);name=' '.join(state['imgname']).lower();image_id=len(images);byname[name]=image_id
            info=dict(id=image_id,name=name,file=n,line=state['linenumber'])
            if name in SPECIAL:
                images.append((3,0,0,0,0,0,0,0,0,0,0,0));info.update(kind='special',atl=story._transparent(state['atl']))
            elif state.get('atl'):
                first=len(sequences);parts=story._sequence(node)
                transitions=[]
                for statement in story.attrs(state['atl'])['statements']:
                    for expression,transition in story.attrs(statement).get('expressions',[]):
                        ticks=0
                        if transition:
                            transition=str(story._expression(transition))
                            m=re.fullmatch(r'Dissolve\s*\(\s*([\d.]+)\s*\)',transition)
                            if transition=='dissolve':ticks=30
                            elif m:ticks=round(float(m[1])*60)
                            else:raise ValueError(f'unknown internal ATL transition {transition}')
                        transitions.append(ticks)
                for index,frame in enumerate(parts['frames']):
                    target=str(frame['image']).lower()
                    if target not in byname:raise ValueError(f'missing ATL frame {target}')
                    target_id=byname[target]
                    if images[target_id][0]!=0:raise ValueError('nested non-static sequence frame')
                    sequences.append((images[target_id][1],round(frame['duration']*60),transitions[index],0))
                images.append((1,first,len(parts['frames']),int(parts['repeat']),0,0,0,0,0,0,0,0))
                info.update(kind='sequence',first=first,frame_count=len(parts['frames']),loop=parts['repeat'],frames=parts['frames'],transition_ticks=transitions)
            else:
                expression=story._expression(state['code']);node=ast.parse(expression,mode='eval').body
                if not (isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='Fixed' and len(node.args)==1 and not node.keywords):raise ValueError('unknown image expression')
                particle=node.args[0]
                if not (isinstance(particle,ast.Call) and isinstance(particle.func,ast.Name) and particle.func.id=='SnowBlossom' and len(particle.args)==2):raise ValueError('unknown particle expression')
                texture,count=map(ast.literal_eval,particle.args);kw={x.arg:ast.literal_eval(x.value) for x in particle.keywords}
                if set(kw)-{'xspeed','yspeed','start'}:raise ValueError('unknown particle parameter')
                vx=sorted(kw['xspeed']);vy=sorted(kw['yspeed']);seed=fnv(name.encode())&0x7fffffff
                images.append((2,paths[texture],count,0,q(vx[0]/5),q(vx[1]/5),q(vy[0]/5),q(vy[1]/5),kw['start'],seed,0,0))
                info.update(kind='particle',texture=texture,count=count,xspeed_source=kw['xspeed'],yspeed_source=kw['yspeed'],start=kw['start'],seed=seed)
            definitions.append(info);image_info.append(info)
    programs=[DEFAULT];program_lookup={DEFAULT:0};tags={};show_keys={};index_keys={};operations=[];inline_count=0;placement_sources=[]
    def tag_id(value):
        value=str(value).lower()
        if value not in tags:tags[value]=len(tags)
        return tags[value]
    for n,objects in all_objects.items():
        for node in objects:
            if node.cls=='renpy.ast.Define':
                state=story.attrs(node)
                if state.get('varname') in ('left','right','center'):
                    placement_sources.append(dict(name=state['varname'],file=n,line=state['linenumber'],expression=story._expression(state['code'])))
        label=next(x for x in objects if x.cls=='renpy.ast.Label' and story.attrs(x).get('name')==('start' if n=='script' else n))
        for source_index,node in enumerate(story.attrs(label)['block']):
            kind=node.cls.rsplit('.',1)[-1].lower()
            if kind not in ('show','hide','scene'):continue
            state=story.attrs(node);spec=state['imspec'];parts=list(spec[0]);name=' '.join(parts).lower()
            row=dict(key=f'{n}:{state["linenumber"]}',file=n,line=state['linenumber'],source_index=source_index,kind=kind,clear=kind=='scene',name=name)
            if parts[:1]==['text']:row['literal']=True;operations.append(row);continue
            tag=spec[2] or parts[0];row['tag_id']=tag_id(tag)
            at=[str(story._expression(x)) for x in spec[3]];props=story._properties(state.get('atl'))
            try:values=program(props,at)
            except (ValueError,KeyError,TypeError) as error:raise ValueError(f'graphics program {n}:{state["linenumber"]}: {error}') from error
            if state.get('atl'):inline_count+=1
            if values not in program_lookup:program_lookup[values]=len(programs);programs.append(values)
            row.update(program_id=program_lookup[values],placement=next(({'center':0,'left':1,'right':2}[x] for x in at if x in ('center','left','right')),-1),behind_tag=tag_id(spec[6][0]) if spec[6] else -1)
            if len(spec[6])>1:raise ValueError('multiple behind targets need explicit catalog support')
            if kind!='hide':
                if name not in byname:
                    try:resolved=ast.literal_eval(name)
                    except (ValueError,SyntaxError):resolved=None
                    if not isinstance(resolved,str) or resolved.lower() not in byname:raise ValueError(f'unresolved source show {n}:{state["linenumber"]} {name}')
                    image_id=byname[resolved.lower()]
                else:image_id=byname[name]
                row.update(image_id=image_id,dispatch='special' if images[image_id][0]==3 else 'normal')
            show_keys[row['key']]=row;index_keys[f'{n}:{source_index}']=row['key'];operations.append(row)
    io=64;so=io+len(images)*48;po=so+len(sequences)*16;go=po+len(programs)*48
    payload=b''.join(IMAGE.pack(*x) for x in images)+b''.join(SEQ.pack(*x) for x in sequences)+b''.join(PROGRAM.pack(*x) for x in programs)+b''.join(GEOMETRY.pack(*x) for x in geometries)
    file=HEADER.pack(b'ZSTGFX01',1,64+len(payload),len(images),len(sequences),len(programs),len(frames),io,so,po,go,len(assets_bytes),zlib.crc32(payload),0,0)+payload
    output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(file)
    report=dict(format='ZSTGFX01',version=1,bytes=len(file),sha256=hashlib.sha256(file).hexdigest(),asset_pack_sha256=selected['package_sha256'],asset_pack_source=str(assets.resolve()),asset_pack_manifest=str(metadata.resolve()),
                image_count=len(images),static_frame_count=len(frames),named_definition_count=len(definitions),sequence_count=sum(x['kind']=='sequence' for x in definitions),special_definition_count=8,particle_definition_count=7,inline_program_operations=inline_count,
                name_to_image_id=byname,tag_to_id=tags,show_keys=show_keys,source_index_keys=index_keys,operations=operations,images=image_info,definitions=definitions,programs=[dict(id=i,values=list(x)) for i,x in enumerate(programs)],
                placement_sources=placement_sources,placement_policy='Game xalign .25/.5/.75 source projection is rounded first: left floor(64+source_width/20-width/2), right floor(192-source_width/20-width/2). Ordinary category2 actors only: packed_x+floor((projection_x-packed_x)*3/2), exact canvas center clamped to [48,208]. Center/explicit anchor programs retain their positions. Sprite dimensions unchanged.',
                actor_placement=dict(category=2,spread_numerator=3,spread_denominator=2,pivot='packed frame x + width/2',center_bounds=[48,208],rounding='floor source projection before signed floor displacement multiplication',standard_225px=dict(center=15,left=-47,right=78),unchanged=['center','explicit anchor programs','non-actor frames','particles','dedicated scene graphics','60x60 dialogue portrait']),
                source_sha256={n+'.rpyc':hashlib.sha256((game/(n+'.rpyc')).read_bytes()).hexdigest() for n in NAMES},
                memory_policy='Preload only active banks and every active sequence map. No compressed staging buffer, no draw/version file reads. 1MiB strict heap allocation cap. Clear normal caches before dedicated scene_gfx.',
                sampling='RGB15 nearest sampling of frozen masters; fixed-point cos easein; native SnowBlossom uses deterministic seeded scatter with original count/speed/start parameters.',
                atl_child_semantics='Verified installed renpy/atl.py Child.execute: old_child=trans.raw_child, transition uses old RAW child; advances without waiting for transition. Short mublur segments interrupt the previous fade; first child has no old image, repeated seam uses last raw frame.',
                precision=dict(zoom='Q16 quantized to 1/1024, sine quarter-wave LUT with 256 segments',alpha='Q12; raw-child animation fades use 16 native steps',sampling='Exact rational nearest-neighbor texel centers; no truncated inverse-scale drift'),
                particle_semantics='Installed SnowBlossom uses start as spawn-delay spread (50s), border50 source pixels and fast=False. Native deterministic seeded delays/speeds/lifetimes preserve these parameters, 1/5 stage at y24; random bitstream and per-respawn re-randomization are an explicit adaptation.',
                move_semantics='Per-tag merged scene list, static backgrounds unchanged. Move endpoints use the actor spacing policy once; _moveleft/_moveright factories start or end the target displayable fully outside DS viewport without the stable-position clamp. ease has no enter/leave factory; directional families use sine-in/cosine-out. Snapshot bank union kept until completion.',
                special_routes=[dict(file='ep2d3',first_line=599,last_line=687),dict(file='ep2d3',first_line=792,last_line=795)])
    manifest.parent.mkdir(parents=True,exist_ok=True);manifest.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--game',type=Path,default=story.DEFAULT_GAME);p.add_argument('--assets',type=Path,default=ROOT/'nds/nitrofs/assets.zat');p.add_argument('--asset-manifest',type=Path);p.add_argument('--output',type=Path,default=ROOT/'nds/nitrofs/story/gfx.bin');p.add_argument('--manifest',type=Path,default=ROOT/'nds/artifacts/story_gfx_manifest.json');a=p.parse_args()
    r=build(a.game,a.assets,a.output,a.manifest,a.asset_manifest);print(json.dumps({k:r[k] for k in ('bytes','image_count','named_definition_count','sequence_count','particle_definition_count','inline_program_operations')},indent=2))
if __name__=='__main__':main()
