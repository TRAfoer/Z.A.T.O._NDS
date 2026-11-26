"""Asya's60px top-right side-image proof; leaves the current NDS untouched."""
from pathlib import Path
import hashlib
import json
import struct
import subprocess
import numpy as np
from PIL import Image
from build_font_lab import ROOT,OUT,HEADER,PAGE,PALETTE,unlz10,DEFAULT_GAME
from font_core import unpack_bitmap,visible_text,extract_translation

PORTRAIT=(196,0,60,60)
DEST=OUT/'asya_portrait_60px'


def layout_around_portrait(text,advances,portrait=PORTRAIT,x=31,y=35,width=194,height=12,step=15,gap=8):
    lines=[];widths=[];line='';used=0
    def available(row):
        top=y+row*step
        return min(width,portrait[0]-gap-x) if portrait and top<portrait[1]+portrait[3] and top+height>portrait[1] else width
    for c in text:
        if c=='\n':lines.append(line);widths.append(available(len(widths)));line='';used=0;continue
        advance=advances[c]
        if line and used+advance>available(len(lines)):
            carry=''
            if c in '，。！？；：、）》】」』…' or line[-1] in '（《【「『':carry=line[-1];line=line[:-1]
            lines.append(line);widths.append(available(len(widths)));line=carry;used=sum(advances[v] for v in line)
        line+=c;used+=advance
    if line:lines.append(line);widths.append(available(len(widths)))
    return lines,widths


def load_font(size):
    data=(OUT/f'font_{size}px.zfn').read_bytes();h=HEADER.unpack_from(data)[1:];n,w,height=h[1:4]
    glyphs=[];stride=(w*height+7)//8
    for i in range(h[7]):
        _,count,codec,offset,packed,raw,crc=PAGE.unpack_from(data,h[10]+i*PAGE.size)
        page=unlz10(data[offset:offset+packed]) if codec else data[offset:offset+packed]
        assert len(page)==raw
        glyphs.extend(unpack_bitmap(page[j*stride:(j+1)*stride],w,height) for j in range(count))
    chars=[chr(cp) for cp in struct.unpack_from('<'+'I'*n,data,h[8])]
    advances=dict(zip(chars,data[h[9]:h[9]+n]))
    return glyphs,advances,{c:i for i,c in enumerate(chars)}


def make_portrait(source,size=60):
    if not 3<=size<=192:raise ValueError('Invalid portrait size')
    image=Image.open(source).convert('RGBA');bbox=image.getbbox();assert bbox
    # Strip the source's old outer/double frame and resize the interior once.
    box=(bbox[0]+10,bbox[1]+10,bbox[2]-10,bbox[3]-10)
    crop=image.crop(box);black=Image.new('RGBA',crop.size,(0,0,0,255));black.alpha_composite(crop)
    small=np.array(black.convert('RGB').resize((size-2,size-2),Image.Resampling.BOX))
    small=(small>>3);small=((small<<3)|(small>>2)).astype(np.uint8)
    result=np.full((size,size,3),255,dtype=np.uint8);result[1:-1,1:-1]=small
    return Image.fromarray(result),box


