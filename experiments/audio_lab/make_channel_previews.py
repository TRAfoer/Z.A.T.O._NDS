"""Make stereo/mono ADPCM listening comparisons without changing the game pack."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys
import wave
import numpy as np
from loop_editor_model import ROOT, RATE, load_sources, load_project, recent_project

sys.path.insert(0,str(ROOT/'nds/tools'))
from music_edit import render_edit
from pack_audio import wav_info

OUT=Path(__file__).resolve().parent/'out/channel_comparison'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--project',type=Path)
    args=parser.parse_args()
    project=args.project or recent_project()
    sources,_=load_sources();tracks=load_project(project,sources)
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'adpcm').mkdir(exist_ok=True)
    results=[]
    for name in ['signal.ogg','tightrope.mp3','mystery.ogg']:
        source=sources[name];state=tracks[name]
        raw=subprocess.check_output(['ffmpeg','-v','error','-i',source['path'],'-vn',
            '-ar',str(RATE),'-ac','2','-c:a','pcm_s16le','-f','s16le','-'])
        pcm=np.frombuffer(raw,dtype='<i2').reshape(-1,2)
        if state['keep_full']:loop_start,loop_end=0,len(pcm)
        else:pcm,loop_start,loop_end=render_edit(pcm,state)
        assert len(pcm)==loop_end
        mono=np.clip(np.rint(pcm.astype(np.int32).mean(axis=1)),-32768,32767).astype('<i2')[:,None]
        decoded={};variants={}
        for label,channels,samples in [('stereo',2,pcm),('mono',1,mono)]:
            encoded=OUT/'adpcm'/f'{Path(name).stem}_{label}_22050.wav'
            subprocess.run(['ffmpeg','-v','error','-y','-f','s16le','-ar',str(RATE),'-ac',str(channels),
                '-i','-','-c:a','adpcm_ima_wav','-block_size','512','-map_metadata','-1',str(encoded)],
                input=samples.tobytes(),check=True)
            info=wav_info(encoded);assert info['channels']==channels
            raw_decoded=subprocess.check_output(['ffmpeg','-v','error','-i',str(encoded),'-c:a','pcm_s16le','-f','s16le','-'])[:len(pcm)*channels*2]
            native=subprocess.check_output([str(ROOT/'nds/artifacts/decode_ima.exe'),str(encoded),
                str(info['data_offset']),str(info['data_bytes']),str(channels),str(len(pcm))])
            assert native==raw_decoded,'NDS C decoder differs from listening PCM'
            signal=np.frombuffer(raw_decoded,dtype='<i2').reshape(-1,channels)
            decoded[label]=signal.astype(np.float64)
            variants[label]=dict(adpcm_file=str(encoded),adpcm_bytes=encoded.stat().st_size,channels=channels,
                decoded_pcm_sha256=hashlib.sha256(raw_decoded).hexdigest())
        # Match full-passage stereo energy so a quieter downmix is not mistaken
        # for worse spatial quality. Mono is duplicated into L/R in audition WAVs
        # to remove playback-device upmix differences. Its stored ADPCM has 1ch.
        rms_stereo=float(np.sqrt(np.mean(decoded['stereo']**2)))
        rms_mono=float(np.sqrt(np.mean(decoded['mono']**2)))
        mono_gain=rms_stereo/rms_mono if rms_mono else 1.
        decoded['mono']=np.repeat(decoded['mono']*mono_gain,2,axis=1)
        common_gain=min(1.,32700/max(1.,max(np.max(np.abs(v)) for v in decoded.values())))
        excerpt_frames=min(round(8*RATE),loop_end-loop_start)
        for label,signal in decoded.items():
            first=np.clip(np.rint(signal*common_gain),-32768,32767).astype('<i2')
            audition=np.concatenate((first,first[loop_start:loop_start+excerpt_frames]))
            output=OUT/f'{Path(name).stem}_{label}_preview.wav'
            with wave.open(str(output),'wb') as f:
                f.setnchannels(2);f.setsampwidth(2);f.setframerate(RATE);f.writeframes(audition.tobytes())
            with wave.open(str(output),'rb') as f:
                assert (f.getnchannels(),f.getframerate(),f.getnframes())==(2,RATE,len(pcm)+excerpt_frames)
            if label=='mono':assert np.array_equal(audition[:,0],audition[:,1])
            variants[label].update(preview=str(output),preview_frames=len(audition))
        assert variants['stereo']['preview_frames']==variants['mono']['preview_frames']
        results.append(dict(source=name,source_sha256=source['source_sha256'],settings=state,
            first_pass_seconds=len(pcm)/RATE,loop_start_seconds=loop_start/RATE,loop_seconds=(loop_end-loop_start)/RATE,
            audition_seconds=(len(pcm)+excerpt_frames)/RATE,mono_audition_gain=mono_gain,common_audition_gain=common_gain,
            rms_match_method='Match full-first-pass RMS after ADPCM decoding, then common peak-safe attenuation; no EQ or dynamic processing',variants=variants))
        print(f'PASS {name}: {len(pcm)/RATE:.3f}s first pass + {excerpt_frames/RATE:.1f}s loop; stereo/mono C decoder exact; mono match gain {mono_gain:.4f}',flush=True)
    report=dict(result='PASS',rate=RATE,codec='4-bit IMA ADPCM,512-byte blocks',
        project=str(project),project_sha256=hashlib.sha256(project.read_bytes()).hexdigest(),
        note='Listening files are PCM WAV decoded from the actual ADPCM conversion. Original first pass +8seconds after loop wrap. Mono stored1ch, audition duplicated to2identical channels. RMS-matched A/B. Game pack and source settings unchanged.',songs=results)
    (OUT/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# 三首音乐立体声/单声道对照','',
        '按已保存人工S/E制作。均先转22050Hz、4-bit IMA ADPCM，再解码成浏览器可播放的PCM WAV。',
        '每份包括完整首轮以及循环后的8秒；A/B按首轮整体RMS对齐，并共同衰减防止削波，不做EQ或动态处理。',
        '单声道试听在左右声道播放完全相同的声音；实际ADPCM只保存一个声道。建议耳机对照。','']
    for r in results:
        lines += [f'## {r["source"]}',f'首轮 {r["first_pass_seconds"]:.2f}秒；循环 {r["loop_seconds"]:.2f}秒。','']
        for label,title in [('stereo','立体声'),('mono','单声道')]:
            v=r['variants'][label]
            lines += [f'{title}；实际编码占用 {v["adpcm_bytes"]/1048576:.3f} MiB。',f'![{title}试听]({Path(v["preview"]).as_posix()})','']
    lines += ['只生成本目录的试听文件，未修改源游戏、人工标记、转换资源包或任何NDS文件。','']
    (OUT/'README.md').write_text('\n'.join(lines),encoding='utf-8')
    print('PASS all6auditions; report:',OUT/'README.md')


if __name__=='__main__':main()
