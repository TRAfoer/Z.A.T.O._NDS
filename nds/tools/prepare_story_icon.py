"""Read the original Steam icon and prepare/verify an NDS banner icon.

No AI assets: Pillow reads the ICO's native 32px frame (or traditionally scales
another image), thresholds binary alpha, and median-cuts to15 opaque RGB15
colours plus transparent index0. ROM verification is read-only and compares
rendered palette pixels, allowing ndstool to reorder the palette.

Banner layout/CRC ranges follow BlocksDS ndstool's primary implementation:
https://github.com/blocksds/ndstool/blob/master/source/banner.cpp
https://github.com/blocksds/ndstool/blob/master/source/banner.h
"""
from pathlib import Path
import argparse,hashlib,json,struct
from PIL import Image,ImageDraw,ImageOps

ROOT=Path(__file__).resolve().parents[2]
SOURCE=Path('C:/Program Files (x86)/Steam/steamapps/common/Z.A.T.O.  I Love the World and Everything In It/game/gui/icon.ico')
SIZES={1:0x840,2:0x940,3:0xa40,0x103:0x23c0}
def sha(data):return hashlib.sha256(data).hexdigest()
def pixels(image):return image.get_flattened_data() if hasattr(image,'get_flattened_data') else image.getdata()
def crc16(data):
    result=0xffff
    for value in data:
        result^=value
        for _ in range(8):result=(result>>1)^(0xa001 if result&1 else 0)
    return result

