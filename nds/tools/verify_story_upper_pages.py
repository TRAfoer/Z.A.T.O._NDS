"""Read-only final-ROM check of three-page ordinary upper presentation.

Runs physical keys through OP -> Title -> first consecutive dissolves -> menus
-> a real cold checkpoint -> B history -> live scene. Only a new private FAT
image is written. No RAM writes, authored shortcuts, or savestates are used.
Boundary snapshots are not a substitute for an instrumented scanline trace.
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

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
NONE = 0xffffffff


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


ui = module('upper_pages_ui_driver', ROOT / 'nds/tools/verify_story_ui_audio_rom.py')
cold = ui.cold


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Probe(cold.Probe):
    def __init__(self, args):
        self.stage = 'boot'
        self.trace = []
        self.pages_seen = set()
        self.transitions = set()
        self.audio_pcm = dict(frames=0, nonzero_frames=0, peak=0)
        super().__init__(args)
        self.page_addresses = {name: self.symbol(name) for name in (
            'upper_front_page', 'upper_old_page', 'upper_stage_page', 'upper_pending_old')}
        self.upper_address = self.symbol('upper')
        self.review_address = self.symbol('reviewing')
        self.review_id_address = self.symbol('review_id')
        import nds_emulator
        self.batch_callback = nds_emulator.BATCH(self.audio_batch)
        self.single_callback = nds_emulator.AUDIO(self.audio_single)
        self.n.emu.core.retro_set_audio_sample_batch(self.batch_callback)
        self.n.emu.core.retro_set_audio_sample(self.single_callback)

    def symbol(self, name):
        for prefix in ('bss', 'data'):
            try:
                return self.n.symbols.section(name, 'story_runtime', prefix)[0]
            except AssertionError:
                pass
        raise AssertionError(('missing upper-page symbol', name))

    def audio_batch(self, pointer, count):
        data = np.frombuffer(C.string_at(pointer, count * 4), dtype='<i2').reshape(-1, 2)
        self.audio_pcm['frames'] += count
        self.audio_pcm['nonzero_frames'] += int(np.count_nonzero(np.any(data, axis=1)))
        self.audio_pcm['peak'] = max(self.audio_pcm['peak'], int(np.abs(data.astype(np.int32)).max(initial=0)))
        return count

    def audio_single(self, left, right):
        self.audio_pcm['frames'] += 1
        self.audio_pcm['nonzero_frames'] += int(bool(left or right))
        self.audio_pcm['peak'] = max(self.audio_pcm['peak'], abs(left), abs(right))

    def pages(self):
        return {name.removeprefix('upper_'): struct.unpack('<I', self.n.read(address, 4))[0]
                for name, address in self.page_addresses.items()}

    def step(self, keys=()):
        for _ in range(120):
            self.n.emu.run(1, keys)
            self.state = self.n.last = self.n.snapshot()
            self.observe()
            state = self.state
            audio = state['a']
            assert not any(audio[k] for k in ('errors', 'music_underruns', 'sfx_underruns')), state
            pages = self.pages()
            s = state['s']
            if s['magic'] == 0x5254535a and state['coherent']:
                assert pages['front_page'] < 3 and pages['old_page'] < 3, pages
                assert pages['pending_old'] in (0, 1, 2, NONE), pages
                if pages['stage_page'] != NONE:
                    assert pages['stage_page'] < 3 and pages['stage_page'] not in (
                        pages['front_page'], pages['old_page']), pages
                if s['ready']:
                    self.pages_seen.add(pages['front_page'])
            if s['ready'] and state['coherent']:
                self.n.healthy(state)
                if s['transition_duration'] and s['transition_kind'] in (1, 2):
                    self.transitions.add((s['transition_kind'], s['transition_start'], s['transition_duration']))
            self.trace.append(dict(core=state['core_frame'], stage=self.stage,
                coherent=state['coherent'], pages=pages, pc=s['pc'], text=s['text_id'],
                ticks=s['ticks'], transition=state['transition'], shell=state['shell'],
                frames=s['frames'], audio=audio))
            if state['coherent']:
                return state
        raise AssertionError(('status stayed incoherent', self.state))

    def settle(self, frames):
        for _ in range(frames):
            self.step()

    def boot_title(self):
        for _ in range(12000):
            shell = self.state.get('shell')
            if shell and shell['magic'] == 0x4c48535a and shell['active'] and shell['mode'] == 0:
                assert shell['available'], self.state
                return
            video = self.state.get('video')
            skip = self.args.opening == 'skip' and video and video['active'] and self.n.emu.frame_count % 20 == 10
            self.step(('A',) if skip else ())
        raise AssertionError(('OP/Title handoff timeout', self.state))

    def pixels(self, name):
        """At a deliberately stable scene, compare all98304 displayed RGB5 channels."""
        self.settle(20)
        before = self.n.read(self.upper_address, 98304)
        self.n.emu.want_video = True
        try:
            self.settle(6)
        finally:
            self.n.emu.want_video = False
        after = self.n.read(self.upper_address, 98304)
        assert before == after, ('pixel oracle requires stable upper RAM', name)
        image = self.n.emu.frame
        assert image is not None and image.size == (256, 384), (name, None if image is None else image.size)
        rgb = np.asarray(image)[:192, :256, :3].astype(np.uint16)
        actual = ((rgb[:, :, 0] >> 3) | ((rgb[:, :, 1] >> 3) << 5) |
                  ((rgb[:, :, 2] >> 3) << 10))
        expected = np.frombuffer(after, dtype='<u2').reshape(192, 256) & 0x7fff
        difference = actual != expected
        assert not np.any(difference), (name, 'upper GPU/RAM pixel mismatch', int(difference.sum()))
        image.save(self.args.output / (name + '.png'))
        return dict(pixels=49152, exact_RGB5=True, upper_sha256=hashlib.sha256(after).hexdigest(), pages=self.pages())

    def opening_and_transitions(self):
        self.stage = 'opening_and_title'
        self.boot_title()
        title = self.pixels('title_after_op')
        self.stage = 'first_consecutive_dissolves'
        self.edge('A', lambda state: not state['shell']['active'])
        controller = cold.native.ManualController()
        for _ in range(12000):
            s = self.state['s']
            if s['pc'] >= 39 and s['blocked'] and not s['waiting']:
                break
            self.step(controller.keys(self.n, self.state))
        else:
            raise AssertionError(('consecutive transition walk timeout', self.state))
        assert len(self.transitions) >= 2, ('did not observe both first consecutive dissolves', self.transitions)
        self.edge('L', lambda state: state['s']['paused'])
        stable = self.pixels('ordinary_after_consecutive_dissolves')
        self.edge('L', lambda state: not state['s']['paused'])
        return dict(title=title, ordinary=stable, observed_transitions=sorted(self.transitions))

    def menu_load(self):
        self.stage = 'game_menu_and_cold_load'
        self.edge('START', lambda state: state['shell']['active'] and state['shell']['mode'] == 1)
        self.settle(100)
        before = self.voices()
        menu = self.pixels('game_menu')
        assert self.voices() == before, 'paused menu advanced plot audio'
        for selection in (1, 2, 3):
            self.edge('DOWN', lambda state, n=selection: state['shell']['selection'] == n)
        self.edge('A', lambda state: state['shell']['mode'] == 3)
        saved, _ = cold.saved_slot(self.args.image, self.args.slot)
        self.expected = saved
        for selection in range(1, self.args.slot + 1):
            self.edge('RIGHT', lambda state, n=selection: state['shell']['selection'] == n)
        self.edge('A', lambda state: state['shell']['mode'] == 8 or state['shell']['load_ops'])
        if self.state['shell']['mode'] == 8:
            self.edge('LEFT', lambda state: state['shell']['selection'] == 0)
            self.edge('A', lambda state: state['shell']['load_ops'])
        self.wait(lambda state: self.first is not None and not state['shell']['active'])
        assert self.first['ack']['text_id'] == saved['checkpoint']['flow']['text_id']
        assert self.state['shell']['load_ops'] == 1 and not self.state['shell']['save_errors']
        self.expected = None
        self.wait(lambda state: not state['transition']['duration'] or
                  state['s']['ticks'] - state['transition']['start'] >= state['transition']['duration'])
        self.edge('L', lambda state: state['s']['paused'])
        restored = self.pixels('restored_ordinary')
        self.edge('L', lambda state: not state['s']['paused'])
        return dict(game_menu=menu, cold_checkpoint=saved['checkpoint']['sha256'], restored=restored)

    def history_and_resume(self):
        self.stage = 'history_and_resume'
        self.wait(lambda state: state['s']['blocked'] and not state['s']['waiting'])
        live = self.state['s'].copy()
        self.edge('B', lambda state: bool(self.n.read(self.review_address, 1)[0]))
        self.settle(20)
        history_pixels = self.n.read(self.upper_address, 98304)
        before = self.voices()
        self.settle(40)
        assert self.n.read(self.upper_address, 98304) == history_pixels, 'history upper moved while held'
        assert self.voices()['music']['read'] > before['music']['read'], 'history stopped live music'
        history_id = struct.unpack('<I', self.n.read(self.review_id_address, 4))[0]
        self.screenshot('history_held')
        self.edge('A', lambda state: not self.n.read(self.review_address, 1)[0])
        assert self.state['s']['pc'] == live['pc'] and self.state['s']['text_id'] == live['text_id'], 'A advanced live story while closing history'
        self.edge('L', lambda state: state['s']['paused'])
        restored = self.pixels('live_after_history')
        return dict(history_text=history_id, historical_upper_stable=True, music_continued=True,
                    live_story_not_advanced=True, restored=restored)

    def quick_case(self):
        """Small scanline-copy test: physical Title load, then400 ordinary core frames."""
        self.stage = 'quick_title_load'
        self.boot_title()
        self.edge('DOWN', lambda state: state['shell']['selection'] == 1)
        self.edge('A', lambda state: state['shell']['mode'] == 3)
        saved, _ = cold.saved_slot(self.args.image, self.args.slot)
        self.expected = saved
        for selection in range(1, self.args.slot + 1):
            self.edge('RIGHT', lambda state, n=selection: state['shell']['selection'] == n)
        self.edge('A', lambda state: state['shell']['load_ops'])
        self.wait(lambda state: self.first is not None and not state['shell']['active'])
        self.expected = None
        self.stage = 'quick_ordinary'
        started = self.n.emu.frame_count
        texts = set()
        for i in range(400):
            self.step(('A',) if i % 15 == 0 and self.state['s']['blocked'] else ())
            texts.add(self.state['s']['text_id'])
        return dict(ordinary_core_frames=self.n.emu.frame_count - started, text_ids=sorted(texts))

    def scan_counters(self):
        address, size = self.n.symbols.section('g_upper_pages_scan', 'story_runtime', 'data')
        assert size == 80, ('scan counter ABI', size)
        names = ('magic version stage_before stage_after stage_bad publish_before publish_after publish_bad '
                 'first_kind first_line first_dest first_old first_new stage_min stage_max '
                 'publish_start_min publish_start_max publish_end_min publish_end_max reserved').split()
        result = dict(zip(names, struct.unpack('<20I', self.n.read(address, size))))
        assert result['magic'] == 0x55504753 and result['version'] == 1
        assert result['stage_before'] and result['stage_after'] and result['publish_before'] and result['publish_after'], result
        assert not result['stage_bad'] and not result['publish_bad'], result
        assert 192 <= result['publish_start_min'] <= result['publish_start_max'] <= 220, result
        assert 192 <= result['publish_end_min'] <= result['publish_end_max'] <= 220, result
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('rom', 'map', 'image', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--expected-sha', required=True)
    parser.add_argument('--opening', choices=('skip', 'natural'), default='skip')
    parser.add_argument('--quick', action='store_true')
    parser.add_argument('--scan-probe', action='store_true', help='Require the80-byte ignored-copy instrumentation counter')
    parser.add_argument('--fixture', type=Path, default=ROOT / 'nds/artifacts/font_reflow_native/old2267_cn_retry1_save/extracted.sav')
    parser.add_argument('--slot', type=int, default=1)
    parser.add_argument('--core', type=Path, default=ROOT / 'nds/artifacts/save_spike/desmume_fat_test.dll')
    parser.add_argument('--emulator-tools', type=Path, default=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools'))
    parser.add_argument('--cpu-mode', choices=('interpreter',), default='interpreter')
    parser.add_argument('--text-pack', type=Path, default=ROOT / 'nds/nitrofs/story/text.bin')
    parser.add_argument('--text-manifest', type=Path, default=ROOT / 'nds/artifacts/story_text_manifest.json')
    args = parser.parse_args()
    assert sha(args.rom) == args.expected_sha
    assert sha(args.core) == '509d3f2c05f3034668093e908f43b447f2afc52996ec5cb3697ea0c016f15106'
    assert os.environ.get('ZATO_MPCF_IMAGE') and Path(os.environ['ZATO_MPCF_IMAGE']).resolve() == args.image.resolve()
    assert not args.image.exists() and not args.output.exists(), 'fresh owned image/output required'
    args.output.mkdir(parents=True)
    supplied = args.rom
    fixture_sha = sha(args.fixture)
    ui.seed.seed_image(args.image, args.fixture)
    args.rom = args.output / 'ZATO_Story_Chain.nds'
    shutil.copyfile(supplied, args.rom)
    report = dict(result='RUNNING', rom=str(supplied.resolve()), rom_sha256=args.expected_sha,
        map_sha256=sha(args.map), core_sha256=sha(args.core), tool_sha256=sha(__file__),
        opening=args.opening, cases={}, limits=[
            'Final-ROM physical key playback and read-only core-boundary observations; no product instrumentation.',
            'Core-boundary page roles cannot prove absence of sub-frame VRAM writes or bound register-transaction VCOUNT. Use the separate instrumented scanline proof for those claims.',
            'Exact pixels are checked only at intentionally stable title/menu/paused ordinary scenes; active dissolve/punch hardware output is not compared directly with its unblended RAM source.',
            'This is an emulator check, not a physical NDS/R4 guarantee.'])
    probe = None
    started = time.monotonic()
    try:
        probe = Probe(args)
        if args.quick:
            report['cases']['quick'] = probe.quick_case()
        else:
            report['cases']['opening_and_transitions'] = probe.opening_and_transitions()
            report['cases']['menu_and_load'] = probe.menu_load()
            report['cases']['history_and_resume'] = probe.history_and_resume()
            assert probe.pages_seen == {0, 1, 2}, ('not all three pages submitted', probe.pages_seen)
        if args.scan_probe:
            report['scan_counters'] = probe.scan_counters()
        assert sha(supplied) == args.expected_sha and sha(args.fixture) == fixture_sha
        report.update(result='PASS', core_frames=probe.n.emu.frame_count,
            wall_seconds=time.monotonic() - started, pages_seen=sorted(probe.pages_seen),
            audio_pcm=probe.audio_pcm, final_audio=probe.state['a'], final_state=probe.state,
            trace=probe.trace, fixture_unchanged=True)
        print(json.dumps({k: v for k, v in report.items() if k not in ('trace', 'final_state', 'cases')}, ensure_ascii=False), flush=True)
    except BaseException as error:
        report.update(result='FAIL', error=str(error), traceback=traceback.format_exc(),
                      state=probe.state if probe else None)
        raise
    finally:
        if probe:
            probe.n.close()
        cold.native.atomic_json(args.output / 'report.json', report)


if __name__ == '__main__':
    main()
