"""Selective soft actor masters: original RGBA -> 2x2 BOX, no edge rewriting.

The normal actor converter intentionally remains unchanged. This closed list
contains broad authored gradients/blur, not the usual one-pixel antialiasing.
"""
from pathlib import Path
import hashlib,json,math,sys
import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'experiments/asset_lab'))
from asset_model import DEFAULT_GAME

SOFT_ACTORS={
    330:'images/sprites/asya/fbodgray.png',
    375:'images/sprites/garin/uniform/gubw.png',
    407:'images/sprites/ira/uniform/iugraygrad.png',
    **{450+i:f'images/sprites/marina/uniform/blur{i}.png' for i in range(1,7)},
    459:'images/sprites/marina/uniform/mubw.png',
}
SOFT_ASSETS={150:'images/bgs/cinemablur.png',**SOFT_ACTORS}
MASTERS=ROOT/'experiments/asset_lab/out/story_soft_alpha'

def actor_rgba(original):
    original=original.convert('RGBA');width,height=original.size
    crop=min(height,572) if height>=700 else height
    size=(math.ceil(width/2),288 if height>=700 else math.ceil(crop/2))
    canvas=Image.new('RGBA',(size[0]*2,size[1]*2))
    canvas.paste(original.crop((0,0,width,crop)),(0,0))
    return canvas.resize(size,Image.Resampling.BOX)

def native_planes(image):
    a=np.asarray(image.convert('RGBA'));alpha=a[:,:,3]
    q=(a[:,:,:3].astype(np.uint16)*31+127)//255
    words=np.where(alpha,q[:,:,0]|(q[:,:,1]<<5)|(q[:,:,2]<<10)|0x8000,0).astype('<u2')
    return words,alpha

def encode_tiles(image):
    words,alpha=native_planes(image);height,width=alpha.shape
    padding=((0,(-height)%8),(0,(-width)%8))
    words=np.pad(words,padding);alpha=np.pad(alpha,padding)
    return [words[y:y+8,x:x+8].tobytes()+alpha[y:y+8,x:x+8].tobytes()
        for y in range(0,words.shape[0],8) for x in range(0,words.shape[1],8)]

def native_pixels(image):
    words,alpha=native_planes(image);pixels=np.empty((*alpha.shape,3),dtype='u1')
    pixels[:,:,0]=words&255;pixels[:,:,1]=words>>8;pixels[:,:,2]=alpha
    return pixels.tobytes()

def pixel_hash(image):
    value=2166136261
    for byte in native_pixels(image):value=((value^byte)*16777619)&0xffffffff
    return value

def background_rgba(original,frame,tiles,crop):
    """Preserve the approved RGB palette/border and visible extent exactly."""
    width,height,cols,rows=frame[4:8];content=original.convert('RGBA').crop(crop)
    aligned=Image.new('RGBA',(width*4,height*4));aligned.paste(content,(0,0))
    source=np.asarray(aligned.resize((width,height),Image.Resampling.BOX))
    values=np.frombuffer(b''.join(tiles),dtype='u1').reshape(rows,cols,40)
    ids=np.empty((rows,cols,64),dtype='u1');ids[:,:,::2]=values[:,:,:32]&15;ids[:,:,1::2]=values[:,:,:32]>>4
    ids=ids.reshape(rows,cols,8,8).transpose(0,2,1,3).reshape(rows*8,cols*8)[:height,:width]
    visible=np.unpackbits(values[:,:,32:],axis=2,bitorder='little').reshape(rows,cols,8,8).transpose(0,2,1,3).reshape(rows*8,cols*8)[:height,:width]>0
    colors=np.frombuffer(frame[13],dtype='<u2')[ids]
    rgba=np.stack([(((colors>>shift)&31)*255+15)//31 for shift in (0,5,10)]+[np.where(visible,source[:,:,3],0)],axis=2).astype('u1')
    return Image.fromarray(rgba)

def convert(frames,output=MASTERS,game=DEFAULT_GAME,source_pack=None):
    output=Path(output).resolve();game=Path(game).resolve()
    if output.is_relative_to(game) or output.is_relative_to((ROOT/'experiments/asset_lab/out/full_asset_audit').resolve()):
        raise ValueError('Soft masters must not overwrite original or frozen legacy masters')
    result={};rows=[]
    for id,name in SOFT_ASSETS.items():
        f=frames[id]
        category,format=(0,1) if id==150 else (2,0)
        if f[-1].split(b'\0')[0].decode()!=name or f[10]!=category or f[11]!=format:
            raise ValueError('Soft alpha selection does not match the frozen directory')
        source=game/name;before=source.read_bytes()
        with Image.open(source) as image:
            if id==150:
                if source_pack is None:raise ValueError('Soft background requires the approved RGB directory')
                metadata=json.loads((ROOT/'nds/artifacts/assets_manifest.json').read_text(encoding='utf8'))['frames'][id]
                master=background_rgba(image,f,source_pack.tiles(id),metadata['source_crop'])
            else:master=actor_rgba(image)
        if master.size!=(f[4],f[5]):raise ValueError('Soft actor geometry changed')
        path=output/'converted'/name;path.parent.mkdir(parents=True,exist_ok=True);master.save(path)
        native=native_pixels(master);alpha=np.asarray(master)[:,:,3]
        result[id]=dict(tiles=encode_tiles(master),palette=bytes(32),hash=pixel_hash(master),master=str(path),
            native_sha256=hashlib.sha256(native).hexdigest(),format=2)
        rows.append(dict(id=id,name=name,source_sha256=hashlib.sha256(before).hexdigest(),master=str(path),
            master_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),width=master.width,height=master.height,
            alpha_levels=len(np.unique(alpha)),partial_pixels=int(((alpha>0)&(alpha<255)).sum()),
            native_rgb15_alpha8_sha256=result[id]['native_sha256'],hash=result[id]['hash']))
        if source.read_bytes()!=before:raise ValueError('Read-only original changed during conversion')
    report=dict(version=1,frame_ids=sorted(SOFT_ASSETS),tile_bytes=192,format=2,frames=rows,
        conversion='Ten actors: original straight RGBA, unchanged top-572 halfbody crop and 2x2 BOX; no edge rewriting. Cinemablur150: approved245px geometry, existing16-color RGB and gray border/visibility retained; source4x4BOX alpha restored only inside the old visible mask.',
        hash_order='Row-major RGB15 low byte, RGB15 high byte, alpha8 per pixel; FNV-1a32.',
        alpha='Exact BOX alpha8, zero RGB15 only where alpha is zero. No dithering or binary threshold.')
    output.mkdir(parents=True,exist_ok=True);(output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return result,report
