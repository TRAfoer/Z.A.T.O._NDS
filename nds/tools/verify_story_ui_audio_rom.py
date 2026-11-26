"""Bounded actual-ROM UI-audio smoke: physical keys, SPU PCM, read-only RAM.

Set ZATO_MPCF_IMAGE to a NEW image path before starting Python. The tool puts
an unchanged real old-2267 save into that private FAT16 image, copies the ROM
without changing any byte to its deployed filename, then runs two short cases.
No savestates, RAM writes, authored-event shortcuts or product hooks are used.
"""
from pathlib import Path
import argparse
import ctypes as C
import hashlib
import importlib.util
import json
import os
import shutil
import struct
import time
import traceback
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


cold = module('ui_audio_cold', ROOT / 'nds/tools/verify_story_scroll_cold.py')
seed = module('ui_audio_seed', ROOT / 'nds/tools/verify_story_audio_scroll.py')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Probe(cold.Probe):
    def __init__(self, args):
        self.recording = False
        self.pcm = bytearray()
        self.trace = []
        self.stage = 'boot'
        super().__init__(args)
        import nds_emulator
        self.batch_callback = nds_emulator.BATCH(self.audio_batch)
        self.single_callback = nds_emulator.AUDIO(self.audio_single)
        self.n.emu.core.retro_set_audio_sample_batch(self.batch_callback)
        self.n.emu.core.retro_set_audio_sample(self.single_callback)
        av = C.create_string_buffer(40)
        self.n.emu.core.retro_get_system_av_info(C.byref(av))
        self.fps, self.rate = struct.unpack_from('<2d', av.raw, 24)
        self.ui_address, size = self.n.symbols.section('ui_voice', 'audio_demo')
        assert size == 20, ('immediate/rate UI voice ABI', size)
        self.ui_buffers = {'button': self.n.symbols.section('ui_button_pcm', 'audio_demo')[0]}
        self.ui_ready = self.n.symbols.section('ui_ready', 'audio_demo')[0]

    def audio_batch(self, pointer, count):
        if self.recording:
            self.pcm.extend(C.string_at(pointer, count * 4))
        return count

    def audio_single(self, left, right):
        if self.recording:
            self.pcm.extend(struct.pack('<hh', left, right))

    def ui(self):
        raw = self.n.read(self.ui_address, 20)
        pointer, frames, gain, cursor = struct.unpack_from('<4I', raw)
        return dict(buffer=next((k for k, v in self.ui_buffers.items() if v == pointer), hex(pointer)),
                    frames=frames, gain=gain, cursor=cursor,
                    active=bool(raw[16]), phase=raw[17], step=raw[18], cue=raw[19],
                    ready=bool(self.n.read(self.ui_ready, 1)[0]))

    def step(self, keys=()):
        # Sample each core boundary, including status-fence retries, so the
        # first source advance is visible independently of menu commits.
        for _ in range(120):
            self.n.emu.run(1, keys)
            state = self.n.snapshot()
            self.state = self.n.last = state
            audio = state['a']
            assert not audio['errors'] and not audio['music_underruns'] and not audio['sfx_underruns'], state
            self.observe()
            if self.recording:
                self.trace.append(dict(core=state['core_frame'], pcm_frame=len(self.pcm) // 4,
                                       stage=self.stage, shell=state['shell'], audio=audio,
                                       voices=self.voices(), ui=self.ui(), ticks=state['s']['ticks']))
            if state['coherent']:
                return state
        raise AssertionError(('status stayed incoherent', self.state))

    @staticmethod
    def move_window(rows):
        moves = [row for row in rows if row['ui']['active'] and row['ui']['cue'] == 0]
        assert moves, 'MOVE was not observed'
        for row in moves:
            u = row['ui']
            assert u['buffer'] == 'button' and u['gain'] == 512 and u['frames'] == 29636 and u['step'] == 3, u
            assert u['phase'] < 4 and u['cursor'] >= 2205, u
        body = next((row for row in moves if row['ui']['cursor'] > 2205), None)
        assert body is not None, ('MOVE source cursor never advanced', moves)
        return dict(first_observed=moves[0], first_body=body,
                    interpretation='Single button source advances from cut index2205 at step3/4. There is no added delay or pending queue.')

    def settle(self, frames):
        for _ in range(frames):
            self.step()

    def begin(self, name):
        self.stage = name
        self.pcm.clear()
        self.trace.clear()
        self.recording = True

    def finish(self, name, start_core, minimum_seconds=2.0):
        deadline = self.n.emu.frame_count + 600
        while len(self.pcm) // 4 < round(self.rate * minimum_seconds):
            assert self.n.emu.frame_count < deadline, 'SPU PCM capture did not reach bounded duration'
            self.step()
        self.recording = False
        path = self.args.output / (name + '.wav')
        with wave.open(str(path), 'wb') as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(round(self.rate))
            output.writeframes(self.pcm)
        a = np.frombuffer(self.pcm, dtype='<i2').reshape(-1, 2)
        nonzero = np.flatnonzero(np.any(a != 0, axis=1))
        result = dict(path=str(path), sha256=sha(path), frames=len(a), seconds=len(a) / self.rate,
                      sample_rate=self.rate, peak=int(np.max(np.abs(a.astype(np.int32)), initial=0)),
                      nonzero_frames=int(len(nonzero)), first_nonzero_frame=int(nonzero[0]) if len(nonzero) else None,
                      last_nonzero_frame=int(nonzero[-1]) if len(nonzero) else None,
                      core_start=start_core, core_end=self.n.emu.frame_count,
                      trace=self.trace.copy())
        assert result['seconds'] >= minimum_seconds and result['nonzero_frames'] > 0, result
        return result

    def title_case(self):
        self.boot_title()
        self.settle(100)  # Opening and startup output queues have retired.
        assert self.ui()['ready'], 'optional UI PCM did not preload'
        self.screenshot('title')
        before = self.voices()
        baseline = self.state['a'].copy()
        self.begin('title_move_confirm')
        start = self.n.emu.frame_count
        self.settle(12)
        move_trace_start = len(self.trace)
        self.edge('DOWN', lambda s: s['shell']['selection'] == 1)
        move = self.ui()
        assert move['buffer'] == 'button' and move['gain'] == 512 and move['frames'] == 29636 and move['step'] == 3 and move['cue'] == 0, move
        self.settle(18)
        window = self.move_window(self.trace[move_trace_start:])
        self.edge('A', lambda s: s['shell']['mode'] == 3)
        confirm = self.ui()
        assert confirm['buffer'] == 'button' and confirm['gain'] == 1536 and confirm['frames'] == 29636 and confirm['step'] == 4 and confirm['cue'] == 1 and not confirm['phase'], confirm
        self.screenshot('title_load_confirmed')
        result = self.finish('title_move_confirm', start)
        after = self.voices()
        assert self.state['a']['music_starts'] == baseline['music_starts'], 'UI restarted Title music'
        assert self.state['a']['sfx_starts'] == baseline['sfx_starts'], 'UI counted as authored SFX'
        assert after['music']['read'] > before['music']['read'] and after['music']['source_base'] == before['music']['source_base']
        result.update(move=move, move_window=window, confirm=confirm, before=before, after=after,
                      interpretation='Actual SPU PCM contains Title music plus UI; one button cache with immediate cut-source step3 MOVE and full-rate confirmation. Paused-story case isolates audible UI output.')
        print('PASS Title move/confirm with actual SPU PCM', flush=True)
        return result

    def load_fixture(self):
        saved, _ = cold.saved_slot(self.args.image, self.args.slot)
        assert saved['checkpoint']['flow']['text_id'] == 2267, saved
        self.expected = saved
        for selection in range(1, self.args.slot + 1):
            self.edge('RIGHT', lambda s, n=selection: s['shell']['selection'] == n)
        self.edge('A', lambda s: s['shell']['load_ops'] or s['shell']['mode'] == 8 or s['shell']['save_errors'])
        if self.state['shell']['mode'] == 8:
            self.edge('LEFT', lambda s: s['shell']['selection'] == 0)
            self.edge('A', lambda s: s['shell']['load_ops'] or s['shell']['save_errors'])
        self.wait(lambda s: self.first is not None and not s['shell']['active'])
        assert self.first['ack']['text_id'] == 2267 and self.state['shell']['load_ops'] == 1
        assert not self.state['shell']['save_errors'], self.state
        self.expected = None
        self.screenshot('loaded_2267')
        return saved

    @staticmethod
    def frozen_fields(voices):
        fields = ('index', 'read', 'source_base', 'enabled', 'loop', 'gain_q12',
                  'fade_step', 'fade_target', 'fade_total', 'fade_left', 'fade_remainder', 'fade_error', 'flags')
        return {name: {field: voices[name][field] for field in fields} for name in ('music', 'sfx')}

    def paused_story_case(self):
        saved = self.load_fixture()
        self.edge('START', lambda s: s['shell']['active'] and s['shell']['mode'] == 1)
        self.settle(100)  # UI cue and already queued music samples must expire.
        assert self.voices()['paused'] and self.voices()['music']['enabled'], self.voices()
        self.screenshot('paused_game_menu')
        before = self.voices()
        baseline = self.state['a'].copy()
        ticks = self.state['s']['ticks']
        self.begin('paused_story_ui')
        start = self.n.emu.frame_count
        self.settle(12)
        silent_frames = len(self.pcm) // 4
        assert not np.any(np.frombuffer(self.pcm, dtype='<i2')), 'paused and settled baseline was not silent'
        move_trace_start = len(self.trace)
        for selection in range(1, 5):
            self.edge('DOWN', lambda s, n=selection: s['shell']['selection'] == n)
            self.settle(4)
        move_trace_end = len(self.trace)
        move_pcm_end = len(self.pcm) // 4
        # Recording continues below: never retain a NumPy export of the live,
        # resizable callback bytearray across another retro_run.
        isolated_move = np.frombuffer(self.pcm, dtype='<i2').reshape(-1, 2)[silent_frames:move_pcm_end].copy()
        assert np.any(isolated_move), 'paused MOVE had no actual SPU PCM before confirmation'
        move_window = self.move_window(self.trace[move_trace_start:move_trace_end])
        self.edge('A', lambda s: s['shell']['mode'] == 5)
        self.screenshot('paused_settings_with_ui_sound')
        result = self.finish('paused_story_ui', start)
        frozen = self.frozen_fields(before)
        for row in result['trace']:
            assert row['voices']['paused'] and row['ticks'] == ticks, row
            assert self.frozen_fields(row['voices']) == frozen, ('plot voice moved in UI', row)
            assert row['audio']['music_starts'] == baseline['music_starts'] and row['audio']['sfx_starts'] == baseline['sfx_starts'], row
        assert any(row['ui']['cursor'] > 0 for row in result['trace']), 'UI cursor did not run while scene paused'
        result.update(silent_baseline_frames=silent_frames, saved=saved, frozen_plot_voices=frozen,
                      isolated_move=dict(window=move_window, pcm_start=silent_frames, pcm_end=move_pcm_end,
                          nonzero_frames=int(np.count_nonzero(np.any(isolated_move != 0, axis=1)))),
                      interpretation='Settled paused scene baseline is exact digital zero; subsequent nonzero SPU PCM is independent UI sound. Music enabled; SFX enabled state is reported, not assumed.')
        self.edge('B', lambda s: s['shell']['mode'] == 1)
        self.edge('B', lambda s: not s['shell']['active'])
        self.settle(120)
        after = self.voices()
        assert not after['paused'] and after['music']['read'] > before['music']['read'], (before, after)
        assert after['music']['source_base'] == before['music']['source_base'] and after['music']['index'] == before['music']['index']
        assert self.state['a']['music_starts'] == baseline['music_starts'] and self.state['a']['sfx_starts'] == baseline['sfx_starts']
        self.screenshot('resumed_same_source_music')
        result['resumed_voices'] = after
        result['resumed_audio'] = self.state['a']
        print('PASS paused plot frozen + independent audible UI + resumed same music cursor', flush=True)
        return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom', type=Path, required=True)
    p.add_argument('--map', type=Path, required=True)
    p.add_argument('--expected-sha', required=True)
    p.add_argument('--image', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--fixture', type=Path, default=ROOT / 'nds/artifacts/font_reflow_native/old2267_cn_retry1_save/extracted.sav')
    p.add_argument('--slot', type=int, default=1)
    p.add_argument('--core', type=Path, default=ROOT / 'nds/artifacts/save_spike/desmume_fat_test.dll')
    p.add_argument('--emulator-tools', type=Path, default=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools'))
    p.add_argument('--cpu-mode', choices=('interpreter',), default='interpreter')
    p.add_argument('--text-pack', type=Path, default=ROOT / 'nds/nitrofs/story/text.bin')
    p.add_argument('--text-manifest', type=Path, default=ROOT / 'nds/artifacts/story_text_manifest.json')
    a = p.parse_args()
    assert sha(a.rom) == a.expected_sha.lower(), 'ROM SHA mismatch'
    assert sha(a.core) == '509d3f2c05f3034668093e908f43b447f2afc52996ec5cb3697ea0c016f15106', 'unverified core'
    assert os.environ.get('ZATO_MPCF_IMAGE') and Path(os.environ['ZATO_MPCF_IMAGE']).resolve() == a.image.resolve(), 'parent must preset private MPCF image'
    assert not a.image.exists() and not a.output.exists(), 'new owned image and report directory required'
    a.output.mkdir(parents=True)
    fixture_sha = sha(a.fixture)
    supplied_rom = a.rom
    seed.seed_image(a.image, a.fixture)
    rom = a.output / 'ZATO_Story_Chain.nds'
    shutil.copyfile(a.rom, rom)
    assert sha(rom) == a.expected_sha
    a.rom = rom
    report = dict(result='RUNNING', identity=dict(rom=str(supplied_rom.resolve()), rom_sha256=a.expected_sha,
        map_sha256=sha(a.map), core_sha256=sha(a.core), fixture=str(a.fixture.resolve()), fixture_sha256=fixture_sha,
        image=str(a.image.resolve()), tool_sha256=sha(__file__)), cases={}, limitations=[
        'Bounded actual emulator output, not physical NDS/R4 listening.',
        'No SFX-volume-zero native repeat: host tests cover it. No-I/O trigger contract is host-tested, not inferred from a native test hook.',
        'Title PCM contains music and UI together; paused plot case is isolated by exact silent baseline.',
        'Approved MOVE is button source2205, immediate step3/4 and gain512, with no added delay. Exact source PCM equivalence is a separate host callback regression; native verifies source advance and isolated audible output.',
        'Plot SFX enabled/disabled state is explicitly reported; disabled source is not claimed to be an active-SFX test.'])
    probe = None
    started = time.monotonic()
    try:
        probe = Probe(a)
        report['cases']['title'] = probe.title_case()
        report['cases']['paused_story'] = probe.paused_story_case()
        assert sha(supplied_rom) == a.expected_sha and sha(a.fixture) == fixture_sha
        report.update(result='PASS', wall_seconds=time.monotonic() - started,
                      core_frames=probe.n.emu.frame_count, final=probe.state)
        print('PASS bounded native UI-audio smoke', flush=True)
    except BaseException as error:
        report.update(result='FAIL', error=str(error), traceback=traceback.format_exc(),
                      state=probe.state if probe else None)
        raise
    finally:
        if probe:
            probe.n.close()
        report['image_sha256_after'] = sha(a.image)
        cold.native.atomic_json(a.output / 'report.json', report)


if __name__ == '__main__':
    main()
