"""Verify shipping scene ROM audio, pause, checkpoint restores and playback clocks.

Only the generic libretro frontend is borrowed from Nds_UI_Test. All status,
script, audio and art data belong to this independent NDS project. No source or
approved audio is rebuilt or modified by this verifier.
"""
from pathlib import Path
import argparse
import ctypes as C
import hashlib
import json
import math
import re
import struct
import sys
import time
import wave
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(r'C:\DeskBox\Source\Nds_UI_Test\tools')
AUDIO_FIELDS = ('magic initialized rate music_count sfx_count music_index sfx_index '
    'music_playing sfx_playing music_starts sfx_starts callbacks music_underruns '
    'sfx_underruns errors music_frames sfx_frames music_loops sfx_finishes '
    'ui_version nonzero_samples output_peak').split()


def audio_metrics(raw):
    if not raw or len(raw) % 4:
        raise ValueError('Capture must contain complete interleaved stereo s16 frames')
    pcm = np.frombuffer(raw, dtype='<i2').reshape(-1, 2).astype(np.int32)
    return dict(frames=len(pcm), peak=int(np.max(np.abs(pcm))),
        rms=float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2))),
        channel_peaks=[int(x) for x in np.max(np.abs(pcm), axis=0)],
        channel_nonzero_samples=[int(x) for x in np.count_nonzero(pcm, axis=0)],
        different_channel_frames=int(np.count_nonzero(pcm[:, 0] != pcm[:, 1])))


def symbol_offset(mapping, symbol):
    match = re.search(r'0x([0-9a-fA-F]+)\s+' + re.escape(symbol) + r'\s', mapping)
    if not match:
        raise ValueError(f'MAP lacks {symbol}')
    address = int(match[1], 16)
    if not 0x02000000 <= address < 0x02400000:
        raise ValueError(f'{symbol} is outside DS main RAM: {address:#x}')
    return address - 0x02000000


def scene_fields(header):
    # Read the actual all-u32 ABI rather than guessing offsets from an old ROM.
    declaration = re.search(r'extern\s+volatile\s+(\w+)\s+g_scene_status\s*;', header)
    if not declaration:
        raise ValueError('scene_runtime.h lacks g_scene_status declaration')
    definition = re.search(r'typedef\s+struct(?:\s+\w+)?\s*\{([^{}]*)\}\s*' +
        re.escape(declaration[1]) + r'\s*;', header, re.S)
    if not definition:
        raise ValueError('Cannot locate SceneStatus structure')
    body = re.sub(r'/\*.*?\*/|//[^\n]*', '', definition[1], flags=re.S)
    fields = []
    for statement in body.split(';'):
        if not statement.strip():
            continue
        match = re.fullmatch(r'\s*(?:volatile\s+)?uint32_t\s+([\w\s,]+)\s*', statement)
        if not match:
            raise ValueError(f'Scene status member is not plain uint32_t: {statement.strip()}')
        fields.extend(name.strip() for name in match[1].split(','))
    return fields


def find_map(rom):
    name = rom.with_suffix('.map').name
    candidates = [ROOT/'build_scene'/name, ROOT/'build'/name, ROOT/name]
    candidates.extend(ROOT.glob(f'build*/{name}'))
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(f'No MAP for {rom.name}; pass --map explicitly')


class Geometry(C.Structure):
    _fields_ = [('base_width', C.c_uint), ('base_height', C.c_uint),
        ('max_width', C.c_uint), ('max_height', C.c_uint), ('aspect_ratio', C.c_float)]


class Timing(C.Structure):
    _fields_ = [('fps', C.c_double), ('sample_rate', C.c_double)]


