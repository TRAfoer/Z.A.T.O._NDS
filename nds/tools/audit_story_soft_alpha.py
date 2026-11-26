"""Read-only source/legacy-pack soft-alpha diagnosis and selective-bank sizing.

Only diagnostic JSON/PNG under artifacts are written. Original game, existing
masters, packages, source manifests and ROMs stay unchanged.
"""
from pathlib import Path
from collections import Counter
import argparse,hashlib,json,math,sys,time
import numpy as np
from PIL import Image,ImageDraw

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'nds/tools'));sys.path.insert(0,str(ROOT/'experiments/asset_lab'))
from pack_assets import lz10,unlz10
from pack_story_assets import SourcePack
from asset_model import DEFAULT_GAME
from pixel_passes import remove_white_edge

SOFT_ACTORS=(330,375,407,451,452,453,454,455,456,459)
def sha(data):return hashlib.sha256(data).hexdigest()
def broad_alpha(alpha):
    soft=(alpha>0)&(alpha<255)
    deep=np.ones((max(0,alpha.shape[0]-4),max(0,alpha.shape[1]-4)),bool)
    if deep.size:
        for y in range(5):
            for x in range(5):deep&=soft[y:y+deep.shape[0],x:x+deep.shape[1]]
    return int(soft.sum()),int(deep.sum())
def soft_master(source):
    width,height=source.size;crop=min(height,572) if height>=700 else height
    size=(math.ceil(width/2),288 if height>=700 else math.ceil(crop/2))
    canvas=Image.new('RGBA',(size[0]*2,size[1]*2));canvas.paste(source.crop((0,0,width,crop)),(0,0));return canvas.resize(size,Image.Resampling.BOX)
