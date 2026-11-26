"""Record real-ROM SPU PCM and ring/callback health during physical scrolling.

No RAM writes or ROM rebuild. A previously extracted, real .sav is placed into
a NEW FAT16 test image unchanged. Set ZATO_MPCF_IMAGE before process startup.
"""
from pathlib import Path
import argparse,ctypes as C,hashlib,importlib.util,json,os,re,shutil,struct,sys,traceback,wave
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('audio_scroll_cold',ROOT/'nds/tools/verify_story_scroll_cold.py')
cold=importlib.util.module_from_spec(spec);spec.loader.exec_module(cold)

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def map_memory_summary(path):
    text=path.read_text(encoding='utf8');sections={}
    for m in re.finditer(r'^(\.[A-Za-z0-9_]+)\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)',text,re.M):
        sections[m[1]]=dict(address=int(m[2],16),bytes=int(m[3],16))
    def symbol(name):
        match=re.search(r'0x([0-9a-fA-F]+)\s+'+re.escape(name)+r'\s*=',text)
        assert match,('missing memory symbol',name);return int(match[1],16)
    region=re.search(r'^ewram\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)',text,re.M)
    assert region;origin=int(region[1],16);end=symbol('__end__');heap_end=symbol('__eheap_end')
    allocations=[]
    for m in re.finditer(r'^ (\.bss\.[^\s]+)\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\s+([^\r\n]+)',text,re.M):
        address,size=int(m[2],16),int(m[3],16)
        if origin<=address<heap_end and size:allocations.append(dict(name=m[1],address=address,bytes=size,owner=m[4]))
    rings={a['name'].removeprefix('.bss.'):a['bytes'] for a in allocations if a['name'] in ('.bss.music_ring','.bss.sfx_ring')}
    voices=sum(a['bytes'] for a in allocations if a['name'] in ('.bss.music','.bss.sfx') and 'audio_demo.c.o' in a['owner'])
    return dict(map_sha256=sha(path),sections=sections,ewram_origin=origin,ewram_bytes=int(region[2],16),
        static_end=end,main_ram_static_span=end-origin,linker_heap_interval=heap_end-end,
        audio_ring_bytes=rings,audio_voice_bytes=voices,largest_bss=sorted(allocations,key=lambda a:a['bytes'],reverse=True)[:12],
        note='Static span includes code/RO data/DLDI/BSS/TLS, not runtime heap usage. Linker heap interval is an upper bound before dynamic text/gfx/menu/Maxmod/libfat allocations; ITCM load bytes can overlap BSS after relocation.')


