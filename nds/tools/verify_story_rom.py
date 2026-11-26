"""Exercise the actual full-story NDS through libretro, using natural timing.

The driver presses A only for a complete manual page presented for two game
frames. Source {nw}, {w}, finite soft/hard waits and transitions run naturally.
Only emulator controls/RAM inspection are used; source files/ROM are read-only.
"""
from __future__ import annotations
import argparse,collections,ctypes as C,datetime,hashlib,json,re,struct,subprocess,sys,time,traceback
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
NONE=0xffffffff
STATUS='magic frames ready pc chapter source_line text_id visible page layers special_group gfx_version paused bilingual automatic errors ticks wait_hard done commands text_bytes text_peak text_reads gfx_bytes gfx_reads missed_vblanks io_vblanks requested_chapter unlock_low unlock_high'.split()
AUDIO='magic initialized rate music_count sfx_count music_index sfx_index music_playing sfx_playing music_starts sfx_starts callbacks music_underruns sfx_underruns errors music_frames sfx_frames music_loops sfx_finishes ui_version nonzero_samples output_peak'.split()
VIDEO='magic active ready frame decoded displayed total_frames fps colors audio_frame paused seeking finished errors late_frames max_late_samples bytes reads commits source_hash requested_seek'.split()
FLOW_U32='script ops context pc now chapter line commands text_id literal_id date_id local visible page pause_index page_ready wait_started wait_end presented_local presented_visible literal_start literal_duration unlock_low unlock_high'.split()
FLOW_BOOL='window quick_menu blocked waiting hard forever done failed quiet text_active source_auto revealed page_hold presented paused bilingual automatic skip'.split()
SHELL='magic active mode page selection available save_ops load_ops save_errors last_slot io_epoch autosaves global_writes title_visits'.split()

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def atomic_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8');tmp.replace(path)

class Symbols:
    def __init__(self,path):self.text=path.read_text(encoding='utf8')
    def public(self,name):
        for match in re.finditer(r'0x([0-9a-fA-F]+)\s+'+re.escape(name)+r'\s',self.text):
            value=int(match[1],16)
            if 0x02000000<=value<0x02400000:return value
        raise AssertionError('missing RAM symbol '+name)
    def section(self,name,owner,prefix='bss'):
        pattern=r'\.'+prefix+r'\.'+re.escape(name)+r'\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\s+[^\r\n]*[/\\]'+re.escape(owner)+r'\.c\.o'
        matches=list(re.finditer(pattern,self.text))
        assert len(matches)==1,(name,owner,len(matches))
        return int(matches[0][1],16),int(matches[0][2],16)

class CommitSampler:
    """A libretro frame may stop between public status's sequential stores.

    Defer a torn pc/command snapshot; a persistent mismatch still fails. This
    never consumes a source operation and never suppresses audio/error checks.
    """
    def __init__(self):self.pending=None;self.deferred=0
    def accept(self,state,events,walk):
        s=state['s'];n=s['commands']
        if not n:expected=0
        else:
            index=walk[n-1];last=events[index]
            expected=last['a'] if last['name']=='JUMP' else index+1
        if s['pc']==expected:self.pending=None;return True
        if self.pending is None:self.pending=state['core_frame']
        self.deferred+=1
        assert state['core_frame']-self.pending<3,('persistent compiled instruction mismatch',s['pc'],expected,state)
        return False

PRESENTATION='serial commands text_id page visible local flags ticks'.split()
def presentation_entries(raw,old_head):
    assert len(raw) in (1040,1552,1936),('presentation size',len(raw))
    stride=(len(raw)-16)//32;names=PRESENTATION
    if stride>=48:names=names+['cn_first','cn_end','en_first','en_end']
    if stride>=60:names=names+['scroll','scroll_manual','scroll_covered']
    magic,capacity,head,reserved=struct.unpack_from('<4I',raw)
    assert (magic,capacity,reserved)==(0x5054535a,32,0),('presentation header',magic,capacity,reserved)
    count=(head-old_head)&NONE;assert count<=capacity,('presentation ring overrun',old_head,head)
    result=[]
    for step in range(1,count+1):
        serial=(old_head+step)&NONE;at=16+((serial-1)&31)*stride
        values=struct.unpack_from('<'+'I'*len(names),raw,at)
        assert values[0]==serial,('partial presentation entry',serial,values[0])
        result.append(dict(zip(names,values)))
    return head,result

class WireRecords:
    """Decode independent ROM payload requirements, including unseen short Say.

    CN command planes contain one logical index per character; gid65535 is an
    explicit no-ink newline. The full host C/pixel oracle checks the codec too.
    """
    def __init__(self,data,manifest):self.data=data;self.manifest=manifest;self.h=struct.unpack_from('<32I',data,8);self.cache={}
    def get(self,id):
        if id in self.cache:return self.cache[id]
        at,flags,size,checksum=struct.unpack_from('<IHHI',self.data,self.h[8]+id*12)
        blob=self.data[at:at+(flags&32767)]
        if flags&32768:raw=blob
        else:
            assert blob[0]==16 and int.from_bytes(blob[1:4],'little')==size
            out=bytearray();cursor=4
            while len(out)<size:
                mask=blob[cursor];cursor+=1
                for bit in range(7,-1,-1):
                    if len(out)==size:break
                    a=blob[cursor];cursor+=1
                    if mask&(1<<bit):
                        token=a*256+blob[cursor];cursor+=1;n=(token>>12)+3;distance=(token&4095)+1
                        assert distance<=len(out) and len(out)+n<=size
                        for _ in range(n):out.append(out[-distance])
                    else:out.append(a)
            raw=bytes(out)
        import zlib
        assert len(raw)==size and zlib.crc32(raw)==checksum,('wire record checksum',id)
        fields=struct.unpack_from('<18H',raw);m=self.manifest['records'][id]
        assert fields[:9]==tuple(m[k] for k in ('source_line','char_count','duration_ticks','pause_count','pages_cn','pages_bi','mode','continuation','new_char_start')),('wire record metadata',id)
        def plane(start,count,first,pages):
            assert 0<=start<=len(raw) and count*5+2<=len(raw)-start,('wire command bounds',id)
            result=[]
            for i in range(count):
                gid=struct.unpack_from('<H',raw,start+i*2)[0];page=raw[start+count*4+i]
                assert page<pages,('wire page bounds',id,i,page)
                result.append({'gid':gid,'x':raw[start+count*2+i],'y':raw[start+count*3+i],'page':page,'index':first+i})
            return result
        cn=plane(fields[11],fields[12],0,fields[4]);en=plane(fields[13],fields[14],fields[12],fields[5]);bi_cn=cn
        extension=struct.unpack_from('<6H',raw,36) if self.h[29]==4 else (0,)*6
        paired=bool(extension[1]);thresholds=[fields[1]]*fields[14]
        if paired:
            assert extension[1]==fields[12] and extension[3]==fields[14]
            bi_cn=plane(extension[0],extension[1],0,fields[5])
            thresholds=list(struct.unpack_from('<'+'H'*extension[3],raw,extension[2]))
            limits_bi=list(struct.unpack_from('<'+'H'*fields[5],raw,extension[4]))
        else:limits_bi=[min((c['index'] for c in cn if c['page']>p),default=fields[1]) for p in range(fields[5])]
        def required(commands,n):
            pages=[set() for _ in range(n)]
            for c in commands:
                if c['gid']!=65535 and c['index']>=fields[8]:pages[c['page']].add(c['index'])
            return pages
        result={'fields':fields,'pages':required(cn,fields[4]),'pages_bi':required(bi_cn,fields[5]),
            'commands_cn':cn,'commands_bi_cn':bi_cn,'commands_en':en,'english_thresholds':thresholds,
            'limits_bi':limits_bi,'extension':extension,'manifest':m}
        self.cache[id]=result;return result

