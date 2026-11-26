"""Actual-ROM B history pixels/live rollback and uninterrupted music.

Physical keys, read-only RAM, authentic checkpoint seed in a fresh owned FAT.
Completion comes from the first real presentation ACK, not a later screenshot.
The original video image is matched to its source-PNG oracle at that ACK tick;
video-callback latency is recorded separately. No savestates or RAM writes.
"""
from pathlib import Path
from collections import deque
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import struct
import sys
import traceback
import wave
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'nds/tests'))
from story_gfx_oracle import Oracle


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    out = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(out)
    return out


pulse = module('history_physical_driver', ROOT / 'nds/tools/verify_story_pulse_perf.py')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def rgb5(image):
    return np.asarray(image.convert('RGB'), dtype=np.uint16)[:192] >> 3


class Probe(pulse.Probe):
    def __init__(self, args):
        self.watch = set()
        self.completions = {}
        self.video_frames = deque(maxlen=12)
        self.return_expected = None
        self.first_return = None
        self.actions = []
        super().__init__(args)
        self.oracle = Oracle()
        self.reviewing_address = self.n.symbols.section('reviewing', 'story_runtime')[0]
        self.review_id_address = self.n.symbols.section('review_id', 'story_runtime', prefix='data')[0]
        try:
            self.review_view_address = self.n.symbols.section('review_view', 'story_runtime')[0]
        except AssertionError:
            self.review_view_address = None

    def reviewing(self):
        return bool(self.n.read(self.reviewing_address, 1)[0])

    def review_id(self):
        return struct.unpack('<I', self.n.read(self.review_id_address, 4))[0]

    def expected_upper(self, layers, ticks):
        words = self.oracle.draw([(r['image'], r['placement'], r['program'], r['start']) for r in layers], ticks)
        return np.stack((words & 31, (words >> 5) & 31, (words >> 10) & 31), axis=2)

    def observe(self):
        before = len(self.entries)
        super().observe()
        if self.n.emu.want_video and self.n.emu.frame is not None:
            self.video_frames.append((self.n.emu.frame_count, self.n.emu.frame.copy()))
        for entry in self.entries[before:]:
            id = entry['text_id']
            if id in self.watch and id not in self.completions and entry['flags'] & 1:
                record = self.text['records'][id]
                pages = record['pages_bi'] if entry['flags'] & 8 else record['pages_cn']
                complete = entry['visible'] >= record['char_count'] and entry['page'] + 1 >= pages
                if entry['flags'] & 16:
                    complete = complete and entry['scroll_covered'] >= entry['visible']
                if complete:
                    layers = self.n.active_layers()
                    self.completions[id] = dict(ack=dict(entry), observed_core=self.n.emu.frame_count, layers=layers,
                        expected=self.expected_upper(layers, entry['ticks']), actual=None, actual_core=None)
            if self.return_expected and self.first_return is None and entry['flags'] & 1 and id == self.return_expected['text_id']:
                self.first_return = dict(entry)
        for id, record in self.completions.items():
            if record['actual'] is None:
                match = self.find_video(record['expected'], record['observed_core'] - 2)
                if match:
                    core, image = match
                    name = f'completed_{id}_actual.png'
                    image.save(self.args.output / name)
                    record.update(actual=name, actual_core=core)

    def find_video(self, expected, minimum):
        for core, image in self.video_frames:
            if core >= minimum and np.array_equal(rgb5(image), expected):
                return core, image
        return None

    def edge(self, key, predicate, maximum=1800):
        self.actions.append(dict(key=key, core=self.n.emu.frame_count, ticks=self.state['s']['ticks'], text_id=self.state['s']['text_id']))
        return super().edge(key, predicate, maximum)

    def settle_transition(self):
        self.wait(lambda state: not state['transition']['duration'] or
                  state['s']['ticks'] - state['transition']['start'] >= state['transition']['duration'] + 2)
        assert self.state['s']['literal_id'] == self.state['s']['date_id'] == self.state['s']['special_group'] == 0xffffffff
        assert self.state['s']['text_mode'] != 3

    def enter_complete(self, id):
        self.edge('A', lambda state: state['s']['text_id'] == id)
        self.wait(lambda state: id in self.completions and self.completions[id]['actual'] is not None)
        self.wait(lambda state: state['s']['text_id'] == id and state['s']['local'] >= state['s']['duration_ticks'] and state['f'].get('scroll_ready', True))
        assert self.state['s']['text_id'] == id

    @staticmethod
    def scene(layers):
        return [{k:r[k] for k in ('image', 'tag', 'program', 'placement', 'start')} for r in layers]

    @staticmethod
    def flow(state):
        return {k:state['s'][k] for k in ('pc', 'text_id', 'commands', 'ticks', 'local', 'visible', 'page', 'pause_index')}

    def saved_completion(self, id):
        record = self.completions[id]
        assert record['actual'] is not None
        return {k:v for k,v in record.items() if k != 'expected'}

    def review_check(self, id, label, live, layers):
        self.edge('B', lambda state: self.reviewing() and self.review_id() == id and state['s']['paused'])
        self.wait(lambda state: self.n.presentation_head > 2)
        self.screenshot(label)
        # screenshot() restores want_video=False; continuous capture resumes.
        self.n.emu.want_video = True
        actual = rgb5(self.n.emu.frame)
        expected = self.completions[id]['expected']
        mismatches = int(np.count_nonzero(np.any(actual != expected, axis=2)))
        assert mismatches == 0, ('history upper versus first complete original image', id, mismatches, self.state)
        assert self.flow(self.state) == live, ('live flow moved while reading history', live, self.flow(self.state))
        expected_layers = self.completions[id]['layers'] if self.args.history_resident else layers
        assert self.scene(self.n.active_layers()) == self.scene(expected_layers), 'review resource ownership metadata mismatch'
        tick = struct.unpack('<I', self.n.read(self.review_view_address, 4))[0] if self.review_view_address else None
        if tick is not None:
            assert tick == self.completions[id]['ack']['ticks'], ('historical completion tick', id, tick, self.completions[id]['ack'])
        return dict(id=id, first_complete=self.saved_completion(id), history_png=label + '.png',
                    full_upper_pixel_mismatches=0, review_view_tick=tick, live_frozen=self.flow(self.state))

    def case(self, previous, target, following, label):
        self.drive(previous - 1)
        self.settle_transition()
        self.watch = {previous, target}
        self.n.emu.want_video = True
        self.enter_complete(previous)
        self.enter_complete(target)
        self.edge('A', lambda state: state['s']['text_id'] == following)
        self.wait(lambda state: state['s']['text_id'] == following and state['s']['visible'] == state['s']['char_count'] and
                  state['s']['local'] >= state['s']['duration_ticks'] and state['f'].get('scroll_ready', True))
        self.settle_transition()
        assert self.state['a']['music_playing'] and not self.state['s']['paused']
        starts, index = self.state['a']['music_starts'], self.state['a']['music_index']
        live_layers_before_B = self.n.active_layers()
        self.pcm.clear()
        self.measuring = True
        self.edge('B', lambda state: self.reviewing() and self.review_id() == target and state['s']['paused'])
        live = self.flow(self.state)
        layers = live_layers_before_B
        voice0 = self.voices()
        assert not voice0['paused']
        original_index = self.review_id()
        self.screenshot(label + '_history_first')
        self.n.emu.want_video = True
        mismatch = int(np.count_nonzero(np.any(rgb5(self.n.emu.frame) != self.completions[target]['expected'], axis=2)))
        assert mismatch == 0, ('first history frame', target, mismatch)
        first = dict(id=target, first_complete=self.saved_completion(target), full_upper_pixel_mismatches=0,
                     history_png=label + '_history_first.png')
        if self.review_view_address:
            first['review_view_tick'] = struct.unpack('<I', self.n.read(self.review_view_address, 4))[0]
            assert first['review_view_tick'] == self.completions[target]['ack']['ticks']
        for _ in range(120):
            self.step()
            assert self.flow(self.state) == live
            expected_layers = self.completions[target]['layers'] if self.args.history_resident else layers
            assert self.scene(self.n.active_layers()) == self.scene(expected_layers)
            assert not self.voices()['paused'] and self.state['a']['music_playing']
            assert self.state['a']['music_starts'] == starts and self.state['a']['music_index'] == index
        second = self.review_check(previous, label + '_history_second', live, layers)
        for _ in range(60):
            self.step()
            assert self.flow(self.state) == live
            expected_layers = self.completions[previous]['layers'] if self.args.history_resident else layers
            assert self.scene(self.n.active_layers()) == self.scene(expected_layers)
        voice1 = self.voices()
        delta = (voice1['music']['source_base'] + voice1['music']['read']) - (voice0['music']['source_base'] + voice0['music']['read'])
        assert delta > 22050, ('music did not advance during history', voice0, voice1)
        assert self.state['a']['music_starts'] == starts and self.state['a']['music_index'] == index
        self.measuring = False
        pcm = np.frombuffer(self.pcm, dtype='<i2').reshape(-1, 2)
        assert len(pcm) and np.any(pcm != 0)
        wav = self.args.output / (label + '_history_actual_spu.wav')
        with wave.open(str(wav), 'wb') as out:
            out.setnchannels(2);out.setsampwidth(2);out.setframerate(round(self.rate));out.writeframes(self.pcm)
        expected_live = self.expected_upper(layers, live['ticks'])
        self.return_expected = live
        self.first_return = None
        self.n.emu.want_video = True
        before = self.n.emu.frame_count
        self.edge('A', lambda state: not self.reviewing() and self.first_return is not None)
        first_ack = self.first_return
        for k in ('text_id', 'commands', 'ticks', 'local', 'visible', 'page'):
            assert first_ack[k] == live[k], ('first live ACK after history', k, first_ack, live)
        self.wait(lambda state: self.find_video(expected_live, before - 1) is not None, maximum=16)
        core, image = self.find_video(expected_live, before - 1)
        image.save(self.args.output / (label + '_returned_live.png'))
        assert self.state['s']['pc'] == live['pc'] and self.state['s']['text_id'] == following
        assert self.scene(self.n.active_layers()) == self.scene(layers)
        assert self.state['a']['music_starts'] == starts and self.state['a']['music_index'] == index
        self.return_expected = None
        self.watch = set()
        self.n.emu.want_video = False
        return dict(label=label, previous=previous, target=target, live_text=following,
            histories=[first, second], frozen_live=live, first_return_ACK=first_ack,
            return_actual_core=core, return_full_upper_pixel_mismatches=0, live_layers=layers,
            music_index=index, music_starts_unchanged=True, consumed_source_delta=delta,
            PCM_frames=len(pcm), PCM_rms=float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2))),
            WAV=wav.name, WAV_sha256=sha(wav.read_bytes()), final=self.state)

    def first_complete(self, id):
        record = self.text['records'][id]
        for entry in self.entries:
            pages = record['pages_bi'] if entry['flags'] & 8 else record['pages_cn']
            if entry['text_id'] == id and entry['flags'] & 1 and entry['visible'] >= record['char_count'] and entry['page'] + 1 >= pages:
                if not entry['flags'] & 16 or entry['scroll_covered'] >= entry['visible']:
                    return entry
        return None

    def special_case(self):
        # Accurate completions come from actual passage, never quiet seek.
        self.drive(4613)
        self.edge('A', lambda state: state['s']['text_id'] == 4614)
        self.wait(lambda state: self.first_complete(4614) is not None)
        original_special = self.state['s']['special_group']
        assert original_special != 0xffffffff
        self.drive(4615)
        assert self.state['s']['special_group'] == 0xffffffff and self.state['a']['music_playing']
        self.n.emu.want_video = True
        index, starts = self.state['a']['music_index'], self.state['a']['music_starts']
        live_layers_before_B = self.n.active_layers()
        self.pcm.clear();self.measuring = True
        observations = []
        live = layers = voice0 = None
        for target in (4614,4613):
            self.edge('B', lambda state, t=target: self.reviewing() and self.review_id() == t and state['s']['paused'])
            if live is None:
                live, layers, voice0 = self.flow(self.state), live_layers_before_B, self.voices()
            self.screenshot(f'special_history_{target}')
            self.n.emu.want_video = True
            words = rgb5(self.n.emu.frame)
            nonblack = int(np.count_nonzero(np.any(words != 0, axis=2)))
            assert nonblack > 16, ('special history failed to display meaningful pixels', target, nonblack)
            hist_now, hist_special, hist_group = (struct.unpack('<I', self.n.read(self.review_view_address + offset,4))[0] for offset in (0,20,24))
            assert hist_special == 1 and hist_group == original_special
            assert hist_now == self.first_complete(target)['ticks']
            for _ in range(90):
                self.step()
                assert self.flow(self.state) == live
                if not self.args.history_resident:
                    assert self.scene(self.n.active_layers()) == self.scene(layers)
                assert self.state['a']['music_starts'] == starts and self.state['a']['music_index'] == index
                assert not self.voices()['paused']
            observations.append(dict(target=target, first_complete_ACK=self.first_complete(target), historical_now=hist_now,
                historical_special=hist_special, historical_group=hist_group, nonblack_pixels=nonblack,
                png=f'special_history_{target}.png', pixel_scope='Liveness/loading only; no exact special-frame pixel oracle is claimed.'))
        voice1 = self.voices()
        delta = voice1['music']['source_base'] + voice1['music']['read'] - voice0['music']['source_base'] - voice0['music']['read']
        assert delta > 22050
        self.measuring = False
        pcm = np.frombuffer(self.pcm,dtype='<i2').reshape(-1,2)
        assert len(pcm) and np.any(pcm != 0)
        wav = self.args.output / 'special_history_actual_spu.wav'
        with wave.open(str(wav),'wb') as out:
            out.setnchannels(2);out.setsampwidth(2);out.setframerate(round(self.rate));out.writeframes(self.pcm)
        expected = self.expected_upper(layers,live['ticks'])
        self.return_expected, self.first_return = live, None
        before = self.n.emu.frame_count
        self.edge('A',lambda state:not self.reviewing() and self.first_return is not None)
        for key in ('text_id','commands','ticks','local','visible','page'):
            assert self.first_return[key] == live[key]
        self.wait(lambda state:self.find_video(expected,before-1) is not None,maximum=16)
        core,image = self.find_video(expected,before-1)
        image.save(self.args.output / 'special_returned_normal.png')
        assert self.state['s']['text_id'] == 4615 and self.state['s']['pc'] == live['pc']
        assert self.scene(self.n.active_layers()) == self.scene(layers)
        assert self.state['a']['music_index'] == index and self.state['a']['music_starts'] == starts
        return dict(label='special_to_normal_back_to_special', histories=observations, frozen_live=live,
            first_return_ACK=self.first_return, live_layers=layers, return_actual_core=core,
            return_normal_RGB5_mismatches=0, music_index=index,music_starts_unchanged=True,
            consumed_source_delta=delta, PCM_frames=len(pcm),PCM_rms=float(np.sqrt(np.mean(pcm.astype(np.float64)**2))),
            WAV=wav.name,WAV_sha256=sha(wav.read_bytes()),final=self.state)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('rom', 'map', 'image', 'output', 'seed-sav'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--expected-sha', required=True)
    p.add_argument('--slot', type=int, default=1)
    p.add_argument('--language', type=int, choices=(0,1), default=1)
    p.add_argument('--special-only', action='store_true', help='Minimal4614 special→4615 normal→B liveness/audio/rollback check')
    p.add_argument('--history-resident', action='store_true', help='B holds historical assets; A reloads saved live metadata (v3+)')
    p.add_argument('--text-pack', type=Path, default=ROOT / 'nds/nitrofs/story/text.bin')
    p.add_argument('--text-manifest', type=Path, default=ROOT / 'nds/artifacts/story_text_manifest.json')
    a = p.parse_args()
    assert Path(os.environ['ZATO_MPCF_IMAGE']).resolve() == a.image.resolve()
    assert not a.image.exists() and not a.output.exists()
    assert sha(a.rom.read_bytes()) == a.expected_sha
    a.output.mkdir(parents=True)
    pulse.audio.seed_image(a.image, a.seed_sav)
    from verify_scene_package import files
    embedded = files(a.rom)
    assert embedded['story/text.bin'] == a.text_pack.read_bytes()
    assert sha(embedded['story/script.bin']) == pulse.poem.SOURCE_SCRIPT_SHA
    supplied = a.rom
    copy = a.output.parent / (a.expected_sha[:8] + '_rom') / 'ZATO_Story_Chain.nds'
    copy.parent.mkdir(exist_ok=True)
    if not copy.exists():shutil.copyfile(a.rom, copy)
    assert sha(copy.read_bytes()) == a.expected_sha
    a.rom = copy
    a.core = ROOT / 'nds/artifacts/save_spike/desmume_fat_test.dll'
    a.emulator_tools = Path(r'C:\DeskBox\Source\Nds_UI_Test\tools')
    a.cpu_mode = 'interpreter'
    identity = dict(rom=str(supplied), rom_sha256=a.expected_sha, map_sha256=sha(a.map.read_bytes()),
        core_sha256=sha(a.core.read_bytes()), tool_sha256=sha(Path(__file__).read_bytes()),
        text_sha256=sha(a.text_pack.read_bytes()), script_sha256=sha(embedded['story/script.bin']),
        seed_sav=str(a.seed_sav), seed_sav_sha256=sha(a.seed_sav.read_bytes()), image=str(a.image),
        PNG_oracle_sha256=sha((ROOT / 'nds/tests/story_gfx_oracle.py').read_bytes()))
    identity['history_ownership'] = 'retained historical assets, metadata-only live' if a.history_resident else 'transient history with live rollback'
    probe = None
    try:
        probe = Probe(a);probe.boot_title();probe.load_seed();probe.chapter(4 if a.special_only else 8)
        cases = [probe.special_case()] if a.special_only else [probe.case(9058,9059,9060,'scene_change'), probe.case(9133,9134,9135,'animated_pulse')]
        assert sha(a.seed_sav.read_bytes()) == identity['seed_sav_sha256']
        _, sav = pulse.audio.cold.save_file(a.image)
        (a.output / 'extracted_after.sav').write_bytes(sav)
        pulse.audio.cold.native.atomic_json(a.output / 'report.json', dict(result='PASS', identity=identity, cases=cases,
            actions=probe.actions, final=probe.state, image_sha256=sha(a.image.read_bytes()),
            method='Physical A/B only, fresh owned FAT, read-only RAM. First-complete published ACK is retained without requiring local>=duration. Continuous actual video is image-matched against source-PNG oracle at that ACK tick; observed video callback latency is separately retained. B freezes live flow; resource ownership follows identity.history_ownership. In retained mode active metadata must match the captured historical scene, and A reloads the saved live metadata. Music source consumption and actual PCM continue; no audible-cursor claim. A first live ACK restores frozen time/local exactly.'))
        print(json.dumps(dict(result='PASS', identity=identity, cases=[(c['label'],c['consumed_source_delta']) for c in cases]),indent=2),flush=True)
    except BaseException as error:
        pulse.audio.cold.native.atomic_json(a.output / 'report.json', dict(result='FAIL', identity=identity, error=str(error),
            traceback=traceback.format_exc(), state=probe.state if probe else None,
            completions={str(k):{f:v for f,v in r.items() if f!='expected'} for k,r in probe.completions.items()} if probe else {}))
        raise
    finally:
        if probe:probe.n.close()


if __name__ == '__main__':main()
