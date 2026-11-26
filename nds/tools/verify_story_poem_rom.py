"""Physical-key split-poem proof on a fresh, file-backed FAT image.

Seed an authentic extracted checkpoint near ep1's poem, then walk normally.
No RAM writes, savestates, source edits or ROM rebuilding. The parent process
must set ZATO_MPCF_IMAGE before launching this tool. Each language run owns a
different image/output directory. Packed masks are decoded independently of
both the native renderer and the Python layout producer.
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
import sys
import traceback
import wave
import zlib
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
NONE = 0xffffffff
SOURCE_SCRIPT_SHA = '9e09569bdcc90902fbb04a3f56b4ae75732643b8f1f144a4a2cc5abc4cee1c25'
GROUPS = {
    1: dict(ids=(11952, 11953), durations=(1200, 960), pages=2, sound=32, sound_frames=442263, before=2379, after=2380, chapter=1),
    2: dict(ids=(11960, 11961), durations=(1860, 1260), pages=3, sound=33, sound_frames=669650, before=9855, after=9856, chapter=8),
}


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


cold = module('poem_cold_driver', ROOT / 'nds/tools/verify_story_scroll_cold.py')
seed = module('poem_seed_image', ROOT / 'nds/tools/verify_story_audio_scroll.py')


def digest(data):
    return hashlib.sha256(data).hexdigest()


class PackedPoems:
    """Small read-only codec/mask oracle, with no producer layout imports."""
    def __init__(self, data):
        self.data = data
        self.h = struct.unpack_from('<32I', data, 8)
        assert data[:8] == b'ZATSTX02' and self.h[29] == 4
        self.cache = {}

    def record(self, id):
        if id in self.cache:
            return self.cache[id]
        at, flags, size, checksum = struct.unpack_from('<IHHI', self.data, self.h[8] + id * 12)
        blob = self.data[at:at + (flags & 32767)]
        if flags & 32768:
            raw = blob
        else:
            assert blob[0] == 16 and int.from_bytes(blob[1:4], 'little') == size
            raw = bytearray()
            p = 4
            while len(raw) < size:
                control = blob[p]
                p += 1
                for bit in range(7, -1, -1):
                    if len(raw) == size:
                        break
                    a = blob[p]
                    p += 1
                    if control & (1 << bit):
                        token = a * 256 + blob[p]
                        p += 1
                        count, distance = (token >> 12) + 3, (token & 4095) + 1
                        assert distance <= len(raw) and len(raw) + count <= size
                        for _ in range(count):
                            raw.append(raw[-distance])
                    else:
                        raw.append(a)
        assert len(raw) == size and zlib.crc32(raw) == checksum
        f = struct.unpack_from('<24H', raw)
        assert f[16] == 4 and f[4] == f[5] and f[14] and not any(f[18:])
        planes = []
        for start, count in ((f[11], f[12]), (f[13], f[14])):
            colors = [0xffff] * count
            runs = struct.unpack_from('<H', raw, start + count * 5)[0]
            for i in range(runs):
                first, n, color = struct.unpack_from('<3H', raw, start + count * 5 + 2 + i * 6)
                assert first + n <= count
                colors[first:first + n] = [color] * n
            planes.append([dict(gid=struct.unpack_from('<H', raw, start + i * 2)[0],
                                x=raw[start + count * 2 + i], y=raw[start + count * 3 + i],
                                page=raw[start + count * 4 + i], color=colors[i]) for i in range(count)])
        self.cache[id] = f, planes
        return f, planes

    def paint(self, screen, command):
        tag = command['gid']
        if tag == 65535:
            return
        at, width, packed = struct.unpack_from('<IHH', self.data, self.h[9] + (tag & 0x3fff) * 8)
        height, advance = packed & 255, packed >> 8
        bits = self.data[self.h[10] + at:self.h[10] + at + (width * height + 7) // 8]
        mask = np.unpackbits(np.frombuffer(bits, dtype='u1'))[:width * height].reshape(height, width)
        yy, xx = np.nonzero(mask)
        x, y, color = command['x'], command['y'], command['color']
        if tag & 0x8000:
            screen[y + yy + 1, x + xx + 1] = 0xb18c
        screen[y + yy, x + xx] = color
        if tag & 0x4000:
            screen[y + height, x:x + advance] = color

    def expected(self, id, page):
        f, planes = self.record(id)
        assert page < f[4]
        lower = np.full((192, 256), 0x8000, dtype='<u2')
        upper = lower.copy()
        lower[10, 4:252] = lower[190, 4:252] = 0xffff
        lower[10:191, 4] = lower[10:191, 251] = 0xffff
        for screen, commands in ((lower, planes[0]), (upper, planes[1])):
            for command in commands:
                if command['page'] == page:
                    self.paint(screen, command)
        assert np.any(lower[20:174, 5:251] != 0x8000) and np.any(upper != 0x8000)
        return upper, lower


def screenshot_rgb15(image):
    pixels = np.asarray(image.convert('RGB'), dtype=np.uint16)
    assert pixels.shape == (384, 256, 3), pixels.shape
    return (pixels[..., 0] >> 3) | ((pixels[..., 1] >> 3) << 5) | ((pixels[..., 2] >> 3) << 10)


class Probe(cold.Probe):
    def __init__(self, args, poems):
        self.poems = poems
        self.trace, self.inputs, self.pages, self.transitions = [], [], {}, []
        self.first_pages = {}
        self.last_literal = NONE
        self.record_pcm = False
        self.pcm = bytearray()
        self.last_progress = None
        super().__init__(args)
        sys.path.insert(0, str(args.emulator_tools))
        import nds_emulator
        self.audio_cb = nds_emulator.BATCH(self.audio_batch)
        self.single_cb = nds_emulator.AUDIO(self.audio_single)
        self.n.emu.core.retro_set_audio_sample_batch(self.audio_cb)
        self.n.emu.core.retro_set_audio_sample(self.single_cb)
        av = C.create_string_buffer(40)
        self.n.emu.core.retro_get_system_av_info(C.byref(av))
        _, self.sample_rate = struct.unpack_from('<2d', av.raw, 24)

    def audio_batch(self, pointer, count):
        if self.record_pcm:
            self.pcm.extend(C.string_at(pointer, count * 4))
        return count

    def audio_single(self, left, right):
        if self.record_pcm:
            self.pcm.extend(struct.pack('<hh', left, right))

    def observe(self):
        super().observe()
        if not self.state['coherent'] or not self.state['s']['ready']:
            return
        s, a = self.state['s'], self.state['a']
        literal = s['literal_id']
        if literal in (11952, 11953, 11960, 11961):
            self.first_pages.setdefault((literal, s['literal_page']), self.state)
        if literal != self.last_literal:
            self.transitions.append(dict(previous=self.last_literal, current=literal, state=self.state))
            self.last_literal = literal
        if self.record_pcm:
            self.trace.append({k:s[k] for k in ('frames', 'ticks', 'pc', 'source_line', 'text_id', 'local', 'literal_id', 'literal_start', 'literal_duration', 'literal_page', 'bilingual')}
                              | dict(core=self.n.emu.frame_count, audio={k:a[k] for k in ('sfx_index', 'sfx_playing', 'sfx_starts', 'sfx_frames', 'sfx_finishes', 'music_underruns', 'sfx_underruns', 'errors')}))
        if s['text_id'] != self.last_progress and s['text_id'] % 100 == 0:
            print('walk', s['chapter'], s['text_id'], 'core', self.n.emu.frame_count, flush=True)
            self.last_progress = s['text_id']

    def edge(self, key, predicate, maximum=1800):
        self.inputs.append(dict(key=key, core=self.n.emu.frame_count, state=self.state['s']))
        return super().edge(key, predicate, maximum)

    def load_seed(self):
        saved, _ = cold.saved_slot(self.args.image, self.args.slot)
        assert saved['checkpoint']['flow']['text_id'] == 2267
        self.expected = saved
        self.edge('DOWN', lambda st: st['shell']['selection'] == 1)
        self.edge('A', lambda st: st['shell']['mode'] == 3)
        for selection in range(1, self.args.slot + 1):
            self.edge('RIGHT', lambda st, n=selection: st['shell']['selection'] == n)
        self.edge('A', lambda st: st['shell']['load_ops'] or st['shell']['save_errors'])
        self.wait(lambda st: self.first is not None)
        assert self.state['shell']['load_ops'] == 1 and not self.state['shell']['save_errors']
        assert self.state['s']['text_id'] == 2267 and self.state['s']['chapter'] == 1
        if self.state['s']['bilingual'] != self.args.language:
            self.edge('X', lambda st: st['s']['bilingual'] == self.args.language)
        if self.state['s']['scroll_manual']:
            self.edge('A', lambda st: not st['s']['scroll_manual'])
        self.expected = None
        return saved

    def chapter(self, target):
        while self.state['s']['chapter'] != target:
            current = self.state['s']['chapter']
            self.edge('RIGHT', lambda st, n=current + 1: st['s']['chapter'] == n)
            # seek() clears the sampled edge; allow its initial I/O and at least
            #90 source ticks before producing another physical RIGHT edge.
            start = self.state['s']['ticks']
            self.wait(lambda st: st['s']['ticks'] >= start + 90)
            self.release()

    def drive(self, target):
        controller = cold.native.ManualController()
        for _ in range(240000):
            s, f = self.state['s'], self.state['f']
            if s['text_id'] == target:
                self.release()
                self.wait(lambda st: st['s']['text_id'] != target or
                          (st['s']['visible'] == st['s']['char_count'] and st['f'].get('scroll_ready', False)))
                assert self.state['s']['text_id'] == target
                return
            assert s['text_id'] == NONE or s['text_id'] < target, ('passed target', target, self.state)
            transition = self.state['transition']
            finished = s['ticks'] - transition['start'] >= transition['duration']
            if f['waiting'] and not f['hard']:
                old = s['pc']
                self.edge('A', lambda st: st['s']['pc'] != old)
                controller = cold.native.ManualController()
            elif f['blocked'] and not f['waiting'] and not f['source_auto'] and not f['paused'] and s['visible'] < s['page_limit'] and finished:
                old = tuple(s[k] for k in ('text_id', 'page', 'visible', 'local'))
                self.edge('A', lambda st: tuple(st['s'][k] for k in ('text_id', 'page', 'visible', 'local')) != old)
                controller = cold.native.ManualController()
            elif controller.outstanding is None and f.get('scrolling') and s['text_mode'] != 3 and not f.get('scroll_ready'):
                self.step()
            else:
                self.step(controller.keys(self.n, self.state))
        raise AssertionError(('walk timeout', target, self.state))

    def capture_page(self, id, page):
        name = f'poem_{id}_page{page + 1}_' + ('bi' if self.args.language else 'cn')
        self.screenshot(name)
        s = self.state['s']
        assert (s['literal_id'], s['literal_page']) == (id, page), ('page changed during capture', s)
        actual = screenshot_rgb15(self.n.emu.frame)
        upper, lower = self.poems.expected(id, page)
        upper_bad = int(np.count_nonzero(actual[:192] != (upper & 32767)))
        # Runtime's menu pack composes the existing footer at y174 separately.
        lower_bad = int(np.count_nonzero(actual[192:366] != (lower[:174] & 32767)))
        record = dict(id=id, page=page, png=name + '.png', upper_mismatches=upper_bad, lower_body_mismatches=lower_bad,
                      upper_sha256=digest(actual[:192].astype('<u2').tobytes()),
                      lower_body_sha256=digest(actual[192:366].astype('<u2').tobytes()),
                      first_seen=self.first_pages[id, page], state=self.state, layers=self.n.active_layers())
        self.pages[id, page] = record
        assert upper_bad == lower_bad == 0, ('actual screenshot differs from independent packed masks', record)
        print('captured', name, 'tick', s['ticks'], flush=True)

    def run_poem(self, number):
        group = GROUPS[number]
        self.chapter(group['chapter'])
        self.drive(group['before'])
        self.screenshot(f'poem{number}_before_say')
        before = self.state
        start_transitions = len(self.transitions)
        self.pcm.clear()
        self.record_pcm = True
        controller = cold.native.ManualController()
        entered = False
        stages = {}
        try:
            for _ in range(12000):
                s, a = self.state['s'], self.state['a']
                literal = s['literal_id']
                if literal in group['ids']:
                    if not entered:
                        self.release()
                        entered = True
                    stage = group['ids'].index(literal)
                    assert s['literal_duration'] == group['durations'][stage]
                    assert a['sfx_index'] == group['sound'] and a['sfx_starts'] == before['a']['sfx_starts'] + 1
                    assert s['bilingual'] == self.args.language
                    stages.setdefault(literal, dict(start=s['literal_start'], duration=s['literal_duration'], first=self.state))
                    elapsed = s['ticks'] - s['literal_start']
                    want_page = min(group['pages'] - 1, elapsed * group['pages'] // s['literal_duration'])
                    assert s['literal_page'] == want_page, ('source-clock page schedule', self.state, want_page)
                    if (literal, want_page) not in self.pages and elapsed >= 90:
                        transition = self.state['transition']
                        if s['ticks'] - transition['start'] >= transition['duration']:
                            self.capture_page(literal, want_page)
                if entered and s['text_id'] == group['after'] and s['blocked']:
                    break
                self.step(() if entered else controller.keys(self.n, self.state))
            else:
                raise AssertionError(('poem natural endpoint timeout', number, self.state))
        finally:
            self.record_pcm = False
        for id, duration in zip(group['ids'], group['durations']):
            assert id in stages
            assert {(id, page) for page in range(group['pages'])} <= self.pages.keys()
            exit_state = next(t['state'] for t in self.transitions[start_transitions:] if t['previous'] == id and t['current'] != id)
            assert exit_state['s']['ticks'] - stages[id]['start'] >= duration, ('poem hidden before authored wait', stages[id], exit_state)
            stages[id]['exit'] = exit_state
        assert self.state['s']['literal_id'] == NONE
        assert self.state['a']['sfx_starts'] == before['a']['sfx_starts'] + 1
        assert not self.state['a']['sfx_playing'], ('poem voice not stopped at source endpoint', self.state)
        assert self.state['a']['sfx_frames'] == group['sound_frames'], ('poem source samples truncated or repeated', self.state)
        first_audio = stages[group['ids'][0]]['first']['a']
        assert self.state['a']['sfx_finishes'] == first_audio['sfx_finishes'] + 1, ('poem one-shot did not finish naturally', self.state)
        self.screenshot(f'poem{number}_after_say')
        assert self.state['s']['text_id'] == group['after'] and self.state['s']['blocked']
        pcm_path = self.args.output / f'poem{number}_actual_spu.wav'
        with wave.open(str(pcm_path), 'wb') as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(round(self.sample_rate))
            out.writeframes(self.pcm)
        pcm = np.frombuffer(self.pcm, dtype='<i2').reshape(-1, 2)
        assert np.any(pcm != 0), 'actual SPU capture was entirely silent'
        return dict(before=before, after=self.state, stages=stages, wav=pcm_path.name, wav_sha256=digest(pcm_path.read_bytes()),
                    actual_audio_frames=len(pcm), audio_rate=self.sample_rate, output_peak=int(np.abs(pcm.astype(np.int32)).max()))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('rom', 'map', 'image', 'output', 'seed-sav'):
        p.add_argument('--' + name, required=True, type=Path)
    p.add_argument('--expected-sha', required=True)
    p.add_argument('--seed-report', type=Path)
    p.add_argument('--slot', type=int, default=1)
    p.add_argument('--language', type=int, choices=(0, 1), default=1)
    p.add_argument('--poem', choices=('1', '2', 'both'), default='both')
    p.add_argument('--text-pack', type=Path, default=ROOT / 'nds/nitrofs/story/text.bin')
    p.add_argument('--text-manifest', type=Path, default=ROOT / 'nds/artifacts/story_text_manifest.json')
    a = p.parse_args()
    assert Path(os.environ['ZATO_MPCF_IMAGE']).resolve() == a.image.resolve(), 'set parent environment before process start'
    assert not a.image.exists() and not a.output.exists(), 'owned fresh paths required'
    assert digest(a.rom.read_bytes()) == a.expected_sha
    a.output.mkdir(parents=True)
    seed_hash = digest(a.seed_sav.read_bytes())
    seed.seed_image(a.image, a.seed_sav)
    assert cold.global_settings(cold.save_file(a.image)[1])['chapter_skip'] == 1, 'authentic seed must enable physical chapter navigation'
    from verify_scene_package import files
    embedded = files(a.rom)
    assert embedded['story/text.bin'] == a.text_pack.read_bytes()
    assert digest(embedded['story/script.bin']) == SOURCE_SCRIPT_SHA, 'source event/timing identity changed'
    poems = PackedPoems(embedded['story/text.bin'])
    for group in GROUPS.values():
        for id in group['ids']:
            f, _ = poems.record(id)
            assert f[4] == f[5] == group['pages']
    source_rom = a.rom
    copy_rom = a.output.parent / 'candidate_rom/ZATO_Story_Chain.nds'
    copy_rom.parent.mkdir(parents=True, exist_ok=True)
    if not copy_rom.exists():
        shutil.copyfile(a.rom, copy_rom)
    assert digest(copy_rom.read_bytes()) == a.expected_sha
    a.rom = copy_rom
    a.core = ROOT / 'nds/artifacts/save_spike/desmume_fat_test.dll'
    a.emulator_tools = Path(r'C:\DeskBox\Source\Nds_UI_Test\tools')
    a.cpu_mode = 'interpreter'
    identity = dict(rom=str(source_rom), rom_sha256=a.expected_sha, map=str(a.map), map_sha256=digest(a.map.read_bytes()),
                    text_sha256=digest(embedded['story/text.bin']), script_sha256=SOURCE_SCRIPT_SHA,
                    seed_sav=str(a.seed_sav), seed_sav_sha256=seed_hash, core_sha256=digest(a.core.read_bytes()), image=str(a.image),
                    tool_sha256=digest(Path(__file__).read_bytes()), language=a.language)
    identity['audio_payload_sha256'] = {name:digest(embedded[name]) for name in
        ('audio/index.bin', 'audio/sfx/032.wav', 'audio/sfx/033.wav')}
    if a.seed_report:
        assert json.loads(a.seed_report.read_text(encoding='utf8'))['result'] == 'PASS'
        identity['seed_report_sha256'] = digest(a.seed_report.read_bytes())
    probe = None
    try:
        probe = Probe(a, poems)
        probe.boot_title()
        loaded = probe.load_seed()
        groups = (1, 2) if a.poem == 'both' else (int(a.poem),)
        results = {str(number): probe.run_poem(number) for number in groups}
        assert digest(a.seed_sav.read_bytes()) == seed_hash, 'original seed changed'
        cold.native.atomic_json(a.output / 'trace.json', probe.trace)
        cold.native.atomic_json(a.output / 'report.json', dict(result='PASS', identity=identity, seed_checkpoint=loaded,
            poems=results, pages=list(probe.pages.values()), inputs=probe.inputs, final=probe.state,
            sav_sha256=digest(cold.save_file(a.image)[1]), image_sha256=digest(a.image.read_bytes()),
            method='Physical keys and read-only RAM; fresh FAT seeded byte-identically. Actual emulator screenshots match packed masks: full upper and lower rows0..173; footer is separately composed. Existing source waits run naturally after poem entry. Normal NDS seen/global/autosave writes are permitted only in this owned image.'))
        print(json.dumps(dict(result='PASS', identity=identity, captured_pages=len(probe.pages)), indent=2), flush=True)
    except BaseException as error:
        cold.native.atomic_json(a.output / 'report.json', dict(result='FAIL', identity=identity, error=str(error),
            traceback=traceback.format_exc(), state=probe.state if probe else None,
            pages=list(probe.pages.values()) if probe else [], trace=probe.trace[-30:] if probe else []))
        raise
    finally:
        if probe:
            probe.n.close()


if __name__ == '__main__':
    main()
