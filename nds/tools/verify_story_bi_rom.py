"""Actual final ROM BI extremes, using chapter requests and ordinary input only.

This is a representative BI probe, not the complete default-CN story run.
Every selected new CN/EN command must occur in the native presentation ring.
"""
from pathlib import Path
import argparse
import ctypes
import hashlib
import json
import struct
import time
import traceback
import numpy as np
import unicodedata
from collections import defaultdict

from verify_story_rom import NativeSession,ManualController,WireRecords,STATUS,NONE,atomic_json
from pack_assets import unlz10

ROOT=Path(__file__).resolve().parents[1]
SHADOW=0x8000;UNDERLINE=0x4000;INDEX_MASK=0x3fff


def compact_commands(raw,start,end,count,first,default_color,pages):
    """Independent column-stream decoder for the native pixel oracle."""
    assert 0<=start<=end<=len(raw) and end-start>=count*5+2,'compact command bounds'
    runs=struct.unpack_from('<H',raw,start+count*5)[0]
    assert end-start==count*5+2+runs*6,'compact command length'
    result=[]
    for i in range(count):
        gid=struct.unpack_from('<H',raw,start+i*2)[0];page=raw[start+count*4+i]
        assert page<pages,'compact command page'
        result.append(dict(gid=gid,x=raw[start+count*2+i],y=raw[start+count*3+i],page=page,index=first+i,color=default_color))
    previous=0
    for i in range(runs):
        at,n,color=struct.unpack_from('<3H',raw,start+count*5+2+i*6)
        assert n and previous<=at<=count and n<=count-at and color&0x8000,'compact color run'
        for j in range(at,at+n):result[j]['color']=color
        previous=at+n
    return result


def decode_record4(raw):
    assert len(raw)>=48,'format4 record header'
    f=struct.unpack_from('<24H',raw)
    assert f[9]==48 and f[11]==f[10]+f[3]*12 and f[23]==0,'format4 record offsets'
    paired=bool(f[18]);en_end=f[18] if paired else len(raw)
    cn=compact_commands(raw,f[11],f[13],f[12],0,0xffff,f[4])
    en=compact_commands(raw,f[13],en_end,f[14],f[12],0xffff if f[16]==2 else 0xca52,f[5])
    if paired:
        assert f[19]==f[12]==f[1] and f[21]==f[14] and f[22]==f[20]+2*f[21] and len(raw)==f[22]+2*f[5],'format4 paired extension'
        bi=compact_commands(raw,f[18],f[20],f[19],0,0xffff,f[5])
        thresholds=list(struct.unpack_from('<'+'H'*f[21],raw,f[20]))
        caps=list(struct.unpack_from('<'+'H'*f[5],raw,f[22]))
        assert all(t<=f[1] for t in thresholds) and caps and caps[-1]==f[1] and caps==sorted(caps),'format4 reveal/caps'
    else:
        assert not any(f[18:]),'nonpaired extension must be empty'
        bi=cn;thresholds=[f[1]]*len(en);caps=[]
    return dict(fields=f,cn=cn,bi_cn=bi,en=en,thresholds=thresholds,caps=caps,paired=paired)


def revealed_english(record,page,visible):
    return [c for c,t in zip(record['en'],record['thresholds']) if c['page']==page and c['gid']!=65535 and t<=visible]


def language_switch_due(text_id,anchor,switched):
    return not switched and anchor is not None and text_id!=NONE and text_id>=anchor


def punctuation_hang_rows(commands,oracle,plain,nominal=225,hard=248):
    rows=defaultdict(list)
    for c in commands:
        if c['gid']!=65535:rows[(c['page'],c['y'])].append(c)
    result=[]
    for (page,y),row in rows.items():
        hanging=[]
        for c in row:
            assert c['x']+oracle.bounds(c)[0]<=hard,'punctuation physical boundary'
            if c['x']+oracle.glyph(c['gid'])[1]>nominal:hanging.append(c)
        assert len(hanging)<=2,('more than two hanging punctuation glyphs',page,y,hanging)
        assert all(unicodedata.category(plain[c['index']]).startswith('P') for c in hanging),('nonpunctuation beyond nominal edge',page,y,hanging)
        if hanging:result.append(dict(page=page,y=y,indices=[c['index'] for c in hanging],characters=''.join(plain[c['index']] for c in hanging)))
    return result


