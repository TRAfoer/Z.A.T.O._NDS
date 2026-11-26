"""Shot-reviewed, bounded palette improvement using the existing video format.

No runtime, source assets, audio or production files are modified. Candidate
families retain all original distinct colors and all actual caption pixels.
"""
from pathlib import Path
import argparse,hashlib,json,math,struct
import cv2
import numpy as np
from PIL import Image,ImageDraw
from experiments.video_lab import convert_palette as p,restore_op_text as text
from experiments.video_lab.lossless_packet_opt import optimize_packets

ROOT=p.ROOT
OUT=ROOT/'experiments/video_lab/out/deband'
PATTERN=(np.array([[0,8,2,10],[12,4,14,6],[3,11,1,9],[15,7,13,5]],np.float32)+.5)/16-.5

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,obj):Path(path).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
def lookup(palette):return text.neutral_lookup(palette)

def refill_palette(palette,frames):
    new=np.array(palette,copy=True);seen=set();slots=[]
    for i,c in enumerate(new&32767):
        if int(c) in seen:slots.append(i)
        seen.add(int(c))
    codes=np.arange(32768,dtype=np.uint16)
    xyz=np.stack([codes&31,(codes>>5)&31,codes>>10],axis=1).astype(np.int16)
    target=np.stack([new&31,(new>>5)&31,(new>>10)&31],axis=1).astype(np.int16)
    distance=np.full(32768,10000,np.int32)
    for c in target:distance=np.minimum(distance,np.sum((xyz-c)**2,axis=1,dtype=np.int32))
    counts=np.bincount((p.rgb555(frames)&32767).ravel(),minlength=32768)
    used=[]
    for slot in slots:
        code=int(np.argmax(counts*distance))
        if counts[code]*distance[code]==0:break
        new[slot]=code|0x8000;used.append(slot)
        distance=np.minimum(distance,np.sum((xyz-xyz[code])**2,axis=1,dtype=np.int32))
    return new,dict(duplicate_slots=slots,filled_slots=used)

