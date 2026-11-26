"""Independent packed-mask/manifest-row oracle for real story_text viewport C.

Audit artifact only. It reads immutable game output and builds a private host DLL;
it never imports the producer layout or modifies product/pack/ROM files.
"""
from pathlib import Path
from collections import defaultdict
import argparse,ctypes,hashlib,json,shutil,struct,subprocess,tempfile,time,zlib
import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[2]
NONE=65535
class View(ctypes.Structure):
    _fields_=[(x,ctypes.c_uint) for x in ('scroll','max_scroll','cn_first','cn_end','en_first','en_end')]

class Pack:
    def __init__(self):
        self.data=(ROOT/'nds/nitrofs/story/text.bin').read_bytes()
        self.manifest=json.loads((ROOT/'nds/artifacts/story_text_manifest.json').read_text(encoding='utf8'))
        self.h=struct.unpack_from('<32I',self.data,8);assert self.h[29]==4
        self.glyphs={};self.records=self.manifest['records']
    def glyph(self,tag):
        if tag==NONE:return None
        if tag not in self.glyphs:
            at,w,packed=struct.unpack_from('<IHH',self.data,self.h[9]+(tag&0x3fff)*8);height=packed&255;advance=packed>>8
            mask=np.unpackbits(np.frombuffer(self.data[self.h[10]+at:self.h[10]+at+(w*height+7)//8],dtype='u1'))[:w*height].reshape(height,w)
            yy,xx=np.nonzero(mask);shadow=bool(tag&0x8000);underline=bool(tag&0x4000)
            self.glyphs[tag]=dict(xx=xx,yy=yy,w=w,h=height,advance=advance,ink=bool(len(xx) or underline),width=max(w,advance if underline else 0)+int(shadow),height=height+int(shadow or underline),shadow=shadow,underline=underline)
        return self.glyphs[tag]
    def raw(self,id):
        at,flags,size,checksum=struct.unpack_from('<IHHI',self.data,self.h[8]+id*12);blob=self.data[at:at+(flags&32767)]
        if flags&32768:raw=blob
        else:
            assert blob[0]==16 and int.from_bytes(blob[1:4],'little')==size
            raw=bytearray();p=4
            while len(raw)<size:
                control=blob[p];p+=1
                for bit in range(7,-1,-1):
                    if len(raw)==size:break
                    value=blob[p];p+=1
                    if control&(1<<bit):
                        token=value*256+blob[p];p+=1;length=(token>>12)+3;distance=(token&4095)+1
                        for _ in range(length):raw.append(raw[-distance])
                    else:raw.append(value)
        assert len(raw)==size and zlib.crc32(raw)==checksum
        return raw,struct.unpack_from('<24H',raw)
    def plane(self,raw,start,count,first,color):
        colors=[color]*count;runs=struct.unpack_from('<H',raw,start+5*count)[0]
        for n in range(runs):
            at,size,ink=struct.unpack_from('<3H',raw,start+5*count+2+n*6);colors[at:at+size]=[ink]*size
        return [dict(gid=struct.unpack_from('<H',raw,start+i*2)[0],x=raw[start+count*2+i],y=raw[start+count*3+i],page=raw[start+count*4+i],index=first+i,color=colors[i]) for i in range(count)]
    def rows(self,id,bi):
        raw,f=self.raw(id);cn=self.plane(raw,f[18] if bi and f[19] else f[11],f[12],0,0xffff)
        en=self.plane(raw,f[13],f[14],f[12],0xca52) if bi else []
        threshold=list(struct.unpack_from('<'+'H'*f[21],raw,f[20])) if bi and f[19] else [f[1]]*len(en)
        groups=defaultdict(lambda:dict(cn=[],en=[]));mapping={}
        for c in cn:groups[c['page'],c['y']]['cn'].append(c)
        if bi:
            for pair in self.records[id]['pairs']:
                key=(pair['page'],pair['cn_y'])
                for j in pair['en_indices']:
                    assert j not in mapping;mapping[j]=key
                    assert en[j]['y']==pair['en_y']
            assert set(mapping)==set(range(len(en))),('manifest EN mapping',id)
            for j,c in enumerate(en):groups[mapping[j]]['en'].append(c)
        rows=[]
        for (page,y),r in sorted(groups.items()):
            r.update(page=page,y=y)
            ch=max((self.glyph(c['gid'])['height'] if c['gid']!=NONE else 0 for c in r['cn']),default=0) or 12
            eh=max((self.glyph(c['gid'])['height'] if c['gid']!=NONE else 0 for c in r['en']),default=0) or 8
            en_y=r['en'][0]['y'] if r['en'] else y+14
            r['height']=max(ch if r['cn'] else 0,en_y-y+eh if bi else 0)
            r['pitch']=max(25 if bi else 15,r['height']+2)
            offsets=[c['y']-y for c in r['cn']+r['en'] if c['gid']!=NONE and self.glyph(c['gid'])['ink'] and c['x']+self.glyph(c['gid'])['width']>190]
            r['wide']=min(offsets) if offsets else None;rows.append(r)
        for a,b in zip(rows,rows[1:]):
            if a['page']==b['page']:a['pitch']=b['y']-a['y'];assert a['pitch']>=a['height'],('overlapping source row',id,bi,a,b)
        return f,cn,en,threshold,rows
    def paint(self,screen,c,y):
        g=self.glyph(c['gid'])
        if g is None:return
        x=c['x'];xx=g['xx'];yy=g['yy'];color=c['color']
        if g['shadow']:screen[y+yy+1,x+xx+1]=0xb18c
        screen[y+yy,x+xx]=color
        if g['underline']:screen[y+g['h'],x:x+g['advance']]=color
    def frame(self,f):
        p=np.full((192,256),0x8000,dtype='<u2');p[10,4:252]=p[190,4:252]=0xffff;p[10:191,4]=p[10:191,251]=0xffff
        at,n=struct.unpack_from('<II',self.data,self.h[12]+f[17]*8);name=[]
        for i in range(n):
            gid,x,y,color,index,page,advance=struct.unpack_from('<HBBHHBB',self.data,self.h[13]+(at+i)*10)
            name.append(dict(gid=gid,x=x,y=y,color=color))
        if name:
            first=min(c['x'] for c in name);last=max(c['x']+self.glyph(c['gid'])['advance'] for c in name);p[10,max(0,first-2):min(252,last+2)]=0x8000
        if f[15]:p[10,191:252]=0x8000
        for c in name:self.paint(p,c,c['y'])
        if f[15]:p[:60,196:256]=np.frombuffer(self.data,dtype='<u2',count=3600,offset=self.h[20]+(f[15]-1)*7200).reshape(60,60)
        return p
    def expected(self,f,rows,threshold,visible,origin):
        screen=self.frame(f);y=30 if self.h[31] else 35;seen_cn=[];seen_en=[];placed=[]
        def revealed(r):return any(c['index']<visible for c in r['cn']) or any(threshold[c['index']-f[1]]<=visible for c in r['en'])
        maximum=max((i for i,r in enumerate(rows) if revealed(r)),default=0);origin=min(origin,maximum)
        for index,r in enumerate(rows[origin:],origin):
            if not revealed(r):break
            if f[15] and r['wide'] is not None and y+r['wide']<60:y=60-r['wide']
            if y+r['height']>174:break
            for c in r['cn']:
                if c['index']<visible:self.paint(screen,c,y);seen_cn.append(c['index'])
            for c in r['en']:
                if threshold[c['index']-f[1]]<=visible:self.paint(screen,c,y+c['y']-r['y']);seen_en.append(c['index'])
            placed.append((index,y,r['height']));y+=r['pitch']
        bounds=lambda x:(min(x),max(x)+1) if x else (0,0)
        return screen,(origin,maximum,*bounds(seen_cn),*bounds(seen_en)),placed,seen_cn,seen_en

def png(path,pixels):
    Image.fromarray(np.stack([(pixels&31)*255//31,((pixels>>5)&31)*255//31,((pixels>>10)&31)*255//31],axis=-1).astype('u1')).save(path)

def run(args):
    start=time.monotonic();pack=Pack();out=ROOT/'nds/artifacts/story_scroll_pixel_audit';out.mkdir(exist_ok=True)
    inputs=['nds/source/story_text.c','nds/source/story_text.h','nds/source/asset_pack.c','nds/nitrofs/story/text.bin','nds/artifacts/story_text_manifest.json']
    hashes={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in inputs}
    report={'result':'RUNNING','method':'Independent wire codec + manifest physical rows + original packed 1bpp masks/style/colour; real C viewport DLL. No producer/old pagination oracle used.','sha256':hashes,'records':0,'viewports':0,'partial_viewports':0,'cn_commands_reached':0,'en_commands_reached':0,'portrait_viewports':0,'shifted_rows':0}
    with tempfile.TemporaryDirectory(prefix='zato_scroll_audit_',ignore_cleanup_errors=True) as temp:
        folder=Path(temp);stub=folder/'stub.c';stub.write_text('void audio_demo_service(void){}\n');dll=folder/'scroll.dll'
        subprocess.run([shutil.which('gcc'),'--shared','-std=c11','-O2','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-I'+str(ROOT/'nds/source'),str(ROOT/'nds/source/story_text.c'),str(ROOT/'nds/source/asset_pack.c'),str(stub),'-o',str(dll)],check=True,capture_output=True)
        lib=ctypes.CDLL(str(dll));lib.story_text_open.argtypes=[ctypes.c_char_p];lib.story_text_error.restype=ctypes.c_char_p
        uint=ctypes.c_uint;ptr=ctypes.POINTER(ctypes.c_uint16)
        lib.story_text_select.argtypes=[uint,uint];lib.story_text_draw_lower_scroll.argtypes=[uint,uint,uint,ctypes.c_int,ctypes.c_int,ctypes.c_int,ptr,ctypes.POINTER(View)]
        lib.story_text_scroll_max.argtypes=[uint,uint,ctypes.c_int];lib.story_text_scroll_follow.argtypes=[uint,uint,ctypes.c_int,uint,uint]
        assert lib.story_text_open(str(ROOT/'nds/nitrofs/story/text.bin').encode()),lib.story_text_error()
        native=np.zeros((192,256),dtype='<u2');np_ptr=native.ctypes.data_as(ptr);vp=View()
        ids=args.ids or [r['id'] for r in pack.records if r['kind']=='say' and r['mode']!=3]
        for id in ids:
            if args.limit and report['records']>=args.limit:break
            assert lib.story_text_select(id,0),lib.story_text_error();reads=lib.story_text_read_calls()
            for bi in (0,1):
                f,cn,en,threshold,rows=pack.rows(id,bi);cn_seen=set();en_seen=set()
                maximum=lib.story_text_scroll_max(id,f[1],bi);assert maximum==len(rows)-1,('maximum',id,bi,maximum,len(rows))
                samples=[(f[1],origin) for origin in range(maximum+1)]
                if id in args.partial_ids:
                    for origin,r in enumerate(rows):
                        for visible in sorted({min(c['index']+1,f[1]) for c in r['cn']}|{threshold[c['index']-f[1]] for c in r['en']}):samples.append((visible,origin))
                for visible,origin in samples:
                    expected,bounds,placed,cs,es=pack.expected(f,rows,threshold,visible,origin)
                    lib.story_text_draw_lower_scroll(id,visible,origin,bi,1,0,np_ptr,ctypes.byref(vp));actual=tuple(getattr(vp,n) for n,_ in vp._fields_)
                    if actual!=bounds or not np.array_equal(native,expected):
                        stem=f'FAIL_{id}_{bi}_{visible}_{origin}';png(out/(stem+'_actual.png'),native);png(out/(stem+'_expected.png'),expected)
                        report.update(result='FAIL',failure={'id':id,'bi':bi,'visible':visible,'origin':origin,'ranges_actual':actual,'ranges_expected':bounds,'mismatch_pixels':int(np.count_nonzero(native!=expected)),'screens':stem});raise AssertionError(report['failure'])
                    report['viewports']+=1;report['partial_viewports']+=visible<f[1];report['portrait_viewports']+=bool(f[15]);report['shifted_rows']+=sum(y!=(30 if pack.h[31] else 35)+sum(rows[i]['pitch'] for i in range(origin,j)) for j,y,_ in placed)
                    if id in args.capture_ids and visible==f[1] and origin in {0,1,maximum}:png(out/f'host_{id}_{"bi" if bi else "cn"}_row{origin}.png',native)
                    if visible==f[1]:cn_seen.update(cs);en_seen.update(es)
                assert cn_seen==set(range(f[1])),('unreachable CN',id,bi,sorted(set(range(f[1]))-cn_seen))
                assert en_seen==set(range(f[1],f[1]+len(en))),('unreachable EN',id,bi)
                report['cn_commands_reached']+=len(cn_seen);report['en_commands_reached']+=len(en_seen)
                assert lib.story_text_read_calls()==reads,('viewport IO',id)
            report['records']+=1
            if report['records']%1000==0:print(json.dumps(report,ensure_ascii=False),flush=True)
        lib.story_text_close()
        import _ctypes
        _ctypes.FreeLibrary(lib._handle)
    for n,value in hashes.items():assert hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==value,('input changed during audit',n)
    report.update(result='PASS',elapsed_seconds=round(time.monotonic()-start,3));return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--ids',type=int,nargs='*');p.add_argument('--limit',type=int);p.add_argument('--partial-ids',type=int,nargs='*',default=[0,197,198,338,492,974,989,3803,9158,11231,11902]);p.add_argument('--capture-ids',type=int,nargs='*',default=[]);p.add_argument('--output',type=Path,default=ROOT/'nds/artifacts/story_scroll_pixel_audit.json');args=p.parse_args()
    try:
        result=run(args);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(json.dumps(result,ensure_ascii=False))
    except Exception:
        import traceback;traceback.print_exc();raise
