"""Verify the real runtime block/ring code against decoded PCM through 3 wraps."""
from pathlib import Path
import hashlib
import json
import struct
import subprocess
import tempfile
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def main():
    manifest = json.loads((ROOT/'artifacts/audio_manifest.json').read_text(encoding='utf-8'))
    profile = json.loads(Path(manifest['music_edit_profile']).read_text(encoding='utf-8'))
    decisions = profile['edits']
    assert all(r['channels']==(2 if r['group']=='music' else 1) for r in manifest['assets'])
    executable = ROOT/'artifacts/validate_music_loop.exe'
    checks = []
    with tempfile.TemporaryDirectory(prefix='music_loop_', dir=ROOT/'artifacts') as temporary:
        reference = Path(temporary)/'decoded.pcm'
        for row in manifest['assets']:
            if not row.get('music_edit'): continue
            assert row['group']=='music'
            assert 0<=row['loop_start_frame']<row['loop_end_frame']==row['frames']
            decision=decisions[row['source']]
            assert row['music_edit']==decision
            assert row['frames']==decision['intro_end_frame']+decision['source_loop_end_frame']-decision['source_loop_start_frame']
            pcm = subprocess.check_output(['ffmpeg','-v','error','-i',str(ROOT/'nitrofs'/row['file']),'-f','s16le','-c:a','pcm_s16le','-'])[:row['frames']*4]
            reference.write_bytes(pcm)
            output = subprocess.check_output([str(executable),str(row['index']),str(reference)],cwd=ROOT,text=True)
            print(output.strip(),flush=True)
            samples = np.frombuffer(pcm,dtype='<i2').reshape(-1,2).astype(np.int32)
            jump = int(np.max(np.abs(samples[row['loop_end_frame']-1]-samples[row['loop_start_frame']])))
            checks.append(dict(source=row['source'],frames=row['frames'],loop_start=row['loop_start_frame'],
                block_skip=row['loop_start_frame']%row['samples_per_block'],
                exact_runtime_wraps=3,seam_jump=jump,decoded_pcm_sha256=hashlib.sha256(pcm).hexdigest()))
        assert len(checks)==len(decisions)>0
        catalog = (ROOT/'nitrofs/audio/index.bin').read_bytes()
        _,_,_,_,size = struct.unpack_from('<8s4I',catalog)
        assert size==144
        failures=[]
        first=manifest['assets'][0]
        for name,offset,value in [('empty_loop',24+136,first['loop_end_frame']),
            ('end_past_frames',24+140,first['frames']+1),
            ('frames_past_payload',24+112,first['data_bytes']//512*first['samples_per_block']+1),
            ('non_block_payload',24+132,first['data_bytes']-1),('old_version',8,1)]:
            bad=bytearray(catalog);struct.pack_into('<I',bad,offset,value)
            path=Path(temporary)/(name+'.bin');path.write_bytes(bad)
            subprocess.run([str(executable),'--reject',str(path)],cwd=ROOT,check=True,capture_output=True)
            failures.append(name)
    result=dict(result='PASS',checks=checks,rejected_catalogs=failures,
        music_edit_profile=manifest['music_edit_profile'],music_edit_profile_sha256=manifest['music_edit_profile_sha256'],
        validation='Actual audio_demo.c code compiled on host with only platform/path stubs; all consumed PCM samples equal FFmpeg output through intro once and three wraps per edited song, including inside-block loop starts',
        limitation='Sample correctness is verified; representative passage and musical seam quality still require human audition')
    (ROOT/'artifacts/music_loop_verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(f'PASS {len(checks)} edited songs x 3 exact runtime wraps; {len(failures)} malformed catalogs rejected')


if __name__=='__main__': main()
