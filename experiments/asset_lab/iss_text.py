"""Native-dot `is` cloud fitted to the special flash, without source textures.

The first fourteen ink-box anchors were measured from is1..4. Later positions
are deterministic fitted scatter, not a reconstruction of the source artwork.
Mean red levels were measured after Box / 5; 10.png is only a visual density
reference here, not an inferred Ren'Py is10 image alias.
"""
from copy import deepcopy
from functools import lru_cache
import random
import numpy as np
from PIL import Image
if __package__:
    from .anim_text import SceneText
else:
    from anim_text import SceneText

SIZE=(256,144)
INK=(122,24,24)  # Original #F33030 hue, fitted ink brightness for hard dot edges.
SEED=0x5a41544f
OVERLAP_GAIN=.25
OLDEST_DIMMING=.25
SOURCE_MEAN_R=(.076552,.152968,.298340,.819526,1.970405,5.622830,9.337701,18.393392,26.896783,42.860758)
# first frame, original ink-bbox x/y, original word width
ANCHORS=((0,290,172,53),(1,859,471,53),(2,761,237,53),(2,951,322,37),(2,440,461,36),
         (3,541,95,53),(3,62,136,53),(3,928,136,53),(3,449,177,53),(3,731,180,37),
         (3,252,221,37),(3,220,319,36),(3,396,342,37),(3,278,517,53))


@lru_cache(maxsize=1)
def _build():
    fonts=SceneText();atlas={}
    for size in (8,12):
        mask=Image.new('L',(size*3,size));x=0
        for char in 'is':
            glyph,advance=fonts.glyph(char,size);mask.paste(glyph,(x,0));x+=advance
        mask=mask.crop(mask.getbbox());atlas[size]=np.asarray(mask)>0
    rng=random.Random(SEED);ink=np.zeros((144,256),dtype=bool)
    hits=np.zeros((144,256),dtype=np.uint16);birth=np.full((144,256),-1,dtype=np.int8)
    words=[];frames=[];statistics=[]
    def add(x,y,size):
        word=atlas[size];h,w=word.shape
        x0,y0=max(0,x),max(0,y);x1,y1=min(256,x+w),min(144,y+h)
        if x0<x1 and y0<y1:
            stamp=word[y0-y:y1-y,x0-x:x1-x]
            region=hits[y0:y1,x0:x1]
            birth[y0:y1,x0:x1][stamp & (region==0)]=step
            region+=stamp
            ink[y0:y1,x0:x1]|=stamp
        words.append(dict(text='is',x=x,y=y,font=size))
    def scatter(step):
        size=12 if rng.random()<.58 else 8
        height,width=atlas[size].shape
        add(rng.randrange(-width+1,256),rng.randrange(19 if step==4 else -height+1,144),size)
    for step in range(10):
        for first,x,y,width in ANCHORS:
            if first==step:add(round(x/5),round(y/5),12 if width>=50 else 8)
        # First five original counts are known. Later density fits measured
        # mean red, with one-word precision, instead of guessing word counts.
        if step==4:
            while len(words)<33:scatter(step)
        if step>=5:
            target=round(SOURCE_MEAN_R[step]*256*144/INK[0]);attempts=0
            while np.count_nonzero(ink)<target:
                scatter(step);attempts+=1
                if attempts>10000:raise RuntimeError('iss density fit did not converge')
        # Overlapping strokes add light. The last two steps dim accumulated
        # ink according to when each pixel first appeared.
        tail=max(0.,(step-7)/2)
        age=np.clip((4-birth.astype(np.float32))/4,0.,1.)
        base=1.-OLDEST_DIMMING*tail*age
        strength=(1.+OVERLAP_GAIN*np.maximum(hits.astype(np.float32)-1,0))*base
        color=np.minimum(np.rint(strength[:,:,None]*np.array(INK)),(243,48,48)).astype(np.uint8)
        pixels=np.zeros((144,256,4),dtype=np.uint8);pixels[:,:,3]=255;pixels[ink,:3]=color[ink]
        frames.append(Image.fromarray(pixels))
        statistics.append(dict(step=step+1,words=len(words),ink_pixels=int(np.count_nonzero(ink)),
                               mean_r=float(pixels[:,:,0].mean()),density_fit_mean_r=float(np.count_nonzero(ink)*INK[0]/(256*144)),
                               overlap_pixels=int(np.count_nonzero(hits>1)),
                               aged_pixels=int(np.count_nonzero(ink & (birth<4))) if tail else 0,
                               source_box_mean_r=SOURCE_MEAN_R[step]))
    metadata=dict(font_based=True,reads_original_textures=False,word='is',seed=SEED,
                  font_sizes=[8,12],font='existing Fusion Pixel 1bpp subsets',ink=list(INK),
                  source_reference_color=[243,48,48],opaque_black_background=True,
                  fit='14 measured anchors; first five word counts; later cumulative scatter fitted to Box mean red',
                  final_density_reference='10.png visual reference only; no is10 alias created',
                  overlap_gain=OVERLAP_GAIN,oldest_pixel_dimming=OLDEST_DIMMING,
                  dimming='Final two steps; accumulated brightness fades by pixel birth age, strongest for the first pixels, tapering through birth step4.',
                  frame_interval=.05,frames=statistics,words=words,
                  adaptation='Native dot text approximation, not pixel-identical source artwork.')
    return frames,metadata


class IssText:
    def __init__(self):
        self._frames,metadata=_build();self.metadata=deepcopy(metadata)

    def render(self,step):
        return self._frames[min(max(0,int(step)),9)].copy()
