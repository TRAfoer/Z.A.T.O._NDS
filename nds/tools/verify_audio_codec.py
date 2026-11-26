"""Compare the portable NDS IMA decoder to FFmpeg for every converted file."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def verify(row):
    file = ROOT / 'nitrofs' / row['file']
    pcm = subprocess.check_output([str(ROOT / 'artifacts/decode_ima.exe'), str(file),
        str(row['data_offset']), str(row['data_bytes']), str(row['channels']), str(row['frames'])])
    reference = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(file), '-f', 's16le', '-c:a', 'pcm_s16le', '-'])
    expected_bytes = row['frames'] * row['channels'] * 2
    reference = reference[:expected_bytes]
    assert len(pcm) == expected_bytes and pcm == reference, f'IMA decoder mismatch: {row["source"]}'
    return dict(group=row['group'], source=row['source'], frames=row['frames'], sha256=hashlib.sha256(pcm).hexdigest())


def main():
    manifest = json.loads((ROOT / 'artifacts/audio_manifest.json').read_text(encoding='utf-8'))
    with ThreadPoolExecutor(max_workers=4) as pool:
        checks = list(pool.map(verify, manifest['assets']))
    (ROOT / 'artifacts/audio_codec_verification.json').write_text(json.dumps(dict(result='PASS', checks=checks), indent=2), encoding='utf-8')
    print(f'PASS {len(checks)} complete mono/stereo IMA files match FFmpeg exactly, source target lengths retained')


if __name__ == '__main__':
    main()
