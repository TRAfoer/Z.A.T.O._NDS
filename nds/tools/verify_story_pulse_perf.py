"""Local chapter9 ppulse timing/PCM capture; no product or RAM writes.

Set ZATO_MPCF_IMAGE in the parent, then use an authentic text2267 checkpoint
to load physically and RIGHT-jump to chapter8. Ordinary manual A advances to
9060/9145; both animated dialogues then run naturally for multiple cycles.
"""
from pathlib import Path
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


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    out = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(out)
    return out


audio = module('pulse_audio_observer', ROOT / 'nds/tools/verify_story_audio_scroll.py')
poem = module('pulse_physical_controls', ROOT / 'nds/tools/verify_story_poem_rom.py')
source_wire = module('natural_old_source_wire', ROOT / 'nds/tools/story_scroll_pixel_audit.py')


def sha(data):
    return hashlib.sha256(data).hexdigest()


class LocalTrace(list):
    def __init__(self, owner):
        self.owner = owner

    def append(self, row):
        p = self.owner
        if not p.measuring:
            return
        s = p.state['s']
        row.update({k:s[k] for k in ('ticks', 'pc', 'source_line', 'layers', 'gfx_version', 'gfx_reads', 'io_vblanks', 'transition_kind', 'transition_start', 'transition_duration')})
        row['coherent'] = p.state['coherent']
        row['upper_dirty'] = p.n.read(p.upper_dirty_address, 1)[0]
        row['upper_valid'] = p.n.read(p.upper_valid_address, 1)[0]
        super().append(row)


