"""Read-only BDF/Ren'Py text helpers and lossless character codecs."""
from dataclasses import dataclass
import ast
import re
import struct
import numpy as np


@dataclass
class BitmapFont:
    width:int
    height:int
    xoff:int
    yoff:int
    ascent:int
    descent:int
    glyphs:dict
    declared:int

    def bitmap(self,cp):
        g=self.glyphs[cp];w,h,x,y=g['box'];out=np.zeros((self.height,self.width),dtype=np.uint8)
        left=x-self.xoff;top=self.ascent-y-h
        for yy,row in enumerate(g['rows']):
            bits=np.unpackbits(np.frombuffer(row,dtype=np.uint8))[:w]
            for xx in np.flatnonzero(bits):
                dx,dy=left+int(xx),top+yy
                if not(0<=dx<self.width and 0<=dy<self.height):raise ValueError(f'Glyph U+{cp:04X} exceeds BDF font bounding box')
                out[dy,dx]=1
        return out


def parse_bdf(data):
    box=None;ascent=descent=declared=None;glyphs={};g=None;bitmap=False
    for line in data.decode('utf-8-sig').splitlines():
        fields=line.split()
        if not fields:continue
        key=fields[0]
        if key=='STARTCHAR':g={'rows':[]};bitmap=False
        elif key=='ENDCHAR':
            w,h,_,_=g['box']
            if len(g['rows'])!=h or any(len(r)!=(w+7)//8 for r in g['rows']):raise ValueError('BDF bitmap dimensions inconsistent')
            if g['cp']>=0:
                if g['cp'] in glyphs:raise ValueError('Duplicate BDF encoding')
                glyphs[g['cp']]=g
            g=None;bitmap=False
        elif g is not None:
            if key=='ENCODING':g['cp']=int(fields[1])
            elif key=='DWIDTH':g['advance']=int(fields[1])
            elif key=='BBX':g['box']=tuple(map(int,fields[1:]))
            elif key=='BITMAP':bitmap=True
            elif bitmap:g['rows'].append(bytes.fromhex(line.strip()))
        elif key=='FONTBOUNDINGBOX':box=tuple(map(int,fields[1:]))
        elif key=='FONT_ASCENT':ascent=int(fields[1])
        elif key=='FONT_DESCENT':descent=int(fields[1])
        elif key=='CHARS':declared=int(fields[1])
    if box is None or ascent is None or descent is None:raise ValueError('Missing BDF global metrics')
    return BitmapFont(*box,ascent,descent,glyphs,declared)


LITERAL=re.compile(r'''[rRuU]?(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')''')


def extract_translation(text,filename,language):
    records=[];block=None;source_ref=None
    for number,line in enumerate(text.splitlines(),1):
        stripped=line.strip()
        reference=re.match(r'#\s*game/(.+?):(\d+)(?=\D|$)',stripped)
        if reference:source_ref={'file':reference[1],'line':int(reference[2])}
        if not stripped or stripped.startswith('#'):continue
        heading=re.match(r'translate\s+(\w+)\s+(\w+)\s*:',stripped)
        if heading:block=heading[2] if heading[1]==language else None;continue
        if block is None or block=='python':continue
        matches=[];at=0
        for match in LITERAL.finditer(stripped):
            if '#' in stripped[at:match.start()]:break
            matches.append(match);at=match.end()
        if not matches:continue
        prefix=stripped[:matches[0].start()].strip()
        if block=='strings':
            if prefix!='new':continue
            kind='ui'
        elif re.fullmatch(r'[\w@ +\-]*',prefix) and prefix not in {'old','new','voice','image','scene','show','play music','play sound'}:
            kind='dialogue'
        else:raise ValueError(f'Unclassified translated literal: {filename}:{number}: {stripped[:100]}')
        value=ast.literal_eval(matches[-1].group())
        if not isinstance(value,str):raise ValueError('Translation must be a static string')
        records.append(dict(file=filename,line=number,translation_id=block,speaker=prefix if kind=='dialogue' else None,
                            text=value,kind=kind,source_ref=source_ref))
    return records


def visible_text(text):
    out=[];i=0
    while i<len(text):
        if text.startswith(('{{','[['),i):out.append(text[i]);i+=2;continue
        if text[i]=='{':
            end=text.find('}',i+1)
            if end>=0:i=end+1;continue
        if text[i]=='[':
            depth=1;end=i+1;quote=None;escaped=False
            while end<len(text) and depth:
                c=text[end]
                if escaped:escaped=False
                elif c=='\\':escaped=True
                elif quote:
                    if c==quote:quote=None
                elif c in ('"',"'"):quote=c
                elif c=='[':depth+=1
                elif c==']':depth-=1
                end+=1
            if depth==0:i=end;continue
        out.append(text[i]);i+=1
    return ''.join(out)


def encode_ids(ids,mode,bits=12):
    if any(i<0 or i>65535 for i in ids):raise ValueError('Symbol ID outside u16')
    if mode=='u16':return struct.pack('<'+'H'*len(ids),*ids)
    if mode=='vbyte':
        out=bytearray()
        for i in ids:
            if i<224:out.append(i)
            else:
                n=i-224
                if n>=4096:raise ValueError('vbyte symbol alphabet exceeds4320')
                out.extend((0xe0|(n>>8),n&255))
        return bytes(out)
    if mode=='packed':
        out=bytearray();acc=left=0
        for i in ids:
            if i>=1<<bits:raise ValueError('Symbol outside packed bit width')
            acc=(acc<<bits)|i;left+=bits
            while left>=8:
                left-=8;out.append((acc>>left)&255)
        if left:out.append((acc<<(8-left))&255)
        return bytes(out)
    raise ValueError('Unknown character codec')


def decode_ids(data,count,mode,bits=12):
    if mode=='u16':
        if len(data)!=count*2:raise ValueError('Truncated u16 stream')
        return list(struct.unpack('<'+'H'*count,data))
    if mode=='vbyte':
        ids=[];i=0
        while i<len(data):
            first=data[i];i+=1
            if first<224:ids.append(first)
            elif first<240 and i<len(data):ids.append(224+((first&15)<<8)+data[i]);i+=1
            else:raise ValueError('Invalid/truncated vbyte stream')
        if len(ids)!=count:raise ValueError('Character count mismatch')
        return ids
    if mode=='packed':
        if len(data)!=(count*bits+7)//8:raise ValueError('Truncated packed stream')
        value=int.from_bytes(data,'big');padding=len(data)*8-count*bits
        if padding and value&((1<<padding)-1):raise ValueError('Nonzero packed padding')
        return [(value>>(len(data)*8-(i+1)*bits))&((1<<bits)-1) for i in range(count)]
    raise ValueError('Unknown character codec')


def pack_bitmap(bitmap):return np.packbits(np.asarray(bitmap,dtype=np.uint8).ravel()).tobytes()
def unpack_bitmap(data,width,height):return np.unpackbits(np.frombuffer(data,dtype=np.uint8))[:width*height].reshape(height,width)