class GlyphCoverage:
    def __init__(self,records,bilingual=False):
        self.records=records;self.seen={};self.checked=set();self.glyphs=0;self.pages=0;self.bilingual=bilingual
        self.english_seen={};self.english_glyphs=0;self.english_pages=0
    def chinese_pages(self,record):return record['pages_bi'] if self.bilingual else record['pages']
    def english_commands(self,record):
        first=record['fields'][1]+record.get('manifest',{}).get('new_en_start',0)
        return [c for c in record.get('commands_en',[]) if c['gid']!=65535 and c['index']>=first]
    def present(self,entry):
        if not(entry['flags']&1) or not(entry['flags']&6) or entry['text_id']==NONE:return
        id=entry['text_id'];record=self.records.get(id);fields=record['fields']
        assert bool(entry['flags']&8)==bool(self.bilingual),('presentation language mismatch',entry,self.bilingual)
        assert entry['page']<fields[5 if self.bilingual else 4] and entry['visible']<=fields[1],('presentation glyph bounds',entry,fields)
        scrolling=bool(entry['flags']&16)
        for page,required in enumerate(self.chinese_pages(record)):
            if not scrolling and page!=entry['page']:continue
            shown=(i for i in required if i<entry['visible'] and
                   (not scrolling or entry['cn_first']<=i<entry['cn_end']))
            self.seen.setdefault(id,{}).setdefault(page,set()).update(shown)
        if self.bilingual:
            seen=self.english_seen.setdefault(id,set())
            for c in self.english_commands(record):
                threshold=record['english_thresholds'][c['index']-fields[1]]
                inside=entry['en_first']<=c['index']<entry['en_end'] if scrolling else c['page']==entry['page']
                if inside and threshold<=entry['visible']:seen.add(c['index'])
    def finish(self,id):
        record=self.records.get(id);seen=self.seen.get(id,{})
        pages=self.chinese_pages(record)
        missing={page:sorted(indices-seen.get(page,set())) for page,indices in enumerate(pages) if indices-seen.get(page,set())}
        assert not missing,('unpresented new Chinese glyphs',id,missing)
        english=self.english_commands(record) if self.bilingual else []
        missing_en={c['index'] for c in english}-self.english_seen.get(id,set())
        assert not missing_en,('unpresented new English glyphs',id,sorted(missing_en))
        if id not in self.checked:
            self.checked.add(id);self.glyphs+=sum(map(len,pages));self.pages+=sum(bool(p) for p in pages)
            self.english_glyphs+=len(english);self.english_pages+=len({c['page'] for c in english})

