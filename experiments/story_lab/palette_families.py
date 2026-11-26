"""Read-only current-pack audit plus isolated15-content+gray family masters.

The only lossy redesigns here are the two user-selected library/vents families.
They reuse asset_model.common_indices (joint RGB-vector clustering), at the
already approved source crop/4x4 BOX framing. No existing asset/ROM is written.
"""
from pathlib import Path
from collections import defaultdict,deque
import argparse
import hashlib
import json
import math
import struct
import sys
import numpy as np
from PIL import Image,ImageDraw

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'nds/tools'))
sys.path.insert(0,str(ROOT/'experiments/asset_lab'))
from pack_assets import HEADER,BANK,FRAME,unlz10,lz10,fnv,BG_EDGE_COLOR
from asset_model import DEFAULT_GAME,common_indices,palette_bytes,tiled,untile,mask_tiles,unmask

GROUPS={'library':['library','library2','library3','library4','library5'],'vents':['vents','vents_red']}
DEST=ROOT/'experiments/story_lab/out/palette_families'


def sha(data):return hashlib.sha256(data).hexdigest()
def rgb_words(rgb):
    q=(np.asarray(rgb,dtype=np.uint16)*31+127)//255
    return (q[...,0]|q[...,1]<<5|q[...,2]<<10).astype('<u2')