class PacketPixelOracle:
    """RGB15 body pixels from 1bpp bytes, independent of PIL font rendering."""
    def __init__(self,data):self.data=data;self.h=struct.unpack_from('<32I',data,8);self.cache={}
    def glyph(self,gid):
        if gid==65535:return None
        index=gid&INDEX_MASK;assert index<self.h[5],'styled glyph index'
        if index not in self.cache:
            offset,w,packed=struct.unpack_from('<IHH',self.data,self.h[9]+index*8)
            height=packed&255;advance=packed>>8
            assert 1<=w<=64 and 1<=height<=64 and 1<=advance<=64,'format4 glyph dimensions'
            n=(w*height+7)//8;bits=self.data[self.h[10]+offset:self.h[10]+offset+n]
            assert len(bits)==n
            mask=np.unpackbits(np.frombuffer(bits,dtype=np.uint8))[:w*height].reshape(height,w).astype(bool)
            self.cache[index]=(mask,advance)
        return self.cache[index]
    def bounds(self,c):
        value=self.glyph(c['gid'])
        if value is None:return 0,0
        mask,advance=value;flags=c['gid']&~INDEX_MASK
        return (mask.shape[1]+1 if flags&SHADOW else max(mask.shape[1],advance) if flags&UNDERLINE else mask.shape[1],mask.shape[0]+int(bool(flags)))
    def paint(self,screen,c):
        value=self.glyph(c['gid'])
        if value is None:return
        mask,advance=value;h,w=mask.shape;x,y=c['x'],c['y'];flags=c['gid']&~INDEX_MASK
        iw,ih=self.bounds(c);assert x+iw<=256 and y+ih<=192,'styled command outside screen'
        if flags&SHADOW:screen[y+1:y+h+1,x+1:x+w+1][mask]=0xb18c
        screen[y:y+h,x:x+w][mask]=c['color']
        if flags&UNDERLINE:screen[y+h,x:x+advance]=c['color']
    def body(self,record,page,visible):
        screen=np.full((192,256),0x8000,dtype=np.uint16)
        for c in record['bi_cn']:
            if c['page']==page and c['index']<visible:self.paint(screen,c)
        for c in revealed_english(record,page,visible):self.paint(screen,c)
        return screen
    def has_ink(self,c):
        value=self.glyph(c['gid'])
        return value is not None and (bool(value[0].any()) or bool(c['gid']&UNDERLINE))
    def header(self,record):
        screen=np.full((192,256),0x8000,dtype=np.uint16);screen[10,4:252]=0xffff
        screen[10:191,4]=0xffff;screen[10:191,251]=0xffff
        f=record['fields'];portrait=f[15]
        if portrait:screen[10,191:252]=0x8000
        at,count=struct.unpack_from('<II',self.data,self.h[12]+f[17]*8);names=[]
        for i in range(count):
            gid,x,y,color,index,page,advance=struct.unpack_from('<HBBHHBB',self.data,self.h[13]+(at+i)*10)
            assert not gid&~INDEX_MASK,'static header must contain plain glyph ids'
            names.append(dict(gid=gid,x=x,y=y,color=color,index=index,page=page))
        if names:
            first=min(c['x'] for c in names);last=max(c['x']+self.glyph(c['gid'])[1] for c in names)
            screen[10,max(0,first-2):min(252,last+2)]=0x8000
        for c in names:self.paint(screen,c)
        if portrait:screen[:60,196:]=np.frombuffer(self.data,dtype='<u2',count=3600,offset=self.h[20]+(portrait-1)*7200).reshape(60,60)
        return screen


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Probe:
    def __init__(self,args):
        self.args=args;self.text=json.loads(args.manifest.read_text(encoding='utf8'))
        self.data=args.text.read_bytes();self.h=struct.unpack_from('<32I',self.data,8)
        assert self.h[29]==4 and self.text['record_format']==4,'this BI probe requires format4'
        assert hashlib.sha256(self.data).hexdigest()==self.text['sha256'],'text/manifest mismatch'
        self.rom_sha=sha(args.rom)
        if args.expected_sha:assert self.rom_sha==args.expected_sha,'unexpected ROM SHA256'
        rom=args.rom.read_bytes();fat,length=struct.unpack_from('<2I',rom,0x48)
        ranges=[struct.unpack_from('<2I',rom,at) for at in range(fat,fat+length,8)]
        assert sum(rom[a:b]==self.data for a,b in ranges if b-a==len(self.data))==1,'ROM contains different text package'
        self.wire=WireRecords(self.data,self.text);self.session=NativeSession(args)
        self.oracle=PacketPixelOracle(self.data)
        assert self.session.presentations,'presentation ring is required'
        self.started=time.monotonic();self.required={};self.cn_seen={};self.en_seen={};self.entries={};self.target_ids=set()
        self.observed=set();self.last_progress=0;self.controller=ManualController();self.capture_keys=set();self.inspecting=False
        self.report={'result':'RUNNING','scope':'Actual ROM representative BI extremes only; complete default-CN OP-to-END validation is separate.',
            'rom':{'path':str(args.rom.resolve()),'sha256':self.rom_sha,'bytes':args.rom.stat().st_size},
            'map':str(args.map.resolve()),'cpu_mode':args.cpu_mode,'input_policy':'Only held A at complete manual pages, X for BI, L to freeze naturally reached counts for inspection, and the official requested_chapter hook. Source NW/internal w/finite waits run naturally. No flow/text/pc RAM changes.',
            'opening':'A skips OP in this probe; the separate formal full-chain run verifies natural complete OP.',
            'chapter_requests':[],'phases':[],'keys':{'A':0,'X':0,'L':0},'screens':[],'pixel_checks':[]}

    def requirements(self,id):
        if id in self.required:return self.required[id]
        record=self.wire.get(id);f=record['fields'];at,flags,size,crc=struct.unpack_from('<IHHI',self.data,self.h[8]+id*12)
        raw=self.data[at:at+(flags&32767)];raw=raw if flags&32768 else unlz10(raw)
        packet=decode_record4(raw);assert tuple(packet['fields'][:18])==tuple(f),'independent wire decoders disagree'
        f=packet['fields'];m=self.text['records'][id]
        new_en=m.get('new_en_start',m.get('segments',[{'en_start':0}])[-1]['en_start'] if m.get('segments') else 0)
        chinese=[set() for _ in range(f[5])];english=[set() for _ in range(f[5])]
        for c in packet['bi_cn']:
            if c['gid']!=65535 and c['index']>=f[8]:chinese[c['page']].add(c['index'])
        for c in packet['en']:
            if c['gid']!=65535 and c['index']>=f[12]+new_en:english[c['page']].add(c['index'])
        pairs=[];cnmap={c['index']:c for c in packet['bi_cn']};enmap={c['index']-f[12]:c for c in packet['en']}
        for pair in m.get('pairs',[]):
            if f[7] and pair['source_event_id']!=m['source_event_id']:continue
            cn_ink=[cnmap[i] for i in pair['cn_indices'] if self.oracle.has_ink(cnmap[i])]
            en_ink=[enmap[i] for i in pair['en_indices'] if self.oracle.has_ink(enmap[i])]
            if cn_ink and en_ink:
                cn_end=cn_ink[-1]['index']+1;j=en_ink[-1]['index']-f[12];en_end=packet['thresholds'][j]
                assert cn_end==en_end,('pair final ink reveal differs',id,pair,cn_end,en_end)
                pairs.append(dict(page=pair['page'],cn_y=pair['cn_y'],en_y=pair['en_y'],last_CN_visible=cn_end,last_EN_visible=en_end))
        for c in packet['bi_cn']+packet['en']:
            if c['gid']==65535:continue
            width,height=self.oracle.bounds(c)
            assert c['x']+width<=256 and c['y']+height<=192,('record ink bounds',id,c,width,height)
            if packet['paired']:
                assert c['x']+width<=248 and c['y']+height<=173,('BI physical body boundary',id,c,width,height)
                if f[15] and c['y']<60:assert c['x']+width<=190,('portrait physical first two lines',id,c,width)
        hang=[]
        if id==2167:
            ir=json.loads((ROOT/'artifacts/full_story_ir.json').read_text(encoding='utf8'))
            event=next(e for label in ir['labels'] for e in label['events'] if e.get('global_text_id')==id)
            plain=event['display']['plain'];assert len(plain)==f[1]
            hang=punctuation_hang_rows(packet['bi_cn'],self.oracle,plain)
            assert any(len(row['indices'])==2 for row in hang),'real punctuation2 example no longer exercised'
        if id==3803:assert min(c['y'] for c in packet['bi_cn'] if self.oracle.has_ink(c))==35,'leading EN newlines consumed Chinese rows'
        result={'fields':f,'cn':chinese,'en':english,'packet':packet,'pairs':pairs,'new_en_start':new_en,'punctuation_hang2':hang};self.required[id]=result;return result

    def observe(self,state):
        if not state['s']['ready']:return
        self.session.healthy(state);self.session.cache_records(self.text)
        for entry in self.session.read_presentations():
            if entry['text_id']!=NONE and entry['flags']&1:self.observed.add(entry['text_id'])
            id=entry['text_id']
            if id not in self.target_ids or not(entry['flags']&1) or not(entry['flags']&6) or self.inspecting:continue
            assert entry['flags']&8,('selected extreme was not actually BI',entry)
            record=self.requirements(id);f=record['fields'];page=entry['page'];visible=entry['visible']
            assert page<f[5] and visible<=f[1],('BI bounds',entry,f)
            if page<len(record['cn']):self.cn_seen.setdefault(id,{}).setdefault(page,set()).update(i for i in record['cn'][page] if i<visible)
            self.en_seen.setdefault(id,{}).setdefault(page,set()).update(c['index'] for c in revealed_english(record['packet'],page,visible) if c['index'] in record['en'][page])
            self.entries.setdefault(id,[]).append(entry)
        if time.monotonic()-self.last_progress>=15:
            self.last_progress=time.monotonic();self.persist(state)
            print(f"BI PROGRESS chapter{state['s']['chapter']} text{state['s']['text_id']} seen{len(self.entries)}/{len(self.target_ids)} core{state['core_frame']} wall{time.monotonic()-self.started:.1f}s",flush=True)

    def step(self,keys=()):
        for key in keys:
            if key in self.report['keys']:self.report['keys'][key]+=1
        state=self.session.step(keys);self.observe(state);return state

    def persist(self,state):
        self.report.update(core_frames=state['core_frame'],wall_seconds=time.monotonic()-self.started,state=state['s'],audio=state['a'],records_observed=len(self.observed))
        atomic_json(self.args.output,self.report)

    def boot_skip_op(self):
        pressed=False
        for _ in range(10000):
            state=self.session.step()
            video=state.get('video')
            if video and video['magic']==0x4449565a and video['ready'] and not pressed:
                self.session.step(('A',));pressed=True
            if state['s']['ready']:
                self.observe(state);self.report['opening_skipped_with_A']=pressed;return state
        raise AssertionError('BI probe boot timeout')

    def seek(self,chapter):
        # The only RAM write is the public, authorized chapter request hook.
        address=self.session.status+STATUS.index('requested_chapter')*4
        ctypes.memmove(self.session.base+address-0x02000000,struct.pack('<I',chapter),4)
        self.report['chapter_requests'].append({'chapter':chapter,'hook':'g_story_status.requested_chapter','address':hex(address)})
        self.controller=ManualController()
        for _ in range(3000):
            state=self.step()
            if state['s']['chapter']==chapter and state['s']['requested_chapter']==NONE:return state
        raise AssertionError(('chapter request timeout',chapter,state))

    def language(self,bilingual):
        state=self.session.last
        if bool(state['s']['bilingual'])==bilingual:return state
        for _ in range(120):
            if bool(state['s']['bilingual'])==bilingual:
                before=state['input_vblanks'];state=self.step()
                while state['input_vblanks']==before:state=self.step()
                self.controller=ManualController();return state
            state=self.step(('X',))
        raise AssertionError('X language toggle timeout')

    def pause(self,paused):
        state=self.session.last
        for _ in range(120):
            if bool(state['s']['paused'])==paused:
                before=state['input_vblanks'];state=self.step()
                while state['input_vblanks']==before:state=self.step()
                self.controller=ManualController();return state
            state=self.step(('L',))
        raise AssertionError('L pause toggle timeout')

    def partial_picture(self,state):
        s=state['s'];key=(s['text_id'],s['page'])
        if self.args.no_pixel_captures or key in self.capture_keys or s['text_id'] not in self.target_ids or not s['blocked'] or not s['visible']:return state
        required=self.requirements(s['text_id']);page=s['page']
        if not(required['cn'][page] or required['en'][page]):return state
        # L holds a naturally reached count for precise pixels. Inspection
        # commits are excluded from the source/glyph coverage accumulator.
        try:
            state=self.pause(True);s=state['s'];actual_key=(s['text_id'],s['page'])
            if actual_key!=key or not s['blocked']:return state
            self.inspecting=True
            path=self.args.screens/f'bi_text{key[0]}_page{key[1]+1}_v{s["visible"]}.png'
            state=self.session.capture(path,self.observe);s=state['s'];record=required['packet']
            expected=self.oracle.body(record,s['page'],s['visible']);screen=np.asarray(self.session.emu.frame)[0 if s['text_mode']==3 else 192:192 if s['text_mode']==3 else 384]>>3
            actual=0x8000|screen[:,:,0].astype(np.uint16)|screen[:,:,1].astype(np.uint16)<<5|screen[:,:,2].astype(np.uint16)<<10
            if s['text_mode']==3:roi=expected!=0x8000
            else:
                roi=np.zeros((192,256),dtype=bool);roi[24:168,5:251]=True
                if record['fields'][15]:roi[:60,196:]=False
            mismatch=np.argwhere((actual!=expected)&roi)
            assert not len(mismatch),('native BI body pixels',key,s['visible'],mismatch[:8].tolist(),[(int(actual[y,x]),int(expected[y,x])) for y,x in mismatch[:8]])
            portrait=record['fields'][15]
            if portrait and s['text_mode']!=3:
                words=np.frombuffer(self.data,dtype='<u2',count=3600,offset=self.h[20]+(portrait-1)*7200).reshape(60,60)
                np.testing.assert_array_equal(actual[:60,196:],words)
            header_match=False
            if s['text_mode']!=3:
                header=self.oracle.header(record)
                np.testing.assert_array_equal(actual[:24],header[:24],err_msg=f'native continuous name line/header {key}')
                header_match=True
            self.capture_keys.add(key);self.report['screens'].append(str(path))
            self.report['pixel_checks'].append(dict(id=key[0],page=key[1],visible=s['visible'],page_limit=s['page_limit'],partial=s['visible']<s['page_limit'],kind='body region' if s['text_mode']!=3 else 'upper text ink only',pixels=int(roi.sum()),portrait_match=bool(portrait),continuous_header_match=header_match,header_pixels=24*256 if header_match else 0,image=str(path)))
            return state
        finally:
            self.inspecting=False;self.pause(False)

    def phase(self,chapter,anchor,ids):
        self.target_ids.update(ids);state=self.seek(chapter);state=self.language(False)
        phase_start=state['core_frame'];switched=anchor is None;maxid=max(ids)
        if switched:state=self.language(True)
        while state['core_frame']-phase_start<180000:
            if language_switch_due(state['s']['text_id'],anchor,switched):
                assert state['s']['text_id']<min(ids),('language anchor reached too late',state)
                state=self.language(True);switched=True
                state=self.session.capture(self.args.screens/f'bi_chapter{chapter}_before_extremes.png',self.observe)
                self.report['screens'].append(str(self.args.screens/f'bi_chapter{chapter}_before_extremes.png'))
            if switched and state['s']['text_id']>maxid and all(id in self.entries for id in ids):break
            if switched:
                state=self.partial_picture(state)
                state=self.session.last
            state=self.step(self.controller.keys(self.session,state))
        else:raise AssertionError(('BI phase timeout',chapter,state))
        rows=[]
        for id in ids:
            record=self.requirements(id);f=record['fields'];pages=[];missing_cn={};missing_en={}
            for page in range(f[5]):
                cn=record['cn'][page] if page<len(record['cn']) else set();en=record['en'][page]
                cn_seen=self.cn_seen.get(id,{}).get(page,set());en_seen=self.en_seen.get(id,{}).get(page,set())
                if cn-cn_seen:missing_cn[page]=sorted(cn-cn_seen)
                if en-en_seen:missing_en[page]=sorted(en-en_seen)
                if cn or en:pages.append({'page':page,'cn_required':sorted(cn),'cn_seen':sorted(cn_seen),'en_required':sorted(en),'en_seen':sorted(en_seen)})
            assert not missing_cn and not missing_en,('actual BI glyphs never submitted',id,missing_cn,missing_en)
            entries=self.entries[id];raw=b''.join(struct.pack('<8I',*(entry[k] for k in ('serial','commands','text_id','page','visible','local','flags','ticks'))) for entry in entries)
            m=self.text['records'][id];rows.append({'id':id,'chapter':m['chapter'],'source_line':m['source_line'],'new_char_start':f[8],'char_count':f[1],'pages_cn':f[4],'pages_bi':f[5],
                'cn_required':sum(map(len,record['cn'])),'cn_seen':sum(len(self.cn_seen.get(id,{}).get(p,set())) for p in range(f[5])),
                'en_required':sum(map(len,record['en'])),'en_seen':sum(len(self.en_seen.get(id,{}).get(p,set())) for p in range(f[5])),'new_en_start':record['new_en_start'],'pair_reveal_checks':record['pairs'],'punctuation_hang2':record['punctuation_hang2'],
                'pages':pages,'presentation_sha256':hashlib.sha256(raw).hexdigest(),'presentations':entries})
        self.report['phases'].append({'result':'PASS','chapter':chapter,'toggle_before_id':anchor,'targets':list(ids),'core_frames':state['core_frame']-phase_start,'records':rows})
        self.persist(state);print(f'BI PHASE PASS chapter{chapter} targets{list(ids)}',flush=True);return state

    def run(self):
        try:
            self.boot_skip_op()
            state=self.phase(0,None,(0,84,198,213,492))
            if not self.args.smoke:
                state=self.phase(1,None,(1430,2167))
                state=self.phase(3,None,(3803,4297,4298,4299))
                state=self.phase(8,9140,tuple(range(9148,9159)))
            assert sha(self.args.rom)==self.rom_sha,'Frozen ROM changed during BI probe'
            self.report.update(result='PASS',conclusion='Final ROM actual presentation ring covered every selected new CN command and every EN command/page in BI mode; this is not a full-story BI traversal.',
                text_package_sha256=hashlib.sha256(self.data).hexdigest(),reader_sha256=sha(Path(__file__).with_name('verify_story_rom.py')),inspection='Official L pause holds naturally reached counts for screenshots. Those paused inspection entries are excluded from glyph/source coverage. Fast authored batches may have no intermediate partial count.')
            self.persist(state);return self.report
        except BaseException as error:
            self.report.update(result='FAIL',error=str(error),traceback=traceback.format_exc())
            if self.session.last:self.persist(self.session.last)
            else:atomic_json(self.args.output,self.report)
            raise
        finally:self.session.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom',type=Path,required=True)
    parser.add_argument('--map',type=Path)
    parser.add_argument('--text',type=Path,default=ROOT/'nitrofs/story/text.bin')
    parser.add_argument('--manifest',type=Path,default=ROOT/'artifacts/story_text_manifest.json')
    parser.add_argument('--expected-sha',help='optional pinned candidate SHA256')
    parser.add_argument('--smoke',action='store_true',help='only ordinary/portrait/style early examples')
    parser.add_argument('--no-pixel-captures',action='store_true')
    parser.add_argument('--emulator-tools',type=Path,default=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools'))
    parser.add_argument('--cpu-mode',choices=['interpreter','jit'],default='interpreter')
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/story_rom_bi_extremes.json')
    parser.add_argument('--screens',type=Path,default=ROOT/'artifacts/story_rom_bi_extremes_screens')
    args=parser.parse_args()
    if args.map is None:args.map=ROOT/'build'/(args.rom.stem+'.map')
    report=Probe(args).run();print(report['result'],report['conclusion'],flush=True)


if __name__=='__main__':main()
