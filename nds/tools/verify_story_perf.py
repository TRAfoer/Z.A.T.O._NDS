"""Record actual-ROM committed-frame cost and strict audio health.

Core frames are emulated VBlanks, not host wall-clock performance. No input is
sent: this probe preserves the original opening waits and particle population.
"""
from pathlib import Path
import argparse,hashlib,json,struct,time,zlib
from verify_story_rom import NativeSession,ROOT,atomic_json,sha

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom',type=Path,default=ROOT/'zato_story_chain_nitrofs.nds')
    p.add_argument('--map',type=Path)
    p.add_argument('--emulator-tools',type=Path,default=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools'))
    p.add_argument('--cpu-mode',choices=['interpreter','jit'],default='interpreter')
    p.add_argument('--frames',type=int,default=2000)
    p.add_argument('--output',type=Path,default=ROOT/'artifacts/story_perf_trace.json')
    p.add_argument('--fingerprints',action='store_true',help='record both screens and committed status at every core frame')
    p.add_argument('--after-ready',action='store_true',help='start frame budget after the complete natural opening and engine boot')
    args=p.parse_args()
    if args.map is None:args.map=ROOT/'build'/(args.rom.stem+'.map')
    s=NativeSession(args);trace=[];fingerprints=[];before=None;last=None;started=time.monotonic()
    if args.fingerprints:s.emu.want_video=True
    try:
        first_ready=s.boot()['core_frame'] if args.after_ready else None
        for _ in range(args.frames):
            now=s.step();last=now
            if args.fingerprints:
                pixels=s.emu.frame.tobytes() if s.emu.frame is not None else b''
                fingerprints.append([zlib.crc32(pixels),hashlib.sha256(json.dumps(now,sort_keys=True,separators=(',',':')).encode()).hexdigest()])
            if not now['s']['ready']:continue
            if before is None or now['s']['frames']!=before['s']['frames']:
                record={'state':now,'layers':s.active_layers()}
                if before is not None:
                    record.update(vblanks=now['core_frame']-before['core_frame'],gfx_reads=now['s']['gfx_reads']-before['s']['gfx_reads'],io_vblanks=now['s']['io_vblanks']-before['s']['io_vblanks'],redrawn=now['s']['gfx_version']!=before['s']['gfx_version'])
                trace.append(record);before=now
            if now['a']['music_underruns'] or now['a']['sfx_underruns'] or now['a']['errors'] or now['s']['errors']:break
        steady=[r for r in trace if r.get('redrawn') and not r.get('gfx_reads') and not r.get('io_vblanks')]
        healthy=bool(last and last['s']['ready'] and not any(last['a'][k] for k in ['music_underruns','sfx_underruns','errors']) and not last['s']['errors'])
        report={'result':'PASS' if healthy else 'FAIL','rom_sha256':sha(args.rom),'cpu_mode':args.cpu_mode,'core_frames':last['core_frame'] if last else 0,'wall_seconds':time.monotonic()-started,'commits':len(trace),'steady_redrawn_commits':len(steady),'steady_max_vblanks':max((r['vblanks'] for r in steady),default=0),'steady_vblanks_histogram':{str(n):sum(r['vblanks']==n for r in steady) for n in sorted(set(r['vblanks'] for r in steady))},'final':last,'trace':trace,'units':'vblanks are emulator ARM9 execution cost; wall_seconds are host throughput'}
        report['first_ready_core']=first_ready
        if args.fingerprints:report['fingerprints']=fingerprints
        atomic_json(args.output,report)
        print(json.dumps({k:v for k,v in report.items() if k not in ['trace','final','fingerprints']},indent=2),flush=True)
        assert healthy,report['final']
    finally:s.close()

if __name__=='__main__':main()