def display_rgb(words):
    words=np.asarray(words,dtype=np.uint16)
    q=np.stack([words&31,(words>>5)&31,(words>>10)&31],axis=-1).astype(np.uint32)
    return ((q*255+15)//31).astype(np.uint8)


class CurrentPack:
    def __init__(self,path):
        self.path=Path(path);self.data=self.path.read_bytes();self.sha256=sha(self.data)
        h=HEADER.unpack_from(self.data);assert h[0]==b'ZATONDP1' and h[1]==1
        self.banks=[BANK.unpack_from(self.data,HEADER.size+i*BANK.size) for i in range(h[3])]
        self.frames=[FRAME.unpack_from(self.data,HEADER.size+h[3]*BANK.size+i*FRAME.size) for i in range(h[2])]
        self.names={f[-1].split(b'\0')[0].decode('utf-8'):i for i,f in enumerate(self.frames)}
        self.raw_banks={};self.cache={}
    def decode(self,id):
        if id in self.cache:return self.cache[id]
        f=self.frames[id];bank,at,packed,raw,w,h,cols,rows,x,y,category,fmt,hashv,palette,name=f
        if bank not in self.raw_banks:
            offset,n,raw_bytes,stride=self.banks[bank];decoded=unlz10(self.data[offset:offset+n]);assert len(decoded)==raw_bytes;self.raw_banks[bank]=decoded
        dictionary=self.raw_banks[bank];stride=self.banks[bank][3];mapping=unlz10(self.data[at:at+packed]);assert len(mapping)==raw
        ids=np.frombuffer(mapping,dtype='<u2');tiles=[dictionary[int(k)*stride:(int(k)+1)*stride] for k in ids]
        assert fmt==1 and stride==40,'palette audit only decodes indexed BG'
        index=untile([tile[:32] for tile in tiles],(h,w),4);mask=unmask([tile[32:] for tile in tiles],(h,w))
        colors=np.frombuffer(palette,dtype='<u2').copy();words=np.where(mask,colors[index]|0x8000,0).astype('<u2')
        assert fnv(words.tobytes())==hashv
        result=dict(id=id,name=name.split(b'\0')[0].decode(),width=w,height=h,x=x,y=y,indices=index,mask=mask,words=words,palette=colors,tiles=tiles)
        self.cache[id]=result;return result


def equivalent_classes(words):
    if not words or any(a.shape!=words[0].shape for a in words):return dict(same_shape=False,alpha_equal=False,minimum_classes=None,palette_only_exact=False)
    masks=[(a&0x8000)!=0 for a in words];alpha_equal=all(np.array_equal(masks[0],a) for a in masks)
    visible=np.logical_or.reduce(masks);vectors=np.stack(words,axis=-1)
    count=len(np.unique(vectors[visible],axis=0))
    gray=np.uint16(0x8000|0x2108);common_gray=bool(np.any(np.all(vectors==gray,axis=-1)&visible))
    return dict(same_shape=True,alpha_equal=alpha_equal,minimum_classes=count,palette_only_exact=alpha_equal and count<=16,
                exact_with_fixed_gray_slot=alpha_equal and count-int(common_gray)<=15,common_gray_class=common_gray,
                proof='Each distinct per-pixel tuple of decoded RGB15 colors across variants needs a separate common index, regardless of palette permutations or duplicate-slot merges.')


def exterior_edge(visible):
    transparent=np.pad(~visible,1,constant_values=True);outside=np.zeros_like(transparent);outside[0,0]=True;queue=deque([(0,0)])
    height,width=outside.shape
    while queue:
        y,x=queue.popleft()
        for dy in (-1,0,1):
            for dx in (-1,0,1):
                yy,xx=y+dy,x+dx
                if 0<=yy<height and 0<=xx<width and transparent[yy,xx] and not outside[yy,xx]:outside[yy,xx]=True;queue.append((yy,xx))
    neighbors=np.zeros_like(visible)
    for dy in (-1,0,1):
        for dx in (-1,0,1):neighbors|=outside[1+dy:1+dy+visible.shape[0],1+dx:1+dx+visible.shape[1]]
    return visible&neighbors


def source_box(frame,meta,game):
    path=Path(game)/frame['name']
    with Image.open(path) as opened:source=opened.convert('RGBA')
    crop=meta['source_crop']
    if crop is None:raise ValueError('selected family needs the approved visible-art crop')
    content=source.crop(tuple(crop));size=((content.width+3)//4,(content.height+3)//4)
    aligned=Image.new('RGBA',(size[0]*4,size[1]*4));aligned.paste(content,(0,0));box=np.asarray(aligned.resize(size,Image.Resampling.BOX))
    assert size==(frame['width'],frame['height'])
    assert np.array_equal(box[:,:,3]>=128,frame['mask'])
    return box,dict(path=str(path),source_sha256=sha(path.read_bytes()),source_size=list(source.size),crop=crop,box_size=list(size),partial_alpha_pixels=int(np.count_nonzero((box[:,:,3]>0)&(box[:,:,3]<255))))


def error_metrics(target,reference,mask):
    d=np.asarray(target,dtype=np.float64)[mask]-np.asarray(reference,dtype=np.float64)[mask]
    mse=float(np.mean(d*d)) if d.size else 0
    return dict(mae_rgb8=float(np.mean(np.abs(d))) if d.size else 0,rmse_rgb8=math.sqrt(mse),max_channel_error=int(np.max(np.abs(d))) if d.size else 0,psnr_db=99.0 if mse==0 else 10*math.log10(255*255/mse),pixels=int(mask.sum()))


def native_error_metrics(words,reference,mask):
    target=np.asarray(words,dtype=np.uint16)&0x7fff;source=rgb_words(reference)
    a=np.stack([target&31,(target>>5)&31,(target>>10)&31],axis=-1).astype(np.int16)
    b=np.stack([source&31,(source>>5)&31,(source>>10)&31],axis=-1).astype(np.int16)
    d=np.abs(a[mask]-b[mask])
    return dict(rgb15_changed_pixels=int(np.count_nonzero((target!=source)&mask)),
                mae_channel_5bit=float(d.mean()) if d.size else 0,
                max_channel_error_5bit=int(d.max()) if d.size else 0,pixels=int(mask.sum()))


def structure_metrics(a,b,mask):
    def gradients(rgb):
        gray=np.asarray(rgb,dtype=np.float64)@np.array([.299,.587,.114]);gy,gx=np.gradient(gray);return gx,gy,np.hypot(gx,gy)
    ax,ay,am=gradients(a);bx,by,bm=gradients(b)
    aa=am[mask];bb=bm[mask]
    correlation=float(np.corrcoef(aa,bb)[0,1]) if aa.std()>0 and bb.std()>0 else float(aa.std()==bb.std()==0)
    weight=(am*bm)[mask];direction=float(np.abs(ax*bx+ay*by)[mask].sum()/max(1e-12,weight.sum()))
    return dict(gradient_magnitude_correlation=correlation,absolute_gradient_orientation_agreement=direction,note='Auxiliary aligned-structure measures; not proof of identical objects/content. Source comparison sheets remain required.')


def family_storage(frames):
    dictionary=[];lookup={};maps=[]
    for tiles in frames:
        ids=[]
        for tile in tiles:
            if tile not in lookup:lookup[tile]=len(dictionary);dictionary.append(tile)
            ids.append(lookup[tile])
        maps.append(struct.pack('<'+'H'*len(ids),*ids))
    raw=b''.join(dictionary);unique_maps=list(dict.fromkeys(maps))
    return dict(tile_bytes=40,unique_tiles=len(dictionary),dictionary_raw_bytes=len(raw),dictionary_lz10_bytes=len(lz10(raw)),
                map_count=len(maps),distinct_maps=len(unique_maps),map_lz10_bytes_with_exact_reuse=sum(len(lz10(m)) for m in unique_maps),
                isolated_payload_with_reused_maps=len(lz10(raw))+sum(len(lz10(m)) for m in unique_maps),palette_bytes=32*len(frames),
                note='Isolated family estimate; actual whole-pack bank regrouping and alignment can change the exact savings. Frame metadata and per-frame palettes already exist in ZAT records.')


def compare_sheet(folder,key,names,boxes,old,new):
    width,height=boxes[0].shape[1],boxes[0].shape[0];row_h=height+22
    sheet=Image.new('RGB',(3*width,len(names)*row_h),(18,18,18));draw=ImageDraw.Draw(sheet)
    for row,name in enumerate(names):
        for column,(label,image) in enumerate((('Source BOX',Image.fromarray(boxes[row]).convert('RGB')),('Current independent16',Image.fromarray(old[row])),('New joint15 + gray',Image.fromarray(new[row])))):
            x=column*width;y=row*row_h;draw.text((x+4,y+5),f'{name} / {label}',fill='white');sheet.paste(image,(x,y+22))
    sheet.save(folder/f'{key}_comparison.png');sheet.resize((sheet.width*2,sheet.height*2),Image.Resampling.NEAREST).save(folder/f'{key}_comparison_2x.png')


def strict_scan(pack):
    groups=defaultdict(list);rows=[]
    for id,f in enumerate(pack.frames):
        if f[10]!=0:continue
        frame=pack.decode(id);words=frame['words'];mask=frame['mask'];visible=words[mask]
        _,first,inverse=np.unique(visible,return_index=True,return_inverse=True)
        order=np.argsort(first);remap=np.empty(len(first),dtype=np.uint8);remap[order]=np.arange(len(order),dtype=np.uint8)
        plane=np.zeros(words.shape,dtype=np.uint8);plane[mask]=remap[inverse]
        key=(frame['width'],frame['height'],frame['x'],frame['y'],sha(mask.tobytes()),sha(plane.tobytes()))
        groups[key].append(frame)
    for frames in groups.values():
        proof=equivalent_classes([f['words'] for f in frames]);rows.append(dict(frame_ids=[f['id'] for f in frames],names=[f['name'] for f in frames],equivalence=proof,
            grouping_basis='Exact palette-independent equality partition plus identical size,position,alpha; no filename grouping.',fixed_gray_compatible=proof['exact_with_fixed_gray_slot']))
    return dict(backgrounds=sum(len(row['frame_ids']) for row in rows),exact_partition_family_count=len(rows),multi_frame_families=[r for r in rows if len(r['frame_ids'])>1],singletons=[r['frame_ids'][0] for r in rows if len(r['frame_ids'])==1],
        note='Conservative scan: only equal color-class partitions are grouped; this permits arbitrary palette slot permutation but not collapsing a detailed scene into an unrelated flat fill. Non-bijective palette collapses are not auto-grouped.')


def build(output=DEST,scan_all=True,game=DEFAULT_GAME):
    output=Path(output).resolve();allowed=(ROOT/'experiments/story_lab/out').resolve()
    if not output.is_relative_to(allowed):raise ValueError('palette experiments must stay in the independent story_lab/out directory')
    output.mkdir(parents=True,exist_ok=True)
    pack=CurrentPack(ROOT/'nds/nitrofs/assets.zat');meta=json.loads((ROOT/'nds/artifacts/assets_manifest.json').read_text(encoding='utf-8'))['frames']
    report=dict(method='Read-only ZAT/LZ10 decode verified by each existing native frame hash. New masters reuse asset_model.common_indices over source BOX vectors with15 content classes, then append fixed gray slot15.',
        current_pack=dict(path=str(pack.path),bytes=len(pack.data),sha256=pack.sha256),existing={},new_families=[],notes=['These masters intentionally change quantized RGB to satisfy the selected family index rule. This is not an index-only lossless rewrite of current independent masters.','No existing pack/master/ROM is written. All outputs stay in this independent experiment directory.','Do not pass these RGB masters back through independent train_palette/encode: a new pack writer must consume the explicit common idx4/mask1/pal streams to retain slot semantics.'])
    for key,names in GROUPS.items():
        frames=[pack.decode(pack.names['images/bgs/'+name+'.png']) for name in names]
        assert len({(f['width'],f['height'],f['x'],f['y']) for f in frames})==1
        boxes=[];sources=[]
        for frame in frames:box,source=source_box(frame,meta[frame['id']],game);boxes.append(box);sources.append(source)
        assert all(np.array_equal(frames[0]['mask'],f['mask']) for f in frames)
        edges=[exterior_edge(f['mask']) for f in frames];assert all(np.array_equal(edges[0],e) for e in edges)
        edge=edges[0];mask=frames[0]['mask'];interior=mask&~edge
        existing=dict(frame_ids=[f['id'] for f in frames],names=names,size=[frames[0]['width'],frames[0]['height']],position=[frames[0]['x'],frames[0]['y']],sources=sources,
            equivalence=equivalent_classes([f['words'] for f in frames]),alpha_sha256=sha(mask.tobytes()),gray_edge_pixels=int(edge.sum()),
            different_index_pixels_from_first=[int(np.count_nonzero(f['indices']!=frames[0]['indices'])) for f in frames],
            source_structure_from_first=[structure_metrics(boxes[0][:,:,:3],box[:,:,:3],interior) for box in boxes])
        report['existing'][key]=existing
        training=[a.copy() for a in boxes]
        for a in training:a[edge,3]=0
        indices,palettes=common_indices(training,colors=15);indices[~mask]=0;indices[edge]=15
        palettes=[np.vstack([pal,np.array([BG_EDGE_COLOR],dtype=np.uint8)]) for pal in palettes]
        palwords=np.stack([np.frombuffer(palette_bytes(pal),dtype='<u2') for pal in palettes]);assert np.all(palwords[:,15]==0x2108)
        words=[np.where(mask,pal[indices]|0x8000,0).astype('<u2') for pal in palwords]
        folder=output/key;folder.mkdir(exist_ok=True)
        color_tiles=tiled(indices,4);alpha_tiles=mask_tiles(mask);tiles=[c+a for c,a in zip(color_tiles,alpha_tiles)]
        index_raw=b''.join(color_tiles);alpha_raw=b''.join(alpha_tiles)
        assert np.array_equal(untile([index_raw[i:i+32] for i in range(0,len(index_raw),32)],mask.shape),indices)
        assert np.array_equal(unmask([alpha_raw[i:i+8] for i in range(0,len(alpha_raw),8)],mask.shape),mask)
        (folder/'common.idx4').write_bytes(index_raw);(folder/'common.mask1').write_bytes(alpha_raw)
        arrays=folder/'family_arrays.npz';np.savez_compressed(arrays,indices=indices,mask=mask,edge=edge,palettes=palwords)
        variants=[];new_rgb=[];old_rgb=[]
        for i,name in enumerate(names):
            rgb=display_rgb(words[i]);rgba=np.dstack([rgb,mask.astype(np.uint8)*255]);rgba[~mask]=0
            master=folder/(name+'.png');Image.fromarray(rgba).save(master);(folder/(name+'.pal')).write_bytes(palwords[i].astype('<u2').tobytes())
            Image.fromarray(boxes[i]).save(folder/(name+'_source_box.png'));old=display_rgb(frames[i]['words']);Image.fromarray(np.dstack([old,mask.astype(np.uint8)*255])).save(folder/(name+'_current.png'))
            new_rgb.append(rgb);old_rgb.append(old)
            variants.append(dict(name=name,frame_id=frames[i]['id'],master=str(master),palette=str(folder/(name+'.pal')),rgba_sha256=sha(rgba.tobytes()),native_word_sha256=sha(words[i].tobytes()),
                rgb15_changed_from_current_pixels=int(np.count_nonzero(words[i]!=frames[i]['words'])),
                native_versus_source_box_interior=native_error_metrics(words[i],boxes[i][:,:,:3],interior),
                versus_source_box_visible=error_metrics(rgb,boxes[i][:,:,:3],mask),versus_source_box_interior=error_metrics(rgb,boxes[i][:,:,:3],interior),
                current_versus_source_box_interior=error_metrics(old,boxes[i][:,:,:3],interior),versus_current_interior=error_metrics(rgb,old,interior)))
        compare_sheet(output,key,names,boxes,old_rgb,new_rgb)
        family=dict(key=key,names=names,frame_ids=[f['id'] for f in frames],arrays=str(arrays),indices=str(folder/'common.idx4'),mask=str(folder/'common.mask1'),palette_slots=16,content_slots=15,gray_slot=15,gray_rgb=list(BG_EDGE_COLOR),
            shared_index_sha256=sha(index_raw),shared_mask_sha256=sha(alpha_raw),alpha_unchanged=True,index_and_mask_roundtrip=True,variants=variants,
            old_isolated_storage=family_storage([f['tiles'] for f in frames]),new_isolated_storage=family_storage([tiles for _ in frames]),comparison=str(output/f'{key}_comparison_2x.png'))
        report['new_families'].append(family)
    if scan_all:report['strict_other_background_scan']=strict_scan(pack)
    assert sha(pack.path.read_bytes())==pack.sha256,'read-only current asset package changed'
    report['result']='PASS isolated family construction / current policy mismatch proven'
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=DEST);parser.add_argument('--no-all-scan',action='store_true');args=parser.parse_args()
    report=build(args.output,scan_all=not args.no_all_scan)
    print(json.dumps(dict(result=report['result'],output=str(args.output),current_sha256=report['current_pack']['sha256'],families=[dict(key=f['key'],old=f['old_isolated_storage']['isolated_payload_with_reused_maps'],new=f['new_isolated_storage']['isolated_payload_with_reused_maps']) for f in report['new_families']],strict_other_background_scan=report.get('strict_other_background_scan')),ensure_ascii=False,indent=2))
