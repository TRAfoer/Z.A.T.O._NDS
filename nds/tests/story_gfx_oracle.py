"""Pixel oracle using PNG masters, NumPy and source metadata, never C/tile data."""
from pathlib import Path
import json,math
from functools import lru_cache
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parents[2]
Q=65536

def trunc(a,b):return a//b if a>=0 else -((-a)//b)
def hash32(v):
    v&=0xffffffff;v^=v>>16;v=v*0x7feb352d&0xffffffff;v^=v>>15;v=v*0x846ca68b&0xffffffff;return (v^(v>>16))&0xffffffff

class Oracle:
    def __init__(self,actor_spread=(3,2),manifest_path=None):
        self.actor_spread=actor_spread
        self.manifest=json.loads(Path(manifest_path or ROOT/'nds/artifacts/story_gfx_manifest.json').read_text(encoding='utf8'))
        metadata=Path(self.manifest.get('asset_pack_manifest',ROOT/'nds/artifacts/assets_manifest.json'))
        self.frames=json.loads(metadata.read_text(encoding='utf8'))['frames']
        self.sizes={x['asset']:x['source_size'] for x in json.loads((ROOT/'experiments/asset_lab/out/full_asset_audit/report.json').read_text(encoding='utf8'))['assets']}
        self.images=self.manifest['images'];self.programs=[x['values'] for x in self.manifest['programs']]
    @lru_cache(maxsize=64)
    def pixels(self,frame):
        f=self.frames[frame];path=Path(f['master']) if f['master'] else ROOT/'experiments/asset_lab/out/full_asset_audit/converted'/f['name']
        if not path.is_absolute():path=ROOT/'nds'/path
        a=np.asarray(Image.open(path).convert('RGBA'),dtype=np.uint16)
        q=(a[:,:,:3]*31+127)//255;words=q[:,:,0]|q[:,:,1]<<5|q[:,:,2]<<10|0x8000
        visible=a[:,:,3]>0 if f['format']==2 else a[:,:,3]>=128
        return np.where(visible,words,0).astype(np.uint16)
    @lru_cache(maxsize=64)
    def alpha32(self,frame):
        f=self.frames[frame]
        if f['format']!=2:return (self.pixels(frame)>>15).astype(np.int64)*32
        path=Path(f['master']);a=np.asarray(Image.open(path).convert('RGBA'),dtype=np.uint16)
        return ((a[:,:,3]+4)>>3).astype(np.int64)
    def sample(self,image,ticks):
        info=self.images[image]
        if info['kind']!='sequence':return info.get('frame_id'),None,4096
        rows=info['frames'];steps=[round(x['duration']*60) for x in rows];period=sum(steps)
        phase=ticks%period if info['loop'] else ticks;offset=0;index=0
        while index+1<len(rows) and steps[index] and phase>=offset+steps[index]:offset+=steps[index];index+=1
        current=self.manifest['name_to_image_id'][rows[index]['image']]
        prior=self.manifest['name_to_image_id'][rows[index-1]['image']] if index else self.manifest['name_to_image_id'][rows[-1]['image']] if info['loop'] and ticks>=period else None
        duration=info['transition_ticks'][index];mix=(phase-offset)*16//duration*256 if prior is not None and duration and phase-offset<duration else 4096
        return current,prior if mix<4096 else None,mix
    def position(self,frame,placement,p,ticks):
        f=self.frames[frame];source_width,source_height=self.sizes[f['name']]
        soft_actor=f['format']==2 and f['category']==2
        scale=2 if soft_actor else 4 if f['master'] else 2 if f['category']==2 else 5
        fullw,fullh=source_width/scale,source_height/scale
        sh=192 if soft_actor else fullh if f['master'] else 192 if f['category']==2 else 144
        zfrom,zto=p[2]/Q,p[3]/Q
        z=zfrom+(zto-zfrom)*math.sin(min(ticks/p[4],1)*math.pi/2) if p[4] else zfrom
        z=math.floor(z*1024+.5)/1024
        x=f['x'] if placement==0 else math.floor((64+source_width/20 if placement==1 else 192-source_width/20)-f['width']/2)
        if f['category']==2 and placement in (1,2) and not p[11]&1:
            a,b=self.actor_spread;x=f['x']+(x-f['x'])*a//b
            x=max(math.ceil(48-f['width']/2),min(math.floor(208-f['width']/2),x))
        ox=x+f['width']*(1-z)/2;oy=f['y']+f['height']*(1-z)/2
        if p[11]&1:
            cropx=fullw/2+f['x']-128;cropy=fullh/2+f['y']-96
            ox=p[7]/Q*256+(cropx-p[5]/Q*fullw)*z
            oy=(192-sh)/2+p[8]/Q*sh+(cropy-p[6]/Q*fullh)*z
        if p[11]&2:oy=(192-f['height']*z)*p[10]/Q
        return ox,oy,z
    def particle_positions(self,image,ticks):
        info=self.images[image];vx0,vx1=sorted(round(x/5*Q) for x in info['xspeed_source']);vy0,vy1=sorted(round(x/5*Q) for x in info['yspeed_source'])
        for at in range(info['count']):
            h=hash32(info['seed']+at*0x9e3779b9);delay=h%(info['start']*60+1) if at and info['start'] else 0
            if ticks<delay:continue
            vx=vx0+trunc((vx1-vx0)*(hash32(h)&65535),65535);vy=vy0+trunc((vy1-vy0)*(hash32(h+1)&65535),65535)
            if not vy:vy=13107
            travel=164*Q*60//abs(vy);age=(ticks-delay)%max(travel,1);xdist=trunc(vx*travel,60)
            xmin=-xdist if xdist>0 else 0;xmax=256*Q+(xdist if xdist>0 else 0)
            x=xmin+trunc((xmax-xmin)*(hash32(h+2)&65535),65535)+trunc(vx*age,60)
            y=(-10*Q if vy>0 else 154*Q)+trunc(vy*age,60)
            yield math.floor(x/Q),24+math.floor(y/Q)
    def paint(self,screen,current,prior,mix,p,position,clip=(0,192)):
        source=self.pixels(current);h,w=source.shape;ox,oy,z=position
        # Independent NumPy sampling from full masters; no tiles/maps/decoder.
        xx=np.floor((np.arange(256)+.5-ox)/z).astype(int);yy=np.floor((np.arange(192)+.5-oy)/z).astype(int)
        if p[9]<0:xx=w-1-xx
        valid=(xx[None,:]>=0)&(xx[None,:]<w)&(yy[:,None]>=0)&(yy[:,None]<h)
        valid[:clip[0]]=False;valid[clip[1]:]=False
        color=source[np.clip(yy,0,h-1)[:,None],np.clip(xx,0,w-1)[None,:]]
        color=np.where(valid,color,0).astype(np.uint32)
        if self.frames[current]['format']==2 or prior is not None and self.frames[prior]['format']==2:
            # Straight PNG alpha is independently quantized to the documented
            # Q5 coverage; use Q12 only when global/sequence blending needs it.
            ca=self.alpha32(current)[np.clip(yy,0,h-1)[:,None],np.clip(xx,0,w-1)[None,:]]
            ca=np.where(valid,ca,0);old=np.zeros_like(color,dtype=np.int64);oa=np.zeros_like(ca)
            if prior is not None:
                prior_pixels=self.pixels(prior);ph,pw=prior_pixels.shape
                ov=(xx[None,:]>=0)&(xx[None,:]<pw)&(yy[:,None]>=0)&(yy[:,None]<ph)
                ov[:clip[0]]=False;ov[clip[1]:]=False
                old=np.where(ov,prior_pixels[np.clip(yy,0,ph-1)[:,None],np.clip(xx,0,pw-1)[None,:]],0).astype(np.int64)
                oa=np.where(ov,self.alpha32(prior)[np.clip(yy,0,ph-1)[:,None],np.clip(xx,0,pw-1)[None,:]],0)
            ca=(ca*p[0]+16)>>5;oa=(oa*p[0]+16)>>5
            alpha=(ca*mix+oa*(4096-mix)+2048)>>12
            result=np.full(screen.shape,0x8000,dtype=np.int64)
            for shift in (0,5,10):
                c=(color.astype(np.int64)>>shift)&31;o=(old>>shift)&31;dest=(screen.astype(np.int64)>>shift)&31
                prem=(ca*c*mix+oa*o*(4096-mix)+2048)>>12
                if p[1]==1:out=np.minimum(dest+((prem+2048)>>12),31)
                elif p[1]==2:out=(dest*((31*(4096-alpha)+prem+15)//31)+2048)>>12
                else:out=(dest*(4096-alpha)+prem+2048)>>12
                result|=out<<shift
            screen[:]=result.astype(np.uint16);return
        old=np.zeros_like(color)
        if prior is not None:
            prior_pixels=self.pixels(prior);old=prior_pixels[np.clip(yy,0,h-1)[:,None],np.clip(xx,0,w-1)[None,:]].astype(np.uint32);old=np.where(valid,old,0)
        ca=color>>15;oa=old>>15;coverage=ca*mix+oa*(4096-mix);alpha=(coverage*p[0]+2048)>>12
        result=np.full(screen.shape,0x8000,dtype=np.uint32)
        for shift in (0,5,10):
            c=(color>>shift)&31;o=(old>>shift)&31;dest=(screen>>shift)&31
            # Q12 is part of the catalog's documented blend precision.
            prem=(ca*c*mix+oa*o*(4096-mix))*p[0]//4096
            if p[1]==1:out=np.minimum(dest+((prem+2048)>>12),31)
            elif p[1]==2:out=(dest*((31*(4096-alpha)+prem+15)//31)+2048)>>12
            else:out=(dest*(4096-alpha)+prem+2048)>>12
            result|=out<<shift
        screen[:]=result.astype(np.uint16)
    def draw(self,layers,ticks):
        screen=np.full((192,256),0x8000,dtype=np.uint16)
        for image,placement,program,start in layers:
            info=self.images[image];p=self.programs[program];elapsed=ticks-start
            if info['kind']=='particle':
                frame=self.manifest['name_to_image_id'][info['texture']]
                for x,y in self.particle_positions(image,elapsed):self.paint(screen,frame,None,4096,p,(x,y,1),(24,168))
            else:
                cur,old,mix=self.sample(image,elapsed);self.paint(screen,cur,old,mix,p,self.position(cur,placement,p,elapsed))
        return screen