class Probe(audio.Probe):
    load_seed = poem.Probe.load_seed
    chapter = poem.Probe.chapter
    drive = poem.Probe.drive

    def __init__(self, a):
        self.measuring = False
        self.typing_target = None
        self.typing_entries = []
        self.natural_results = []
        self.pixel_results = []
        super().__init__(a)
        self.trace = LocalTrace(self)
        self.upper_dirty_address = self.n.symbols.section('upper_dirty', 'story_runtime')[0]
        self.upper_valid_address = self.n.symbols.section('upper_valid', 'story_runtime')[0]

    def observe(self):
        before = len(self.entries)
        super().observe()
        for entry in self.entries[before:]:
            if entry['text_id'] == self.typing_target and entry['flags'] & 1:
                self.typing_entries.append(dict(entry, observed_core=self.n.emu.frame_count))

    def audio_batch(self, pointer, count):
        if self.measuring:
            return super().audio_batch(pointer, count)
        return count

    def hold(self, text, label, frames):
        assert self.state['s']['text_id'] == text and not self.state['s']['source_auto']
        baseline = self.state
        self.mark(label)
        for _ in range(frames):
            self.step()
            assert self.state['s']['text_id'] == text
            assert self.state['a']['music_starts'] == baseline['a']['music_starts']
            assert self.state['a']['music_index'] == baseline['a']['music_index']
        self.screenshot(label)

    def natural(self, target, label):
        """One A enters from the completed predecessor; no A on target."""
        assert self.state['s']['text_id'] == target - 1
        old = source_wire.Pack.__new__(source_wire.Pack)
        old.data = self.args.source_text.read_bytes()
        old.h = struct.unpack_from('<32I', old.data, 8)
        raw, fields = old.raw(target)
        assert not fields[3] and not fields[7], 'choose independent no-pause source Say'
        timeline = []
        clock = 0
        runs = struct.unpack_from('<H', raw, fields[9])[0]
        for i in range(runs):
            n, delta = struct.unpack_from('<2H', raw, fields[9] + 2 + i * 4)
            for _ in range(n):
                clock += delta
                timeline.append(clock)
        assert len(timeline) == fields[1]
        current = self.wire.get(target)
        assert current['fields'][1:4] == fields[1:4], 'source logical/timing changed'
        cn_caps = [min((c['index'] for c in current['commands_cn'] if c['page'] > page), default=fields[1])
                   for page in range(current['fields'][4])]
        self.mark(label)
        self.typing_target = target
        self.typing_entries = []
        self.edge('A', lambda state: state['s']['text_id'] == target)
        assert not self.state['s']['source_auto'] and not self.state['f']['automatic'] and not self.state['f']['skip'] and not self.state['s']['paused']
        self.wait(lambda state: state['s']['text_id'] == target and
                  state['s']['visible'] == state['s']['char_count'] and
                  state['s']['local'] >= state['s']['duration_ticks'] and state['f'].get('scroll_ready', True), maximum=12000)
        self.screenshot(label)
        self.typing_target = None
        entries = self.typing_entries
        assert entries and len({e['serial'] for e in entries}) == len(entries)
        previous = 0
        source_checks = []
        for entry in entries:
            caps = current['limits_bi'] if entry['flags'] & 8 else cn_caps
            expected = min(sum(t <= entry['local'] for t in timeline), caps[entry['page']])
            assert entry['visible'] == expected, ('natural source timeline mismatch', target, entry, expected)
            source_checks.append(dict(entry, old_source_expected=expected, visible_jump=entry['visible'] - previous))
            previous = entry['visible']
        result = dict(target=target, source_line=fields[0], source_char_count=fields[1], source_duration_ticks=fields[2],
                      frozen_source_text_sha256=sha(old.data), old_source_timeline=timeline,
                      actual_presentations=source_checks, max_visible_jump=max(e['visible_jump'] for e in source_checks),
                      observed_commit_core_gaps=sorted(set(b['observed_core'] - a['observed_core'] for a,b in zip(entries,entries[1:]))),
                      source_timeline_mismatches=0, final=self.state,
                      input_contract='One physical A ends predecessor and starts target; no A/touch/skip/auto input on target. Every published target ACK is retained, including entries seen before a coherent status snapshot.')
        self.natural_results.append(result)
        print('natural', target, 'ACKs', len(entries), 'max visible jump', result['max_visible_jump'], flush=True)
        return result

    def paused_pixels(self, text, label, require_mixes=False):
        """Separate from performance/PCM. Freeze via L, then compare PNG oracle."""
        assert not self.measuring and self.state['s']['text_id'] == text
        sys.path.insert(0, str(ROOT / 'nds/tests'))
        from story_gfx_oracle import Oracle
        from PIL import Image
        oracle = Oracle()
        seen, mixes = set(), set()
        output = []
        for attempt in range(60):
            self.edge('L', lambda state: state['s']['paused'] == 1)
            try:
                frozen = self.state['s']
                for field in ('literal_id', 'date_id', 'special_group'):
                    assert frozen[field] == 0xffffffff, ('non-graphics upper overlay', field, frozen)
                assert frozen['text_mode'] != 3 and not self.state['shell']['active']
                tr = self.state['transition']
                assert not tr['duration'] or frozen['ticks'] - tr['start'] >= tr['duration'], ('outer transition not finished', tr)
                voices = self.voices()
                assert voices['paused'] == 1
                name = f'{label}_paused_{attempt:02d}'
                self.screenshot(name)  # Two real commits before the video callback.
                now = self.state['s']
                for field in ('ticks', 'pc', 'text_id', 'local', 'visible', 'page'):
                    assert now[field] == frozen[field], ('paused source moved', field, frozen, now)
                assert struct.unpack('<I', self.n.read(self.n.flow + 16, 4))[0] == now['ticks']
                after_voice = self.voices()
                assert after_voice['paused'] == 1
                for voice in ('music', 'sfx'):
                    assert after_voice[voice]['read'] == voices[voice]['read'], ('paused source audio moved', voice)
                layers = self.n.active_layers()
                native = [(r['image'], r['placement'], r['program'], r['start']) for r in layers]
                expected = oracle.draw(native, now['ticks'])
                expected_rgb = np.stack((expected & 31, (expected >> 5) & 31, (expected >> 10) & 31), axis=2)
                actual = np.asarray(self.n.emu.frame.convert('RGB'), dtype=np.uint16)[:192] >> 3
                diff = np.any(actual != expected_rgb, axis=2)
                mismatch = int(np.count_nonzero(diff))
                sequences = []
                png_sources = {}
                for layer in layers:
                    info = oracle.images[layer['image']]
                    current, prior, mix = oracle.sample(layer['image'], now['ticks'] - layer['start'])
                    if info['kind'] == 'sequence':
                        sequences.append(dict(image=layer['image'], current=current, prior=prior, mix_q12=mix,
                                              alpha_q12=oracle.programs[layer['program']][0]))
                        if prior is not None:
                            mixes.add('zero' if mix == 0 else 'nonzero')
                    for frame in (current, prior):
                        if frame is None:
                            continue
                        meta = oracle.frames[frame]
                        path = Path(meta['master']) if meta['master'] else ROOT / 'experiments/asset_lab/out/full_asset_audit/converted' / meta['name']
                        if not path.is_absolute():
                            path = ROOT / 'nds' / path
                        png_sources[str(path)] = sha(path.read_bytes())
                key = tuple((r['image'], r['current'], r['prior'], r['mix_q12'], r['alpha_q12']) for r in sequences)
                image = Image.fromarray(((expected_rgb << 3) | (expected_rgb >> 2)).astype(np.uint8), 'RGB')
                expected_name = name + '_expected_upper.png'
                image.save(self.args.output / expected_name)
                result = dict(label=label, text_id=text, attempt=attempt, actual_png=name + '.png', expected_png=expected_name,
                    frozen_ticks=now['ticks'], source_state=now, layers=layers, sequences=sequences,
                    full_upper_rgb5_mismatches=mismatch, max_channel_delta=int(np.abs(actual.astype(np.int16) - expected_rgb.astype(np.int16)).max()),
                    first_mismatches=np.argwhere(diff)[:16].tolist(), png_source_sha256=png_sources,
                    oracle_sha256=sha((ROOT / 'nds/tests/story_gfx_oracle.py').read_bytes()),
                    gfx_manifest_sha256=sha((ROOT / 'nds/artifacts/story_gfx_manifest.json').read_bytes()))
                self.pixel_results.append(result)
                assert mismatch == 0, ('actual paused upper differs from source-PNG oracle', result)
                if key not in seen:
                    seen.add(key)
                    output.append(result)
            finally:
                if self.state['s']['paused']:
                    self.edge('L', lambda state: not state['s']['paused'])
            if len(seen) >= (4 if require_mixes else 2) and (not require_mixes or mixes == {'zero', 'nonzero'}):
                break
            for _ in range(2 + attempt % 5):
                self.step()
        else:
            raise AssertionError(('physical sampling did not reach required mixture coverage', label, mixes, len(seen)))
        return dict(label=label, unique_samples=len(seen), mixture_kinds=sorted(mixes), samples=output)


