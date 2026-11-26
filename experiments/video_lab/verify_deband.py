"""Verify the final encoded pixels, captions, audio timing and shot boundaries."""
from pathlib import Path
import hashlib,json
import cv2
import numpy as np
from PIL import Image,ImageDraw
from experiments.video_lab import convert_palette as p,restore_op_text as text
from experiments.video_lab.lossless_packet_opt import decoded_tiles

ROOT=p.ROOT;OUT=ROOT/'experiments/video_lab/out/deband'
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,obj):Path(path).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf8')

def main():
    selection=json.loads((OUT/'selection.json').read_text());movie=OUT/'op_deband.zpv';data=movie.read_bytes();info=p.inspect_pack(data)
    olddata=Path(selection['source_movie']).read_bytes();old=p.inspect_pack(olddata)
    master=np.load(text.OUT/'restored_rgb.npy',mmap_mode='r');source=np.load(text.OUT/'source_rgb.npy',mmap_mode='r')
    previous=np.load(text.OUT/'decoded_rgb.npy',mmap_mode='r')
    cues=text.prepare_cues(json.loads(text.CUES.read_text(encoding='utf8')),source,text.BitmapText())
    palettes=np.frombuffer(data,dtype='<u2',count=info['family_count']*64,offset=info['palette_offset']).reshape(-1,64)
    frames=np.lib.format.open_memmap(OUT/'decoded_rgb.npy',mode='w+',dtype=np.uint8,shape=master.shape)
    guarded=[];protected=0;outside=0;changed=0;stream=hashlib.sha256()
    for n,raw in enumerate(decoded_tiles(data,info)):
        assert info['index'][n]['audio_sample']==old['index'][n]['audio_sample']
        indices=np.frombuffer(raw,np.uint8).reshape(18,32,8,8).transpose(0,2,1,3).reshape(144,256)
        frames[n]=p.rgb_from_555(palettes[n//60][indices]);stream.update(raw)
        mask=np.zeros((144,256),np.uint8)
        for cue in cues:
            if cue['start']<=n<cue['end']:
                ink,trail=text.caption_ink(cue,n);g=((ink>0)|(trail>0)).astype(np.uint8)
                if cue.get('outline'):g=cv2.dilate(g,np.ones((3,3),np.uint8))
                mask|=g
        guard=(mask>0)|(master[n].max(axis=2)==0)|(master[n].min(axis=2)==255)
        np.testing.assert_array_equal(frames[n][guard],previous[n][guard],err_msg=f'Caption/black/white frame{n}')
        protected+=int((mask>0).sum());guarded.append(guard)
        delta=np.any(frames[n]!=previous[n],axis=2);changed+=int(delta.sum())
        if selection['selected'][n//60]['profile']=='current':outside+=int(delta.sum())
    assert outside==0;frames.flush()
    transitions=[]
    for n in range(60,len(master),60):
        a,b=master[n-1].astype(np.int16),master[n].astype(np.int16)
        stable=(np.max(np.abs(a-b),axis=2)<=2)&(b.max(axis=2)>20)&~guarded[n-1]&~guarded[n]
        if int(stable.sum())<256:continue
        old_delta=previous[n].astype(np.int16)-previous[n-1].astype(np.int16)
        new_delta=frames[n].astype(np.int16)-frames[n-1].astype(np.int16)
        transitions.append(dict(frame=n,stable_pixels=int(stable.sum()),
            original_large_jumps=int(((np.max(np.abs(old_delta),axis=2)>16)&stable).sum()),
            new_large_jumps=int(((np.max(np.abs(new_delta),axis=2)>16)&stable).sum()),
            original_rmse=float(np.sqrt(np.mean(((old_delta-(b-a))[stable].astype(float))**2))),
            new_rmse=float(np.sqrt(np.mean(((new_delta-(b-a))[stable].astype(float))**2)))))
    rois=[(931,(146,7,186,35)),(1046,(95,72,135,100)),(1090,(1,75,41,103)),(1317,(169,106,209,134)),(1322,(169,106,209,134)),(1618,(92,73,164,108))]
    sheet=Image.new('RGB',(768,220*len(rois)),(24,24,24));draw=ImageDraw.Draw(sheet);roi_metrics=[]
    for row,(n,rect) in enumerate(rois):
        x0,y0,x1,y1=rect;goal=master[n,y0:y1,x0:x1].astype(float);measure={}
        for col,(name,array) in enumerate([('Source',master[n]),('Before',previous[n]),('After',frames[n])]):
            draw.text((col*256+4,row*220+4),f'{name} frame{n}',fill='white')
            image=Image.fromarray(array).crop(rect);scale=min(6,240//image.width,180//image.height)
            sheet.paste(image.resize((image.width*scale,image.height*scale),Image.Resampling.NEAREST),(col*256+4,row*220+28))
            if col:
                actual=array[y0:y1,x0:x1].astype(float)
                measure[name]=dict(mae=float(np.abs(actual-goal).mean()),
                    lowpass_rmse=float(np.sqrt(np.mean((cv2.GaussianBlur(actual,(5,5),1)-cv2.GaussianBlur(goal,(5,5),1))**2))))
        roi_metrics.append(dict(frame=n,rect=rect,metrics=measure))
    sheet.save(OUT/'Before_After_Details.png')
    text.encode_preview(OUT,frames,'OP_Deband_Preview.mp4',Path(json.loads((ROOT/'experiments/video_lab/out/movie_manifest.json').read_text(encoding='utf8'))['embedded_files']['movie/op.wav']))
    quality=dict(result='PASS',video=str(movie),video_sha256=digest(movie),frames=len(frames),
        unchanged_protected_caption_pixels=protected,unchanged_primary_trail_outline_black_white=True,
        pixels_changed_in_untreated_families=outside,changed_background_pixels=changed,
        original_text_and_gates_unchanged=True,audio_timestamps_unchanged=True,tile_stream_sha256=stream.hexdigest(),
        roi_metrics=roi_metrics,palette_transitions=transitions,
        selected_profiles=[dict(family=i,start=x['start'],end=x['end'],profile=x['profile']) for i,x in enumerate(selection['selected'])],
        lossless_redundancy_policy=dict(max_raw_delta_bytes=selection['max_delta_bytes'],no_additional_lz_decode=True),
        original_quality_loss=selection['initial_quality_loss'],new_quality_loss=selection['selected_quality_loss'],
        preview=str(OUT/'OP_Deband_Preview.mp4'),detail=str(OUT/'Before_After_Details.png'))
    write(OUT/'quality.json',quality)
    manifest=json.loads((ROOT/'experiments/video_lab/out/movie_manifest.json').read_text(encoding='utf8'))
    manifest.pop('integrated_rom',None);manifest['embedded_files']['movie/op.zpv']=str(movie)
    manifest['payloads']['movie/op.zpv']=dict(bytes=len(data),sha256=digest(movie))
    manifest['payload_bytes']=sum(x['bytes'] for x in manifest['payloads'].values())
    manifest['debanding']=dict(quality=str(OUT/'quality.json'),selection=str(OUT/'selection.json'),palette_colors=64,
        policy='Shot/family budget selection; refill duplicate colors; fixed weak4x4 Bayer on smooth background; exact original caption pixels')
    manifest['evidence']['debanding']=str(OUT/'quality.json');write(OUT/'movie_manifest.json',manifest)
    print(json.dumps({k:v for k,v in quality.items() if k not in ('selected_profiles','palette_transitions','roi_metrics')},indent=2))
    print('Transitions',[(x['frame'],x['original_large_jumps'],x['new_large_jumps']) for x in transitions],flush=True)

if __name__=='__main__':main()