def frame_rgba(pack,id):
    f=pack.frames[id];tiles=pack.tiles(id);width,height=f[4:6];cols,rows=f[6:8]
    alpha=None
    if f[11]==2:
        q=np.frombuffer(b''.join(tiles),dtype='u1').reshape(rows,cols,192)
        values=q[:,:,:128].copy().view('<u2').reshape(rows,cols,8,8).transpose(0,2,1,3).reshape(rows*8,cols*8)[:height,:width]
        alpha=q[:,:,128:].reshape(rows,cols,8,8).transpose(0,2,1,3).reshape(rows*8,cols*8)[:height,:width]
    elif not f[11]:values=np.frombuffer(b''.join(tiles),dtype='<u2').reshape(rows,cols,8,8).transpose(0,2,1,3).reshape(rows*8,cols*8)[:height,:width]
    else:
        q=np.frombuffer(b''.join(tiles),dtype='u1').reshape(rows,cols,40);pixels=np.empty((rows,cols,64),dtype='u1');pixels[:,:,::2]=q[:,:,:32]&15;pixels[:,:,1::2]=q[:,:,:32]>>4
        ids=pixels.reshape(rows,cols,8,8).transpose(0,2,1,3).reshape(rows*8,cols*8)[:height,:width]
        visible=np.unpackbits(q[:,:,32:],axis=2,bitorder='little').reshape(rows,cols,8,8).transpose(0,2,1,3).reshape(rows*8,cols*8)[:height,:width]
        palette=np.frombuffer(f[13],dtype='<u2');values=np.where(visible,palette[ids]|0x8000,0).astype('<u2')
    rgba=np.stack([((values>>shift)&31)*255//31 for shift in (0,5,10)]+[alpha if alpha is not None else (values>>15)*255],axis=2).astype('u1');return Image.fromarray(rgba)
def tile192(image):
    a=np.asarray(image);alpha=a[:,:,3];q=(a[:,:,:3].astype(np.uint16)*31+127)//255;words=np.where(alpha,q[:,:,0]|(q[:,:,1]<<5)|(q[:,:,2]<<10)|0x8000,0).astype('<u2')
    h,w=alpha.shape;words=np.pad(words,((0,(-h)%8),(0,(-w)%8)));alpha=np.pad(alpha,((0,(-h)%8),(0,(-w)%8)))
    return [words[y:y+8,x:x+8].tobytes()+alpha[y:y+8,x:x+8].tobytes() for y in range(0,words.shape[0],8) for x in range(0,words.shape[1],8)]
def composite(image,color):
    background=Image.new('RGBA',image.size,(*color,255));background.alpha_composite(image);return background.convert('RGB')
def sheet(entries,path):
    cellw=225;cellh=288;header=24;labels=['SOURCE BOX / BLACK','PACK NOW / BLACK','SOURCE BOX / WHITE','PACK NOW / WHITE','SOURCE BOX ALPHA','PACK NOW ALPHA']
    out=Image.new('RGB',(cellw*6,(cellh+header)*len(entries)+24),(30,30,30));d=ImageDraw.Draw(out);d.text((4,3),'DIAGNOSIS / SOURCE RGBA SOFTNESS VS CURRENT BINARY CONTOUR / NOT A ROM CAPTURE',fill='white')
    for row,(name,reference,current) in enumerate(entries):
        images=[composite(reference,(0,0,0)),composite(current,(0,0,0)),composite(reference,(255,255,255)),composite(current,(255,255,255)),reference.getchannel('A').convert('RGB'),current.getchannel('A').convert('RGB')]
        for col,(label,image) in enumerate(zip(labels,images)):
            x=col*cellw;y=24+row*(cellh+header);d.text((x+3,y+2),name+' '+label,fill='white');out.paste(image,(x,y+header))
    out.save(path)

def memory_audit(pack,catalog,new_banks,new_frames):
    """Mirror cache ownership, including the pre-batch retained tag snapshot.

    This reads inert compiled source and directory metadata only. It counts
    every frame of an ATL sequence, not merely the currently sampled frame.
    Heap figures use ARM's four-byte pointer size, not the host Python ABI.
    """
    import anim_story as source
    frames=pack.frames;images=catalog['images'];names=catalog['name_to_image_id']
    bypath={f[-1].split(b'\0')[0].decode():i for i,f in enumerate(frames)}
    dependencies={}
    for image in images:
        if image['kind']=='static':ids=[image['frame_id']]
        elif image['kind']=='sequence':ids=[names[row['image']] for row in image['frames']]
        elif image['kind']=='particle':ids=[bypath[image['texture']]]
        else:ids=[]
        dependencies[image['id']]=ids
    base_index=pack.header[3]*16+pack.header[2]*196+(pack.header[4]+pack.header[5])*2
    constants=len(images)*4+sum(row['count']*24 for row in images if row['kind']=='particle')
    base=base_index+catalog['bytes']+constants
    # Six new table records; old banks are kept byte-identical, including any
    # dictionary whose only old member becomes unreachable after replacement.
    new_base=base+len(new_banks)*16
    def usage(ids,new):
        used=sorted({f for image in ids for f in dependencies[image]});banks={}
        for id in used:
            key=('new',new_frames[id]['bank']) if new and id in new_frames else ('old',frames[id][0])
            banks[key]=new_banks[key[1]]['raw_bytes'] if key[0]=='new' else pack.banks[key[1]][2]
        maps=sum(frames[id][3] for id in used)
        return dict(total_bytes=(new_base if new else base)+sum(banks.values())+maps,
            bank_bytes=sum(banks.values()),map_bytes=maps,frames=used,
            banks=[dict(kind=k[0],id=k[1],raw_bytes=v) for k,v in sorted(banks.items())])
    individual=[]
    for id in sorted(new_frames):
        individual.append(dict(id=id,name=frames[id][-1].split(b'\0')[0].decode(),old=usage([id],False),new=usage([id],True)))
    lookup={row['key']:row for row in catalog['operations']};layers=[];snapshot=None
    maximum={};soft_states=[];count=Counter()
    def observe(kind,module,line,old=()):
        stable=[x['image'] for x in layers];union=[*stable,*(x['image'] for x in old)]
        scope='batch_union' if old else 'steady';count[scope]+=1
        row=dict(kind=kind,file=module,line=line,layers=[dict(x) for x in layers],
            old_layers=[dict(x) for x in old],old=usage(union,False),new=usage(union,True))
        for variant in ('old','new'):
            key=scope+'_'+variant
            if row[variant]['total_bytes']>maximum.get(key,{}).get(variant,{}).get('total_bytes',-1):maximum[key]=row
        if set(row['new']['frames'])&set(new_frames):soft_states.append(row)
    for module in ['script','ep1']+[f'ep2d{i}' for i in range(1,8)]+['ep3']:
        objects=source.read_compiled(DEFAULT_GAME/(module+'.rpyc'))
        block=next(source.attrs(x)['block'] for x in objects if x.cls=='renpy.ast.Label' and source.attrs(x).get('name')==('start' if module=='script' else module))
        for node in block:
            state=source.attrs(node);kind=node.cls.rsplit('.',1)[-1];line=state.get('linenumber',0)
            if module=='ep2d3' and (599<=line<=687 or 792<=line<=795):layers=[];snapshot=None;continue
            if kind in ('Scene','Show','Hide'):
                row=lookup.get(f'{module}:{line}')
                if row is None:continue
                if snapshot is None:snapshot=[dict(x) for x in layers]
                if row['clear']:layers=[]
                if row.get('literal'):observe(kind,module,line,snapshot);continue
                previous=next((x for x in layers if x['tag']==row['tag_id']),None)
                at=next((i for i,x in enumerate(layers) if x['tag'] in (row['tag_id'],row['behind_tag'])),len(layers))
                layers[:]=[x for x in layers if x['tag']!=row['tag_id']]
                if kind!='Hide':
                    place=row['placement'] if row['placement']>=0 else previous['placement'] if previous else 0
                    layers.insert(min(at,len(layers)),dict(image=row['image_id'],tag=row['tag_id'],placement=place,program=row['program_id']))
                observe(kind,module,line,snapshot)
            elif kind in ('Say','With') or kind=='UserStatement' and state['line'].startswith('pause') or kind=='Python' and 'pause' in str(source._expression(state.get('code'))):
                if kind=='With':observe(kind,module,line,snapshot or [])
                snapshot=None;observe(kind,module,line)
    blur=next(row['id'] for row in images if row['name']=='mublur')
    return dict(base_heap_old_bytes=base,base_heap_new_bytes=new_base,index_old_bytes=base_index,catalog_bytes=catalog['bytes'],
        particle_constants_bytes=constants,individual=individual,mublur=dict(old=usage([blur],False),new=usage([blur],True)),
        observed_checkpoints=dict(count),maximum=maximum,soft_source_states=soft_states,
        scope='Original main-label source visual operations, Say/pause and With; same-tag replacement, clear, sequence dependencies and retained pre-batch union. Heap excludes static .bss and libc allocator overhead, matching story_gfx resident().',
        normal_limit=1048576,transition_limit=1572864)
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT/'nds/artifacts/soft_alpha_diagnosis');parser.add_argument('--pack',type=Path,default=ROOT/'nds/nitrofs/assets.zat',help='Frozen binary-alpha baseline; original legacy pack by default');args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    started=time.monotonic();meta=json.loads((ROOT/'nds/artifacts/assets_manifest.json').read_text(encoding='utf8'));catalog=json.loads((ROOT/'nds/artifacts/story_gfx_manifest.json').read_text(encoding='utf8'))
    source=args.pack;pack=SourcePack(source);before=sha(pack.data);rows=[];targets=[];masters={};soft_tiles={}
    for f in meta['frames']:
        with Image.open(DEFAULT_GAME/f['name']) as opened:original=opened.convert('RGBA')
        alpha=np.asarray(original)[:,:,3];partial,broad=broad_alpha(alpha)
        if partial:rows.append(dict(id=f['id'],name=f['name'],category=f['category'],source_size=list(original.size),source_sha256=sha((DEFAULT_GAME/f['name']).read_bytes()),alpha_levels=int(len(np.unique(alpha))),partial_pixels=partial,partial_percent=round(partial/alpha.size*100,4),broad5x5_pixels=broad,broad_percent=round(broad/alpha.size*100,4)))
        if f['id'] not in SOFT_ACTORS:continue
        reference=soft_master(original);current=frame_rgba(pack,f['id']);assert reference.size==current.size==(f['width'],f['height'])
        clean,removed=remove_white_edge(original);cleaned=soft_master(clean);diff=np.count_nonzero(np.any(np.asarray(cleaned)!=np.asarray(reference),axis=2));a=np.asarray(reference);cur=np.asarray(current)
        targets.append(dict(id=f['id'],name=f['name'],width=f['width'],height=f['height'],legacy_bank=f['bank'],source_white_pixels_removed=int(removed.sum()),source_removal_changed_target_pixels=int(diff),box_partial_pixels=int(((a[:,:,3]>0)&(a[:,:,3]<255)).sum()),legacy_alpha_levels=np.unique(cur[:,:,3]).tolist(),legacy_white_pixels=int(((cur[:,:,:3]>=240).all(axis=2)&(cur[:,:,3]>0)).sum()),legacy_rgb15_palette_not_indexed=True))
        masters[f['id']]=reference;soft_tiles[f['id']]=tile192(reference)
        stem=Path(f['name']).stem;reference.save(args.output/(stem+'_reference_rgba.png'));current.save(args.output/(stem+'_current_pack.png'))
        composite(original,(255,255,255)).resize((original.width//2,original.height//2),Image.Resampling.BOX).save(args.output/(stem+'_original_full_white.png'))
    broad=[r for r in rows if r['broad5x5_pixels']>=64];assert sorted(r['id'] for r in broad if r['category']==2)==list(SOFT_ACTORS)
    banks=[];placements={}
    for folder in sorted({str(Path(meta['frames'][id]['name']).parent) for id in SOFT_ACTORS}):
        bank=None
        for id in SOFT_ACTORS:
            if str(Path(meta['frames'][id]['name']).parent)!=folder:continue
            tiles=soft_tiles[id];missing=set(tiles)-(set(bank['lookup']) if bank else set())
            if bank is None or (len(bank['lookup'])+len(missing))*192>262144:bank=dict(folder=folder,lookup={},tiles=[],members=[]);banks.append(bank)
            for tile in tiles:
                if tile not in bank['lookup']:bank['lookup'][tile]=len(bank['tiles']);bank['tiles'].append(tile)
            mapping=b''.join(int(bank['lookup'][tile]).to_bytes(2,'little') for tile in tiles);blob=lz10(mapping);assert unlz10(blob)==mapping;placements[id]=dict(bank=banks.index(bank),map_raw=len(mapping),map_lz10=len(blob));bank['members'].append(id)
    sizes=[]
    for index,bank in enumerate(banks):
        raw=b''.join(bank['tiles']);blob=lz10(raw);assert len(raw)<=262144 and unlz10(blob)==raw
        sizes.append(dict(id=index,members=bank['members'],raw_bytes=len(raw),lz10_bytes=len(blob),tile_count=len(bank['tiles']),folder=bank['folder']))
    uses=[r for r in catalog['operations'] if r.get('image_id') in set(SOFT_ACTORS)|{521}]
    for group,name in [(SOFT_ACTORS[:3]+(459,),'dark_masks_comparison.png'),(tuple(range(451,457)),'blur_comparison.png')]:sheet([(Path(meta['frames'][id]['name']).stem,masters[id],frame_rgba(pack,id)) for id in group],args.output/name)
    # Authored mublur occurs over the ordinary garages summer background.
    bg=frame_rgba(pack,160);frame=Image.new('RGBA',(256,192),(0,0,0,255));frame.alpha_composite(bg,(pack.frames[160][8],pack.frames[160][9]));goldens=[]
    for id in (451,453,452,456,455,454):
        old=frame.copy();old.alpha_composite(frame_rgba(pack,id),(pack.frames[id][8],pack.frames[id][9]));new=frame.copy();new.alpha_composite(masters[id],(pack.frames[id][8],pack.frames[id][9]));goldens.append((Path(meta['frames'][id]['name']).stem,old,new))
    contact=Image.new('RGB',(512,len(goldens)*212),(30,30,30));d=ImageDraw.Draw(contact)
    for i,(name,old,new) in enumerate(goldens):d.text((3,i*212+2),name+' CURRENT PACK / ORIGINAL SOFT BOX ON SOURCE BG160',fill='white');contact.paste(old.convert('RGB'),(0,i*212+20));contact.paste(new.convert('RGB'),(256,i*212+20))
    contact.save(args.output/'mublur_source_background_comparison.png')
    report=dict(result='DIAGNOSED_NOT_APPLIED',elapsed_seconds=round(time.monotonic()-started,3),source_package=str(source),source_package_sha256=before,all_frame_count=len(meta['frames']),any_partial_alpha_count=len(rows),broad_soft_alpha_count=len(broad),broad_soft_category_counts=dict(Counter(r['category'] for r in broad)),soft_actor_ids=list(SOFT_ACTORS),all_partial_assets=rows,broad_soft_assets=broad,targets=targets,source_show_operations=uses,soft_candidate_banks=sizes,soft_candidate_frames=placements,soft_candidate_lz10_payload_bytes=sum(b['lz10_bytes'] for b in sizes)+sum(r['map_lz10'] for r in placements.values()),mublur_candidate_resident_bank_bytes=sum(b['raw_bytes'] for b in sizes if any(id in b['members'] for id in range(451,457))),mublur_candidate_resident_map_bytes=sum(placements[id]['map_raw'] for id in range(451,457)),notes=['No canonical master/package/ROM modified.','Only ten actor images have broad soft alpha; other broad UI/BG sources are reported but excluded from this repair.','Proposed bank tile layout:128-byte straight RGB15 then64-byte alpha8, full192-byte dedup; existing geometry/2x2 framing retained.','Source mublur ep2d6:939 sequence order1,3,2,6,5,4 at0.2s each, interrupted0.5s raw-child fades; show ep2d6:1051 over garagessummerb1 (frame160). Not a separate fullscreen mask.'])
    report['memory']=memory_audit(pack,catalog,sizes,placements)
    assert sha(source.read_bytes())==before;target=args.output/'report.json';target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(json.dumps({k:report[k] for k in ('result','elapsed_seconds','all_frame_count','any_partial_alpha_count','broad_soft_alpha_count','soft_actor_ids','soft_candidate_lz10_payload_bytes','mublur_candidate_resident_bank_bytes','mublur_candidate_resident_map_bytes','soft_candidate_banks')},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