def convert_icon(source):
    source=Path(source)
    with Image.open(source) as opened:
        image=opened.ico.getimage((32,32)) if source.suffix.lower()=='.ico' and (32,32) in opened.ico.sizes() else opened
        original=image.convert('RGBA')
    frame_size=original.size
    if frame_size!=(32,32):
        fit=ImageOps.contain(original,(32,32),Image.Resampling.LANCZOS);image=Image.new('RGBA',(32,32));image.alpha_composite(fit,((32-fit.width)//2,(32-fit.height)//2))
    else:image=original
    rgba=list(pixels(image));opaque=[tuple(v&248 for v in pixel[:3]) for pixel in rgba if pixel[3]>=128]
    if not opaque:raise ValueError('Original icon has no opaque pixels')
    strip=Image.new('RGB',(len(opaque),1));strip.putdata(opaque)
    reduced=strip.quantize(colors=15,method=Image.Quantize.MEDIANCUT,dither=Image.Dither.NONE)
    reduced_rgb=[tuple(v&248 for v in color) for color in pixels(reduced.convert('RGB'))]
    # Sort/deduplicate after RGB15 reduction so GIF/PNG output is deterministic.
    colours=sorted(set(reduced_rgb));lookup={c:i+1 for i,c in enumerate(colours)};indices=[];cursor=0
    for pixel in rgba:
        if pixel[3]<128:indices.append(0)
        else:indices.append(lookup[reduced_rgb[cursor]]);cursor+=1
    palette=[0,0,0]+[v for c in colours for v in c];palette.extend([0]*(48-len(palette)))
    icon=Image.new('P',(32,32));icon.putpalette(palette);icon.putdata(indices);icon.info['transparency']=0
    meta=dict(source=str(source),source_sha256=sha(source.read_bytes()),source_frame_size=list(frame_size),output_size=[32,32],resized=frame_size!=(32,32),resize='LANCZOS contain, transparent letterbox' if frame_size!=(32,32) else 'native original ICO32 frame; no resizing',quantisation='Pillow MEDIANCUT15 opaque colours, no dither, RGB15, alpha>=128; index0 transparent',opaque_colours=len(colours),transparent_pixels=indices.count(0),index_sha256=sha(bytes(indices)))
    return icon,meta

def icon_words(icon):
    palette=icon.getpalette();result=[]
    for index in pixels(icon):
        r,g,b=palette[index*3:index*3+3];result.append(0 if index==0 else 0x8000|(r>>3)|((g>>3)<<5)|((b>>3)<<10))
    return result

def verify_rom(rom,icon,expected_title=None):
    def need(ok,message):
        if not ok:raise ValueError(message)
    need(len(rom)>=0x200,'Truncated ROM header');offset=struct.unpack_from('<I',rom,0x68)[0]
    need(offset>=0x200 and offset+0x20<=len(rom),'Banner pointer outside ROM')
    version=struct.unpack_from('<H',rom,offset)[0];need(version in SIZES,'Unknown banner version')
    need(offset+SIZES[version]<=len(rom),'Truncated banner');banner=rom[offset:offset+SIZES[version]];checks=[]
    for slot,(minimum,start,end) in enumerate([(1,0x20,0x840),(2,0x20,0x940),(3,0x20,0xa40),(0x103,0x1240,0x23c0)]):
        if version<minimum:continue
        stored=struct.unpack_from('<H',banner,2+slot*2)[0];computed=crc16(banner[start:end]);need(stored==computed,f'Banner CRC{slot} mismatch')
        checks.append(dict(slot=slot,start=start,end=end,crc=stored))
    palette=struct.unpack_from('<16H',banner,0x220);need(all(v<32768 for v in palette),'Palette colour exceeds RGB15')
    actual=[];indices=[]
    for y in range(32):
        for x in range(32):
            tile=(y//8)*4+x//8;value=banner[0x20+tile*32+(y%8)*4+(x%8)//2];index=(value>>(4*(x&1)))&15
            indices.append(index);actual.append(palette[index]|0x8000 if index else 0)
    expected=icon_words(icon);need(actual==expected,'Banner icon pixels/alpha/palette differ from converted Steam icon')
    titles=[]
    for language in range(8 if version>=3 else 7 if version==2 else 6):
        text=banner[0x240+language*256:0x340+language*256].decode('utf-16le').split('\0',1)[0];titles.append(text)
        if expected_title is not None:need(text==expected_title,f'Banner title changed for language{language}')
    return dict(result='PASS',rom_bytes=len(rom),rom_sha256=sha(rom),banner_offset=offset,banner_version=version,banner_bytes=SIZES[version],crcs=checks,pixels_verified=1024,transparent_pixels=indices.count(0),used_palette_indices=sorted(set(indices)),palette_rgb15=list(palette),icon_rgb15_sha256=sha(struct.pack('<1024H',*actual)),titles=titles)

def visible_icon(icon):
    words=icon_words(icon);rgba=[]
    for value in words:rgba.append(tuple(((value>>shift)&31)*255//31 for shift in (0,5,10))+(255 if value else 0,))
    image=Image.new('RGBA',(32,32));image.putdata(rgba);return image

def preview(source,icon,path):
    with Image.open(source) as opened:original=(opened.ico.getimage((32,32)) if source.suffix.lower()=='.ico' else opened).convert('RGBA')
    canvas=Image.new('RGB',(560,316),(24,24,24));draw=ImageDraw.Draw(canvas);draw.text((8,6),'ORIGINAL STEAM ICO32 / NDS RGB15 15+TRANSPARENT',fill='white')
    for i,image in enumerate((original.convert('RGBA'),visible_icon(icon))):
        x=8+i*280
        for y in range(32):
            for xx in range(32):draw.rectangle((x+xx*8,30+y*8,x+xx*8+7,37+y*8),fill=(64,64,64) if (y//2+xx//2)&1 else (40,40,40))
        canvas.paste(image.resize((256,256),Image.Resampling.NEAREST),(x,30),image.resize((256,256),Image.Resampling.NEAREST))
    draw.text((8,294),'Traditional Pillow quantisation; original files read only.',fill='white');path.parent.mkdir(parents=True,exist_ok=True);canvas.save(path)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,default=SOURCE);p.add_argument('--output',type=Path,default=ROOT/'nds/icon/steam.gif');p.add_argument('--manifest',type=Path,default=ROOT/'nds/artifacts/story_icon_manifest.json');p.add_argument('--preview',type=Path,default=ROOT/'nds/artifacts/story_icon_preview.png');p.add_argument('--verify-rom',type=Path);p.add_argument('--expected-title');p.add_argument('--verification-output',type=Path,default=ROOT/'nds/artifacts/story_icon_rom_verification.json');args=p.parse_args()
    if args.verify_rom:
        with Image.open(args.output) as icon:report=verify_rom(args.verify_rom.read_bytes(),icon,args.expected_title)
        report.update(rom_path=str(args.verify_rom),icon_path=str(args.output),icon_sha256=sha(args.output.read_bytes()));args.verification_output.parent.mkdir(parents=True,exist_ok=True);args.verification_output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(json.dumps(report,ensure_ascii=False));return
    icon,meta=convert_icon(args.source);args.output.parent.mkdir(parents=True,exist_ok=True);icon.save(args.output,transparency=0,optimize=False)
    words=icon_words(icon);meta.update(output=str(args.output),bytes=args.output.stat().st_size,sha256=sha(args.output.read_bytes()),icon_rgb15_sha256=sha(struct.pack('<1024H',*words)),preview=str(args.preview),source_primary_docs=['https://github.com/blocksds/ndstool/blob/master/source/banner.cpp','https://github.com/blocksds/ndstool/blob/master/source/banner.h'])
    preview(args.source,icon,args.preview);args.manifest.parent.mkdir(parents=True,exist_ok=True);args.manifest.write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(json.dumps(meta,ensure_ascii=False))

if __name__=='__main__':main()