def quantize(rgb,palette,lut,baseline,guard,strength):
    if strength:
        h,w=rgb.shape[:2];pattern=np.tile(PATTERN,((h+3)//4,(w+3)//4))[:h,:w]
        luma=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY).astype(np.float32)
        soft=cv2.GaussianBlur(luma,(3,3),0)
        gradient=np.maximum(np.abs(cv2.Sobel(soft,cv2.CV_32F,1,0,ksize=3)),np.abs(cv2.Sobel(soft,cv2.CV_32F,0,1,ksize=3)))/8
        amount=np.clip((luma-8)/24,0,1)*np.clip((16-gradient)/8,0,1)
        adjusted=np.clip(rgb.astype(np.float32)+(pattern*strength*amount)[:,:,None],0,255).astype(np.uint8)
    else:adjusted=rgb
    indices=lut[p.rgb555(adjusted)&32767];indices[guard]=baseline[guard]
    return indices

def assemble(template,parts,duration_us=None):
    count=sum(len(rows) for rows,palette,payload in parts);colors=template['colors']
    info=dict(template);info.update(frame_count=count,family_count=len(parts),duration_us=duration_us or template['duration_us'],
        index_offset=88,palette_offset=88+count*36,payload_offset=88+count*36+len(parts)*colors*2)
    position=info['payload_offset'];index=[];packets=[];first=0;maximum=0
    for family,(rows,palette,payloads) in enumerate(parts):
        for n,(row,packet) in enumerate(zip(rows,payloads)):
            index.append(p.ENTRY.pack(position,len(packet),row['decoded'],row['flags'],row['key']+first,
                family,(first+n)*22050//info['fps'],row['hash'],0))
            packets.append(packet);position+=len(packet);maximum=max(maximum,len(packet))
        first+=len(rows)
    info.update(file_bytes=position,max_packed=maximum)
    return p.HEADER.pack(b'ZATPAL01',*(info[k] for k in p.FIELDS))+b''.join(index)+b''.join(x[1] for x in parts)+b''.join(packets)

def split_families(data):
    info=p.inspect_pack(data);parts=[];step=info['fps']*2
    for family,start in enumerate(range(0,info['frame_count'],step)):
        end=min(start+step,info['frame_count']);rows=[dict(r,key=r['key']-start) for r in info['index'][start:end]]
        assert rows[0]['flags']&p.KEY
        palette=data[info['palette_offset']+family*info['colors']*2:info['palette_offset']+(family+1)*info['colors']*2]
        payloads=[data[r['offset']:r['offset']+r['length']] for r in info['index'][start:end]]
        duration=min(info['duration_us']-start*1000000//info['fps'],(end-start)*1000000//info['fps'])
        parts.append(assemble(info,[(rows,palette,payloads)],duration))
    return parts

def stitch(packs,template):
    parts=[]
    for data in packs:
        info=p.inspect_pack(data);assert info['family_count']==1
        parts.append((info['index'],data[info['palette_offset']:info['payload_offset']],
            [data[r['offset']:r['offset']+r['length']] for r in info['index']]))
    return assemble(template,parts)

def choose(options,limit):
    # Multiple-choice knapsack, 64-byte conservative cost buckets. Neighboring
    # families in the same continuous portrait use the same treatment profile.
    groups=[];at=0
    while at<len(options):
        ids=[at,at+1] if at in (17,26) and at+1<len(options) else [at]
        choices=[]
        for profile in range(4):
            choices.append(dict(ids=ids,profile=profile,size=sum(options[i][profile]['bytes'] for i in ids),
                loss=sum(options[i][profile]['loss'] for i in ids)))
        best_base=choices[0]['loss']
        choices=[x for x in choices if x['profile']==0 or x['loss']<=best_base]
        floor=min(x['size'] for x in choices)
        for x in choices:x['cost']=math.ceil((x['size']-floor)/64)
        groups.append((floor,choices));at+=len(ids)
    available=(limit-sum(x[0] for x in groups))//64
    if available<0:raise ValueError('Even the lossless baseline exceeds the movie allowance')
    dp={0:(0.,[])}
    for floor,variants in groups:
        next_dp={}
        for used,(quality,path) in dp.items():
            for v in variants:
                cost=used+v['cost']
                if cost>available:continue
                score=quality-v['loss']
                if cost not in next_dp or score>next_dp[cost][0]:next_dp[cost]=(score,path+[v])
        # Remove dominated states while retaining exact backpointers.
        best=-float('inf');dp={}
        for cost,(score,path) in sorted(next_dp.items()):
            if score>best:dp[cost]=(score,path);best=score
    selected=max(dp.values(),key=lambda x:x[0])[1];profiles={}
    for group in selected:
        for i in group['ids']:profiles[i]=group['profile']
    return [profiles[i] for i in range(len(options))]

def build(out=OUT,max_delta_bytes=2048):
    out.mkdir(parents=True,exist_ok=True);cache=out/'families';cache.mkdir(exist_ok=True)
    manifest=json.loads((ROOT/'experiments/video_lab/out/movie_manifest.json').read_text(encoding='utf8'))
    movie=Path(manifest['embedded_files']['movie/op.zpv']);data=movie.read_bytes();info=p.inspect_pack(data)
    assert info['colors']==64 and info['frame_count']==1746
    base_rom=ROOT/'Z.A.T.O.nds';initial_sha=sha(base_rom)
    rgb=np.load(text.OUT/'restored_rgb.npy',mmap_mode='r');reference=np.load(text.OUT/'source_rgb.npy',mmap_mode='r')
    baseline=np.load(text.OUT/'decoded_rgb.npy',mmap_mode='r')
    cues=text.prepare_cues(json.loads(text.CUES.read_text(encoding='utf8')),reference,text.BitmapText())
    original_parts=split_families(data);assert stitch(original_parts,info)==data
    options=[];profiles=('current','refill','bayer4','bayer8');cuts=[0];previous=None
    protected_counts=[]
    encoder=p.Encoder(out/'encoder')
    identity=hashlib.sha256((sha(__file__)+sha(ROOT/'experiments/video_lab/lossless_packet_opt.py')+sha(movie)+str(max_delta_bytes)).encode()).hexdigest()
    try:
        for family,original in enumerate(original_parts):
            start=family*60;end=min(start+60,len(rgb));frames=rgb[start:end];masks=[];base_indices=[];goals=[];quality_masks=[]
            pi=p.inspect_pack(original);old=np.frombuffer(original,dtype='<u2',count=64,offset=pi['palette_offset']).copy()
            old_lut=lookup(old)
            for n in range(start,end):
                frame=rgb[n];mask=np.zeros((144,256),np.uint8)
                for cue in cues:
                    if cue['start']<=n<cue['end']:
                        ink,trail=text.caption_ink(cue,n);g=((ink>0)|(trail>0)).astype(np.uint8)
                        if cue.get('outline'):g=cv2.dilate(g,np.ones((3,3),np.uint8))
                        mask|=g
                guard=(mask>0)|(frame.max(axis=2)==0)|(frame.min(axis=2)==255);masks.append(guard)
                protected_counts.append(int((mask>0).sum()))
                indices=old_lut[p.rgb555(frame)&32767];base_indices.append(indices)
                np.testing.assert_array_equal(p.rgb_from_555(old[indices]),baseline[n])
                goal=cv2.GaussianBlur(frame.astype(np.float32),(5,5),1);goals.append(goal)
                quality_masks.append((~guard)&(frame.max(axis=2)>12))
                small=cv2.resize(frame,(32,18),interpolation=cv2.INTER_AREA).astype(np.int16)
                if previous is not None and np.abs(small-previous).mean()>18:cuts.append(n)
                previous=small
            new,pmeta=refill_palette(old,frames);new_lut=lookup(new);family_options=[]
            for profile,name in enumerate(profiles):
                file=cache/f'{family:02}_{name}.zpv';meta_file=file.with_suffix('.json')
                if file.exists() and meta_file.exists() and json.loads(meta_file.read_text())['identity']==identity:
                    meta=json.loads(meta_file.read_text());family_options.append(meta);continue
                loss=0.;pixel_loss=0.;tiles=[]
                for local,n in enumerate(range(start,end)):
                    idx=base_indices[local] if profile==0 else quantize(rgb[n],new,new_lut,base_indices[local],masks[local],(0,0,4,8)[profile])
                    palette=old if profile==0 else new;decoded=p.rgb_from_555(palette[idx]);guard=masks[local]
                    np.testing.assert_array_equal(decoded[guard],baseline[n][guard],err_msg=f'Protected caption/black changed at{n}')
                    blurred=cv2.GaussianBlur(decoded.astype(np.float32),(5,5),1);area=quality_masks[local]
                    error=(goals[local]-blurred)**2;direct=(rgb[n].astype(np.float32)-decoded.astype(np.float32))**2
                    loss+=float(error[area].sum()+.05*direct[area].sum());pixel_loss+=float(direct[area].sum())
                    tiles.append(p.tile_bytes(idx,64))
                if profile==0:pack=original
                else:
                    pack,_=p.build_pack(tiles,[new],colors=64,fps=30,duration_us=pi['duration_us'],encoder=encoder)
                    fast_keys={n-start:tiles[n-start] for n in {1261,*range(1320,1365)} if start<=n<end}
                    fast_ranges=[(max(a,start)-start,min(b,end)-start) for a,b in [(1261,1262),(1320,1365),(1413,1443)] if a<end and b>start]
                    pack,_=text.fast_packets(pack,fast_ranges,key_frames=fast_keys)
                pack,opt=optimize_packets(pack,max_delta_bytes=max_delta_bytes)
                file.write_bytes(pack)
                meta=dict(identity=identity,family=family,start=start,end=end,profile=name,bytes=len(pack),loss=loss,
                    direct_squared_error=pixel_loss,lossless_saved=opt['saved_bytes'],palette=pmeta if profile else None,path=str(file))
                write(meta_file,meta);family_options.append(meta)
            options.append(family_options)
            print(f'family{family:02}: '+', '.join(f'{x["profile"]} {x["bytes"]}B/{x["loss"]:.0f}' for x in family_options),flush=True)
    finally:encoder.close()
    limit=len(data)+((67108864-1-base_rom.stat().st_size)//512)*512
    # Each stand-alone family has its own88-byte header; global has just one.
    part_limit=limit+(len(options)-1)*88
    selected=choose(options,part_limit)
    entries=[options[i][j] for i,j in enumerate(selected)];packs=[Path(x['path']).read_bytes() for x in entries]
    candidate=stitch(packs,info);assert len(candidate)<=limit
    target=out/'op_deband.zpv';target.write_bytes(candidate)
    enc=p.Encoder(out/'native')
    try:check=p.verify_native(enc.executable,target,out/'native_samples')
    finally:enc.close()
    write(out/'selection.json',dict(base_rom=str(base_rom),base_rom_bytes=base_rom.stat().st_size,base_rom_sha256=initial_sha,
        source_movie=str(movie),source_movie_sha256=sha(movie),source_video_bytes=len(data),candidate_video_bytes=len(candidate),
        max_movie_bytes=limit,video_delta=len(candidate)-len(data),max_delta_bytes=max_delta_bytes,
        selected=entries,options=options,detected_cut_frames=cuts,source_audio_sha256=sha(Path(manifest['embedded_files']['movie/op.wav'])),
        native_decoder=check,caption_pixels_protected=sum(protected_counts),
        initial_quality_loss=sum(o[0]['loss'] for o in options),selected_quality_loss=sum(x['loss'] for x in entries),
        status='CANDIDATE; full native playback and integrated ROM verification pending'))
    assert sha(base_rom)==initial_sha
    print('Selected',[(i,x['profile']) for i,x in enumerate(entries) if x['profile']!='current'],flush=True)
    print('Movie bytes',len(candidate),'delta',len(candidate)-len(data),flush=True)
    return target

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=OUT)
    parser.add_argument('--max-delta-bytes',type=int,default=2048);args=parser.parse_args()
    build(args.output,args.max_delta_bytes)