def summarize(rows, markers, sample_rate, segments):
    result = []
    for segment in segments:
        subset = [row for row in rows if row['stage'] == segment['stage']]
        assert subset
        first, last = subset[0], subset[-1]
        ap = last['audio_perf']
        factor = ap['timer_div'] * 1000 / ap['clock_hz']
        counts = dict(missed=last['missed'] - first['missed'], io_vblanks=last['io_vblanks'] - first['io_vblanks'],
                      text_reads=last['text_reads'] - first['text_reads'], gfx_reads=last['gfx_reads'] - first['gfx_reads'])
        exact = {}
        for count, stamp in (('service_calls', 'last_service'), ('callback_calls', 'last_callback')):
            singles, aggregates = [], []
            previous = first['audio_perf']
            for row in subset[1:]:
                current = row['audio_perf']
                n = (current[count] - previous[count]) & 0xffffffff
                if n:
                    interval = ((current[stamp] - previous[stamp]) & 0xffffffff) * factor
                    (singles if n == 1 else aggregates).append(dict(events=n, ms=interval))
                    previous = current
            exact[stamp] = dict(single_event_samples=len(singles), max_single_event_gap_ms=max((x['ms'] for x in singles), default=None),
                                multi_event_observations=len(aggregates), max_multi_event_span_ms=max((x['ms'] for x in aggregates), default=None))
        changes = []
        previous = first
        for row in subset[1:]:
            if row['gfx_version'] != previous['gfx_version']:
                changes.append(dict(core=row['core'], ticks=row['ticks'], delta_core=row['core'] - previous['core'],
                                    delta_ticks=row['ticks'] - previous['ticks'], version=row['gfx_version']))
                previous = row
        rp0, rp1 = first['render_perf'], last['render_perf']
        peaks = {key:rp1[key] * factor for key in ('max_prepare', 'max_upper', 'max_lower', 'max_commit', 'max_flow') if rp1[key] > rp0[key]}
        result.append(dict(stage=segment['stage'], counters=counts, local_event_intervals=exact,
                           observed_last_prepare_max_ms=max(row['render_perf']['last_prepare'] for row in subset) * factor,
                           new_global_render_peaks_ms=peaks, gfx_changes=len(changes),
                           gfx_change_tick_intervals=sorted(set(x['delta_ticks'] for x in changes[1:])),
                           upper_dirty_samples=sum(bool(row['upper_dirty']) for row in subset),
                           lower_dirty_samples=sum(bool(row['lower_dirty']) for row in subset),
                           first=first, last=last, pcm_metrics=segment))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('rom', 'map', 'image', 'output', 'seed-sav'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--expected-sha', required=True)
    p.add_argument('--language', type=int, choices=(0, 1), default=1)
    p.add_argument('--slot', type=int, default=1)
    p.add_argument('--hold-frames', type=int, default=600)
    p.add_argument('--alpha02-only', action='store_true')
    p.add_argument('--scenario', choices=('pulse', 'blur'), default='pulse')
    p.add_argument('--skip-natural', action='store_true', help='Repeat the original steady/ramp/NW A/B without the new natural probe')
    p.add_argument('--natural-only', action='store_true', help='Drive directly to the predecessor and record only natural target text')
    p.add_argument('--pixels-only', action='store_true', help='Separate physical-L paused PNG-oracle samples; no perf/PCM recording')
    p.add_argument('--source-text', type=Path, default=ROOT / 'nds/artifacts/text_scale_before/text.bin')
    p.add_argument('--text-pack', type=Path, default=ROOT / 'nds/nitrofs/story/text.bin')
    p.add_argument('--text-manifest', type=Path, default=ROOT / 'nds/artifacts/story_text_manifest.json')
    a = p.parse_args()
    assert Path(os.environ['ZATO_MPCF_IMAGE']).resolve() == a.image.resolve()
    assert not a.output.exists() and not a.image.exists()
    assert sha(a.rom.read_bytes()) == a.expected_sha
    a.output.mkdir(parents=True)
    audio.seed_image(a.image, a.seed_sav)
    assert audio.cold.global_settings(audio.cold.save_file(a.image)[1])['chapter_skip'] == 1
    from verify_scene_package import files
    embedded = files(a.rom)
    assert embedded['story/text.bin'] == a.text_pack.read_bytes()
    assert sha(embedded['story/script.bin']) == poem.SOURCE_SCRIPT_SHA
    source_rom = a.rom
    copy = a.output.parent / (a.expected_sha[:8] + '_rom') / 'ZATO_Story_Chain.nds'
    copy.parent.mkdir(exist_ok=True)
    if not copy.exists():
        shutil.copyfile(a.rom, copy)
    assert sha(copy.read_bytes()) == a.expected_sha
    a.rom = copy
    a.core = ROOT / 'nds/artifacts/save_spike/desmume_fat_test.dll'
    a.emulator_tools = Path(r'C:\DeskBox\Source\Nds_UI_Test\tools')
    a.cpu_mode = 'interpreter'
    identity = dict(rom=str(source_rom), rom_sha256=a.expected_sha, map_sha256=sha(a.map.read_bytes()),
                    text_sha256=sha(a.text_pack.read_bytes()), script_sha256=poem.SOURCE_SCRIPT_SHA,
                    seed_sav=str(a.seed_sav), seed_sav_sha256=sha(a.seed_sav.read_bytes()),
                    image=str(a.image), core_sha256=sha(a.core.read_bytes()), tool_sha256=sha(Path(__file__).read_bytes()))
    identity['scenario'] = a.scenario
    probe = None
    try:
        probe = Probe(a)
        probe.boot_title()
        checkpoint = probe.load_seed()
        if a.pixels_only:
            if a.scenario == 'pulse':
                probe.chapter(8)
                probe.drive(9060)
                pixels = [probe.paused_pixels(9060, 'pulse_alpha02')]
                probe.drive(9145)
                pixels.append(probe.paused_pixels(9145, 'pulse_alpha10'))
            else:
                probe.chapter(7)
                probe.drive(8244)
                pixels = [probe.paused_pixels(8244, 'blur_mix', require_mixes=True)]
            assert sha(a.seed_sav.read_bytes()) == identity['seed_sav_sha256']
            _, sav = audio.cold.save_file(a.image)
            (a.output / 'extracted_after.sav').write_bytes(sav)
            audio.cold.native.atomic_json(a.output / 'report.json', dict(result='PASS', identity=identity, pixel_only=True,
                source_checkpoint=checkpoint, samples=pixels, all_attempts=probe.pixel_results, final=probe.state,
                image_sha256=sha(a.image.read_bytes()),
                method='Physical L freezes source flow/audio; at least two real commits precede each actual RGB5 upper screenshot. Source-PNG/metadata Oracle uses exact frozen ticks and active layer order/start/program. No performance/PCM statistics include these pauses.'))
            print(json.dumps(dict(result='PASS', identity=identity, samples=len(probe.pixel_results)), indent=2), flush=True)
            return
        if a.scenario == 'pulse':
            probe.chapter(8)
            probe.drive(9133 if a.natural_only else 9060)
            probe.pcm.clear()
            probe.measuring = True
            if a.natural_only:
                probe.natural(9134, 'natural_pulse_9134')
            else:
                probe.hold(9060, 'hold_alpha02_9060', a.hold_frames)
            if not a.alpha02_only and not a.natural_only:
                if a.skip_natural:
                    probe.mark('alpha_ramp_9061_9145')
                    probe.drive(9145)
                else:
                    probe.mark('alpha_ramp_9061_9133')
                    probe.drive(9133)
                    probe.natural(9134, 'natural_pulse_9134')
                    probe.mark('alpha_ramp_9135_9145')
                    probe.drive(9145)
                probe.hold(9145, 'hold_alpha10_9145', a.hold_frames)
                probe.mark('fast_nw_tail_9146_9159')
                probe.drive(9159)
                probe.screenshot('after_pulse_9159')
        else:
            probe.chapter(7)
            probe.drive(8243 if a.natural_only else 8233)
            probe.pcm.clear()
            probe.measuring = True
            if not a.natural_only:
                probe.hold(8233, 'hold_blur_8233', a.hold_frames)
                probe.mark('blur_to_predecessor_8243')
                probe.drive(8243)
            probe.natural(8244, 'natural_blur_8244')
            if not a.natural_only:
                probe.hold(8244, 'hold_blur_8244', a.hold_frames)
        probe.mark('done')
        probe.measuring = False
        segments = audio.metrics(probe)
        # The final zero-length marker has no trace or PCM and is only an end fence.
        segments = [s for s in segments if s['stage'] != 'done']
        metrics = summarize(probe.trace, probe.markers, probe.rate, segments)
        wav = a.output / 'actual_spu.wav'
        with wave.open(str(wav), 'wb') as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(round(probe.rate))
            out.writeframes(probe.pcm)
        assert sha(a.seed_sav.read_bytes()) == identity['seed_sav_sha256']
        _, sav = audio.cold.save_file(a.image)
        (a.output / 'extracted_after.sav').write_bytes(sav)
        audio.cold.native.atomic_json(a.output / 'trace.json', probe.trace)
        audio.cold.native.atomic_json(a.output / 'report.json', dict(result='CAPTURED', identity=identity, source_checkpoint=checkpoint,
            segments=metrics, final=probe.state, wav_sha256=sha(wav.read_bytes()), sample_rate=probe.rate,
            natural_typewriter=probe.natural_results,
            map_memory=audio.map_memory_summary(a.map), image_sha256=sha(a.image.read_bytes()),
            limitations='Real ROM on DeSmuME; cache/DMA bus timing is approximated. Runtime max_* values are cumulative; only peaks newly exceeded inside a stage are labeled stage-local. last_prepare is sampled, not every call. Callback work excludes the SDK copy/cache-flush epilogue. Multi-event timestamp spans are not individual service gaps. Upper preparation includes cache flush; commit aggregates upper DMA and lower CPU upload and cannot isolate their costs.'))
        print(json.dumps(dict(result='CAPTURED', identity=identity, stages=[dict(stage=s['stage'], counters=s['counters'], last_prepare_ms=s['observed_last_prepare_max_ms']) for s in metrics]), indent=2), flush=True)
    except BaseException as error:
        audio.cold.native.atomic_json(a.output / 'report.json', dict(result='FAIL', identity=identity, error=str(error),
            traceback=traceback.format_exc(), state=probe.state if probe else None))
        raise
    finally:
        if probe:
            probe.n.close()


if __name__ == '__main__':
    main()
