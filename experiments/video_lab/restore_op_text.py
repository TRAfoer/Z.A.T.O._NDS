"""Restore the Chinese OP's readable lettering at native NDS resolution.

The source game, ZIP, soundtrack, production pack and ROMs are read-only.
Cue data is independently reviewable; no line wrapping or fractional glyph
resampling is allowed. Output is an independent preview and native video pack.
"""
from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from experiments.font_lab.font_core import parse_bdf
from experiments.video_lab import convert_palette as pal

ROOT = pal.ROOT
PREVIOUS_OUT = ROOT / 'experiments/video_lab/out/text_restored'
OUT = ROOT / 'experiments/video_lab/out/text_animated'
CUES = Path(__file__).with_name('op_text_cues.json')
FONT_ZIP = Path(r'C:/Downloads/Compressed/fusion-pixel.zip')
LARGE_FONT = ROOT/'experiments/video_lab/fonts/isas-fangsong-16/gb16fs.bdf'
# The two switches between blurred and sharper source lettering have been
# measured on each black card. Only these transitions disturb the new glyphs.
SOURCE_JITTER_CHANGES = {
    'gentle_world':(908,919,1), 'ungrateful':(962,973,-1),
    'forgot_matter':(1014,1026,1), 'forgot_real':(1062,1074,-1),
    'dream_question':(1116,1128,1), 'dream_statement':(1167,1179,-1),
}
def caption_motion(cue):
    """Half-open transition windows. All glyphs settle between windows."""
    start,end=cue['start'],cue['end']
    if cue['id'] in SOURCE_JITTER_CHANGES:
        move,back,dx=SOURCE_JITTER_CHANGES[cue['id']]
        return [(move,move+4,dx,0),(back-2,back,-dx,0)]
    if cue['id'] in ('presents','tagline'):
        # The source retains its actual glitch, then resolves into stable type.
        # Only modulate the ink during those first resolved pictures.
        return [(start,start+3,0,0)]
    if cue['id']=='mirror':
        return [(start,start+4,1,0),(1439,1442,-1,0)]
    if cue['id']=='break_through':
        return [(start,start+4,-1,0),(739,end,0,1)]
    if cue['id']=='petrol':return [(start,258,0,1)]
    # Black-band texts blur into focus at entry. Brief panel cards have just
    # one cut-in accent; their moving-art tails stay completely still.
    return [(start,min(start+4,end),-1,0)]


def prepare_glyphs(cue, placements):
    """Native ink coordinates and directions that cannot hit adjacent glyphs."""
    directions={(dx,dy) for _,_,dx,dy in cue['motion']}
    occupied=np.zeros((144,256),bool)
    y,x=cue['y'],cue['x']
    h,w=cue['mask'].shape
    occupied[y:y+h,x:x+w]=cue['mask']>0
    glyphs=[]
    for left,top,bitmap in placements:
        ys,xs=np.nonzero(bitmap)
        ys=ys+y+top
        xs=xs+x+left
        if not len(xs):continue
        occupied[ys,xs]=False
        allowed=set()
        for dx,dy in directions:
            xx,yy=xs+dx,ys+dy
            if (xx.min()>=0 and xx.max()<256 and yy.min()>=0 and yy.max()<144
                    and not occupied[yy,xx].any()):
                allowed.add((dx,dy))
        occupied[ys,xs]=True
        glyphs.append((ys,xs,allowed))
    return glyphs