class AvInfo(C.Structure):
    _fields_ = [('geometry', Geometry), ('timing', Timing)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom', type=Path, default=ROOT/'zato_ep2d3_scene_nitrofs.nds')
    parser.add_argument('--map', type=Path)
    parser.add_argument('--scene-header', type=Path, default=ROOT/'source/scene_runtime.h')
    parser.add_argument('--tools', type=Path, default=TOOLS)
    parser.add_argument('--core', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT/'artifacts/scene_audio_rom')
    parser.add_argument('--long-seconds', type=float, default=60.)
    parser.add_argument('--max-missed-vblanks', type=int, default=0,
        help='Maximum missed vblanks during a measured playback stage; counts are always reported')
    args = parser.parse_args()
    if args.long_seconds <= 0 or args.max_missed_vblanks < 0:
        parser.error('Playback duration must be positive and missed-vblank allowance nonnegative')
    mapping_path = args.map or find_map(args.rom)
    mapping = mapping_path.read_text(encoding='utf-8')
    audio_offset = symbol_offset(mapping, 'g_audio_status')
    scene_offset = symbol_offset(mapping, 'g_scene_status')
    fields = scene_fields(args.scene_header.read_text(encoding='utf-8'))
    required = {'magic', 'frames', 'ready', 'source_line', 'group', 'group_frame',
        'checkpoint', 'paused', 'pc', 'visible', 'text_id', 'page', 'errors',
        'missed_vblanks', 'gfx_reads', 'wait_hard', 'ticks'}
    if not required <= set(fields):
        raise ValueError(f'Scene status ABI is missing {required - set(fields)}')
    script = json.loads((ROOT/'artifacts/scene_script_manifest.json').read_text(encoding='utf-8'))
    manifest = json.loads((ROOT/'artifacts/audio_manifest.json').read_text(encoding='utf-8'))
    songs = {row['index']: row for row in manifest['assets'] if row['group'] == 'music'}
    effects = {row['index']: row for row in manifest['assets'] if row['group'] == 'sfx'}
    events_by_line = {event['line']: event for event in script['events']}
    rom_bytes = args.rom.read_bytes()
    assert 0 < len(rom_bytes) < 64_000_000, len(rom_bytes)
    args.output.mkdir(parents=True, exist_ok=True)
    report_path = args.output/'verification.json'
    report = dict(result='RUNNING', rom=str(args.rom), rom_bytes=len(rom_bytes),
        rom_sha256=hashlib.sha256(rom_bytes).hexdigest(), map=str(mapping_path),
        scene_status_fields=fields, max_missed_vblanks_per_stage=args.max_missed_vblanks,
        captures=[], stages=[], checkpoints=[], issues=[],
        limitation='DeSmuME verifies the real ROM and emulated hardware clocks; physical R4 card latency and hardware frame rate require the user test.')
    sys.path.insert(0, str(args.tools))
    from nds_emulator import Emulator, BATCH
    core = args.core or args.tools/'emulator/desmume_libretro.dll'
    emu = Emulator(core, args.rom)
    av = AvInfo()
    emu.core.retro_get_system_av_info.argtypes = [C.POINTER(AvInfo)]
    emu.core.retro_get_system_av_info.restype = None
    emu.core.retro_get_system_av_info(C.byref(av))
    fps = av.timing.fps
    sample_rate = round(av.timing.sample_rate)
    assert 1 <= fps <= 120 and 8000 <= sample_rate <= 192000, (fps, sample_rate)
    report.update(emulator_fps=fps, output_sample_rate=sample_rate)
    recording = False
    chunks = []
    gfx_reads_at_ready = None

    def receive(data, frames):
        if recording:
            chunks.append(C.string_at(data, frames * 4))
        return frames
    callback = BATCH(receive)
    emu.core.retro_set_audio_sample_batch(callback)

    def state(check=True):
        memory = emu.memory()
        scene = dict(zip(fields, struct.unpack_from(f'<{len(fields)}I', memory, scene_offset)))
        audio = dict(zip(AUDIO_FIELDS, struct.unpack_from('<22I', memory, audio_offset)))
        value = dict(scene=scene, audio=audio, emulator_frame=emu.frame_count)
        if check:
            assert scene['ready'] and scene['magic'], value
            assert not scene['errors'], value
            assert audio['magic'] == 0x4455415a and audio['initialized'], value
            assert audio['music_count'] == len(songs) and audio['sfx_count'] == len(effects), value
            assert audio['rate'] == 22050, value
            assert not (audio['errors'] or audio['music_underruns'] or audio['sfx_underruns']), value
            if gfx_reads_at_ready is not None:
                assert scene['gfx_reads'] == gfx_reads_at_ready, value
        return value

    def run(frames, keys=(), check=True):
        while frames:
            count = min(frames, 60)
            emu.run(count, keys)
            frames -= count
            if check:
                state()

    def until(predicate, maximum=1200, step=6):
        elapsed = 0
        while not predicate(state()):
            assert elapsed < maximum, ('Timed out', state())
            run(min(step, maximum - elapsed))
            elapsed += step

    def screenshot(name):
        assert emu.frame is not None
        path = args.output/(name + '.png')
        emu.frame.save(path)
        return str(path)

    def measure(name, frames, audible=None, capture=False):
        nonlocal recording
        before = state()
        started = time.perf_counter()
        if capture:
            chunks.clear()
            recording = True
        try:
            run(frames)
        finally:
            recording = False
        wall = time.perf_counter() - started
        after = state()
        missed = after['scene']['missed_vblanks'] - before['scene']['missed_vblanks']
        stage = dict(name=name, emulator_frames=frames, emulated_seconds=frames/fps,
            wall_seconds=wall, frontend_frames_per_wall_second=frames/wall,
            scene_frames=after['scene']['frames']-before['scene']['frames'],
            scene_ticks=after['scene']['ticks']-before['scene']['ticks'],
            group_frame_delta=after['scene']['group_frame']-before['scene']['group_frame'],
            audio_callbacks=after['audio']['callbacks']-before['audio']['callbacks'],
            music_frames=after['audio']['music_frames']-before['audio']['music_frames'],
            sfx_frames=after['audio']['sfx_frames']-before['audio']['sfx_frames'],
            missed_vblanks=missed, before=before, after=after, screenshot=screenshot(name))
        report['stages'].append(stage)
        assert stage['audio_callbacks'] > 0, stage
        if missed > args.max_missed_vblanks:
            report['issues'].append(f'{name}: {missed} missed vblanks (allowance {args.max_missed_vblanks})')
        if capture:
            raw = b''.join(chunks)
            metric = audio_metrics(raw)
            metric.update(name=name, file=str(args.output/(name + '.wav')))
            with wave.open(metric['file'], 'wb') as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(sample_rate)
                output.writeframes(raw)
            report['captures'].append(metric)
            if audible is True:
                assert min(metric['channel_peaks']) > 100, metric
            elif audible is False:
                assert metric['peak'] == 0, metric
        print(f"{name}: {frames} emulated frames, missed={missed}, audio_callbacks={stage['audio_callbacks']}", flush=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return stage

    def press(key, settle=6):
        run(4, [key])
        run(settle)

    def checkpoint(target):
        count = len(script['checkpoints'])
        current = state()['scene']['checkpoint']
        distance = (target - current) % count
        key, steps = ('RIGHT', distance) if distance <= count // 2 else ('LEFT', count - distance)
        for _ in range(steps):
            expected = (current + (1 if key == 'RIGHT' else -1)) % count
            press(key)
            until(lambda s: s['scene']['checkpoint'] == expected)
            current = expected
        until(lambda s: s['scene']['checkpoint'] == target and not s['scene']['paused'])
        current_state = state()
        report['checkpoints'].append(dict(target=target, name=script['checkpoints'][target]['name'], state=current_state))
        print(f"checkpoint {target}: source {current_state['scene']['source_line']}", flush=True)
        return current_state

    try:
        # Boot time includes preloading the independent gfx/text/script packs.
        for _ in range(120):
            emu.run(15)
            snapshot = state(False)
            if snapshot['scene']['ready'] and snapshot['audio']['initialized']:
                break
        else:
            raise AssertionError(('ROM did not become ready', snapshot))
        gfx_reads_at_ready = snapshot['scene']['gfx_reads']
        run(30)
        boot = state()
        report['boot'] = boot
        assert boot['scene']['checkpoint'] == 1 and 564 <= boot['scene']['source_line'] <= 571, boot
        measure('default_video_silence', 60, audible=False, capture=True)

        checkpoint(2)
        keyboard = measure('keyboard_sfx', 24, audible=True, capture=True)
        assert keyboard['before']['audio']['sfx_index'] == 15, keyboard
        run(60)
        ended = state()['audio']
        # The keyboard passages have authored automatic advances and retrigger
        # the same effect; a later cue may already be playing at this snapshot.
        assert ended['sfx_finishes'] > keyboard['before']['audio']['sfx_finishes'], ended

        checkpoint(3)
        glitch = measure('glitch_sfx', 45, audible=True, capture=True)
        assert glitch['before']['audio']['sfx_index'] == 12, glitch

        checkpoint(4)
        red = measure('red_stereo_music', 180, audible=True, capture=True)
        assert red['after']['audio']['music_index'] == 27 and red['after']['audio']['music_playing'], red
        press('L', settle=18)  # Let queued Maxmod/ARM7 samples drain before silence.
        assert state()['scene']['paused'] == 1, state()
        paused = measure('paused_silence', 120, audible=False, capture=True)
        for group, members in [('audio', ('music_frames', 'sfx_frames')),
                ('scene', ('group_frame', 'source_line', 'pc', 'visible', 'page', 'ticks'))]:
            for member in members:
                assert paused['before'][group][member] == paused['after'][group][member], (group, member, paused)
        press('L', settle=12)
        assert not state()['scene']['paused']
        resumed = measure('resumed_stereo_music', 120, audible=True, capture=True)
        assert resumed['music_frames'] > 22050 and resumed['scene_ticks'] > 0, resumed

        checkpoint(5)
        mirror = state()
        assert mirror['scene']['group'] == script['gfx_ids']['mirror'] and mirror['audio']['music_playing'], mirror
        measure('mirror_stereo_music', 180, audible=True, capture=True)
        music_row = songs[state()['audio']['music_index']]
        period = (music_row['loop_end_frame'] - music_row['loop_start_frame']) / music_row['rate']
        loops_before = state()['audio']['music_loops']
        duration = max(args.long_seconds, period + 2.)
        long_play = measure('mirror_long_play', math.ceil(duration * fps))
        loops_observed = state()['audio']['music_loops'] - loops_before
        assert loops_observed >= 1, (music_row['source'], long_play)
        report['long_play'] = dict(source=music_row['source'], seconds=duration,
            loop_seconds=period, decoded_loop_wraps=loops_observed)

        checkpoint(6)
        after_sfx = measure('after_literal_sfx', 120, audible=True, capture=True)
        assert not after_sfx['before']['audio']['music_playing'] and after_sfx['before']['audio']['sfx_index'] == 14, after_sfx
        # No A/START/R inputs: both the literal's 4s wait and the subsequent 7s
        # forced wait run at their authored duration; music fades in for 5s.
        until(lambda s: s['audio']['music_playing'] and s['audio']['music_index'] == 16, maximum=600)
        after_music = measure('after_music_fade', math.ceil(7.5 * fps), audible=True, capture=True)
        assert after_music['after']['audio']['music_frames'] >= 5 * 22050, after_music

        checkpoint(7)
        run(18)
        stopped = measure('window_lead_silence', 60, audible=False, capture=True)
        assert not stopped['after']['audio']['music_playing'], stopped
        # Advance only text, waiting through authored transition/wait events.
        for _ in range(180):
            current = state()
            if current['scene']['group'] == script['gfx_ids']['window']:
                break
            event = events_by_line.get(current['scene']['source_line'], {})
            if current['scene']['wait_hard'] or event.get('name') in ('WAIT', 'TRANSITION'):
                run(6)
            else:
                press('A', settle=3)
        else:
            raise AssertionError(('Window animation was not reached', state()))
        window = measure('window_animation_sfx', 60, audible=True, capture=True)
        assert window['before']['audio']['sfx_index'] == 25, window
        assert any(c['different_channel_frames'] > 100 for c in report['captures']
            if 'stereo_music' in c['name']), report['captures']
        report['final'] = state()
        report['total_missed_vblanks_measured'] = sum(stage['missed_vblanks'] for stage in report['stages'])
        report['result'] = 'FAIL' if report['issues'] else 'PASS'
        if report['issues']:
            raise AssertionError('; '.join(report['issues']))
        print(f'PASS native scene ROM audio: {len(report["captures"])} real stereo/silence captures; checkpoints, pause, loop and zero underruns/errors', flush=True)
    except Exception as error:
        report['result'] = 'FAIL'
        report['error'] = f'{type(error).__name__}: {error}'
        try:
            report['failure_state'] = state(False)
            report['failure_screenshot'] = screenshot('failure')
        except Exception:
            pass
        raise
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        emu.close()


if __name__ == '__main__':
    main()
