"""Independent bitmap-font/text experiment. No game/ROM files are written."""
from pathlib import Path
from collections import Counter
import argparse
import hashlib
import json
import math
import struct
import sys
import unicodedata
import zipfile
import zlib
import numpy as np
from PIL import Image,ImageDraw
from font_core import parse_bdf,extract_translation,visible_text,encode_ids,decode_ids,pack_bitmap,unpack_bitmap

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent/'out'
sys.path.insert(0,str(ROOT/'nds/tools'))
from pack_assets import lz10,unlz10
from asset_model import DEFAULT_GAME

HEADER=struct.Struct('<8s14I')
PAGE=struct.Struct('<7I')
PALETTE=[(0,0,0),(242,242,242),(136,136,136),(255,255,255),(216,216,216)]+[(255,0,255)]*11
ENCODINGS={'u16':1,'vbyte':2,'packed':3,'utf8':4}


def align4(n):return (n+3)&~3


def add_pages(raw_pages,first_counts,payload_offset):
    directory=bytearray();payload=bytearray()
    for raw,(first,count) in zip(raw_pages,first_counts):
        compressed=lz10(raw)
        codec=1 if len(compressed)<len(raw) else 0
        packed=compressed if codec else raw
        directory.extend(PAGE.pack(first,count,codec,payload_offset+len(payload),len(packed),len(raw),zlib.crc32(raw)))
        payload.extend(packed)
    return bytes(directory),bytes(payload)