def caption_ink(cue, number):
    """Return separate primary and weak afterimage levels before source fade."""
    primary=np.zeros((144,256),np.uint8)
    trail=np.zeros_like(primary)
    value=cue.get('value',224)
    window=next((w for w in cue.get('motion',[]) if w[0]<=number<w[1]),None)
    if window is None:
        h,w=cue['mask'].shape
        x,y=cue['x'],cue['y']
        primary[y:y+h,x:x+w][cue['mask']>0]=value
        return primary,trail
    begin,end,dx,dy=window
    elapsed=number-begin
    for index,(ys,xs,allowed) in enumerate(cue['glyphs']):
        # Three interleaved phases, held for two pictures. During one pulse
        # all moving glyphs share one direction, so their order cannot invert.
        phase=(index+index//3)%min(3,end-begin)
        active=phase<=elapsed<phase+2
        shifted=active and (dx or dy) and (dx,dy) in allowed
        if shifted:
            trail[ys,xs]=64 if elapsed==phase else 48
        xx,yy=(xs+dx,ys+dy) if shifted else (xs,ys)
        dim=16 if not (dx or dy) or index%2 else 32
        primary[yy,xx]=value-dim if active else value
    return primary,trail


def active_ranges(values):
    result = []
    start = None
    for i, value in enumerate([*values, False]):
        if value and start is None:
            start = i
        elif not value and start is not None:
            result.append((start, i))
            start = None
    return result


class BitmapText:
    def __init__(self, archive=FONT_ZIP, large_font=LARGE_FONT):
        with zipfile.ZipFile(archive) as z:
            self.fonts = {size: parse_bdf(z.read(f'fusion-pixel-{size}px-monospaced-zh_hans.bdf'))
                          for size in (8, 10, 12)}
        self.large_font=parse_bdf(Path(large_font).read_bytes())
        mapped={}
        for code,glyph in self.large_font.glyphs.items():
            hi,lo=code>>8,code&255
            if not(0x21<=hi<=0x7e and 0x21<=lo<=0x7e):continue
            try:char=bytes([hi|128,lo|128]).decode('gb2312')
            except UnicodeDecodeError:continue
            mapped[ord(char)]=glyph
        self.large_font.glyphs=mapped
        self.large_codepoints=frozenset(mapped)
        self.large_font_info=dict(name='ISAS FangSong',source=str(Path(large_font).resolve()),
            sha256=pal.op.sha256(Path(large_font)),size=16,author='The Institute of Software, Academia Sinica',
            source_url='https://fontpixel.com/zh/fonts/isas-fangsong/',license='ISAS (1988)',
            license_url='https://xorg.freedesktop.org/archive/individual/font/font-isas-misc-1.0.4.tar.xz',
            local_license=str(Path(large_font).with_name('COPYING').resolve()),
            copyright_notice='Copyright (C) 1988 The Institute of Software, Academia Sinica.',
            unicode_glyphs=len(mapped),rasterization='Native16px BDF bits unchanged; GB2312 GL lookup converted to Unicode; no resampling')

    @lru_cache(maxsize=4096)
    def glyph(self, char, size):
        if size==16:
            if ord(char) not in self.large_codepoints:
                raise ValueError(f'Missing native ISAS FangSong16 glyph U+{ord(char):04X}')
            mask=Image.fromarray(self.large_font.bitmap(ord(char))*255)
            advance=self.large_font.glyphs[ord(char)]['advance']
            if char in '，。！？、；：“”':
                ink=mask.getbbox();advance=max(8,(ink[2] if ink else 0)+1)
            return mask,advance
        native, multiplier = size,1
        font = self.fonts[native]
        if ord(char) not in font.glyphs:
            raise ValueError(f'Missing native{native} glyph U+{ord(char):04X}')
        mask = Image.fromarray(font.bitmap(ord(char))*255)
        advance = font.glyphs[ord(char)]['advance']
        if multiplier != 1:
            mask = mask.resize((mask.width*multiplier, mask.height*multiplier), Image.Resampling.NEAREST)
            advance *= multiplier
        # Preserve every glyph pixel; punctuation's empty half-cell is spacing,
        # not an excuse to scale or delete the glyph.
        bbox = mask.getbbox()
        if char in '，。！？、；：“”':
            advance = max(size//2, (bbox[2] if bbox else 0)+1)
        return mask, advance

    def render_lines(self, lines, max_width, preferred_size=12, line_step=None, align='left'):
        if not lines or any('\n' in line for line in lines):
            raise ValueError('Provide explicit original lines')
        missing = {c for line in lines for c in line
                   if not any(ord(c) in font.glyphs for font in self.fonts.values())}
        if missing:
            raise ValueError(f'Missing glyph: {missing!r}')
        if preferred_size>=16 and any(ord(c) not in self.large_codepoints for line in lines for c in line):
            raise ValueError('Missing glyph in the selected native16 font')
        choices = [16] if preferred_size>=16 else [v for v in (12,10,8) if v<=preferred_size]
        for size in choices:
            try:
                glyphs = [[self.glyph(c, size) for c in line] for line in lines]
            except ValueError:
                continue
            widths = []
            for row in glyphs:
                cursor = end = 0
                for glyph, advance in row:
                    bbox = glyph.getbbox()
                    end = max(end, cursor+(bbox[2] if bbox else 0))
                    cursor += advance
                widths.append(max(end, cursor))
            if max(widths, default=0) > max_width:
                continue
            step = max(size, line_step if line_step is not None else size+1)
            width = max(1, max(widths)); height = step*(len(lines)-1)+size
            mask = Image.new('L', (width, height))
            placements = []
            for row_number, row in enumerate(glyphs):
                x = (width-widths[row_number])//2 if align == 'center' else (
                    width-widths[row_number] if align == 'right' else 0)
                for glyph, advance in row:
                    mask.paste(glyph, (x, row_number*step), glyph)
                    placements.append((x,row_number*step,np.asarray(glyph)))
                    x += advance
            return dict(mask=mask,placements=placements,lines=list(lines),widths=widths,size=size,line_step=step)
        raise ValueError('Original lines do not fit; refusing to wrap, truncate, or resample')


def decode_source(source, out):
    target = out/'source_rgb.npy'
    old_cache=PREVIOUS_OUT/'source_rgb.npy'
    old_report=PREVIOUS_OUT/'report.json'
    if not target.exists() and old_cache.exists() and old_report.exists():
        previous=json.loads(old_report.read_text(encoding='utf8'))
        if previous.get('source_sha256')==pal.op.sha256(source):
            shutil.copyfile(old_cache,target)
    if target.exists():
        frames = np.load(target, mmap_mode='r')
        if frames.shape == (1746, 144, 256, 3):
            return frames
    raw = subprocess.check_output([
        'ffmpeg', '-v', 'error', '-i', str(source), '-an', '-vf',
        'tpad=stop_mode=clone:stop_duration=0.1,fps=30:round=up,scale=256:144:flags=area,format=rgb24',
        '-frames:v', '1746', '-f', 'rawvideo', '-'])
    frames = np.frombuffer(raw, np.uint8).reshape(1746, 144, 256, 3)
    np.save(target, frames)
    return frames


def prepare_cues(data, frames, font):
    prepared = []
    for row in data['cues']:
        cue = dict(row)
        result = font.render_lines(cue['lines'], 248, cue['size'],
                                   cue.get('line_step'), cue['align'])
        placements=result.pop('placements')
        mask = result.pop('mask'); width, height = mask.size
        anchor = cue['anchor']
        x = anchor-width//2 if cue['align'] == 'center' else (anchor-width if cue['align'] == 'right' else anchor)
        x = min(max(x, 4), 252-width)
        y = cue['y']
        if not (4 <= x < x+width <= 252 and 0 <= y < y+height <= 144):
            raise ValueError(f'Cue does not fit the upper movie canvas: {cue["id"]}')
        cue.update(x=x, mask=np.asarray(mask), metrics=result,motion=caption_motion(cue))
        cue['glyphs']=prepare_glyphs(cue,placements)
        # Reuse the source's entry blur/fade envelope for the first band frames.
        # Source hard-cut cards and composited panels have opaque lettering.
        cue['alpha'] = np.ones(cue['end']-cue['start'])
        if cue['clean'] == 'black' and cue['id'] in ('petrol', 'broken_dialogue', 'call', 'body', 'break_through', 'shadow'):
            x0, y0, x1, y1 = cue['clear_rect']
            values = frames[cue['start']:cue['end'], y0:y1, x0:x1].mean(axis=(1, 2, 3))
            baseline = np.median(values[5:min(20, len(values))])
            cue['alpha'][:min(5,len(values))] = np.clip(values[:5]/max(float(baseline), 1), 0, 1)
        if cue['id'] == 'presents':
            # The static letter mask is the temporal median, suppressing moving
            # snow. It avoids erasing particles across the whole credit box.
            x0, y0, x1, y1 = cue['clear_rect']
            median = np.median(frames[75:140, y0:y1, x0:x1], axis=0)
            cue['old_ink'] = (median.mean(axis=2) > 12).astype(np.uint8)*255
        if cue['clean'] == 'reference_ink':
            x0,y0,x1,y1=cue['clear_rect']
            median=np.median(frames[1470:1479,y0:y1,x0:x1],axis=0)
            cue['old_ink']=(median.mean(axis=2)>2).astype(np.uint8)*255
            cue['reference_rgb']=frames[cue['reference_frame'],y0:y1,x0:x1].copy()
        prepared.append(cue)
    return prepared


def restore_frame(rgb, frame_number, prepared_cues):
    result = rgb.copy()
    text = np.zeros((144, 256), dtype=bool)
    for cue in prepared_cues:
        if not cue['start'] <= frame_number < cue['end']:
            continue
        x0, y0, x1, y1 = cue['clear_rect']
        if cue['clean'] == 'black':
            result[y0:y1, x0:x1] = 0
        elif cue['clean'] == 'reference_ink':
            # At the tail of the wound card, artwork slides under the letters.
            # Only the old ink/halo is replaced, using the adjacent clean plate
            # below its measured panel edge. Every other source pixel stays.
            damaged=cv2.dilate(cue['old_ink'],np.ones((3,3),np.uint8))>0
            clean=np.zeros_like(result[y0:y1,x0:x1])
            cutoff=cue['reference_cutoffs'].get(str(frame_number),144)
            if cutoff<y1:
                row=max(0,cutoff-y0)
                clean[row:]=cue['reference_rgb'][row:]
            result[y0:y1,x0:x1][damaged]=clean[damaged]
        else:
            region = result[y0:y1, x0:x1]
            if 'old_ink' in cue:
                damaged = cue['old_ink'].copy()
            else:
                # Neutral letters over saturated magenta/snow, including their
                # outline. Dilation removes the antialiased and black fringes.
                hi = region.max(axis=2).astype(np.int16)
                lo = region.min(axis=2).astype(np.int16)
                damaged = ((lo > 24) & (lo*4 >= hi*3)).astype(np.uint8)*255
            damaged = cv2.dilate(damaged, np.ones((3,3), np.uint8))
            if damaged.any():
                result[y0:y1, x0:x1] = cv2.inpaint(region, damaged, 3, cv2.INPAINT_TELEA)
        primary,trail=caption_ink(cue,frame_number)
        ink=primary>0
        ghost=trail>0
        alpha = float(cue['alpha'][frame_number-cue['start']])
        if cue.get('outline'):
            outline = cv2.dilate((ink|ghost).astype(np.uint8), np.ones((3,3),np.uint8)) > 0
            result[outline] = 0
        result[ghost]=np.rint(trail[ghost]*alpha).astype(np.uint8)[:,None]
        result[ink]=np.rint(primary[ink]*alpha).astype(np.uint8)[:,None]
        text |= ink
    return result, text


def dual(rgb):
    top = np.zeros((192,256,3),np.uint8);top[24:168] = rgb
    lower = np.zeros_like(top)
    return np.concatenate([top,lower],axis=0)


def neutral_palettes(frames, colors=64):
    # Fixed neutral ramp, including the exact caption values and fade levels.
    levels = np.array([0,32,64,96,128,160,192,208,224,240,248,255],np.uint8)
    neutrals = np.repeat(levels[:,None],3,axis=1)
    result = []
    for start in range(0,len(frames),60):
        # Include every source picture: a single-frame red flash or colored
        # panel must not disappear between temporal training samples.
        samples = [Image.fromarray(frames[i]).resize((64,36),Image.Resampling.BOX)
                   for i in range(start,min(start+60,len(frames)))]
        canvas = Image.new('RGB',(64*len(samples),36))
        for j,im in enumerate(samples):canvas.paste(im,(j*64,0))
        quantized = canvas.quantize(colors=colors-len(levels),method=Image.Quantize.MEDIANCUT,dither=Image.Dither.NONE)
        values = quantized.getpalette()[:(colors-len(levels))*3]
        values = np.array(values+[0]*((colors-len(levels))*3-len(values)),np.uint8).reshape(-1,3)
        result.append(pal.rgb555(np.concatenate([neutrals,values])))
    return result


def neutral_lookup(palette):
    lookup=pal.palette_lookup(palette)
    palette=np.asarray(palette,np.uint16)
    r=palette&31;g=(palette>>5)&31;b=(palette>>10)&31
    neutral=np.flatnonzero((r==g)&(g==b))
    if not len(neutral):raise ValueError('Caption palette has no neutral entries')
    for level in range(32):
        nearest=neutral[np.argmin(np.abs(r[neutral].astype(int)-level))]
        lookup[level|(level<<5)|(level<<10)]=nearest
    return lookup


def fast_packets(data, ranges, *, key_frames=None):
    """Store existing decoded delta packets raw during measured CPU peaks.

    This is an existing ZATPAL01 flag, not a format or player change. Keeping
    the exact delta bytes and all hashes preserves following frames and seeks.
    """
    from nds.tools.verify_story_dual_video_rom import lz10
    info=pal.inspect_pack(data);records=[];payload=[];position=info['payload_offset']
    maximum=0;changed=[];key=0;key_frames=key_frames or {}
    for number,row in enumerate(info['index']):
        packet=data[row['offset']:row['offset']+row['length']];flags=row['flags']
        decoded=row['decoded']
        if number in key_frames:
            packet=key_frames[number]
            if len(packet)!=info['raw_bytes']:raise ValueError('Replacement key frame size')
            flags=pal.KEY|pal.RAW;decoded=len(packet);changed.append(number)
        elif any(start<=number<end for start,end in ranges) and not flags&pal.RAW:
            packet=lz10(packet,row['decoded']);flags|=pal.RAW;changed.append(number)
        if flags&pal.KEY:key=number
        records.append(pal.ENTRY.pack(position,len(packet),decoded,flags,key,
                                      row['palette'],row['audio_sample'],row['hash'],0))
        payload.append(packet);position+=len(packet);maximum=max(maximum,len(packet))
    info['file_bytes']=position;info['max_packed']=maximum
    result=pal.HEADER.pack(b'ZATPAL01',*(info[k] for k in pal.FIELDS))+b''.join(records)+data[info['palette_offset']:info['payload_offset']]+b''.join(payload)
    pal.inspect_pack(result)
    return result,dict(ranges=ranges,key_frames=sorted(key_frames),changed_frames=changed,added_bytes=len(result)-len(data),max_packed=maximum)


def encode_preview(out, frames, name, audio):
    p = subprocess.Popen(['ffmpeg','-y','-v','error','-f','rawvideo','-pix_fmt','rgb24',
        '-s','256x384','-r','30','-i','-','-i',str(audio),'-map','0:v:0','-map','1:a:0',
        '-vf','scale=512:768:flags=neighbor','-c:v','libx264','-crf','17','-preset','fast',
        '-pix_fmt','yuv420p','-c:a','aac','-b:a','128k','-af','volume=0.5,apad',
        '-t','58.197','-movflags','+faststart',str(out/name)],stdin=subprocess.PIPE)
    try:
        for frame in frames:p.stdin.write(dual(frame).tobytes())
    finally:p.stdin.close()
    if p.wait():raise RuntimeError('Preview encode failed')


def build(out=OUT, *, preview_only=False):
    out.mkdir(parents=True,exist_ok=True)
    data = json.loads(CUES.read_text(encoding='utf-8'))
    source = pal.op.DEFAULT_SOURCE
    audio = pal.op.DEFAULT_OUTPUT/'op_soundtrack_ima.wav'
    protected = [source,FONT_ZIP,LARGE_FONT,audio,ROOT/'nds/storyfs/movie/op.zpv',ROOT/'ZATO_Story_Chain.nds']
    before = {str(p):pal.op.sha256(p) for p in protected}
    font = BitmapText();frames = decode_source(source,out);cues = prepare_cues(data,frames,font)
    corrected = np.empty_like(frames);text_pixels = 0
    primary_masks=np.empty((len(frames),144,32),np.uint8)
    for i,frame in enumerate(frames):
        corrected[i],mask = restore_frame(frame,i,cues)
        primary_masks[i]=np.packbits(mask,axis=1)
        text_pixels += int(mask.sum())
    report = dict(status='PREVIEW',source_sha256=before[str(source)],source=str(source),
        duration_us=58197000,frame_count=1746,fps=30,audio=str(audio),audio_sha256=before[str(audio)],
        audio_channels=2,audio_gain_q12=2048,no_added_line_breaks=True,
        large_text_font=font.large_font_info,
        text_motion=dict(type='staggered native glyph motion at source transitions',maximum_axis_pixels=1,
                         source_large_card_changes=SOURCE_JITTER_CHANGES,phase_count=3,
                         glyph_pulse_frames=2,primary_dim_levels=[16,32],trail_levels=[48,64],
                         clear_holds='static between transition windows; wound tail stationary',
                         credit_motion='subtle gray brightness only on entry',
                         source_timing_approximation=True,glyph_resampling=False),
        source_and_production_sha256=before,text_pixels=text_pixels,
        cues=[{k:v for k,v in c.items() if k not in ('mask','glyphs','alpha','old_ink','reference_rgb')} for c in cues],
        preserved_graphic_text=data['preserved_graphic_text'])
    # Contact sheet uses source/optimized dual-screen captures at exact cue PTS.
    sheet = Image.new('RGB',(512*3,410*((len(cues)+2)//3)),(24,24,24));draw=ImageDraw.Draw(sheet)
    for j,cue in enumerate(cues):
        n = min(cue['end']-1,cue['start']+max(5,(cue['end']-cue['start'])//2))
        x=j%3*512;y=j//3*410
        draw.text((x+4,y+4),f'{cue["id"]} {n/30:.3f}s | source / restored',fill='white')
        sheet.paste(Image.fromarray(dual(frames[n])),(x,y+24))
        sheet.paste(Image.fromarray(dual(corrected[n])),(x+256,y+24))
        Image.fromarray(dual(corrected[n])).resize((512,768),Image.Resampling.NEAREST).save(out/f'{cue["id"]}_dual.png')
    sheet.save(out/'text_comparison.png')
    np.save(out/'restored_rgb.npy',corrected)
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if preview_only:
        print('Prepared source/optimized comparison:',out/'text_comparison.png',flush=True)
        return report
    print('Building neutral palettes and indices',flush=True)
    palettes = neutral_palettes(corrected);lookups=[neutral_lookup(p) for p in palettes]
    tiles=[];decoded=np.empty_like(corrected)
    for i,rgb in enumerate(corrected):
        family=i//60;indices=lookups[family][pal.rgb555(rgb)&0x7fff]
        tiles.append(pal.tile_bytes(indices,64));decoded[i]=pal.rgb_from_555(palettes[family][indices])
    checked_ink=0
    for n,packed_mask in enumerate(primary_masks):
        ink=np.unpackbits(packed_mask,axis=1).astype(bool)
        pixels=decoded[n][ink]
        if not (np.array_equal(pixels[:,0],pixels[:,1]) and np.array_equal(pixels[:,1],pixels[:,2])):
            raise ValueError(f'Tinted caption pixels at frame {n}')
        checked_ink+=len(pixels)
    encoder=pal.Encoder(out/'native')
    try:
        pack,metadata=pal.build_pack(tiles,palettes,colors=64,fps=30,duration_us=58197000,encoder=encoder)
        # Frame1261 also crosses the service deadline with this final palette.
        # A raw key keeps its exact pixels and removes that measured CPU peak.
        fast_keys={1261,*range(1320,1365)}
        pack,fast=fast_packets(pack,[(1261,1262),(1320,1365),(1413,1443)],
                               key_frames={n:tiles[n] for n in fast_keys})
        metadata.update(fast_packet_policy=fast,max_packed=fast['max_packed'],payload_bytes=len(pack)-pal.inspect_pack(pack)['payload_offset'])
        movie=out/'op_text_restored.zpv';movie.write_bytes(pack)
        native=pal.verify_native(encoder.executable,movie,out/'native_samples')
        metadata['mode_counts']={name:native[name] for name in pal.MODE_NAMES.values()}
    finally:encoder.close()
    print('Writing complete dual-screen preview',flush=True)
    encode_preview(out,decoded,'OP_Text_Restored_DualScreen.mp4',audio)
    np.save(out/'decoded_rgb.npy',decoded)
    # Final screenshots must be actual codec colors, not the prequantized master.
    for cue in cues:
        n=min(cue['end']-1,cue['start']+max(5,(cue['end']-cue['start'])//2))
        Image.fromarray(dual(decoded[n])).resize((512,768),Image.Resampling.NEAREST).save(out/f'{cue["id"]}_dual.png')
    after={str(p):pal.op.sha256(p) for p in protected}
    if after!=before:raise ValueError('Read-only source or production files changed')
    report.update(status='ENCODED',video=str(movie),video_bytes=len(pack),video_sha256=pal.op.sha256(movie),
                  native_decoder=native,codec_metadata=metadata,protected_files_unchanged=True,
                  neutral_glyph_pixels_checked=checked_ink,
                  preview=str(out/'OP_Text_Restored_DualScreen.mp4'))
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('status','video_bytes','video_sha256','native_decoder','neutral_glyph_pixels_checked','protected_files_unchanged','preview')},indent=2),flush=True)
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUT)
    parser.add_argument('--preview-only',action='store_true')
    args=parser.parse_args();build(args.output,preview_only=args.preview_only)