def seed_image(path,fixture):
    cold.create_image(path);data=bytearray(path.read_bytes());payload=fixture.read_bytes();assert len(payload)==524288
    name='ZATO_Story_Chain.sav';short=b'ZATOST~1SAV';checksum=0
    for value in short:checksum=(((checksum&1)<<7)+(checksum>>1)+value)&255
    units=[ord(c) for c in name]+[0];units += [0xffff]*((-len(units))%13)
    root_at=129*512;entries=[]
    for part in range(len(units)//13,0,-1):
        e=bytearray(32);e[0]=part|(0x40 if part==len(units)//13 else 0);e[11]=15;e[13]=checksum
        text=struct.pack('<13H',*units[(part-1)*13:part*13]);e[1:11]=text[:10];e[14:26]=text[10:22];e[28:32]=text[22:];entries.append(e)
    e=bytearray(32);e[:11]=short;e[11]=32;struct.pack_into('<H',e,26,2);struct.pack_into('<I',e,28,len(payload));entries.append(e)
    for i,e in enumerate(entries):data[root_at+i*32:root_at+(i+1)*32]=e
    for cluster in range(2,258):
        for fat_at in (512,65*512):struct.pack_into('<H',data,fat_at+cluster*2,0xffff if cluster==257 else cluster+1)
    data_at=root_at+512*32;data[data_at:data_at+len(payload)]=payload;path.write_bytes(data)
    assert cold.fat_files(path)[name]==payload

class Probe(cold.Probe):
    def __init__(self,a):
        self.stage='boot';self.trace=[];self.pcm=bytearray();self.markers=[]
        super().__init__(a)
        sys.path.insert(0,str(a.emulator_tools));import nds_emulator
        self.audio_cb=nds_emulator.BATCH(self.audio_batch)
        self.single_cb=nds_emulator.AUDIO(lambda left,right:self.pcm.extend(struct.pack('<hh',left,right)))
        self.n.emu.core.retro_set_audio_sample_batch(self.audio_cb);self.n.emu.core.retro_set_audio_sample(self.single_cb)
        av=C.create_string_buffer(40);self.n.emu.core.retro_get_system_av_info(C.byref(av));self.fps,self.rate=struct.unpack_from('<2d',av.raw,24)
        self.resident=self.n.symbols.section('resident','story_text')[0]
        self.lower_dirty_address=self.n.symbols.section('lower_dirty','story_runtime')[0]
        self.lower_valid_address=self.n.symbols.section('lower_valid','story_runtime')[0]
        try:self.perf_address=self.n.symbols.public('g_audio_perf_status')
        except (AssertionError,ValueError,KeyError):self.perf_address=None
        if self.perf_address is not None:
            assert self.n.symbols.section('g_audio_perf_status','audio_demo')[1]==72
        try:self.render_perf_address=self.n.symbols.public('g_story_render_perf')
        except (AssertionError,ValueError,KeyError):self.render_perf_address=None
        if self.render_perf_address is not None:
            self.render_perf_size=self.n.symbols.section('g_story_render_perf','story_runtime',prefix='data')[1]
            assert self.render_perf_size in (48,52)
    def audio_batch(self,pointer,count):self.pcm.extend(C.string_at(pointer,count*4));return count
    def mark(self,label):self.stage=label;self.markers.append(dict(stage=label,core=self.n.emu.frame_count,pcm_frame=len(self.pcm)//4))
    def step(self,keys=()):
        for _ in range(180):
            self.n.emu.run(1,keys);self.state=self.n.snapshot();self.n.last=self.state
            self.observe()
            s,a=self.state['s'],self.state['a'];v=self.n.read(self.voice_addresses['music'],36)
            read,write=struct.unpack_from('<2I',v,20);private=struct.unpack('<24I',self.n.read(self.n.flow,96))
            row=dict(core=self.n.emu.frame_count,stage=self.stage,pcm_frame=len(self.pcm)//4,
                committed=s['frames'],text=s['text_id'],private_text=private[8],private_pc=private[3],
                visible=s['visible'],char_count=s['char_count'],scroll=s.get('scroll',0),max_scroll=s.get('scroll_max',0),
                lower_dirty=self.n.read(self.lower_dirty_address,1)[0],lower_valid=self.n.read(self.lower_valid_address,1)[0],
                read=read,write=write,ready=(write-read)&0xffffffff,enabled=v[32],
                paused=self.n.read(self.voice_addresses['scene_paused'],1)[0],callbacks=a['callbacks'],
                starts=a['music_starts'],index=a['music_index'],underruns=a['music_underruns'],sfx_underruns=a['sfx_underruns'],errors=a['errors'],
                missed=s['missed_vblanks'],text_reads=s['text_reads'],resident=struct.unpack('<I',self.n.read(self.resident,4))[0])
            if self.perf_address is not None:
                names='magic version clock_hz timer_div service_calls last_service max_service_gap max_service_work callback_calls last_callback max_callback_gap max_callback_work max_callback_frames music_min_ready sfx_min_ready music_empty_frames sfx_empty_frames timer_wraps'.split()
                row['audio_perf']=dict(zip(names,struct.unpack('<18I',self.n.read(self.perf_address,72))))
            if self.render_perf_address is not None:
                names='magic version prepare_calls last_prepare max_prepare max_upper max_lower max_commit max_scroll text_bytes text_peak gfx_bytes max_flow'.split()
                count=self.render_perf_size//4
                row['render_perf']=dict(zip(names[:count],struct.unpack('<'+'I'*count,self.n.read(self.render_perf_address,self.render_perf_size))))
            self.trace.append(row)
            if self.state['coherent']:return self.state
        raise AssertionError('no coherent commit')
    def load_fixture(self):
        saved,_=cold.saved_slot(self.args.image,1);self.expected=saved
        self.edge('DOWN',lambda s:s['shell']['selection']==1);self.edge('A',lambda s:s['shell']['mode']==3)
        self.edge('RIGHT',lambda s:s['shell']['selection']==1);self.mark('cold_record_appearance')
        self.edge('A',lambda s:s['shell']['load_ops'] or s['shell']['save_errors']);self.wait(lambda s:self.first is not None)
        assert self.state['shell']['load_ops']==1 and not self.state['shell']['save_errors']
        assert self.first['ack']['text_id']==974 and self.first['ack']['scroll_manual']==1
        return saved
    def run_stress(self,frames):
        self.mark('idle_974')
        for _ in range(120):self.step()
        for repeat in range(3):
            for key in ('UP','DOWN'):
                self.release();self.mark('hold_'+key+'_'+str(repeat))
                for _ in range(frames):self.step((key,))
                self.release()
        if self.state['s']['scroll_manual']:
            self.mark('first_A_resume_follow');self.edge('A',lambda s:s['s']['scroll_manual']==0)
        for target in range(975,self.args.last_text+1):
            self.wait(lambda s:s['s']['visible']==s['s']['char_count'] and s['s']['local']>=s['s']['duration_ticks'] and s['f'].get('scroll_ready',False))
            self.mark('enter_'+str(target));self.edge('A',lambda s,t=target:s['s']['text_id']>=t)
            for _ in range(12):self.step()
            assert self.state['s']['text_id']==target,(target,self.state)
        for key in ('UP','DOWN'):
            self.release();self.mark('longest_'+str(self.args.last_text)+'_hold_'+key)
            for _ in range(frames):self.step((key,))
            self.release()
        self.mark('final_idle')
        for _ in range(120):self.step()

def metrics(probe):
    samples=np.frombuffer(probe.pcm,dtype='<i2').reshape(-1,2);segments=[]
    ends=probe.markers[1:]+[dict(pcm_frame=len(samples),core=probe.n.emu.frame_count)]
    for start,end in zip(probe.markers,ends):
        block=samples[start['pcm_frame']:end['pcm_frame']];zeros=np.all(block==0,axis=1)
        transitions=np.diff(np.r_[False,zeros,False].astype(np.int8));lengths=np.flatnonzero(transitions==-1)-np.flatnonzero(transitions==1)
        rows=[r for r in probe.trace if r['stage']==start['stage']]
        def counter_gaps(field):
            gaps=[];last=None
            for row in rows:
                if last is None:last=row
                elif row[field]!=last[field]:gaps.append(row['core']-last['core']);last=row
            return gaps
        segment=dict(stage=start['stage'],audio_frames=len(block),seconds=len(block)/probe.rate,
            max_zero_run_ms=(int(max(lengths,default=0))*1000/probe.rate),
            rms=float(np.sqrt(np.mean(block.astype(np.float64)**2))) if len(block) else 0,
            min_ring_ready=min((r['ready'] for r in rows if r['enabled'] and not r['paused']),default=None),
            max_callback_gap_core_frames=max(counter_gaps('callbacks'),default=0),
            max_commit_gap_core_frames=max(counter_gaps('committed'),default=0),
            first=rows[0] if rows else None,last=rows[-1] if rows else None)
        if rows and 'audio_perf' in rows[-1]:
            perf=rows[-1]['audio_perf'];factor=perf['timer_div']*1000/perf['clock_hz']
            assert (perf['magic'],perf['version'])==(0x4650415a,1)
            segment['audio_perf_ms']={k:perf[k]*factor for k in ('max_service_gap','max_service_work','max_callback_gap','max_callback_work')}
            segment['audio_perf_cumulative']=perf
            # last_* are event timestamps, not elapsed durations. Compute the
            # observed consecutive gaps too, so a startup maximum cannot hide
            # later scrolling behavior. One emulator sample can contain several
            # services: these observed gaps are bounds, not exact per-call peaks.
            for field in ('last_service','last_callback'):
                timestamps=[]
                for r in rows:
                    value=r['audio_perf'][field]
                    if not timestamps or value!=timestamps[-1]:timestamps.append(value)
                segment['observed_'+field+'_gap_ms']=max((((b-a)&0xffffffff)*factor for a,b in zip(timestamps,timestamps[1:])),default=0)
        if rows and 'render_perf' in rows[-1] and 'audio_perf' in rows[-1]:
            perf=rows[-1]['render_perf'];assert perf['magic']==0x5052465a and perf['version'] in (1,2)
            segment['render_perf_cumulative']=perf
            segment['render_perf_ms']={k:perf[k]*factor for k in ('last_prepare','max_prepare','max_upper','max_lower','max_commit','max_scroll','max_flow') if k in perf}
        if 'hold_' in start['stage'] and rows and 'lower_dirty' in rows[-1]:
            boundary=[]
            for i,row in enumerate(rows):
                limit=0 if 'UP' in start['stage'] else row['max_scroll']
                # Require three preceding observations of this complete source
                # viewport at its bound, so stale flags from the last changed
                # viewport are not confused with repeated no-op input.
                if i>=3 and all(r['scroll']==limit and r['visible']==r['char_count'] and r['text']==row['text'] for r in rows[i-3:i+1]):boundary.append(row)
            segment['boundary_held_observations']=dict(samples=len(boundary),lower_dirty_samples=sum(bool(r['lower_dirty']) for r in boundary),
                lower_invalid_samples=sum(not r['lower_valid'] for r in boundary),
                note='Read-only private flag observations after three stable complete boundary frames; actual submit condition is independently covered by the C runtime test.')
        segments.append(segment)
    return segments

def compare_capture(before,after):
    old={s['stage']:s for s in before['segments']};new={s['stage']:s for s in after['segments']}
    fields=('max_zero_run_ms','min_ring_ready','max_callback_gap_core_frames','max_commit_gap_core_frames')
    return dict(before_rom=before['identity']['rom_sha256'],after_rom=after['identity']['rom_sha256'],
        stages={stage:{field:dict(before=old[stage][field],after=new[stage][field]) for field in fields}
            for stage in old if stage in new},
        note='Core-frame callback gaps are sampled bounds; Timer1 diagnostics are included where present in each raw report. PCM comparisons cover this emulator scene, not physical R4.')


def main():
    p=argparse.ArgumentParser();p.add_argument('--rom',type=Path,default=ROOT/'ZATO_Story_Chain.nds');p.add_argument('--map',type=Path,default=ROOT/'nds/build/zato_story_chain_menu_scroll_dual_final2_nitrofs.map')
    p.add_argument('--expected-sha',default='e51746f661d4f1be2738b6afe1042329d817643531923294a6da5c19041901c2')
    p.add_argument('--image',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--hold-frames',type=int,default=180)
    p.add_argument('--last-text',type=int,default=989)
    p.add_argument('--baseline-report',type=Path)
    p.add_argument('--baseline-map',type=Path,default=ROOT/'nds/build/zato_story_chain_menu_scroll_dual_final2_nitrofs.map')
    p.add_argument('--fixture',type=Path,default=ROOT/'nds/artifacts/cleanup_20261125/fat_fixtures/0f3f654ac9d63849e7a2d1e0bbb527d5ef8db0621e58b1e7c9117e61b9abfab5.sav')
    a=p.parse_args();assert sha(a.rom)==a.expected_sha;assert Path(os.environ['ZATO_MPCF_IMAGE']).resolve()==a.image.resolve()
    assert not a.output.exists();a.output.mkdir(parents=True);seed_image(a.image,a.fixture)
    a.core=ROOT/'nds/artifacts/save_spike/desmume_fat_test.dll';a.emulator_tools=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools');a.cpu_mode='interpreter'
    a.text_manifest=ROOT/'nds/artifacts/story_text_manifest.json';a.text_pack=ROOT/'nds/nitrofs/story/text.bin';probe=None
    identity=dict(rom_sha256=sha(a.rom),map_sha256=sha(a.map),fixture_sha256=sha(a.fixture),tool_sha256=sha(Path(__file__)),core_sha256=sha(a.core))
    # libretro supplies a valid argv filename, so this basename is part of the
    # actual FAT save path. A variant basename would create a different .sav.
    if a.rom.name!='ZATO_Story_Chain.nds':
        copy=a.output/'ZATO_Story_Chain.nds';shutil.copyfile(a.rom,copy)
        assert sha(copy)==identity['rom_sha256'];a.rom=copy
    try:
        probe=Probe(a);probe.boot_title();saved=probe.load_fixture();probe.run_stress(a.hold_frames)
        result=dict(result='CAPTURED',identity=identity,sample_rate=probe.rate,core_fps=probe.fps,source_checkpoint=saved,
            first_ack=probe.first,segments=metrics(probe),final=probe.state,trace=probe.trace,memory_static=map_memory_summary(a.map))
        if a.baseline_report:
            before=json.loads(a.baseline_report.read_text());assert before['result']=='CAPTURED'
            result['comparison']=compare_capture(before,result)
            assert sha(a.baseline_map)==before['identity']['map_sha256']
            old_memory=map_memory_summary(a.baseline_map)
            result['comparison']['memory']=dict(before=old_memory,after=result['memory_static'],
                static_span_delta=result['memory_static']['main_ram_static_span']-old_memory['main_ram_static_span'])
        cold.native.atomic_json(a.output/'report.json',result)
        print(json.dumps(dict(identity=identity,segments=result['segments'],final_audio=probe.state['a']),indent=2))
    except BaseException as error:
        cold.native.atomic_json(a.output/'report.json',dict(result='FAIL',identity=identity,error=str(error),traceback=traceback.format_exc(),state=probe.state if probe else None,trace=probe.trace if probe else None));raise
    finally:
        if probe:
            with wave.open(str(a.output/'actual_spu.wav'),'wb') as f:f.setnchannels(2);f.setsampwidth(2);f.setframerate(round(probe.rate));f.writeframes(probe.pcm)
            probe.n.close()

if __name__=='__main__':main()