def build_font(font,alphabet,member,smaller_fonts=()):
    bitmaps=[];advances=[];missing=[];fallbacks=[];missing_direct=[]
    for char in alphabet:
        cp=ord(char)
        if unicodedata.category(char) in {'Cc','Cf'}:
            bitmap=np.zeros((font.height,font.width),dtype=np.uint8);advance=0
        elif cp in font.glyphs:
            bitmap=font.bitmap(cp);advance=font.glyphs[cp]['advance']
        else:
            missing_direct.append(char);bitmap=None;advance=font.width
            arrow={0x2b06:0x2191,0x2b07:0x2193}.get(cp)
            if arrow in font.glyphs:
                bitmap=font.bitmap(arrow);advance=font.glyphs[arrow]['advance']
                fallbacks.append(dict(char=char,kind='arrow_shape_alias',from_codepoint=f'U+{arrow:04X}'))
            else:
                for small in smaller_fonts:
                    top=font.ascent-small.ascent;left=(font.width-small.width)//2
                    if cp in small.glyphs and top>=0 and left>=0 and top+small.height<=font.height:
                        bitmap=np.zeros((font.height,font.width),dtype=np.uint8)
                        bitmap[top:top+small.height,left:left+small.width]=small.bitmap(cp)
                        fallbacks.append(dict(char=char,kind='smaller_native_glyph_no_resampling',from_size=small.height));break
            if bitmap is None:
                missing.append(char);bitmap=np.zeros((font.height,font.width),dtype=np.uint8)
                bitmap[1:-1,1]=bitmap[1:-1,-2]=1;bitmap[1,1:-1]=bitmap[-2,1:-1]=1
        if not 0<=advance<=255:raise ValueError('Advance outside byte range')
        bitmaps.append(bitmap);advances.append(advance)
    raw_glyphs=[pack_bitmap(g) for g in bitmaps];raw=b''.join(raw_glyphs);stride=len(raw_glyphs[0])
    page_size=128
    raw_pages=[b''.join(raw_glyphs[i:i+page_size]) for i in range(0,len(raw_glyphs),page_size)]
    cp_offset=64;adv_offset=cp_offset+len(alphabet)*4;dir_offset=align4(adv_offset+len(alphabet))
    payload_offset=dir_offset+PAGE.size*len(raw_pages)
    directory,payload=add_pages(raw_pages,[(i,min(page_size,len(alphabet)-i)) for i in range(0,len(alphabet),page_size)],payload_offset)
    header=HEADER.pack(b'ZATFNT01',1,len(alphabet),font.width,font.height,font.ascent,font.descent,page_size,len(raw_pages),
        cp_offset,adv_offset,dir_offset,payload_offset,payload_offset+len(payload),zlib.crc32(raw))
    data=bytearray(header);data.extend(struct.pack('<'+'I'*len(alphabet),*map(ord,alphabet)));data.extend(bytes(advances))
    data.extend(bytes(dir_offset-len(data)));data.extend(directory);data.extend(payload)
    assert len(data)==payload_offset+len(payload)
    path=OUT/f'font_{font.height}px.zfn';path.write_bytes(data)
    row_raw=b''.join(np.packbits(g,axis=1).tobytes() for g in bitmaps)
    row_stride=((font.width+7)//8)*font.height
    row_packed=sum(min(len(block),len(lz10(block))) for at in range(0,len(row_raw),128*row_stride) if (block:=row_raw[at:at+128*row_stride]))
    stats=dict(file=str(path),source_member=member,source_glyphs=len(font.glyphs),subset_symbols=len(alphabet),
        width=font.width,height=font.height,raw_bitmap_bytes=len(raw),package_bytes=len(data),bitmap_page_payload_bytes=len(payload),
        bitmap_whole_zlib_bytes=len(zlib.compress(raw,9)),max_page_raw_bytes=max(map(len,raw_pages)),
        row_aligned_raw_bytes=len(row_raw),row_aligned_lz10_page_bytes=row_packed,
        missing=[dict(char=c,codepoint=f'U+{ord(c):04X}') for c in missing],
        missing_direct=[dict(char=c,codepoint=f'U+{ord(c):04X}') for c in missing_direct],fallbacks=fallbacks,
        full_font_fixed_1bpp_estimate=len(font.glyphs)*stride,bitmap_duplicates=len(raw_glyphs)-len(set(raw_glyphs)))
    return stats,bitmaps,advances


def raw_record_stream(texts):return b''.join(struct.pack('<I',len(t.encode('utf-8')))+t.encode('utf-8') for t in texts)


def build_text(records,alphabet,mode,tag=''):
    texts=[r['text'] for r in records];unique=list(dict.fromkeys(texts));lookup={t:i for i,t in enumerate(unique)}
    assert len(unique)<65536
    symbol_map={c:i for i,c in enumerate(alphabet)};bits=max(1,(len(alphabet)-1).bit_length())
    encoded=[]
    for t in unique:
        ids=[symbol_map[c] for c in t]
        data=t.encode('utf-8') if mode=='utf8' else encode_ids(ids,mode,bits)
        if mode!='utf8':assert decode_ids(data,len(t),mode,bits)==ids
        assert len(t)<65536 and len(data)<=32768
        encoded.append(data)
    positions=[];pages=[];groups=[];current=bytearray();first=0;count=0
    for i,data in enumerate(encoded):
        if current and len(current)+len(data)>32768:
            pages.append(bytes(current));groups.append((first,count));current=bytearray();first=i;count=0
        positions.append((len(pages),len(current),len(data),len(unique[i])))
        current.extend(data);count+=1
    if count:pages.append(bytes(current));groups.append((first,count))
    sym_offset=64;rec_offset=sym_offset+len(alphabet)*4;str_offset=rec_offset+len(records)*2
    dir_offset=align4(str_offset+len(unique)*8);payload_offset=dir_offset+PAGE.size*len(pages)
    directory,payload=add_pages(pages,groups,payload_offset)
    raw=raw_record_stream(texts)
    data=bytearray(HEADER.pack(b'ZATTXT01',1,len(alphabet),len(unique),len(records),len(pages),ENCODINGS[mode],bits,
        sym_offset,rec_offset,str_offset,dir_offset,payload_offset,payload_offset+len(payload),zlib.crc32(raw)))
    data.extend(struct.pack('<'+'I'*len(alphabet),*map(ord,alphabet)))
    data.extend(struct.pack('<'+'H'*len(records),*[lookup[t] for t in texts]))
    for position in positions:data.extend(struct.pack('<4H',*position))
    data.extend(bytes(dir_offset-len(data)));data.extend(directory);data.extend(payload)
    path=OUT/f'text_{tag}{mode}.ztx';path.write_bytes(data)
    # Round trip from the persisted packet, including duplicate occurrences.
    decoded=[]
    packet=path.read_bytes();cached_pages=[]
    for i in range(len(pages)):
        _,_,codec,offset,size,raw_size,crc=PAGE.unpack_from(packet,dir_offset+i*PAGE.size)
        raw_page=unlz10(packet[offset:offset+size]) if codec else packet[offset:offset+size]
        assert len(raw_page)==raw_size and zlib.crc32(raw_page)==crc;cached_pages.append(raw_page)
    persisted_positions=[struct.unpack_from('<4H',packet,str_offset+i*8) for i in range(len(unique))]
    for page,offset,size,chars in persisted_positions:
        stream=cached_pages[page][offset:offset+size]
        t=stream.decode('utf-8') if mode=='utf8' else ''.join(alphabet[i] for i in decode_ids(stream,chars,mode,bits))
        decoded.append(t)
    assert [decoded[lookup[t]] for t in texts]==texts
    return dict(file=str(path),mode=mode,symbol_bits=bits,unique_strings=len(unique),records=len(records),pages=len(pages),
        encoded_unique_payload_bytes=sum(map(len,encoded)),compressed_page_payload_bytes=len(payload),package_bytes=len(data),
        index_and_header_bytes=payload_offset,max_page_raw_bytes=max(map(len,pages)),raw_record_crc32=zlib.crc32(raw))


def layout(text,advances,symbol_map,width):
    lines=[];line=[];used=0;closing=set('，。！？；：、）》】」』…');opening=set('（《【「『')
    for char in text:
        if char=='\n':lines.append(line);line=[];used=0;continue
        if char not in symbol_map:continue
        gid=symbol_map[char];advance=advances[gid]
        if not advance:continue
        if line and used+advance>width:
            carry=[]
            if char in closing or line[-1][0] in opening:carry=[line.pop()]
            lines.append(line);line=carry;used=sum(v[2] for v in line)
        line.append((char,gid,advance));used+=advance
    if line:lines.append(line)
    return lines


def preview(size,bitmaps,advances,alphabet,records,ui_font,bilingual_records):
    symbol_map={c:i for i,c in enumerate(alphabet)};commands=[];ui_commands=[]
    ui_bitmaps,ui_advances=ui_font
    height=bitmaps[0].shape[0];line_step=height+3
    marina=[r for r in records if r['kind']=='dialogue' and r['speaker'] and r['speaker'].split()[0]=='m']
    sample=max(marina,key=lambda r:len(visible_text(r['text'])))
    body=visible_text(sample['text']);lines=layout(body,advances,symbol_map,194);rows=(170-35)//line_step
    def emit(text,x,y,color,items=commands,metrics=advances):
        for char in text:
            if char not in symbol_map:continue
            gid=symbol_map[char];items.append((gid,x,y,color));x+=metrics[gid]
        return x
    name='[玛丽娜]';name_x=8+6*advances[symbol_map['-']]
    end=emit('------'+name,8,4,3)
    while end+advances[symbol_map['-']]<=248:end=emit('-',end,4,3)
    y=35
    for line in lines[:rows]:
        x=31
        for _,gid,advance in line:commands.append((gid,x,y,1));x+=advance
        y+=line_step
    labels=['返回','日志','快进','自动','中英','存档','快速存档','快速读档','设置']
    widths=[sum(ui_advances[symbol_map[c]] for c in label) for label in labels]
    x=(256-sum(widths)-4*(len(labels)-1))//2
    for label,width in zip(labels,widths):emit(label,x,181,2,ui_commands,ui_advances);x+=width+4
    def raster(items,glyphs):
        mask=np.zeros((192,256),dtype=np.uint8)
        for gid,x,y,color in items:
            for yy,xx in np.argwhere(glyphs[gid]):
                dx,dy=x+int(xx),y+int(yy)
                if 0<=dx<256 and 0<=dy<192:mask[dy,dx]=color
        return mask
    def compose(main_items,small_items,tag):
        main=raster(main_items,bitmaps);ui=raster(small_items,ui_bitmaps)
        np.save(OUT/f'{tag}_expected_main.npy',main);np.save(OUT/f'{tag}_expected_ui.npy',ui)
        (OUT/f'{tag}.commands').write_bytes(b''.join(struct.pack('<IhhI',*c) for c in main_items))
        (OUT/f'{tag}_ui.commands').write_bytes(b''.join(struct.pack('<IhhI',*c) for c in small_items))
        mask=np.where(ui!=0,ui,main);frame=np.zeros_like(mask);top=4+height//2
        frame[top,4:252]=4;frame[190,4:252]=4;frame[top:191,4]=4;frame[top:191,251]=4
        name_width=sum(advances[symbol_map[c]] for c in name)
        frame[:height+8,name_x-2:name_x+name_width+2]=0
        combined=np.where(mask!=0,mask,frame)
        screen=Image.fromarray(np.array(PALETTE,dtype=np.uint8)[combined]);screen.save(OUT/f'{tag}_lower.png')
        upper=Image.open(ROOT/'nds/artifacts/audio_controls.png').convert('RGB').crop((0,0,256,192))
        dual=Image.new('RGB',(256,384));dual.paste(upper,(0,0));dual.paste(screen,(0,192));dual.save(OUT/f'{tag}_dual.png')
    compose(commands,ui_commands,f'preview_{size}px')
    if size==12:
        short=next(r for r in marina if '别误会' in r['text'])
        translated=next(r for r in bilingual_records if r['file']==short['file'] and r['translation_id']==short['translation_id'])
        chinese,*english=[t for t in visible_text(translated['text']).split('\n') if t]
        head=[c for c in commands if c[2]==4];body_items=[];small_items=list(ui_commands)
        emit(chinese,31,35,1,body_items)
        for i,line in enumerate(layout(' '.join(english),ui_advances,symbol_map,194)):
            x=31
            for _,gid,advance in line:small_items.append((gid,x,53+i*11,2));x+=advance
        compose(head+body_items,small_items,'preview_12px_bilingual')
    return dict(body_rows=rows,line_step=line_step,cjk_columns=194//size,body_characters=sum(len(l) for l in lines[:rows]),sample=sample,
        preview=str(OUT/f'preview_{size}px_dual.png'))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--font-zip',type=Path,default=Path(r'C:/Downloads/Compressed/fusion-pixel.zip'))
    parser.add_argument('--language',default='chinese');args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    order=['script.rpy','ep1.rpy']+[f'ep2d{i}.rpy' for i in range(1,8)]+['ep3.rpy']
    paths=sorted((DEFAULT_GAME/'tl'/args.language).glob('*.rpy'),key=lambda p:(order.index(p.name) if p.name in order else 100,p.name))
    records=[];sources=[]
    for path in paths:
        entries=extract_translation(path.read_text(encoding='utf-8-sig'),path.name,args.language)
        records.extend(entries);sources.append(dict(file=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),records=len(entries)))
    assert records
    bilingual_records=[]
    for path in sorted((DEFAULT_GAME/'tl/chzh').glob('*.rpy')):
        bilingual_records.extend(extract_translation(path.read_text(encoding='utf-8-sig'),path.name,'chzh'))
    raw_counts=Counter(''.join(r['text'] for r in records));visible_counts=Counter(''.join(visible_text(r['text']) for r in records))
    extra=set(''.join(r['text'] for r in bilingual_records))-set(raw_counts)-set(map(chr,range(32,127)))
    alphabet=sorted(set(raw_counts)|set(map(chr,range(32,127)))|extra,key=lambda c:(-raw_counts[c],ord(c)))
    (OUT/'corpus.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),encoding='utf-8')
    (OUT/'charset.json').write_text(json.dumps([dict(id=i,char=c,cp=f'U+{ord(c):04X}',raw_count=raw_counts[c],visible_count=visible_counts[c]) for i,c in enumerate(alphabet)],ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'expected_text_records.bin').write_bytes(raw_record_stream([r['text'] for r in records]))
    (OUT/'expected_bilingual_text_records.bin').write_bytes(raw_record_stream([r['text'] for r in bilingual_records]))
    fonts=[];smaller_fonts=[];ui_font=None
    with zipfile.ZipFile(args.font_zip) as archive:
        for name in ['LICENSE-OFL','README.txt']:(OUT/name).write_bytes(archive.read(name))
        for size in [8,10,12]:
            member=f'fusion-pixel-{size}px-monospaced-zh_hans.bdf'
            original=archive.read(member);font=parse_bdf(original)
            stats,bitmaps,advances=build_font(font,alphabet,member,smaller_fonts);stats['source_bdf_bytes']=len(original)
            smaller_fonts.append(font)
            if ui_font is None:ui_font=(bitmaps,advances)
            stats.update(preview(size,bitmaps,advances,alphabet,records,ui_font,bilingual_records));fonts.append(stats)
            print(f'FONT {size}px: {len(font.glyphs)} -> {len(alphabet)} symbols; {stats["package_bytes"]:,}B; missing {len(stats["missing"])}',flush=True)
    texts=[]
    for mode in ['utf8','u16','packed','vbyte']:
        stats=build_text(records,alphabet,mode);texts.append(stats)
        print(f'TEXT {mode}: {stats["package_bytes"]:,}B; {stats["unique_strings"]} unique / {len(records)} occurrences; round trip PASS',flush=True)
    bilingual_variants=[build_text(bilingual_records,alphabet,mode,tag='bilingual_') for mode in ['utf8','u16','packed','vbyte']]
    bilingual_stats=min(bilingual_variants,key=lambda r:r['package_bytes'])
    comparison=Image.new('RGB',(768,384))
    for i,size in enumerate([8,10,12]):comparison.paste(Image.open(OUT/f'preview_{size}px_dual.png'),(i*256,0))
    comparison.save(OUT/'font_comparison_native.png');comparison.resize((1536,768),Image.Resampling.NEAREST).save(OUT/'font_comparison_2x.png')
    raw_text=''.join(r['text'] for r in records).encode('utf-8')
    summary=dict(language=args.language,font_archive=str(args.font_zip),font_archive_sha256=hashlib.sha256(args.font_zip.read_bytes()).hexdigest(),sources=sources,
        records=len(records),kinds=dict(Counter(r['kind'] for r in records)),source_characters=sum(len(r['text']) for r in records),
        raw_unique_symbols=len(raw_counts),visible_unique_symbols=len(visible_counts),subset_symbols=len(alphabet),
        han_symbols=sum(0x3400<=ord(c)<=0x9fff for c in visible_counts),raw_utf8_bytes=len(raw_text),
        raw_utf8_whole_zlib_bytes=len(zlib.compress(raw_text,9)),fonts=fonts,text_variants=texts,
        bilingual_extra_symbols=sorted(extra),bilingual_text=bilingual_stats,bilingual_text_variants=bilingual_variants,
        preserved='Raw text tags/interpolations/escapes/duplicate occurrence mapping; no Unicode normalization',
        limitation='Translated text corpus only, not the RenPy branching/effect interpreter. Dynamic input outside subset requires fallback glyph policy.',
        recommended_text=min(texts,key=lambda r:r['package_bytes'])['mode'])
    (OUT/'analysis.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'PASS {len(records)} records, {len(alphabet)} symbols; recommended text: {summary["recommended_text"]}')


if __name__=='__main__':main()
