"""Calibrate the real recorded native PCM against independently decoded IMA audio."""
from pathlib import Path
import argparse
import json
import subprocess
import wave
import numpy as np
from scipy.signal import correlate
from experiments.video_lab import convert_op as op


def read_wave(path):
    with wave.open(str(path),'rb') as wav:
        assert wav.getframerate()==44100 and wav.getnchannels()==2
        return np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2').reshape(-1,2).astype(np.float64)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=op.DEFAULT_OUTPUT/'player_final');args=parser.parse_args();out=args.output
    recorded=read_wave(out/'native_output.wav')
    decoded=subprocess.check_output(['ffmpeg','-v','error','-i',str(op.DEFAULT_OUTPUT/'op_soundtrack_ima.wav'),'-ar','44100','-f','s16le','-'])
    reference=np.frombuffer(decoded,dtype='<i2').reshape(-1,2).astype(np.float64)*0.5
    source_rate=44100;markers=[]
    # First locate an energetic segment close to the opening without assuming a
    # particular fixed Maxmod buffer latency. Later matches search a wide 0.4s.
    opening=np.array([np.mean(reference[i:i+11025]**2) for i in range(0,88200,11025)])
    opening_at=int(np.argmax(opening))*11025
    for number,at in enumerate([opening_at]+[int(t*source_rate) for t in (10,20,30,40,50,57)]):
        if number==0:start,end=0,min(len(recorded),at+3*source_rate)
        else:
            predicted=at+markers[0]['offset_samples'];start=max(0,int(predicted-.2*source_rate));end=min(len(recorded),int(predicted+.2*source_rate)+11025)
        template=reference[at:at+11025];channels=[]
        for channel in (0,1):
            signal=recorded[start:end,channel];target=template[:,channel]
            scores=correlate(signal,target,mode='valid',method='fft');best=int(np.argmax(scores));actual=start+best
            match=recorded[actual:actual+len(target),channel]
            corr=float(np.corrcoef(target,match)[0,1])
            channels.append(dict(channel=channel,recorded_sample=actual,correlation=corr,offset_samples=actual-at))
        assert min(c['correlation'] for c in channels)>.97,channels
        assert abs(channels[0]['recorded_sample']-channels[1]['recorded_sample'])<=1,channels
        markers.append(dict(source_sample=at,source_seconds=at/source_rate,offset_samples=channels[0]['offset_samples'],channels=channels))
    x=np.array([m['source_sample'] for m in markers]);y=np.array([m['channels'][0]['recorded_sample'] for m in markers]);slope,intercept=np.polyfit(x,y,1)
    residual=y-(slope*x+intercept)
    report=json.loads((out/'verification.json').read_text());clock=[]
    for row in report['trace']:
        hw=row['probe']['hardware_position']
        if hw<22050 or hw>1283072-22050:continue
        audible=(row['capture_frames']-intercept)/slope/2
        clock.append(dict(emulator_frame=row['emulator_frame'],hardware_sample=hw,recorded_audible_sample=audible,hardware_minus_audible_samples=hw-audible))
    differences=np.array([row['hardware_minus_audible_samples'] for row in clock])
    result=dict(result='PASS',method='Independent FFmpeg stereo IMA decode, 44100Hz native PCM capture, seven energetic 250ms windows independently cross-correlated per channel; fitted hardware rate and fixed onset, then compared VBlank hardware timer snapshots with captured audible sample positions.',
        rom_sha256=report['rom_sha256'],markers=markers,fitted_native_onset_capture_samples=float(intercept),fitted_native_onset_seconds=float(intercept/44100),
        recorded_samples_per_reference_sample=float(slope),max_marker_residual_samples=float(np.max(np.abs(residual))),
        clock_error_samples=dict(median=float(np.median(differences)),min=float(differences.min()),max=float(differences.max())),
        clock_error_ms=dict(median=float(np.median(differences)*1000/22050),min=float(differences.min()*1000/22050),max=float(differences.max()*1000/22050)),
        hardware_cursor_is_not_prefilled_music_frames=True,clock_trace=clock)
    (out/'sync_calibration.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps({k:v for k,v in result.items() if k!='clock_trace'},indent=2))


if __name__=='__main__':main()