class NativeSession:
    def __init__(self,args):
        sys.path.insert(0,str(args.emulator_tools))
        from nds_emulator import Emulator
        class FastEmulator(Emulator):
            BUTTONS={**Emulator.BUTTONS,'Y':1}
            want_video=False
            def environment(self,command,data):
                result=super().environment(command,data)
                if command==16:self.variables[b'desmume_cpu_mode']=args.cpu_mode.encode()
                return result
            def video(self,data,width,height,pitch):
                if self.want_video:super().video(data,width,height,pitch)
        self.emu=FastEmulator(getattr(args,'core',None) or args.emulator_tools/'emulator/desmume_libretro.dll',args.rom)
        self.symbols=Symbols(args.map)
        self.status=self.symbols.public('g_story_status');self.audio=self.symbols.public('g_audio_status')
        try:self.video_status=self.symbols.public('g_story_video_status')
        except AssertionError:self.video_status=None
        try:self.presentations=self.symbols.public('g_story_presentations')
        except AssertionError:self.presentations=None
        self.presentation_size=self.symbols.section('g_story_presentations','story_runtime',prefix='data')[1] if self.presentations else 0
        try:self.shell_status=self.symbols.public('g_story_shell_status')
        except AssertionError:self.shell_status=None
        self.presentation_head=0
        _,self.status_size=self.symbols.section('g_story_status','story_runtime')
        header=re.sub(r'/\*.*?\*/','',(ROOT/'source/story_runtime.h').read_text(encoding='utf8'),flags=re.S)
        body=re.search(r'typedef\s+struct\s*\{(.*?)\}\s*StoryStatus',header,re.S)[1]
        names=[name.strip() for declaration in re.findall(r'uint32_t\s+([^;]+);',body) for name in declaration.split(',')]
        assert names[:30]==STATUS and self.status_size%4==0 and 120<=self.status_size<=len(names)*4
        self.status_names=names[:self.status_size//4];self.committed_status=self.status_size>120
        self.flow,flow_size=self.symbols.section('flow','story_runtime');assert flow_size==116,flow_size
        self.backend,backend_size=self.symbols.section('backend','story_runtime');assert backend_size==848,backend_size
        self.slots,slot_size=self.symbols.section('slots','story_text');assert slot_size==24,slot_size
        self.layers,layer_size=self.symbols.section('layers','story_gfx');assert layer_size==16*24,layer_size
        self.layer_count,_=self.symbols.section('count','story_gfx')
        self.input_vblanks,_=self.symbols.section('vblanks','story_runtime')
        self.gfx_error,_=self.symbols.section('error_text','story_gfx')
        self.base=self.emu.core.retro_get_memory_data(2);self.memory_size=self.emu.core.retro_get_memory_size(2)
        assert self.base and self.memory_size>=4*1024*1024
        self.records={};self.header_ids=set();self.last=None;self.intro_status=None
    def read(self,address,size):
        assert 0x02000000<=address and size<=0x02400000-address,(hex(address),size)
        return C.string_at(self.base+address-0x02000000,size)
    def snapshot(self):
        sequence=struct.unpack('<I',self.read(self.status+120,4))[0] if self.committed_status else 0
        s=dict(zip(self.status_names,struct.unpack('<'+'I'*len(self.status_names),self.read(self.status,self.status_size))))
        a=dict(zip(AUDIO,struct.unpack('<22I',self.read(self.audio,88))))
        raw=self.read(self.flow,116);f=dict(zip(FLOW_U32,struct.unpack_from('<24I',raw)))
        f.update(zip(FLOW_BOOL,map(bool,raw[96:114])))
        if 'scroll' in self.status_names:f.update(scrolling=bool(raw[114]),scroll_ready=bool(raw[115]))
        private_pc,private_commands=f['pc'],f['commands']
        transition,start,duration=struct.unpack('<3I',self.read(self.backend+836,12))
        if self.committed_status:
            # New ROMs publish these inside the even sequence fence. Private
            # flow can already be preparing the next Say after that commit.
            for name in f:
                if name in s:f[name]=s[name]
            f['now']=s['ticks'];f['line']=s['source_line']
            transition=s.get('transition_kind',transition);start=s.get('transition_start',start);duration=s.get('transition_duration',duration)
        after=struct.unpack('<I',self.read(self.status+120,4))[0] if self.committed_status else 0
        video=dict(zip(VIDEO,struct.unpack('<21I',self.read(self.video_status,84)))) if self.video_status else None
        input_vblanks=struct.unpack('<I',self.read(self.input_vblanks,4))[0]
        shell=dict(zip(SHELL,struct.unpack('<14I',self.read(self.shell_status,56)))) if self.shell_status else None
        return {'s':s,'a':a,'f':f,'shell':shell,'video':video,'transition':{'kind':transition,'start':start,'duration':duration},'core_frame':self.emu.frame_count,'coherent':not(sequence&1) and sequence==after,'private_flow_pc':private_pc,'private_flow_commands':private_commands,'input_vblanks':input_vblanks}
    def step(self,keys=()):
        for attempt in range(120):
            self.emu.run(1,keys);self.last=self.snapshot()
            assert not self.last['a']['errors'] and not self.last['a']['music_underruns'] and not self.last['a']['sfx_underruns'],self.last
            if self.last['coherent']:return self.last
        raise AssertionError(('commit remained incomplete for two emulated seconds',self.last))
    @staticmethod
    def healthy(state):
        s,a,f=state['s'],state['a'],state['f']
        assert s['magic']==0x5254535a and s['ready']==1,state
        assert not s['errors'] and not f['failed'],state
        assert a['magic']==0x4455415a and a['initialized'],a
        assert not a['errors'] and not a['music_underruns'] and not a['sfx_underruns'],a
    def boot(self,maximum=10000):
        for _ in range(maximum):
            state=self.step()
            if state['video'] and state['video']['magic']==0x4449565a:
                self.intro_status=state['video'];assert not self.intro_status['errors'],state
            if state['s']['magic']==0x5254535a and state['s']['errors']:raise AssertionError(('boot fatal',state))
            if state['s']['ready']:
                if self.intro_status:assert self.intro_status['finished'] and self.intro_status['decoded']==self.intro_status['total_frames'],('opening did not finish naturally',self.intro_status)
                self.healthy(state);return self.enter_story(state)
        raise AssertionError(('boot timeout',self.last))
    def enter_story(self,state):
        shell=state.get('shell')
        if not shell or not shell['active']:return state
        assert shell['mode']==0,('boot did not reach original title',state)
        for _ in range(1800):
            state=self.step(('A',))
            if not state['shell']['active']:break
        else:raise AssertionError(('title START input never acknowledged',state))
        release=state['input_vblanks']
        for _ in range(180):
            state=self.step()
            if state['input_vblanks']!=release:return state
        raise AssertionError(('title input release never sampled',state))
    def cache_records(self,manifest):
        for slot in range(2):
            pointer,id,size=struct.unpack('<3I',self.read(self.slots+slot*12,12))
            if id==NONE:continue
            if self.committed_status and self.last:
                s=self.last['s'];wanted={s['text_id'],s.get('date_id',NONE),s.get('literal_id',NONE)}
                if id not in wanted:continue
            assert 0<=id<len(manifest['records']),(slot,id)
            if id in self.records:continue
            assert 72<=size<=manifest['max_expanded_record_bytes'],(id,size)
            raw=self.read(pointer,size);fields=struct.unpack_from('<18I',raw);record=manifest['records'][id]
            expected=(record['source_line'],record['char_count'],record['duration_ticks'],record['pause_count'],record['pages_cn'],record['pages_bi'],record['mode'],record['continuation'],record['new_char_start'])
            assert fields[:9]==expected,('native header',id,fields[:9],expected)
            assert fields[15]==record['portrait_id'] and fields[17]==record['header_id'],('native portrait/header id',id,fields)
            pauses=[struct.unpack_from('<3I',raw,fields[10]+i*12) for i in range(fields[3])]
            limits=[fields[1]]*max(fields[4],fields[5])
            for i in range(fields[12]):
                logical=struct.unpack_from('<H',raw,fields[11]+i*10+6)[0];page=raw[fields[11]+i*10+8]
                for p in range(min(page,len(limits))):limits[p]=min(limits[p],logical)
            limits_bi=limits[:fields[5]]
            if fields[9]>=96:
                extension=struct.unpack_from('<6I',raw,72)
                if extension[1]:
                    assert extension[1]==fields[12] and extension[4]+fields[5]*2<=size,('native paired limits',id,extension)
                    limits_bi=list(struct.unpack_from('<'+'H'*fields[5],raw,extension[4]))
            self.records[id]={'fields':fields,'pauses':pauses,'limits':limits,'limits_cn':limits[:fields[4]],'limits_bi':limits_bi,'manifest':record}
            self.header_ids.add(id)
    def read_presentations(self):
        if not self.presentations:return []
        head,entries=presentation_entries(self.read(self.presentations,self.presentation_size),self.presentation_head)
        self.presentation_head=head;return entries
    def active_layers(self):
        count=struct.unpack('<I',self.read(self.layer_count,4))[0];assert count<=16,count
        result=[]
        for i in range(count):
            image,tag,program,placement,start,loaded=struct.unpack('<IIIiIB3x',self.read(self.layers+i*24,24))
            result.append({'image':image,'tag':tag,'program':program,'placement':placement,'start':start,'loaded':bool(loaded)})
        return result
    def capture(self,path,observe=None):
        # The libretro video callback can precede the most recent VRAM DMA.
        # Hold without input for two complete core frames before the sample.
        for _ in range(2):
            state=self.step()
            if observe:observe(state)
        self.emu.want_video=True
        try:state=self.step()
        finally:self.emu.want_video=False
        if observe:observe(state)
        assert self.emu.frame is not None and self.emu.frame.size==(256,384),getattr(self.emu.frame,'size',None)
        path.parent.mkdir(parents=True,exist_ok=True);self.emu.frame.save(path)
        return state
    def close(self):self.emu.close()

class ManualController:
    def __init__(self):self.key=None;self.ready_frame=0;self.outstanding=None;self.release_vblank=None;self.hold_started=0
    def keys(self,session,state):
        s,f=state['s'],state['f'];transition=state['transition'];ready=False;key=None
        generation=(s['text_id'],s['page'],s['pc'])
        vblank=state.get('input_vblanks',s['frames']);core=state.get('core_frame',s['frames'])
        # A one-core pulse can fall entirely between input IRQ samples. Hold
        # one physical key until its effect is committed; this creates only
        # one keysDown edge even while a following cue is loading.
        if self.outstanding==generation:
            assert core-self.hold_started<6000,('held A never acknowledged',state)
            return ('A',)
        if self.outstanding is not None:
            self.outstanding=None;self.release_vblank=vblank;self.key=None;return ()
        if self.release_vblank is not None:
            if vblank==self.release_vblank:return ()
            self.release_vblank=None;self.key=None
        if f['blocked'] and not f['waiting'] and not f['source_auto'] and not f['paused'] and f['pc']==s['pc'] and f['text_id']==s['text_id'] and f['page']==s['page']:
            record=session.records.get(s['text_id'])
            if record:
                fields=record['fields'];limits=record.get('limits_bi' if f['bilingual'] else 'limits_cn',record['limits']);limit=limits[f['page']]
                pending=record['pauses'][f['pause_index']] if f['pause_index']<len(record['pauses']) else None
                pending_here=pending is not None and pending[2]<=limit
                pages=fields[5] if f['bilingual'] else fields[4]
                timed_transition=transition['duration'] and s['ticks']-transition['start']<transition['duration']
                ready=s['visible']>=limit and f['visible']>=limit and not pending_here and not timed_transition
                if f.get('scrolling') and s.get('text_mode')!=3:
                    # The fast batch's visible count can be full while later
                    # rows still await their real viewport commits. An early
                    # A is correctly refused by Flow; holding that same edge
                    # forever would then deadlock this test controller.
                    ready=ready and s.get('scroll_covered',0)>=s['visible']
                if f['page']+1>=pages:ready=ready and f['local']>=fields[2] and s['visible']>=fields[1]
                key=(s['text_id'],s['page'],s['visible'],s['pc'])
        if not ready:self.key=None;return ()
        if key!=self.key:self.key=key;self.ready_frame=s['frames'];return ()
        if s['frames']>=self.ready_frame+1:self.key=None;self.outstanding=generation;self.hold_started=core;return ('A',)
        return ()

def full_walk(events):
    walk=[];pc=0;seen=set()
    while True:
        assert 0<=pc<len(events) and pc not in seen,('invalid/nonlinear loop',pc)
        seen.add(pc);walk.append(pc);e=events[pc]
        if e['name']=='END':break
        pc=e['a'] if e['name']=='JUMP' else pc+1
    return walk

def load_inputs(args):
    script=json.loads((ROOT/'artifacts/story_script_manifest.json').read_text(encoding='utf8'))
    text=json.loads((ROOT/'artifacts/story_text_manifest.json').read_text(encoding='utf8'))
    gfx=json.loads((ROOT/'artifacts/story_gfx_manifest.json').read_text(encoding='utf8'))
    walk=full_walk(script['events']);events=[script['events'][i] for i in walk]
    assert len([e for e in events if e['name']=='TEXT'])==11951
    assert collections.Counter(e['name'] for e in events)['MUSIC']==89
    assert collections.Counter(e['name'] for e in events)['SFX']==217
    audio=[e for e in events if e['name'] in ('MUSIC','STOP_MUSIC','SFX','STOP_SFX')]
    assert len(audio)==399 and len(script['unlocks'])==36
    rom=args.rom.read_bytes();assert 0<len(rom)
    assert rom[0x12]==0 and struct.unpack_from('<I',rom,0x84)[0]==512
    fat,size=struct.unpack_from('<2I',rom,0x48);ranges=[struct.unpack_from('<2I',rom,at) for at in range(fat,fat+size,8)]
    embedded=[]
    names=['assets.zat','story/gfx.bin','story/text.bin','story/script.bin','scene/gfx.bin']
    if 'g_story_shell_status' in args.map.read_text(encoding='utf8'):names.append('story/menu.bin')
    for name in names:
        source=Path(gfx.get('asset_pack_source',ROOT/'nitrofs/assets.zat')) if name=='assets.zat' else ROOT/'nitrofs'/name
        data=source.read_bytes();matches=sum(rom[start:end]==data for start,end in ranges if end-start==len(data))
        assert matches==1,('NitroFS payload mismatch',name,matches);embedded.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    return script,text,gfx,walk,{'bytes':len(rom),'sha256':hashlib.sha256(rom).hexdigest(),'embedded':embedded}

def preflight(args,text):
    session=NativeSession(args);controller=ManualController();checks=[]
    def step(keys=()):
        state=session.step(keys);session.healthy(state);session.cache_records(text);return state
    def until(test,frames=6000,natural=False):
        state=session.snapshot()
        for _ in range(frames):
            session.healthy(state);session.cache_records(text)
            if test(state):return state
            state=step(controller.keys(session,state) if natural else ())
        raise AssertionError(('preflight timeout',state))
    def button(key,test=None):
        before=session.last['input_vblanks']
        for _ in range(1800):
            state=step((key,))
            if test(state) if test else state['input_vblanks']>=before+3:break
        else:raise AssertionError(('preflight button not accepted',key,state))
        released=state['input_vblanks']
        return until(lambda v:v['input_vblanks']>=released+2,1800)
    try:
        state=session.boot();session.cache_records(text)
        state=until(lambda v:v['s']['text_id']==0 and v['f']['blocked'])
        before=state['s'];step(('A',));state=until(lambda v:v['s']['text_id']==0 and v['s']['visible']==text['records'][0]['char_count'],180)
        assert state['s']['pc']==before['pc'];checks.append({'kind':'initial_A_reveals_current_manual_text','state':state['s']})
        step();step(('A',));state=until(lambda v:v['s']['text_id']==1,300)
        checks.append({'kind':'initial_second_A_advances','state':state['s']})
        step();step(('L',));state=until(lambda v:v['s']['paused']==1,120)
        tick=state['s']['ticks'];music=state['a']['music_frames']
        for _ in range(30):state=step()
        assert state['s']['ticks']==tick and state['a']['music_frames']==music,('pause clocks',state)
        checks.append({'kind':'L_freezes_story_and_audio_cursor','ticks':tick})
        step(('L',));until(lambda v:not v['s']['paused'],120);step();step(('X',));state=until(lambda v:v['s']['bilingual']==1,120)
        session.capture(args.screens/'preflight_bilingual.png');step();step(('X',));until(lambda v:not v['s']['bilingual'],120)
        if 'scroll' in state['s']:
            chapter=session.last['s']['chapter']
            for key in ('LEFT','RIGHT'):
                button(key)
                for _ in range(12):state=step()
                assert state['s']['chapter']==chapter,('chapter keys must default disabled',state)
            checks.append({'kind':'chapter_keys_default_disabled','chapter':chapter})
            button('START',lambda v:v['shell']['active'] and v['shell']['mode']==1)
            for selection in range(1,5):
                button('DOWN',lambda v,selection=selection:v['shell']['selection']==selection)
            button('A',lambda v:v['shell']['mode']==5)
            for selection in range(1,4):
                button('DOWN',lambda v,selection=selection:v['shell']['selection']==selection)
            version=session.last['s']['gfx_version']
            button('RIGHT',lambda v:v['s']['gfx_version']!=version)
            button('B',lambda v:v['shell']['mode']==1)
            button('B',lambda v:not v['shell']['active'])
            checks.append({'kind':'chapter_keys_enabled_through_actual_settings'})
        for key,target in [('LEFT',9),('RIGHT',0),('RIGHT',1),('LEFT',0)]:
            prior=session.snapshot()['s'];step();step((key,));state=until(lambda v:v['s']['chapter']==target and v['s']['requested_chapter']==NONE,1200)
            text_reads=state['s']['text_reads']-prior['text_reads'];gfx_reads=state['s']['gfx_reads']-prior['gfx_reads']
            assert 0<=text_reads<=20 and 0<=gfx_reads<=2048,('seek replay did excessive I/O',key,text_reads,gfx_reads,state)
            checks.append({'kind':'chapter_'+key.lower(),'target':target,'text_reads':text_reads,'gfx_reads':gfx_reads,'state':state['s']})
            if target==1:
                state=until(lambda v:v['s']['layers']>=3 and v['f']['blocked'] and v['s']['visible']==text['records'][v['s']['text_id']]['char_count'],6000,True)
                session.capture(args.screens/'preflight_source_left_right.png')
        if state.get('shell'):
            step();step(('START',));state=until(lambda v:v['shell']['active'] and v['shell']['mode']==1,1200)
            tick=state['s']['ticks'];local=state['s']['local']
            for _ in range(30):state=step()
            assert state['s']['ticks']==tick and state['s']['local']==local,('menu source clock',state)
            step(('B',));state=until(lambda v:not v['shell']['active'],1200)
            checks.append({'kind':'START_menu_freezes_then_B_resumes','state':state['s']})
        else:
            step();step(('START',));state=until(lambda v:v['s']['chapter']==0 and v['f']['waiting'],1200)
            checks.append({'kind':'START_restarts_chapter','state':state['s']})
        return {'result':'PASS','checks':checks,'core_frames':session.emu.frame_count}
    except BaseException as error:
        result={'result':'FAIL','phase':'preflight','cpu_mode':args.cpu_mode,'error':str(error),'state':session.last,'checks':checks,'traceback':traceback.format_exc()}
        try:session.capture(args.screens/'preflight_failure.png')
        except BaseException:pass
        atomic_json(args.output.with_name(args.output.stem+'_preflight.json'),result)
        raise
    finally:session.close()

class FullVerifier:
    def __init__(self,args,script,text,gfx,walk,rom,preflight_result):
        self.args=args;self.script=script;self.text=text;self.gfx=gfx;self.walk=walk;self.rom=rom
        self.session=NativeSession(args);self.controller=ManualController();self.commit_sampler=CommitSampler();self.started=time.monotonic()
        self.report={'result':'RUNNING','cpu_mode':args.cpu_mode,'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'rom':rom,'preflight':preflight_result,'screens':[]}
        self.processed=0;self.texts=[];self.max_visible={};self.chapters=set();self.specials=set();self.dates=set();self.portraits=set();self.literals=set();self.audio_commands=0
        self.expected_texts=[script['events'][i]['a'] for i in walk if script['events'][i]['name']=='TEXT']
        self.text_at_command=[NONE]
        for index in walk:
            e=script['events'][index];self.text_at_command.append(e['a'] if e['name']=='TEXT' else self.text_at_command[-1])
        self.expected_specials={script['events'][i]['a'] for i in walk if script['events'][i]['name']=='SPECIAL' and script['events'][i]['a']>=0}
        self.active=[];self.model_special=NONE;self.model_date=self.model_literal=NONE
        self.model_unlocks=0;self.last_progress=0.;self.last_count=0;self.history=collections.deque(maxlen=40)
        self.max_text=0;self.max_gfx=0;self.a_presses=0;self.a_input_frames=0;self.last_A=False;self.capture_keys=set();self.graphic_matches=[];self.last_semantic=None;self.semantic_frame=0
        self.text_bytes=(ROOT/'nitrofs/story/text.bin').read_bytes();self.text_h=struct.unpack_from('<32I',self.text_bytes,8)
        self.wire=WireRecords(self.text_bytes,text);self.coverage=GlyphCoverage(self.wire,getattr(args,'bilingual',False))
        self.report['bilingual_mode']=self.coverage.bilingual
        assert not args.require_presentations or self.session.presentations,'final verification requires native presentation ring'
        self.portrait_names={p['id']:p['attribute'] for p in text['portraits']}
    def apply(self,e):
        name=e['name']
        if name=='SCENE':self.active=[];self.model_literal=NONE
        elif name=='SHOW':
            placement=e['c']&65535;placement=placement-65536 if placement>=32768 else placement
            behind=(e['c']>>16)&65535;behind=behind-65536 if behind>=32768 else behind
            tag=e['b'];old=next((i for i,r in enumerate(self.active) if r['tag']==tag),None)
            place=placement if placement>=0 else self.active[old]['placement'] if old is not None else 0
            insertion=next((i for i,r in enumerate(self.active) if r['tag']==tag or behind>=0 and r['tag']==behind),len(self.active))
            if old is not None:self.active.pop(old)
            self.active.insert(min(insertion,len(self.active)),{'image':e['a'],'tag':tag,'program':e['d'],'placement':place})
        elif name=='HIDE':self.active=[r for r in self.active if r['tag']!=e['a']]
        elif name=='SPECIAL':
            if e['a']>=0:
                if self.model_special==NONE:self.active=[]
                self.model_special=e['a']
            else:self.model_special=NONE
        elif name=='DATE':self.model_date=e['a'] if e['a']>=0 else NONE
        elif name=='LITERAL':self.model_literal=e['a']
        elif name=='HIDE_LITERAL':self.model_literal=NONE
        elif name=='UNLOCK':
            if e['b']:self.model_unlocks|=1<<e['a']
            else:self.model_unlocks&=~(1<<e['a'])
        if name in ('MUSIC','STOP_MUSIC','SFX','STOP_SFX'):self.audio_commands+=1
    def observe(self,state):
        s,f,a=state['s'],state['f'],state['a'];self.session.healthy(state)
        assert self.processed<=s['commands']<=len(self.walk),('command count',self.processed,s)
        if not self.session.committed_status and not self.commit_sampler.accept(state,self.script['events'],self.walk):return
        self.session.cache_records(self.text)
        presented_texts=[]
        for entry in self.session.read_presentations():
            assert entry['commands']<=s['commands'],('presentation belongs to future snapshot',entry,s)
            if not(entry['flags']&1) or not(entry['flags']&6) or entry['text_id']==NONE:continue
            id=entry['text_id'];assert id==self.text_at_command[entry['commands']],('source TEXT at actual presentation',entry)
            if not self.texts or self.texts[-1]!=id:
                if self.texts:
                    prior=self.texts[-1];self.coverage.finish(prior)
                    assert self.max_visible.get(prior,0)>=self.text['records'][prior]['char_count'],('prior Say endpoint never presented',prior,entry)
                assert id==self.expected_texts[len(self.texts)],('ring Say order',id,len(self.texts),entry)
                self.texts.append(id);presented_texts.append(id);m=self.text['records'][id]
                self.history.append({'id':id,'commands':entry['commands'],'line':m['source_line'],'chapter':m['chapter'],'ticks':entry['ticks'],'core_frame':state['core_frame'],'presentation_serial':entry['serial']})
            self.coverage.present(entry);self.max_visible[id]=max(self.max_visible.get(id,0),entry['visible'])
        new_texts=[]
        for order in range(self.processed,s['commands']):
            e=self.script['events'][self.walk[order]];self.apply(e)
            if e['name']=='TEXT':new_texts.append(e['a'])
        if self.session.presentations:
            assert new_texts==presented_texts,('TEXT executed without a matching actual presentation',new_texts,presented_texts,state)
        else:
            assert len(new_texts)<=1,('multiple Say skipped between legacy frame snapshots',new_texts,state)
        if new_texts and not self.session.presentations:
            if self.texts:
                prior=self.texts[-1];assert self.max_visible.get(prior,0)>=self.text['records'][prior]['char_count'],('prior Say endpoint never presented',prior,state)
            id=new_texts[0];assert id==self.expected_texts[len(self.texts)] and s['text_id']==id,('Say order/presentation',id,len(self.texts),state)
            self.texts.append(id);self.history.append({'id':id,'pc':s['pc'],'line':s['source_line'],'chapter':s['chapter'],'ticks':s['ticks'],'core_frame':state['core_frame']})
        self.processed=s['commands']
        if self.processed:
            index=self.walk[self.processed-1];last=self.script['events'][index];expected_pc=last['a'] if last['name']=='JUMP' else index+1
            assert s['pc']==expected_pc,('actual compiled instruction walk',s['pc'],expected_pc,last)
        if s['text_id']!=NONE:
            record=self.text['records'][s['text_id']];assert record['kind']=='say'
            assert s['visible']<=record['char_count'] and s['page']<(record['pages_bi'] if s['bilingual'] else record['pages_cn']),('native count/page',state,record)
            if self.session.committed_status and s['blocked']:
                assert (s['char_count'],s['duration_ticks'],s['text_mode'],s['pause_count'],bool(s['source_auto']))==(record['char_count'],record['duration_ticks'],record['mode'],record['pause_count'],bool(record['automatic'])),('committed Say metadata',state,record)
                cached=self.session.records.get(s['text_id'])
                if cached:
                    limits=cached['limits_bi' if s['bilingual'] else 'limits_cn']
                    assert s['page_limit']==limits[s['page']],('committed page limit',state,limits)
                    pending=cached['pauses'][s['pause_index']] if s['pause_index']<len(cached['pauses']) else (0,0,0)
                    assert (s['pause_start'],s['pause_end'],s['pause_glyphs'])==pending,('committed natural pause',state,pending)
            if not self.session.presentations:self.max_visible[s['text_id']]=max(self.max_visible.get(s['text_id'],0),s['visible'])
            if record['portrait_id']:self.portraits.add(record['portrait_id'])
        self.chapters.add(s['chapter'])
        if s['special_group']!=NONE:self.specials.add(s['special_group'])
        self.max_text=max(self.max_text,s['text_bytes'],s['text_peak']);self.max_gfx=max(self.max_gfx,s['gfx_bytes'])
        if f['pc']==s['pc'] and not (state.get('shell') or {}).get('active'):
            assert s['special_group']==self.model_special,('special routing',s,self.model_special)
            assert s['layers']==len(self.active),('committed source layer count',s,self.active)
            # Layer RAM is working state, outside StoryStatus's fence. Compare
            # only while the private interpreter belongs to this generation.
            if state.get('private_flow_pc',f['pc'])==s['pc'] and state.get('private_flow_commands',s['commands'])==s['commands']:
                native=[{k:r[k] for k in ('image','tag','program','placement')} for r in self.session.active_layers()]
                assert native==self.active,('source image/tag/placement dispatch',s,native,self.active)
            assert f['date_id']==self.model_date and f['literal_id']==self.model_literal,('date/literal dispatch',state,self.model_date,self.model_literal)
            assert s['unlock_low']|(s['unlock_high']<<32)==self.model_unlocks,('unlock dispatch',s,self.model_unlocks)
            if f['date_id']!=NONE:self.dates.add(f['date_id'])
            if f['literal_id']!=NONE:self.literals.add(f['literal_id'])
        semantic=(s['commands'],s['text_id'],s['page'],s['visible'],f['pause_index'],f['waiting'],s['done'])
        if semantic!=self.last_semantic:self.last_semantic=semantic;self.semantic_frame=state['core_frame']
        assert state['core_frame']-self.semantic_frame<6000,('100s emulated natural-time stall',state,list(self.history))
        self.report['state']=s;self.report['audio']=a
    def progress(self,state,force=False):
        now=time.monotonic()
        if not force and len(self.texts)<self.last_count+250 and now-self.last_progress<20:return
        self.last_progress=now;self.last_count=len(self.texts)
        self.report.update(say_presentations=len(self.texts),validated_native_record_headers=len(self.session.header_ids),core_frames=state['core_frame'],wall_seconds=now-self.started,chapters_seen=sorted(self.chapters),special_groups_seen=sorted(self.specials),date_ids_seen=sorted(self.dates),literal_ids_seen=sorted(self.literals),audio_commands_exercised=self.audio_commands,manual_A_presses=self.a_presses,peak_text_bytes=self.max_text,peak_gfx_bytes=self.max_gfx,recent_presentations=list(self.history),committed_status_fence=self.session.committed_status,legacy_status_snapshots_retried=self.commit_sampler.deferred)
        self.report.update(presentation_ring=bool(self.session.presentations),presentation_head=self.session.presentation_head,glyph_coverage_completed_say=len(self.coverage.checked),new_Chinese_glyphs_verified=self.coverage.glyphs,new_glyph_pages_verified=self.coverage.pages,new_English_glyphs_verified=self.coverage.english_glyphs,new_English_pages_verified=self.coverage.english_pages)
        self.report['manual_A_input_core_frames']=self.a_input_frames
        atomic_json(self.args.output,self.report)
        print(f"PROGRESS {len(self.texts)}/11951 Say; chapter {state['s']['chapter']+1}/10 line {state['s']['source_line']}; core {state['core_frame']}; wall {now-self.started:.1f}s",flush=True)
    def capture(self,label,state):
        if label in self.capture_keys:return state
        path=self.args.screens/(label+'.png');state=self.session.capture(path,self.observe)
        self.capture_keys.add(label);self.report['screens'].append({'label':label,'path':str(path),'state':state['s'],'literal':state['f']['literal_id'],'date':state['f']['date_id']})
        return state
    def captures(self,state):
        if (state.get('shell') or {}).get('active'):return state
        s,f=state['s'],state['f'];record=self.text['records'][s['text_id']] if s['text_id']!=NONE else None
        if f['date_id']!=NONE:
            state=self.capture('date_'+str(f['date_id']),state);s,f=state['s'],state['f']
        if s['special_group']!=NONE:
            state=self.capture('special_'+str(s['special_group']),state);s,f=state['s'],state['f']
        if f['literal_id']!=NONE:
            literal=self.text['records'][f['literal_id']];pages=literal['pages_bi'] if s['bilingual'] else literal['pages_cn']
            page=min(pages-1,(s['ticks']-f['literal_start'])*pages//max(f['literal_duration'],1))
            if pages>1:state=self.capture(f'poem_{f["literal_id"]}_page{page+1}',state)
            elif literal['chapter']=='ep3':state=self.capture('credit_'+str(f['literal_id']),state)
        s,f=state['s'],state['f'];record=self.text['records'][s['text_id']] if s['text_id']!=NONE else None
        if record and f['blocked'] and not f['source_auto'] and s['visible']==record['char_count'] and f['pc']==s['pc']:
            attribute=self.portrait_names.get(record['portrait_id'])
            if attribute in {'hmm','neutral','troubled','red'} and 'asya_'+attribute not in self.capture_keys:
                state=self.capture('asya_'+attribute,state)
                expected=np.frombuffer(self.text_bytes,dtype='<u2',count=3600,offset=self.text_h[20]+(record['portrait_id']-1)*7200).reshape(60,60)
                actual=np.asarray(self.session.emu.frame)[192:252,196:256]>>3
                rgb=np.stack((expected&31,(expected>>5)&31,(expected>>10)&31),axis=2)
                assert np.array_equal(actual,rgb),('actual 60px portrait pixels',attribute,state)
            elif record['mode']==1 and not record['portrait_id']:state=self.capture('asya_no_portrait',state)
            transition=state['transition'];steady=not transition['duration'] or state['s']['ticks']-transition['start']>=transition['duration']
            if state['s']['special_group']==NONE and state['f']['literal_id']==NONE and state['f']['date_id']==NONE and record['mode']!=3 and steady and state.get('private_flow_pc',state['s']['pc'])==state['s']['pc']:
                current=self.session.active_layers();static=all(self.gfx['images'][r['image']]['kind']=='static' and r['program']==0 for r in current)
                if static and current:
                    actors=[r for r in current if self.gfx['images'][r['image']].get('frame_id',-1)>=0 and self._frame_category(r)==2]
                    # Old expression tags can remain stacked at center. A
                    # left/right composition is the useful placement sample.
                    dual=len(actors)>=2 and {1,2}.issubset({r['placement'] for r in actors})
                    label='normal_dual_actor' if dual else 'normal_CG' if any(self._frame_category(r)==1 for r in current) else None
                    if label and label not in self.capture_keys:
                        state=self.capture(label,state)
                        sys.path.insert(0,str(ROOT/'tests'));from story_gfx_oracle import Oracle
                        oracle=Oracle();layers=[(r['image'],r['placement'],r['program'],r['start']) for r in current]
                        expected=oracle.draw(layers,state['s']['ticks']);rgb=np.stack((expected&31,(expected>>5)&31,(expected>>10)&31),axis=2)
                        actual=np.asarray(self.session.emu.frame)[:192]>>3
                        assert np.array_equal(actual,rgb),('actual ordinary upper pixels',label,state)
                        self.graphic_matches.append({'label':label,'layers':current,'pixels':'PASS'})
        return state
    def _frame_category(self,layer):
        if not hasattr(self,'asset_frames'):self.asset_frames=json.loads((ROOT/'artifacts/assets_manifest.json').read_text(encoding='utf8'))['frames']
        return self.asset_frames[self.gfx['images'][layer['image']]['frame_id']]['category']
    def run(self):
        try:
            state=self.session.boot()
            if self.coverage.bilingual:
                assert state['s']['text_id']==NONE and not state['s']['bilingual'],'switch BI before the first Say'
                for _ in range(5):state=self.session.step(('X',))
                state=self.session.step();assert state['s']['bilingual']==1,'physical X input enables BI'
            self.audio_baseline={k:state['a'][k] for k in ('music_starts','sfx_starts')}
            self.title_baseline=(state.get('shell') or {}).get('title_visits',0)
            self.report.update(intro_video=self.session.intro_status,audio_baseline_at_story_ready=self.audio_baseline)
            self.observe(state);self.progress(state,True)
            while not state['s']['done']:
                state=self.captures(state)
                keys=self.controller.keys(self.session,state)
                pressed='A' in keys
                if pressed and not self.last_A:self.a_presses+=1
                self.last_A=pressed;before_core=state['core_frame'];state=self.session.step(keys)
                if pressed:self.a_input_frames+=state['core_frame']-before_core
                self.observe(state);self.progress(state)
                if self.args.max_say and len(self.texts)>=self.args.max_say:
                    self.report['result']='PARTIAL';self.progress(state,True);return self.report
                assert state['core_frame']<self.args.max_frames,('core-frame budget reached',state)
            self.observe(state)
            assert self.texts==self.expected_texts and len(self.texts)==11951
            if self.session.presentations:
                self.coverage.finish(self.texts[-1]);assert len(self.coverage.checked)==11951
            assert self.processed==len(self.walk)==len(self.script['events'])
            assert self.audio_commands==399
            title_cues=(state.get('shell') or {}).get('title_visits',0)-self.title_baseline
            assert state['a']['music_starts']-self.audio_baseline['music_starts']-title_cues==89 and state['a']['sfx_starts']-self.audio_baseline['sfx_starts']==217,state['a']
            self.report['additional_title_music_cues']=title_cues
            assert self.chapters==set(range(10)) and self.specials==self.expected_specials
            assert self.dates==set(self.text['date_name_to_id'].values())
            assert self.literals==set(self.text['literal_source_event_id_to_id'].values())
            assert state['s']['unlock_low']==0xffffffff and state['s']['unlock_high']==15
            assert self.model_unlocks==(1<<36)-1
            for id in range(self.text['record_count']):self.wire.get(id)
            coverage_rule='Every new drawable Chinese logical glyph (index >= new_char_start) must occur in an ACKed native presentation: within the actual drawn viewport range for scrolling frames, or on its assigned page for legacy/upper frames. A final visible count alone never proves offscreen glyphs. Newline no-ink commands are excluded; authored cps0/fast batches may cover several glyphs in one commit. Continued old prefixes were already presented by prior Say and are not required again.'
            if self.coverage.bilingual:coverage_rule+=' Every new English non-newline command must also occur inside its actual drawn viewport range (or assigned legacy page), with its reveal threshold reached; language flags must identify BI in each counted commit.'
            self.report.update(validated_wire_record_headers=len(self.wire.cache),native_record_headers_not_observed=sorted(set(range(self.text['record_count']))-self.session.header_ids),coverage_rule=coverage_rule,header_validation_layers='Captured RAM headers are matched strictly. Unobserved short-record RAM headers are reported, not inferred; all ROM wire headers are checked and the separate full host C/pixel oracle covers all11975 expanded records.')
            assert sha(self.args.rom)==self.rom['sha256'],'ROM changed while verification was running'
            self.report.update(result='PASS',ending='Actual ROM END reached after all 11951 Say in order',normal_graphics_pixel_checks=self.graphic_matches,say_order_sha256=hashlib.sha256(struct.pack('<'+'I'*len(self.texts),*self.texts)).hexdigest(),audio_validation='399 compiled story audio operations exercised in the complete command walk; native start counters minus opening baseline confirm 89 music +217 SFX starts; 93 stop operations have no separate exported counter.',limitation='Actual libretro ROM execution and pixel checks; physical NDS/R4 performance and subjective listening remain separate tests.')
            self.progress(state,True);return self.report
        except BaseException as error:
            self.report.update(result='FAIL',error=str(error),traceback=traceback.format_exc(),recent_presentations=list(self.history))
            self.report['failure_state']=state
            try:self.session.capture(self.args.screens/'failure.png')
            except BaseException:pass
            self.report['gfx_error']=self.session.read(self.session.gfx_error,112).split(b'\0')[0].decode('utf8','replace')
            atomic_json(self.args.output,self.report);raise
        finally:self.session.close()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--rom',type=Path,default=ROOT/'zato_story_chain_nitrofs.nds');p.add_argument('--map',type=Path);p.add_argument('--core',type=Path,help='optional isolated test core');p.add_argument('--emulator-tools',type=Path,default=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools'));p.add_argument('--output',type=Path,default=ROOT/'artifacts/story_rom_verification.json');p.add_argument('--screens',type=Path,default=ROOT/'artifacts/story_rom_screens');p.add_argument('--cpu-mode',choices=['interpreter','jit'],default='interpreter');p.add_argument('--skip-preflight',action='store_true');p.add_argument('--preflight-only',action='store_true',help=argparse.SUPPRESS);p.add_argument('--require-presentations',action='store_true',help='require native per-commit ring and complete per-page new-glyph coverage');p.add_argument('--bilingual',action='store_true',help='run all Say in paired BI mode and require every new English glyph');p.add_argument('--max-say',type=int);p.add_argument('--max-frames',type=int,default=1200000);args=p.parse_args()
    if args.map is None:args.map=ROOT/'build'/(args.rom.stem+'.map')
    script,text,gfx,walk,rom=load_inputs(args)
    preflight_path=args.output.with_name(args.output.stem+'_preflight.json')
    if args.preflight_only:
        atomic_json(preflight_path,preflight(args,text));return
    # This libretro DLL cannot safely retro_init again after retro_deinit in
    # one process. A fresh process also isolates seek/input state and counters.
    if args.skip_preflight:checks={'result':'SKIPPED'}
    else:
        subprocess.run([sys.executable,str(Path(__file__).resolve()),*sys.argv[1:],'--preflight-only'],check=True)
        checks=json.loads(preflight_path.read_text(encoding='utf8'));assert checks['result']=='PASS',checks
    atomic_json(preflight_path,checks)
    result=FullVerifier(args,script,text,gfx,walk,rom,checks).run();print(result['result'],result.get('ending',result.get('say_presentations')),flush=True)
if __name__=='__main__':main()