def main():
    DEST.mkdir(parents=True,exist_ok=True)
    bodyfont=load_font(12);uifont=load_font(8);records=[json.loads(l) for l in (OUT/'corpus.jsonl').read_text(encoding='utf-8').splitlines()]
    sample=next(r for r in records if r['translation_id']=='ep2d1_61a49afa')
    bilingual=extract_translation((DEFAULT_GAME/'tl/chzh'/sample['file']).read_text(encoding='utf-8-sig'),sample['file'],'chzh')
    bilingual=next(r for r in bilingual if r['translation_id']==sample['translation_id'])
    chinese,*english=[s for s in visible_text(bilingual['text']).split('\n') if s]
    source=DEFAULT_GAME/'images/sprites/asya/side as um.png';portrait,crop=make_portrait(source)
    portrait.save(DEST/'asya_60px.png')
    rgb=np.array(portrait).astype(np.uint16)>>3
    pixels=(rgb[:,:,0]|(rgb[:,:,1]<<5)|(rgb[:,:,2]<<10)|0x8000).astype('<u2');(DEST/'asya_60px.rgb15').write_bytes(pixels.tobytes())
    assert np.all(pixels[0]==0xffff) and np.all(pixels[-1]==0xffff) and np.all(pixels[:,0]==0xffff) and np.all(pixels[:,-1]==0xffff)
    native_checks=[]
    def render(tag,text,english_text='',show=True):
        main_commands=[];ui_commands=[]
        def emit(text,x,y,color,font,commands):
            _,adv,ids=font
            for c in text:commands.append((ids[c],x,y,color));x+=adv[c]
            return x
        end=emit('------[阿莎雅]',8,4,3,bodyfont,main_commands)
        bar_right=PORTRAIT[0]-6 if show else 248
        while end+bodyfont[1]['-']<=bar_right:end=emit('-',end,4,3,bodyfont,main_commands)
        obstacle=PORTRAIT if show else None
        lines,widths=layout_around_portrait(text,bodyfont[1],portrait=obstacle)
        for i,line in enumerate(lines):
            if 35+i*15+12>170:break
            emit(line,31,35+i*15,1,bodyfont,main_commands)
        if english_text:
            subtitle_y=35+len(lines)*15+3
            en,_=layout_around_portrait(english_text,uifont[1],portrait=obstacle,y=subtitle_y,height=8,step=11)
            for i,line in enumerate(en):emit(line,31,subtitle_y+i*11,2,uifont,ui_commands)
        labels=['返回','日志','快进','自动','中英','存档','快速存档','快速读档','设置']
        total=sum(uifont[1][c] for label in labels for c in label)+4*(len(labels)-1);x=(256-total)//2
        for label in labels:x=emit(label,x,181,2,uifont,ui_commands)+4
        masks=[]
        for suffix,font,size,commands in [('main',bodyfont,12,main_commands),('ui',uifont,8,ui_commands)]:
            mask=np.zeros((192,256),dtype=np.uint8)
            for gid,x,y,color in commands:
                for yy,xx in np.argwhere(font[0][gid]):
                    dx,dy=x+int(xx),y+int(yy)
                    if 0<=dx<256 and 0<=dy<192:mask[dy,dx]=color
            if show:
                px,py,pw,ph=PORTRAIT;assert not mask[py:py+ph,px:px+pw].any(),'Text overlaps portrait'
            command_file=DEST/f'{tag}_{suffix}.commands';command_file.write_bytes(b''.join(struct.pack('<IhhI',*c) for c in commands))
            tiles=DEST/f'{tag}_{suffix}.tiles4'
            output=subprocess.check_output([str(OUT/'validate_font_text.exe'),str(OUT/f'font_{size}px.zfn'),str(command_file),str(tiles)],text=True)
            from verify_font_lab import unpack_tiles
            np.testing.assert_array_equal(unpack_tiles(tiles.read_bytes()),mask);native_checks.append(output.strip());masks.append(mask)
        frame=np.zeros((192,256),dtype=np.uint8);frame[10,4:252]=4;frame[190,4:252]=4;frame[10:191,4]=4;frame[10:191,251]=4
        name_x=8+6*bodyfont[1]['-'];name_width=sum(bodyfont[1][c] for c in '[阿莎雅]');frame[:20,name_x-2:name_x+name_width+2]=0
        mask=np.where(masks[1]!=0,masks[1],np.where(masks[0]!=0,masks[0],frame))
        lower=Image.fromarray(np.array(PALETTE,dtype=np.uint8)[mask])
        if show:
            lower.paste(portrait,PORTRAIT[:2])
            assert lower.getpixel((255,0))==(255,255,255) and lower.getpixel((196,0))==(255,255,255)
        lower.save(DEST/f'{tag}_lower.png')
        upper=Image.open(ROOT/'nds/artifacts/audio_controls.png').convert('RGB').crop((0,0,256,192))
        dual=Image.new('RGB',(256,384));dual.paste(upper,(0,0));dual.paste(lower,(0,192));dual.save(DEST/f'{tag}_dual.png')
        dual.resize((512,768),Image.Resampling.NEAREST).save(DEST/f'{tag}_dual_2x.png')
        return dual,dict(text=text,line_widths=widths,line_lengths=[len(s) for s in lines])
    before,before_info=render('without_portrait',chinese,' '.join(english),False)
    after,after_info=render('with_portrait',chinese,' '.join(english),True)
    stress=''.join(visible_text(r['text']) for r in records if r.get('speaker') and r['speaker'].split()[0]=='a')[:110]
    _,stress_info=render('wrap_test',stress,show=True)
    comparison=Image.new('RGB',(512,384));comparison.paste(before,(0,0));comparison.paste(after,(256,0))
    comparison.resize((1024,768),Image.Resampling.NEAREST).save(DEST/'comparison_2x.png')
    old=OUT/'asya_portrait/with_portrait_dual.png'
    if old.exists():
        comparison.paste(Image.open(old).convert('RGB'),(0,0))
        comparison.resize((1024,768),Image.Resampling.NEAREST).save(DEST/'size_comparison_2x.png')
    report=dict(result='PASS',portrait_rect=PORTRAIT,portrait_border=1,source=str(source),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),source_crop=crop,
        source_record=sample,before=before_info,after=after_info,wrap_test=stress_info,native_checks=native_checks,
        raw_portrait_bytes=7200,notes='Original um expression. Source frame stripped, BOX once to58px, new1px white frame, total60px; flush lower-screen top/right; RGB15. Stress text concatenates non-contiguous Asya lines solely to verify wrapping; not an authored dialogue. Offline preview; NDS unchanged.')
    (DEST/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('PASS60x60portrait at(196,0),1px white frame; first2rows157px then194px; no text/header overlaps;6native glyph-pixel checks')


if __name__=='__main__':main()
