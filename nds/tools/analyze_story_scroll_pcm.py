"""Match recorded SPU PCM windows to the approved decoded catalog stream.

This observes nonzero replay as well as digital silence. It does not equate
zero ring underruns with uninterrupted hardware output. Positions are measured
by independent waveform correlation, not inferred from diagnostic frame counts.
"""
from pathlib import Path
import argparse,hashlib,json,subprocess,wave
import numpy as np
from scipy.signal import correlate

ROOT=Path(__file__).resolve().parents[2]


def match_window(reference,observed,expected,radius=4096):
    length=len(observed);lo=max(0,int(expected)-radius);hi=min(len(reference),int(expected)+radius+length)
    search=reference[lo:hi].astype(np.float64);block=observed.astype(np.float64)
    # Remove the window DC, including each channel independently. Local search
    # energy uses the same channel-wise DC removal.
    block-=block.mean(axis=0);energy=np.sum(block*block)
    if energy<1:return None
    numerator=sum(correlate(search[:,c],block[:,c],mode='valid',method='fft') for c in range(2))
    ones=np.ones(length);denominator=np.zeros(len(numerator))
    for c in range(2):
        sums=np.convolve(search[:,c],ones,mode='valid')
        squared=np.convolve(search[:,c]**2,ones,mode='valid')
        denominator+=np.maximum(0,squared-sums*sums/length)
    scores=numerator/np.sqrt(np.maximum(1,denominator*energy));best=int(np.argmax(scores))
    return dict(position=lo+best,correlation=float(scores[best]))


def analyze(report_path):
    report=json.loads(report_path.read_text());assert report['result']=='CAPTURED'
    with wave.open(str(report_path.with_name('actual_spu.wav')),'rb') as f:
        assert f.getnchannels()==2 and f.getsampwidth()==2 and f.getframerate()==44100
        samples=np.frombuffer(f.readframes(f.getnframes()),dtype='<i2').reshape(-1,2)
    cp=report['source_checkpoint']['checkpoint']['audio']['music']
    manifest=json.loads((ROOT/'nds/artifacts/audio_manifest.json').read_text())
    row=next(r for r in manifest['assets'] if r['group']=='music' and r['index']==cp['index'])
    payload=ROOT/'nds/nitrofs'/row['file']
    decoded=np.frombuffer(subprocess.check_output(['ffmpeg','-v','error','-i',str(payload),'-f','s16le','-c:a','pcm_s16le','-']),dtype='<i2').reshape(-1,2)
    trace=report['trace'];source_at=cp['offset_frames'];length=max(r['read'] for r in trace)+8192
    indices=np.arange(source_at,source_at+length,dtype=np.int64)
    indices=np.where(indices>=row['loop_end_frame'],row['loop_start_frame']+(indices-row['loop_end_frame'])%(row['loop_end_frame']-row['loop_start_frame']),indices)
    reference=decoded[indices];pcm_points=np.array([r['pcm_frame'] for r in trace]);matches=[]
    # Restrict to the scrolling/appearance stress, after restore's explicitly
    # paused output queue. Each 23ms window has 512 independent source samples.
    first=next(r['pcm_frame'] for r in trace if r['stage']=='hold_UP_0')
    active=[r for r in trace if r['pcm_frame']>=first]
    assert cp['loop'] and all(r['index']==cp['index'] for r in active)
    assert len({r['starts'] for r in active})==1,'source was restarted during the stress capture'
    for at in range(first,len(samples)-1024,1024):
        r=trace[min(int(np.searchsorted(pcm_points,at+1024)),len(trace)-1)]
        block=samples[at:at+1024].astype(np.float64).reshape(-1,2,2).mean(axis=1)
        match=match_window(reference,block,max(0,r['read']-1024))
        if match:matches.append(dict(pcm_frame=at,stage=r['stage'],read=r['read'],**match))
    jumps=[]
    for a,b in zip(matches,matches[1:]):
        delta=b['position']-a['position'];expected=(b['pcm_frame']-a['pcm_frame'])/2
        if min(a['correlation'],b['correlation'])>.96 and abs(delta-expected)>32:
            jumps.append(dict(before=a,after=b,source_delta=delta,expected_delta=expected))
    correlations=np.array([m['correlation'] for m in matches])
    return dict(result='ANALYZED',rom_sha256=report['identity']['rom_sha256'],
        capture_report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest(),
        wav_sha256=hashlib.sha256(report_path.with_name('actual_spu.wav').read_bytes()).hexdigest(),
        approved_payload_sha256=hashlib.sha256(payload.read_bytes()).hexdigest(),
        source=row['source'],source_offset=source_at,window_output_frames=1024,
        windows=len(matches),correlation_min=float(min(correlations)),
        correlation_p01=float(np.quantile(correlations,.01)),correlation_median=float(np.median(correlations)),
        high_confidence_windows=int(np.count_nonzero(correlations>.96)),
        high_confidence_position_discontinuities=jumps,matches=matches,
        limitation='23ms windows, 0.96 correlation and 32-source-sample deviation thresholds; emulator evidence does not establish R4 behavior.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('report',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=analyze(a.report);a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='matches'},indent=2))
