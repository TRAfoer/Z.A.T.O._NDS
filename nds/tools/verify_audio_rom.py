"""Actual ROM input/audio capture tests, independent of the codec checks."""
from pathlib import Path
import ctypes as C
import hashlib
import json
import re
import struct
import sys
import wave
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(r'C:\DeskBox\Source\Nds_UI_Test\tools')
sys.path.insert(0, str(TOOLS))
from nds_emulator import Emulator, BATCH

FIELDS = 'magic initialized rate music_count sfx_count music_index sfx_index music_playing sfx_playing music_starts sfx_starts callbacks music_underruns sfx_underruns errors music_frames sfx_frames music_loops sfx_finishes ui_version nonzero_samples output_peak'.split()


def main():
    mapping = (ROOT / 'build/zato_asset_demo_nitrofs.map').read_text()
    offset = int(re.search(r'0x([0-9a-fA-F]+)\s+g_audio_status\s', mapping)[1], 16) - 0x02000000
    drops_offset = int(re.search(r'0x([0-9a-fA-F]+)\s+g_input_drops\s', mapping)[1], 16) - 0x02000000
    manifest = json.loads((ROOT / 'artifacts/audio_manifest.json').read_text(encoding='utf-8'))
    songs = [r for r in manifest['assets'] if r['group'] == 'music']
    effects = [r for r in manifest['assets'] if r['group'] == 'sfx']
    music_count, sfx_count = len(songs), len(effects)
    rom = ROOT / 'zato_asset_demo_nitrofs.nds'
    emu = Emulator(TOOLS / 'emulator/desmume_libretro.dll', rom)
    record = False
    chunks = []
    checks = []

    def receive(data, frames):
        if record:
            chunks.append(C.string_at(data, frames * 4))
        return frames
    callback = BATCH(receive)
    emu.core.retro_set_audio_sample_batch(callback)

    def state():
        s = dict(zip(FIELDS, struct.unpack_from('<22I', emu.memory(), offset)))
        assert s['magic'] == 0x4455415a and s['initialized'] == 1
        assert s['errors'] == s['music_underruns'] == s['sfx_underruns'] == 0, s
        assert struct.unpack_from('<I', emu.memory(), drops_offset)[0] == 0
        return s

    def press(key, settle=15, held=3):
        emu.press(key, held=held, settle=settle)
        s = state()
        checks.append(dict(key=key, state=s))
        return s

    def capture(name, frames, audible):
        nonlocal record
        chunks.clear(); record = True
        emu.run(frames)
        record = False
        raw = b''.join(chunks)
        pcm = np.frombuffer(raw, dtype='<i2').astype(np.int32)
        assert len(pcm)
        peak = int(np.max(np.abs(pcm)))
        if audible: assert peak > 100, (name, peak)
        else: assert peak == 0, (name, peak)
        with wave.open(str(ROOT / 'artifacts' / name), 'wb') as file:
            file.setnchannels(2); file.setsampwidth(2); file.setframerate(44100); file.writeframes(raw)
        return dict(file=name, peak=peak, rms=float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2))))

    try:
        emu.run(240)
        s = state()
        assert (s['music_count'], s['sfx_count'], s['music_playing']) == (music_count, sfx_count, 0)
        assert s['sfx_index'] == 0xffffffff
        assert press('DOWN')['music_index'] == 1 and state()['music_playing'] == 0
        assert press('UP')['music_index'] == 0
        assert press('L')['music_playing'] == 1
        recordings = [capture('music_from_rom.wav', 240, True)]
        assert press('UP')['music_index'] == music_count - 1 and state()['music_playing'] == 1
        assert press('DOWN')['music_index'] == 0
        assert press('L', held=40)['music_playing'] == 0
        emu.run(30)
        recordings.append(capture('stopped_music_from_rom.wav', 60, False))
        assert press('X')['sfx_index'] == 0
        recordings.append(capture('sfx_from_rom.wav', 60, True))
        emu.run(120)
        s = state()
        assert s['sfx_playing'] == 0 and s['sfx_frames'] == effects[0]['frames'] and s['sfx_finishes'] >= 1
        # Finish the SFX cycle, replacing the previous effect on each press.
        for i in range(1, sfx_count):
            s = press('X', settle=8)
            assert s['sfx_index'] == i
        assert press('X')['sfx_index'] == 0
        assert state()['sfx_starts'] == sfx_count + 1
        emu.run(120)
        assert state()['sfx_playing'] == 0
        assert press('L')['music_playing'] == 1
        for i in range(1, music_count + 1):
            s = press('DOWN', settle=10)
            assert s['music_index'] == i % music_count and s['music_playing'] == 1
        # Exercise an authored short music cue long enough to pass its loop.
        target = next(i for i, r in enumerate(songs) if r['source'] == 'heart.ogg')
        for _ in range(target): press('DOWN', settle=6)
        emu.run(int((songs[target]['seconds'] + 2) * 60))
        assert state()['music_index'] == target
        assert state()['music_loops'] > 0, (target, songs[target]['source'], songs[target]['seconds'], state())
        edited_loop_check = None
        edited = next((i for i,r in enumerate(songs) if r.get('music_edit')),None)
        if edited is not None:
            delta=(edited-state()['music_index'])%music_count
            key='DOWN' if delta<=music_count//2 else 'UP'
            for _ in range(delta if key=='DOWN' else music_count-delta): press(key,settle=6)
            row=songs[edited]
            before=state()['music_loops']
            remaining=max(0,row['seconds']-2-state()['music_frames']/row['rate'])
            emu.run(int(remaining*59.826))
            recordings.append(capture('edited_loop_from_rom.wav',240,True))
            loop_seconds=(row['loop_end_frame']-row['loop_start_frame'])/row['rate']
            emu.run(int((loop_seconds*2+1)*60))
            assert state()['music_index']==edited and state()['music_loops']-before>=3
            edited_loop_check=dict(source=row['source'],loop_start_frame=row['loop_start_frame'],
                loop_end_frame=row['loop_end_frame'],loops_observed=state()['music_loops']-before)
        press('X')
        for key in ['RIGHT', 'A', 'RIGHT', 'B', 'LEFT', 'R', 'R'] * 3:
            press(key, settle=10)
        emu.frame.save(ROOT / 'artifacts/audio_controls.png')
        report = dict(result='PASS', music=music_count, sfx=sfx_count, controls=checks, captures=recordings, final_state=state(),
                      edited_loop_check=edited_loop_check,
                      rom_bytes=rom.stat().st_size, rom_sha256=hashlib.sha256(rom.read_bytes()).hexdigest(),
                      note='Real ROM via DeSmuME: music on/off/wrap, stopped selection, all catalog songs/SFX, one-shot EOF, looping, overlaps and image controls; PCM captured from emulator audio callback, not generated from source')
        (ROOT / 'artifacts/audio_rom_verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(f'PASS actual audio ROM: {len(checks)} input checks; all {music_count} songs / {sfx_count} SFX; recorded real music, SFX and stopped silence; zero underflows/errors')
    finally:
        emu.close()


if __name__ == '__main__':
    main()
