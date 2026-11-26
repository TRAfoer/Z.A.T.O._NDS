"""Convert the game's mixed audio to block IMA WAV for the NDS trial.

Ren'Py command text is read with pickletools; no game code/pickle is executed.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import argparse
import hashlib
import json
import pickletools
import re
import struct
import subprocess
import sys
import zlib
from audio_dedup import deduplicate
from music_edit import render_edit
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / 'experiments/asset_lab'))
from asset_model import DEFAULT_GAME

RATE = 22050
BLOCK = 512
HEADER = struct.Struct('<8s4I')
ENTRY = struct.Struct('<64s48s2I4H4I')
MUSIC_EDITS = {}


def script_strings():
    for path in sorted(DEFAULT_GAME.rglob('*.rpyc')):
        data = path.read_bytes()
        if data[:10] != b'RENPY RPC2':
            continue
        for at in range(10, min(200, len(data) - 11), 12):
            slot, start, size = struct.unpack_from('<III', data, at)
            if not slot:
                break
            if slot != 1:
                continue
            raw = zlib.decompress(data[start:start + size])
            for _, text, _ in pickletools.genops(raw):
                if isinstance(text, str): yield text
            break
    for path in sorted(DEFAULT_GAME.rglob('*.rpy')):
        for line in path.read_text(encoding='utf-8-sig', errors='replace').splitlines():
            if not line.lstrip().startswith('#'): yield line.strip()


def references():
    refs = {'music': set(), 'sfx': set()}
    properties = {'main_menu_music': 'music', 'sample_sound': 'sfx',
                  'activate_sound': 'sfx', 'hover_sound': 'sfx'}
    literal = re.compile(r'[\"\'](?:<[^>]+>)*audio/([^\"\']+?\.(?:ogg|mp3|wav))[\"\']', re.I)
    pending, ttl, all_literals = None, 0, set()
    for text in script_strings():
        found = literal.findall(text)
        all_literals.update(found)
        if text in properties:
            pending, ttl = properties[text], 4
        assignment = re.search(r'\b(main_menu_music|sample_sound|activate_sound|hover_sound)\b\s*(?:=\s*)?[\"\']', text)
        if assignment:
            refs[properties[assignment[1]]].update(found)
        match = re.match(r'^(?:play|queue)\s+(music|sound)\s+[\"\']([^\"\']+)', text)
        if match:
            filename = re.sub(r'^(?:<[^>]+>)+', '', match[2])
            if filename.startswith('audio/'):
                refs['music' if match[1] == 'music' else 'sfx'].add(filename[6:])
        if pending and ttl:
            if found: refs[pending].update(found)
            ttl -= 1
    unclassified = all_literals - refs['music'] - refs['sfx']
    assert not unclassified, f'Audio literal needs channel classification: {sorted(unclassified)}'
    return refs


def wav_info(path):
    data = path.read_bytes()
    assert data[:4] == b'RIFF' and data[8:12] == b'WAVE'
    info = {}
    at = 12
    while at + 8 <= len(data):
        kind, size = struct.unpack_from('<4sI', data, at)
        payload = at + 8
        if kind == b'fmt ':
            codec, channels, rate, _, block, bits = struct.unpack_from('<HHIIHH', data, payload)
            assert codec == 0x11 and bits == 4 and block == BLOCK and rate == RATE
            samples = struct.unpack_from('<H', data, payload + 18)[0]
            info.update(channels=channels, rate=rate, block=block, samples_per_block=samples)
        elif kind == b'data':
            assert payload + size <= len(data)
            info.update(data_offset=payload, data_bytes=size)
        at = payload + size + (size & 1)
    assert info['data_bytes'] % BLOCK == 0
    return info


def convert(job):
    group, index, source_name = job
    source = DEFAULT_GAME / 'audio' / source_name
    channels = 2 if group == 'music' else 1
    rel = Path('audio') / group / f'{index:03d}.wav'
    out = ROOT / 'nitrofs' / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    # Decode once to exact target PCM so original sample count is retained
    # separately from the encoder's padded final block.
    pcm = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(source), '-vn', '-ar', str(RATE),
                                   '-ac', str(channels), '-f', 's16le', '-c:a', 'pcm_s16le', '-'])
    assert pcm and len(pcm) % (channels * 2) == 0
    frames = len(pcm) // (channels * 2)
    original_frames = frames
    loop_start, loop_end = 0, frames
    edit = MUSIC_EDITS.get(source_name) if group == 'music' else None
    if edit:
        assert hashlib.sha256(source.read_bytes()).hexdigest() == edit['source_sha256'], f'Stale music edit: {source_name}'
        samples, loop_start, loop_end = render_edit(np.frombuffer(pcm, dtype='<i2').reshape(-1, channels), edit)
        pcm, frames = samples.tobytes(), len(samples)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 's16le', '-ar', str(RATE), '-ac', str(channels),
                    '-i', '-', '-c:a', 'adpcm_ima_wav', '-block_size', str(BLOCK), '-map_metadata', '-1', str(out)],
                   input=pcm, check=True)
    info = wav_info(out)
    assert info['channels'] == channels
    assert info['data_bytes'] // BLOCK * info['samples_per_block'] >= frames
    return dict(group=group, index=index, source=source_name, file=rel.as_posix(), frames=frames,
                original_frames=original_frames, original_seconds=original_frames/RATE,
                loop_start_frame=loop_start, loop_end_frame=loop_end, music_edit=edit,
                source_bytes=source.stat().st_size, pcm_bytes=len(pcm), compressed_bytes=out.stat().st_size,
                seconds=frames / RATE, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), **info)


def main():
    global MUSIC_EDITS
    MUSIC_EDITS = {}
    parser = argparse.ArgumentParser()
    parser.add_argument('--full-length', action='store_true', help='Ignore musical cut decisions')
    approved = ROOT / 'config/audio_edits.json'
    default_edits = approved if approved.exists() else ROOT.parent / 'experiments/audio_lab/out/music_loops/edits.json'
    parser.add_argument('--music-edits', type=Path, default=default_edits)
    args = parser.parse_args()
    if not args.full_length and args.music_edits.is_file():
        decisions = json.loads(args.music_edits.read_text(encoding='utf-8'))
        assert decisions['rate'] == RATE
        assert decisions.get('status') != 'pending_confirmation', 'Manual draft needs an explicitly approved build snapshot'
        assert decisions.get('music_channels',2) == 2, 'Current build policy requires all music stereo'
        MUSIC_EDITS = decisions['edits']
    refs = references()
    all_files = {p.relative_to(DEFAULT_GAME / 'audio').as_posix()
                 for p in (DEFAULT_GAME / 'audio').rglob('*') if p.suffix.lower() in ('.mp3', '.ogg', '.wav')}
    referenced = refs['music'] | refs['sfx']
    assert referenced <= all_files, f'Missing referenced audio: {sorted(referenced - all_files)}'
    excluded = sorted(all_files - referenced)
    print(f'Static references: {len(referenced)}/{len(all_files)} sources; excluding {excluded}', flush=True)
    aliases, audit = deduplicate(DEFAULT_GAME / 'audio', referenced)
    audit['excluded_unreferenced'] = excluded
    (ROOT / 'artifacts/audio_dedup_audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
    for duplicate in audit['duplicates']:
        print(f'Duplicate: {duplicate["source"]} -> {duplicate["canonical"]}, gain {duplicate["gain"]:.4f}, stereo correlation {duplicate["min_correlation"]:.6f}', flush=True)
    groups = {
        'music': sorted({aliases[n] for n in refs['music']}, key=lambda name: (name != 'menu.ogg', name)),
        'sfx': sorted({aliases[n] for n in refs['sfx']}, key=lambda name: (name != 'button.mp3', name))}
    assert set(MUSIC_EDITS) <= set(groups['music']), f'Unknown edited music sources: {sorted(set(MUSIC_EDITS)-set(groups["music"]))}'
    jobs = [(group, i, name) for group, names in groups.items() for i, name in enumerate(names)]
    rows = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for row in pool.map(convert, jobs):
            rows.append(row)
            if len(rows) % 10 == 0:
                print(f'{len(rows)}/{len(jobs)} complete', flush=True)
    gains = {r['source']: r['gain'] for r in audit['duplicates']}
    for row in rows:
        row['aliases'] = sorted(n for n in refs[row['group']] if aliases[n] == row['source'])
        # Preserve the original names and gain differences for script integration.
        row['alias_gains'] = {n: gains.get(n, 1.) for n in row['aliases']}
    output = bytearray(HEADER.pack(b'ZATAUD02', 2, len(groups['music']), len(groups['sfx']), ENTRY.size))
    for row in rows:
        name, file = row['source'].encode(), row['file'].encode()
        assert len(name) < 64 and len(file) < 48
        output.extend(ENTRY.pack(name.ljust(64, b'\0'), file.ljust(48, b'\0'), row['frames'], row['rate'],
                                 row['channels'], row['block'], row['samples_per_block'], 0,
                                 row['data_offset'], row['data_bytes'], row['loop_start_frame'], row['loop_end_frame']))
    (ROOT / 'nitrofs/audio/index.bin').write_bytes(output)
    expected = {(ROOT / 'nitrofs' / row['file']).resolve() for row in rows}
    for group in ('music', 'sfx'):
        directory = (ROOT / 'nitrofs/audio' / group).resolve()
        assert directory.is_relative_to((ROOT / 'nitrofs/audio').resolve())
        for path in directory.glob('*.wav'):
            if path.resolve() not in expected: path.unlink()
    original_bytes = sum((DEFAULT_GAME / 'audio' / name).stat().st_size for name in all_files)
    summary = dict(codec='IMA ADPCM WAV, 4-bit, 512-byte independent blocks', rate=RATE,
                   music_channels=2, sfx_channels=1, original_files=len(all_files),
                   referenced_sources=len(referenced), unique_sources=len(set(aliases.values())),
                   excluded_unreferenced=excluded, source_aliases=aliases,
                   music=len(groups['music']), sfx=len(groups['sfx']), shared_sources=sorted(refs['music'] & refs['sfx']),
                   source_bytes=original_bytes, target_pcm_bytes=sum(row['pcm_bytes'] for row in rows),
                   encoded_bytes=sum(row['compressed_bytes'] for row in rows) + len(output),
                   classification='Static play/queue, main-menu and screen UI references only; complete stereo waveform deduplication before encoding; OGG preferred for transcode duplicates',
                   musical_edits=sum(bool(r['music_edit']) for r in rows),
                   music_edit_profile=str(args.music_edits.resolve()) if MUSIC_EDITS else None,
                   music_edit_profile_sha256=hashlib.sha256(args.music_edits.read_bytes()).hexdigest() if MUSIC_EDITS else None,
                   loops='Edited music introduction once then sample-accurate loop region; unedited music whole-file loop; SFX once', assets=rows)
    (ROOT / 'artifacts/audio_manifest.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'PASS {len(all_files)} sources, {len(referenced)} referenced, {summary["unique_sources"]} unique -> {summary["music"]} music / {summary["sfx"]} SFX; {summary["encoded_bytes"]:,} B', flush=True)


if __name__ == '__main__':
    main()
