"""Read-only integrated upper-only OP ROM proof, using physical input only.

Default runs two fresh Interpreter processes: all1746 frames naturally to Title,
then a separate paused skip and ordinary Bmp16 handoff. No producer, shared
verifier, movie, soundtrack, ROM or emulator RAM is modified by this tool.
"""
from pathlib import Path
import argparse,hashlib,json,struct,subprocess,sys,time,traceback
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
NONE=0xffffffff
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8');temp.replace(path)

def lz10(data,size):
    if len(data)<4 or data[0]!=16 or int.from_bytes(data[1:4],'little')!=size:raise ValueError('LZ10 header')
    result=bytearray();cursor=4
    while len(result)<size:
        control=data[cursor];cursor+=1
        for bit in range(7,-1,-1):
            if len(result)==size:break
            value=data[cursor];cursor+=1
            if control&(1<<bit):
                token=value*256+data[cursor];cursor+=1;length=(token>>12)+3;distance=(token&4095)+1
                if distance>len(result) or length>size-len(result):raise ValueError('LZ10 bounds')
                for _ in range(length):result.append(result[-distance])
            else:result.append(value)
    return bytes(result)

class ZPVFrameDecoder:
    """Independent wire/delta/tile/palette decoder, not the producer encoder."""
    def __init__(self,data):
        self.data=data;magic,*h=struct.unpack_from('<8s20I',data);self.h=h
        assert magic==b'ZATPAL01' and h[:7]==[1,256,144,256,192,0,24]
        self.fps=h[7];self.count=h[8];self.colours=h[10];self.raw_bytes=h[12];self.palette_at=h[16]
        assert h[14]==36 and h[15]==88 and h[18]==len(data)
        self.entries=[struct.unpack_from('<9I',data,88+i*36) for i in range(self.count)]
        self.cache={}
    def upper(self,number):
        if number in self.cache:return self.cache[number]
        raw=bytearray(self.raw_bytes);start=self.entries[number][4]
        for frame in range(start,number+1):
            at,n,decoded,flags,key,palette,pts,expected,reserved=self.entries[frame]
            assert not reserved and at+n<=len(self.data) and key<=frame
            packet=self.data[at:at+n];packet=packet if flags&8 else lz10(packet,decoded);assert len(packet)==decoded
            mode=flags&~8
            if mode==1:assert len(packet)==self.raw_bytes;raw[:]=packet
            elif mode==2:
                assert len(packet)==self.raw_bytes;raw=bytearray(a^b for a,b in zip(raw,packet))
            elif mode==4:
                cursor=72;tile=self.raw_bytes//576
                for i in range(576):
                    if packet[i//8]&(1<<(i&7)):assert cursor+tile<=len(packet);raw[i*tile:(i+1)*tile]=packet[cursor:cursor+tile];cursor+=tile
                assert cursor==len(packet)
            elif mode==16:
                cursor=position=0
                while position<self.raw_bytes:
                    skip,take=struct.unpack_from('<HH',packet,cursor);cursor+=4;assert (skip or take) and position+skip+take<=self.raw_bytes and cursor+take<=len(packet);position+=skip
                    for i in range(take):raw[position+i]^=packet[cursor+i]
                    position+=take;cursor+=take
                assert cursor==len(packet)
            else:raise ValueError('ZPV delta mode')
            value=2166136261
            for b in raw:value=((value^b)*16777619)&NONE
            assert value==expected,('independent raw frame hash',frame)
        indices=np.frombuffer(raw,dtype='u1')
        if self.colours==16:
            expanded=np.empty(len(indices)*2,dtype='u1');expanded[::2]=indices&15;expanded[1::2]=indices>>4;indices=expanded
        indices=indices.reshape(18,32,8,8).transpose(0,2,1,3).reshape(144,256);assert indices.max()<self.colours
        palette=np.frombuffer(self.data,dtype='<u2',count=self.colours,offset=self.palette_at+self.entries[number][5]*self.colours*2)
        words=palette[indices];out=np.zeros((192,256,3),dtype='u1');out[24:168]=np.stack([words&31,(words>>5)&31,(words>>10)&31],axis=2)
        self.cache[number]=out;return out

def check_dual_pixels(frame,expected_upper=None):
    image=np.asarray(frame);assert image.shape==(384,256,3),image.shape
    top=image[:192];bottom=image[192:];assert not top[:24].any() and not top[168:].any(),'upper letterbox must be exact black'
    assert not bottom.any(),'OP lower display must be entirely black'
    if expected_upper is not None:assert np.array_equal(top>>3,expected_upper),('independent ZPV upper RGB15',int(np.count_nonzero(np.any((top>>3)!=expected_upper,axis=2))))
    assert np.any(top[24:168]),'content sample cannot be an all-black frame'
    return dict(lower_pixels=49152,upper_pixels=49152 if expected_upper is not None else 0,black_border_pixels=12288,relation='lower display exact black; upper full centered movie',independent_upper_rgb15=expected_upper is not None)

class Probe:
    def __init__(self,args):
        from verify_story_rom import NativeSession
        self.args=args;self.started=time.monotonic();self.rom_sha=sha(args.rom);assert self.rom_sha==args.expected_sha
        self.video_bytes=args.video.read_bytes();self.decoder=ZPVFrameDecoder(self.video_bytes);self.seen=set();self.timeline=[];self.last_progress=-1
        rom=args.rom.read_bytes();fat,n=struct.unpack_from('<2I',rom,0x48);ranges=[struct.unpack_from('<2I',rom,p) for p in range(fat,fat+n,8)]
        self.embedded=[]
        for path,data in [(args.video,self.video_bytes),(args.audio,args.audio.read_bytes())]:
            assert sum(rom[a:b]==data for a,b in ranges if b-a==len(data))==1,('movie payload in actual ROM',str(path))
            self.embedded.append(dict(source=str(path),bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
        self.session=NativeSession(args);assert self.session.video_status is not None
        self.file_entry,file_entry_size=self.session.symbols.section('file_entry','audio_demo');assert file_entry_size==144
        self.report=dict(result='RUNNING',case=args.case,rom=str(args.rom),rom_sha256=self.rom_sha,rom_bytes=len(rom),map_sha256=sha(args.map),cpu_mode=args.cpu_mode,embedded=self.embedded,checks=[],captures=[],method='Actual integrated immutable ROM, physical L/A/START input and read-only RAM. Black lower screen plus independent upper ZPV tile/palette reconstruction; no seek or source clock writes.')
    def observe(self,state):
        a=state['a'];v=state['video'];s=state['s']
        assert not a['errors'] and not a['music_underruns'] and not a['sfx_underruns'],state
        assert not s['errors'],state
        if v and v['magic']==0x4449565a:
            assert not v['errors'] and v['requested_seek']==NONE,state
            # Magic is set before bounded index/palette loading. No frame has
            # been decoded/presented until the total is published after open.
            if not v['total_frames']:
                assert not v['decoded'] and not v['displayed'],v
                return state
            assert v['total_frames']==self.decoder.count and v['decoded']<=v['total_frames'] and v['displayed']<=v['total_frames'],v
            if v['displayed']:
                assert v['frame']<v['total_frames'];self.seen.add(v['frame'])
                if not self.timeline or self.timeline[-1]['frame']!=v['frame']:self.timeline.append({k:v[k] for k in ('frame','audio_frame','decoded','displayed','commits','paused')})
            if v['active'] and v['audio_frame']:
                # File playback uses file_entry and leaves the last catalog
                # selection field untouched. The start count is the track proof.
                assert a['music_starts']==1 and a['sfx_starts']==0,('OP must have one file soundtrack, not one per screen',state)
            if v['active'] and v['frame']>=self.last_progress+180:
                self.last_progress=v['frame'];print(f'VIDEO {self.args.case} frame{v["frame"]}/{v["total_frames"]} audio{v["audio_frame"]} errors0 underruns0',flush=True)
        return state
    def step(self,keys=()):return self.observe(self.session.step(keys))
    def wait(self,predicate,maximum=12000):
        for _ in range(maximum):
            state=self.step()
            if predicate(state):return state
        raise AssertionError(('wait timeout',self.session.last))
    def vblanks(self,count):
        start=self.session.last['input_vblanks'];return self.wait(lambda s:s['input_vblanks']>=start+count,2400)
    def press(self,key,predicate):
        for _ in range(2400):
            state=self.step((key,))
            if predicate(state):break
        else:raise AssertionError(('physical key timeout',key,self.session.last))
        self.vblanks(2);return self.session.last
    def capture(self,label,video=False):
        if video:self.vblanks(3)
        else:
            start=self.session.last['s']['frames'];self.wait(lambda s:s['s']['ready'] and s['s']['frames']>=start+2)
        path=self.args.screens/(label+'.png');state=self.session.capture(path,self.observe)
        self.report['captures'].append(dict(label=label,path=str(path.resolve()),state=state));return np.asarray(self.session.emu.frame).copy(),state
    def check(self,name,**details):self.report['checks'].append(dict(name=name,result='PASS',**details));print('PASS '+name,flush=True)
    def pause_frame(self,target):
        self.wait(lambda s:s['video']['active'] and s['video']['displayed'] and s['video']['frame']>=target)
        self.press('L',lambda s:s['video']['paused']==1);self.vblanks(3)
        frame,state=self.capture(f'paused_{target:04}',True);v=state['video'];number=v['frame'];sample=v['audio_frame'];commits=v['commits']
        entry=self.session.read(self.file_entry,144);audio_path=entry[64:112].split(b'\0',1)[0].decode('ascii');audio_frames,audio_rate,channels=struct.unpack_from('<IIH',entry,112)
        assert (audio_path,audio_frames,audio_rate,channels)==('movie/op.wav',1283072,22050,2),('OP source soundtrack',audio_path,audio_frames,audio_rate,channels)
        pixels=check_dual_pixels(frame,self.decoder.upper(number));self.vblanks(30);held=self.session.last
        assert held['video']['paused'] and held['video']['frame']==number and held['video']['audio_frame']==sample and held['video']['commits']==commits,('pause must freeze displayed video and audio timeline',state,held)
        after,_=self.capture(f'paused_{target:04}_held',True);assert np.array_equal(frame,after),'paused physical screen changed'
        self.check('same-frame dual pixels and stable paused one-track timeline',target=target,actual_frame=number,audio_sample=sample,pause_vblanks=30,soundtrack=audio_path,channels=channels,**pixels)
        return state
    def title(self):
        self.wait(lambda s:s['s']['ready'] and s['shell'] and s['shell']['active'] and s['shell']['mode']==0)
        image,state=self.capture('title_after_'+self.args.case)
        from verify_story_menu_rom import title_lower_expected
        menu=json.loads((ROOT/'artifacts/story_menu_manifest.json').read_text(encoding='utf8'));words=title_lower_expected(Path(menu['source_game']))
        expected=np.stack([words&31,(words>>5)&31,(words>>10)&31],axis=2)
        assert np.array_equal(image[192:]>>3,expected),'Title lower has stale video scale/palette or wrong UI'
        assert not state['video']['active'] and not state['video']['ready'] and not state['s']['paused']
        self.check('Title exact49152 lower pixels after video closes',frame=state['video']['frame']);return state
    def natural(self):
        assert self.decoder.count==1746
        for target in self.args.sample_frames:
            paused=self.pause_frame(target);sample=paused['video']['audio_frame'];self.press('L',lambda s:not s['video']['paused']);self.wait(lambda s:s['video']['audio_frame']>sample,600)
        state=self.title();v=state['video'];assert v['finished'] and v['frame']==1745 and v['decoded']==v['displayed']==1746,v
        assert self.seen==set(range(1746)),('unobserved OP frames',sorted(set(range(1746))-self.seen))
        assert state['a']['music_starts']==2 and state['a']['sfx_starts']==0,('one OP track then one Title cue',state)
        self.check('complete natural1746-frame OP followed by Title',decoded=v['decoded'],displayed=v['displayed'],observed=len(self.seen),frame=v['frame'],one_op_music_start=True)
    def handoff(self):
        self.pause_frame(180);self.press('START',lambda s:s['s']['ready'] and s['shell'] and s['shell']['active']);self.title()
        self.press('A',lambda s:not s['shell']['active'])
        self.wait(lambda s:s['s']['blocked'] and s['s']['text_id']!=NONE and s['s']['text_mode']!=3 and (not s['transition']['duration'] or s['s']['ticks']-s['transition']['start']>=s['transition']['duration']))
        self.press('L',lambda s:s['s']['paused']);image,state=self.capture('ordinary_bmp16_after_paused_skip')
        for screen,name in enumerate(('upper','lower')):
            address,size=self.session.symbols.section(name,'story_runtime');assert size==98304
            words=np.frombuffer(self.session.read(address,size),dtype='<u2').reshape(192,256);expected=np.stack([words&31,(words>>5)&31,(words>>10)&31],axis=2)
            assert np.array_equal(image[screen*192:(screen+1)*192]>>3,expected),('ordinary Bmp16 screen has stale affine/8bit/palette',name)
            if not screen:assert np.any(expected),'ordinary handoff should include nonblack scene pixels'
        assert not state['video']['active'] and not state['video']['ready']
        self.check('paused skip -> Title -> ordinary two-screen Bmp16 exact RAM pixels',pixels=98304,state=state['s'])
    def run(self):
        try:
            (self.natural if self.args.case=='natural' else self.handoff)()
            assert sha(self.args.rom)==self.rom_sha and sha(self.args.video)==self.embedded[0]['sha256'] and sha(self.args.audio)==self.embedded[1]['sha256'],'immutable input changed'
            self.report.update(result='PASS',wall_seconds=round(time.monotonic()-self.started,3),core_frames=self.session.emu.frame_count,observed_frames=sorted(self.seen),observed_frame_timeline=self.timeline,final_state=self.session.last,limitations='Read-only emulator proof. Physical NDS/R4 performance and subjective soundtrack listening are separate.');return self.report
        except BaseException as error:
            self.report.update(result='FAIL',error=str(error),traceback=traceback.format_exc(),failure_state=self.session.last)
            try:self.capture('failure',bool(self.session.last and self.session.last['video'] and self.session.last['video']['active']))
            except BaseException:pass
            raise
        finally:
            write_json(self.args.output,self.report);self.session.close()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--rom',type=Path,required=True);p.add_argument('--map',type=Path,required=True);p.add_argument('--expected-sha',required=True);p.add_argument('--core',type=Path);p.add_argument('--emulator-tools',type=Path,default=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools'));p.add_argument('--cpu-mode',choices=['interpreter','jit'],default='interpreter');p.add_argument('--case',choices=['both','natural','handoff'],default='both');p.add_argument('--sample-frames',type=int,nargs='*',default=[120,436,873,1309,1700]);p.add_argument('--video',type=Path,default=ROOT/'storyfs/movie/op.zpv');p.add_argument('--audio',type=Path,default=ROOT/'storyfs/movie/op.wav');p.add_argument('--output',type=Path,default=ROOT/'artifacts/story_dual_video_rom_verification.json');p.add_argument('--screens',type=Path,default=ROOT/'artifacts/story_dual_video_screens');a=p.parse_args()
    if a.case!='both':
        print(Probe(a).run()['result'],flush=True);return
    reports=[]
    for case in ('natural','handoff'):
        out=a.output.with_name(a.output.stem+'_'+case+a.output.suffix)
        command=[sys.executable,str(Path(__file__).resolve()),*sys.argv[1:],'--case',case,'--output',str(out),'--screens',str(a.screens/case)]
        result=subprocess.run(command);report=json.loads(out.read_text(encoding='utf8')) if out.exists() else {'result':'FAIL','error':'child produced no report'};reports.append(report)
        if result.returncode or report['result']!='PASS':break
    combined=dict(result='PASS' if len(reports)==2 and all(r['result']=='PASS' for r in reports) else 'FAIL',rom=str(a.rom),rom_sha256=sha(a.rom),cases=reports)
    write_json(a.output,combined);assert combined['result']=='PASS',combined;print('PASS dual-video natural + fresh paused-skip/Bmp16 handoff',flush=True)

if __name__=='__main__':main()
