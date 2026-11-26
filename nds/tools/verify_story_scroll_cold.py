r"""Cold-load a real scrolling checkpoint through keys and file-backed FAT.

Each phase must run in a separate process. ZATO_MPCF_IMAGE must be set by the
parent BEFORE Python starts. RAM is read-only; save bytes are created by NDS.
Only a new, owned image (or a new copy for v1 compatibility) is permitted.

Example, after the candidate ROM/MAP have been frozen:
  $env:ZATO_MPCF_IMAGE='C:\...\scroll_cold\fat.img'
  python verify_story_scroll_cold.py --phase save --create --image $env:ZATO_MPCF_IMAGE ...
  python verify_story_scroll_cold.py --phase load --image $env:ZATO_MPCF_IMAGE ...
Use --phase legacy-load --copy-from-image OLD.img with a DIFFERENT new image.
"""
from __future__ import annotations

import argparse
import ctypes as C
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import traceback
import zlib

ROOT = Path(__file__).resolve().parents[2]
SPIKE = ROOT / 'nds/artifacts/save_spike'
sys.path.insert(0, str(ROOT / 'nds/tools'))
from fat16 import create as create_image, files as fat_files

spec = importlib.util.spec_from_file_location('scroll_readonly_native', ROOT / 'nds/tools/verify_story_rom.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)

# The log has a nonzero magic/capacity initializer and therefore lives in data,
# while the generic reader historically searched bss. Keep this adapter local.
class ScrollSymbols(native.Symbols):
    def section(self, name, owner, prefix='bss'):
        if name == 'g_story_presentations' and owner == 'story_runtime' and prefix == 'bss':
            prefix = 'data'
        return super().section(name, owner, prefix)

native.Symbols = ScrollSymbols

FLOW = ('pc now chapter line commands text_id literal_id date_id local visible page pause_index '
        'page_ready wait_started wait_end presented_local presented_visible literal_start '
        'literal_duration unlock_low unlock_high').split()
VOICE = ('enabled index loop offset_frames gain_q12 fade_step fade_target fade_total fade_left '
         'fade_remainder fade_error flags').split()
NONE = 0xffffffff


def digest(data):
    return hashlib.sha256(data).hexdigest()


def decode_checkpoint(raw):
    assert len(raw) == 768 and raw[:8] == b'ZATCKP01', 'checkpoint magic/size'
    version, payload = struct.unpack_from('<2I', raw, 8)
    assert (version, payload) in ((1, 600), (2, 612), (3, 616)), ('checkpoint version/length', version, payload)
    assert not any(raw[16 + payload:]), 'nonzero checkpoint reserved bytes'
    flow = dict(zip(FLOW, struct.unpack_from('<21I', raw, 16)))
    flow['flags'] = struct.unpack_from('<I', raw, 100)[0]
    count = struct.unpack_from('<I', raw, 104)[0]
    assert count <= 16
    layers = [dict(zip(('image', 'tag', 'program', 'placement', 'start'),
                       struct.unpack_from('<IIIiI', raw, 108 + i * 20))) for i in range(count)]
    assert not any(raw[108 + count * 20:428]), 'unused checkpoint layers'
    audio = dict(zip(('version', 'paused', 'master_music', 'master_sfx'), struct.unpack_from('<4I', raw, 428)))
    assert audio['version'] == 1 and audio['paused'] <= 1
    for name, offset in (('music', 444), ('sfx', 492)):
        values = list(struct.unpack_from('<12I', raw, offset))
        for i in (4, 5, 6):
            values[i] = C.c_int32(values[i]).value
        audio[name] = dict(zip(VOICE, values))
    backend = dict(zip(('special group phase origin0 origin1 origin2 origin3 origin4 have_music '
                        'music_tick transition transition_start transition_duration').split(),
                       struct.unpack_from('<13I', raw, 540)))
    backend['music_event'] = list(struct.unpack_from('<6I', raw, 592))
    if version >= 2:
        scroll, manual, covered = struct.unpack_from('<3I', raw, 616)
    else:
        scroll, manual, covered = 0, 0, flow['visible']
    assert scroll <= 65535 and manual <= 1 and covered <= flow['visible'], 'invalid checkpoint viewport'
    layout_key = struct.unpack_from('<I', raw, 628)[0] if version == 3 else 0
    return dict(version=version, payload_bytes=payload, layout_key=layout_key, flow=flow, layers=layers, audio=audio,
                backend=backend, scroll=scroll, scroll_manual=manual, scroll_covered=covered,
                sha256=digest(raw))


def save_file(image):
    candidates = [(name, data) for name, data in fat_files(image).items()
                  if name.lower().endswith('.sav') and len(data) == 524288 and data[:8] == b'ZATSAV01']
    assert len(candidates) == 1, ('expected one actual game .sav', [name for name, _ in candidates])
    return candidates[0]


def global_settings(sav):
    banks = []
    for base in (0, 8192):
        assert sav[base:base + 8] == b'ZATSAV01'
        assert struct.unpack_from('<2I', sav, base + 8) == (1, 524288)
        assert zlib.crc32(sav[base:base + 8188]) == struct.unpack_from('<I', sav, base + 8188)[0]
        seq, language, music, sfx, ticks = struct.unpack_from('<5I', sav, base + 48)
        chapter_skip = struct.unpack_from('<I', sav, base + 1572)[0]
        assert language <= 1 and chapter_skip <= 1
        banks.append(dict(base=base, seq=seq, language=language, music_q12=music,
                          sfx_q12=sfx, play_ticks=ticks, chapter_skip=chapter_skip))
    assert max(b['seq'] for b in banks) < 0xfffffff0, 'test does not cross generation wrap'
    return max(banks, key=lambda bank: bank['seq'])


def saved_slot(image, slot):
    name, sav = save_file(image)
    settings = global_settings(sav)
    identity = digest((ROOT / 'nds/nitrofs/story/script.bin').read_bytes())
    assert sav[16:48].hex() == identity and sav[8208:8240].hex() == identity, 'story identity mismatch'
    record = bytearray(sav[24576 + slot * 4096:24576 + (slot + 1) * 4096])
    assert record[:8] == b'ZATSLOT1' and struct.unpack_from('<I', record, 8)[0] == 1
    assert record[12:44].hex() == identity
    assert struct.unpack_from('<I', record, 44)[0] == slot and struct.unpack_from('<I', record, 52)[0] == 1
    checksum = struct.unpack_from('<I', record, 252)[0]
    record[252:256] = bytes(4)
    assert zlib.crc32(record) == checksum, 'slot CRC'
    assert struct.unpack_from('<2I', record, 80) == (768, 3072)
    cp = decode_checkpoint(record[256:1024])
    meta = dict(zip(('seq', 'valid', 'unix_time', 'play_seconds', 'chapter', 'text_id', 'page', 'bilingual'),
                    struct.unpack_from('<8I', record, 48)))
    assert (meta['chapter'], meta['text_id'], meta['page']) == tuple(cp['flow'][k] for k in ('chapter', 'text_id', 'page'))
    return dict(name=name, save_sha256=digest(sav), slot=slot, meta=meta, settings=settings,
                checkpoint=cp, thumb_sha256=digest(record[1024:])), sav


def audio_matches(live, cp):
    checks = []
    for name in ('music', 'sfx'):
        expected, actual = cp['audio'][name], live[name]
        if not expected['enabled']:
            assert not actual['enabled'], (name, 'unexpected enabled voice', actual)
            checks.append(dict(voice=name, disabled=True))
            continue
        assert actual['index'] == expected['index'] and actual['enabled']
        assert actual['loop'] == expected['loop'] and actual['source_base'] == expected['offset_frames']
        consumed = actual['read']
        assert consumed <= 4096, ('first-ACK audio observed too late', name, consumed)
        for field in ('fade_step', 'fade_target', 'fade_total', 'fade_remainder', 'flags'):
            assert actual[field] == expected[field], (name, field, expected, actual)
        steps = min(consumed, expected['fade_left'])
        gain, error = expected['gain_q12'], expected['fade_error']
        if expected['fade_total'] and steps:
            carry = error + steps * expected['fade_remainder']
            gain += steps * expected['fade_step'] + (-1 if expected['flags'] & 1 else 1) * (carry // expected['fade_total'])
            error = carry % expected['fade_total']
            if steps == expected['fade_left']:
                gain = expected['fade_target']
        assert (actual['gain_q12'], actual['fade_left'], actual['fade_error']) == (gain, expected['fade_left'] - steps, error)
        checks.append(dict(voice=name, source_base=actual['source_base'], consumed_after_restore=consumed,
                           gain_q12=gain, fade_left=actual['fade_left']))
    for field in ('master_music', 'master_sfx'):
        assert live[field] == cp['audio'][field]
    return checks


class Probe:
    def __init__(self, args):
        self.args = args
        self.text = json.loads(args.text_manifest.read_text(encoding='utf8'))
        self.wire = native.WireRecords(args.text_pack.read_bytes(), self.text)
        # The emulator's .dsv/system directories are also private to this phase.
        sys.path.insert(0, str(args.emulator_tools))
        import nds_emulator
        original = nds_emulator.Emulator
        private = str(args.output.resolve()).encode()

        class PrivateEmulator(original):
            def environment(self, command, data):
                if command in (9, 30, 31):
                    C.cast(data, C.POINTER(C.c_char_p))[0] = private
                    return True
                return super().environment(command, data)

        nds_emulator.Emulator = PrivateEmulator
        self.n = native.NativeSession(args)
        self.state = self.n.snapshot()
        self.entries, self.pre_ack, self.first = [], [], None
        self.expected = None
        self.voice_addresses = {name: self.n.symbols.section(name, 'audio_demo',
            prefix='data' if name.startswith('master_') else 'bss')[0]
            for name in ('music', 'sfx', 'scene_paused', 'master_music', 'master_sfx')}
        self.pending_address = self.n.symbols.section('audio_resume_pending', 'story_runtime')[0]
        assert self.n.presentation_size == 1936 and self.n.status_size >= 244, 'exact viewport cold verification requires the 15-word presentation ABI'

    def voices(self):
        result = {}
        for name in ('music', 'sfx'):
            raw = self.n.read(self.voice_addresses[name], 82)
            result[name] = dict(index=self.state['a'][name + '_index'],
                read=struct.unpack_from('<I', raw, 20)[0], source_base=struct.unpack_from('<I', raw, 28)[0],
                enabled=raw[32], loop=raw[35], gain_q12=struct.unpack_from('<i', raw, 52)[0],
                fade_step=struct.unpack_from('<i', raw, 56)[0], fade_target=struct.unpack_from('<i', raw, 60)[0],
                fade_total=struct.unpack_from('<I', raw, 64)[0], fade_left=struct.unpack_from('<I', raw, 68)[0],
                fade_remainder=struct.unpack_from('<I', raw, 72)[0], fade_error=struct.unpack_from('<I', raw, 76)[0],
                flags=raw[80] | raw[81] << 1)
        result['paused'] = self.n.read(self.voice_addresses['scene_paused'], 1)[0]
        for name in ('master_music', 'master_sfx'):
            result[name] = struct.unpack('<I', self.n.read(self.voice_addresses[name], 4))[0]
        return result

    def step(self, keys=()):
        # NativeSession.step may run another core frame while a status fence is
        # odd. Drain the presentation ring BEFORE that retry, so the first ACK
        # cannot disappear behind the next coherent status.
        for _ in range(120):
            self.n.emu.run(1, keys)
            self.state = self.n.snapshot()
            self.n.last = self.state
            audio = self.state['a']
            assert not audio['errors'] and not audio['music_underruns'] and not audio['sfx_underruns'], self.state
            self.observe()
            if self.state['coherent']:
                return self.state
        raise AssertionError(('status stayed incoherent', self.state))

    def observe(self):
        if self.state['s']['ready']:
            if self.state['coherent']:
                self.n.cache_records(self.text)
        entries = self.n.read_presentations()
        self.entries.extend(entries)
        if self.expected:
            target = self.expected['checkpoint']['flow']['text_id']
            for entry in entries:
                if self.first is None and entry['text_id'] == target and entry['flags'] & 1:
                    view = {key: entry[key] for key in ('scroll', 'scroll_manual', 'scroll_covered')}
                    self.first = dict(ack=entry, state=self.state, layers=self.n.active_layers(), voices=self.voices(),
                                      published_head=self.n.presentation_head, viewport=view)
            pending = self.n.read(self.pending_address, 1)[0]
            if self.first is None and (pending or self.state['shell']['load_ops']):
                observation = dict(state=self.state, voices=self.voices(), audio_resume_pending=pending)
                assert observation['voices']['paused'] == 1, ('audio resumed before first ACK', observation)
                self.pre_ack.append(observation)

    def wait(self, predicate, maximum=12000, keys=()):
        for _ in range(maximum):
            if predicate(self.state):
                return self.state
            self.step(keys)
        raise AssertionError(('timeout', self.state))

    def release(self):
        before = self.state['input_vblanks']
        self.step()
        self.wait(lambda s: s['input_vblanks'] != before, 180)

    def edge(self, key, predicate, maximum=1800):
        self.release()
        for _ in range(maximum):
            self.step((key,))
            if predicate(self.state):
                self.release()
                return self.state
        raise AssertionError(('input not acknowledged', key, self.state))

    def screenshot(self, name):
        # GUI evidence is deliberately later than the exact first-ACK sample.
        start = self.n.presentation_head
        self.wait(lambda s: (self.n.presentation_head - start) & NONE >= 2)
        self.n.emu.want_video = True
        try:
            self.step()
        finally:
            self.n.emu.want_video = False
        assert self.n.emu.frame is not None
        self.n.emu.frame.save(self.args.output / (name + '.png'))

    def boot_title(self):
        for _ in range(10000):
            shell = self.state.get('shell')
            if shell and shell['magic'] == 0x4c48535a and shell['active'] and shell['mode'] == 0:
                assert shell['available'] == 1, self.state
                self.screenshot('title')
                return
            video = self.state.get('video')
            self.step(('A',) if video and video['active'] and self.n.emu.frame_count % 20 == 10 else ())
        raise AssertionError(('boot did not reach writable Title', self.state))

    def configure_start(self):
        before = global_settings(save_file(self.args.image)[1])
        language = getattr(self.args, 'language', 1)
        for selection in (1, 2, 3):
            self.edge('DOWN', lambda s, n=selection: s['shell']['selection'] == n)
        self.edge('A', lambda s: s['shell']['mode'] == 5)
        if before['language'] != language:
            count = self.state['shell']['global_writes']
            self.edge('A', lambda s: s['shell']['global_writes'] > count)
        for selection in (1, 2, 3):
            self.edge('DOWN', lambda s, n=selection: s['shell']['selection'] == n)
        if not before['chapter_skip']:
            count = self.state['shell']['global_writes']
            self.edge('A', lambda s: s['shell']['global_writes'] > count)
        self.screenshot('settings_bilingual_chapter_skip')
        settings = global_settings(save_file(self.args.image)[1])
        assert settings['language'] == language and settings['chapter_skip'] == 1
        self.edge('B', lambda s: s['shell']['mode'] == 0)
        # Returning from Settings preserves the original Title selection.
        while self.state['shell']['selection']:
            selection = self.state['shell']['selection'] - 1
            self.edge('UP', lambda s, n=selection: s['shell']['selection'] == n)
        self.edge('A', lambda s: not s['shell']['active'])
        self.edge('RIGHT', lambda s: s['s']['chapter'] == 1)
        self.wait(lambda s: s['s']['chapter'] == 1 and s['s']['blocked'] and s['s']['text_id'] != NONE)
        assert self.state['s']['bilingual'] == language
        return settings

    def drive_to(self, target):
        controller = native.ManualController()
        for _ in range(200000):
            s, f = self.state['s'], self.state['f']
            if s['text_id'] == target:
                self.release()
                self.wait(lambda st: st['s']['text_id'] != target or
                    (st['s']['visible'] == st['s']['char_count'] and st['f'].get('scroll_ready', False)))
                assert self.state['s']['text_id'] == target, ('target auto-advanced before manual capture', target, self.state)
                return
            assert s['text_id'] == NONE or s['text_id'] < target, ('passed target', target, self.state)
            transition = self.state['transition']
            finished = s['ticks'] - transition['start'] >= transition['duration']
            if f['blocked'] and not f['waiting'] and not f['source_auto'] and not f['paused'] and s['visible'] < s['page_limit'] and finished:
                old = (s['text_id'], s['page'], s['visible'], s['local'])
                self.edge('A', lambda st: tuple(st['s'][k] for k in ('text_id', 'page', 'visible', 'local')) != old)
                controller = native.ManualController()
            elif controller.outstanding is None and f.get('scrolling') and s['text_mode'] != 3 and not f.get('scroll_ready'):
                self.step()  # Never hold an A edge before real viewport coverage.
            else:
                self.step(controller.keys(self.n, self.state))
        raise AssertionError(('story walk budget', target, self.state))

    def save(self):
        settings = self.configure_start()
        requested, chosen = self.args.target, self.args.target
        self.drive_to(chosen)
        while self.state['s']['scroll_max'] == 0:
            candidates = [record['id'] for record in self.text['records']
                if chosen < record['id'] <= requested + self.args.fallback_span and record['kind'] == 'say'
                and record['mode'] != 3 and record['pages_bi'] > 1]
            assert candidates, ('974 has no scroll and no nearby long Say was found', self.state)
            chosen = min(candidates)
            self.drive_to(chosen)
        self.wait(lambda s: s['s']['text_id'] == chosen and s['s']['scroll'] > 0)
        row = self.state['s']['scroll']
        self.edge('UP', lambda s: s['s']['text_id'] == chosen and s['s']['scroll'] < row and s['s']['scroll_manual'] == 1)
        self.screenshot('manual_scrolled_before_menu')
        before = self.state
        assert before['s']['scroll_manual'] == 1 and before['s']['bilingual'] == getattr(self.args, 'language', 1) and not before['s']['paused']
        self.edge('START', lambda s: s['shell']['active'] and s['shell']['mode'] == 1)
        for selection in (1, 2):
            self.edge('DOWN', lambda s, n=selection: s['shell']['selection'] == n)
        self.edge('A', lambda s: s['shell']['mode'] == 2)
        for selection in range(1, self.args.slot + 1):
            self.edge('RIGHT', lambda s, n=selection: s['shell']['selection'] == n)
        sav = save_file(self.args.image)[1]
        assert not any(sav[24576 + self.args.slot * 4096:24576 + (self.args.slot + 1) * 4096]), 'use a new empty manual slot'
        count, errors = self.state['shell']['save_ops'], self.state['shell']['save_errors']
        self.edge('A', lambda s: s['shell']['save_ops'] > count or s['shell']['save_errors'] > errors)
        assert self.state['shell']['save_errors'] == errors, self.state
        self.wait(lambda s: s['shell']['mode'] == 9)
        self.screenshot('saved_message')
        saved, sav = saved_slot(self.args.image, self.args.slot)
        cp, flow = saved['checkpoint'], saved['checkpoint']['flow']
        assert cp['version'] in (2, 3) and cp['scroll_manual'] == 1 and flow['text_id'] == chosen
        assert (cp['scroll'], cp['scroll_manual'], cp['scroll_covered']) == tuple(before['s'][k] for k in ('scroll', 'scroll_manual', 'scroll_covered'))
        assert bool(flow['flags'] & (1 << 15)) == bool(getattr(self.args, 'language', 1)) and not flow['flags'] & (1 << 14)
        expected_ack = dict(commands=flow['commands'], text_id=flow['text_id'], page=flow['page'],
                            visible=flow['visible'], local=flow['local'], ticks=flow['now'])
        source_ack = next((e for e in reversed(self.entries) if e['flags'] & 1 and all(e[k] == value for k, value in expected_ack.items())), None)
        assert source_ack is not None, 'stored point was never really displayed'
        (self.args.output / 'extracted.sav').write_bytes(sav)
        return dict(settings=settings, requested_text=requested, chosen_text=chosen, source_before_menu=before,
                    source_ack=source_ack, saved=saved, final=self.state)

    def load(self, legacy=False):
        image_before = digest(self.args.image.read_bytes())
        saved, sav_before = saved_slot(self.args.image, self.args.slot)
        cp, flow = saved['checkpoint'], saved['checkpoint']['flow']
        assert cp['version'] in ((1,) if legacy else (2, 3))
        if legacy:
            assert flow['text_id'] == 579 and flow['visible'] == 5
        else:
            assert cp['scroll_manual'] == 1
            source = json.loads(self.args.save_report.read_text(encoding='utf8'))
            assert source['result'] == 'PASS' and source['identity']['rom_sha256'] == self.args.expected_sha
            assert source['details']['saved']['checkpoint']['sha256'] == cp['sha256'], 'source ACK report belongs to a different checkpoint'
        self.expected = saved
        self.edge('DOWN', lambda s: s['shell']['selection'] == 1)
        self.edge('A', lambda s: s['shell']['mode'] == 3)
        for selection in range(1, self.args.slot + 1):
            self.edge('RIGHT', lambda s, n=selection: s['shell']['selection'] == n)
        head = self.n.presentation_head
        self.edge('A', lambda s: s['shell']['load_ops'] > 0 or s['shell']['save_errors'] > 0)
        self.wait(lambda s: self.first is not None)
        assert self.state['shell']['load_ops'] == 1 and not self.state['shell']['save_errors'], self.state
        ack, status = self.first['ack'], self.first['state']['s']
        expected_ack = dict(commands=flow['commands'], text_id=flow['text_id'], page=flow['page'],
                            visible=flow['visible'], local=flow['local'], ticks=flow['now'])
        for key, value in expected_ack.items():
            assert ack[key] == value, ('FIRST ACK mismatch', key, value, ack)
        assert ack['serial'] > head and ack['flags'] & 1 and ack['flags'] & 16
        assert bool(ack['flags'] & 8) == bool(flow['flags'] & (1 << 15))
        # All exact restore scalars come from the earliest published entry.
        # One retro_run may encompass multiple commits; later status is never
        # substituted for the first entry's local/visible/viewport values.
        for key, field in (('pc', 'pc'), ('chapter', 'chapter'), ('source_line', 'line')):
            assert status[key] == flow[field], ('first ACK source', key, status, flow)
        expected_view = dict(scroll=cp['scroll'], scroll_manual=cp['scroll_manual'], scroll_covered=cp['scroll_covered'])
        for key, value in expected_view.items():
            assert self.first['viewport'][key] == value, ('FIRST ACK viewport mismatch', key, value, self.first)
            if self.first['state']['coherent'] and status['frames'] == ack['serial']:
                assert status[key] == value, ('first ACK coherent status disagrees', key, value, status)
        if not legacy:
            expected_ranges = {key: source['details']['source_ack'][key] for key in ('cn_first', 'cn_end', 'en_first', 'en_end')}
            assert all(ack[key] == value for key, value in expected_ranges.items()), ('first ACK differs from saved viewport ranges', expected_ranges, ack)
        else:
            assert ack['cn_first'] == 0 and ack['cn_end'] == flow['visible'], ('legacy partial viewport', ack)
            expected_ranges = None
        layers = [{k: layer[k] for k in ('image', 'tag', 'program', 'placement', 'start')} for layer in self.first['layers']]
        assert layers == cp['layers'], ('first ACK layers', cp['layers'], layers)
        audio = audio_matches(self.first['voices'], cp)
        assert ack['flags'] & 32, 'first restore ACK must publish while audio-resume barrier is still pending'
        if self.pre_ack:
            pause_proof = 'first ACK carried the pending audio barrier; all directly observed pre-ACK samples were paused'
        else:
            zero_consumed = all(item.get('disabled') or item['consumed_after_restore'] == 0 for item in audio)
            pause_proof = ('first ACK carried the pending audio barrier; zero source samples consumed at observation'
                           if zero_consumed else 'first ACK carried the pending audio barrier; no direct pre-ACK sampling interval, later audio counter is reported without claiming an exact audible cursor')
        self.screenshot('cold_restored_after_two_commits')
        after, sav_after = saved_slot(self.args.image, self.args.slot)
        assert sav_after == sav_before and after['checkpoint']['sha256'] == cp['sha256'], 'cold read changed actual save bytes'
        assert digest(self.args.image.read_bytes()) == image_before, 'cold read changed owned FAT image'
        (self.args.output / 'extracted.sav').write_bytes(sav_after)
        return dict(saved=saved, expected_ack=expected_ack, expected_view=expected_view, expected_ranges=expected_ranges, first_ack=self.first,
                    pre_ack_observations=self.pre_ack, pause_proof=pause_proof, audio_checks=audio, final=self.state)


def owned_image(args):
    marker = args.image.with_suffix(args.image.suffix + '.scroll-owner.json')
    if args.create or args.copy_from_image:
        assert not args.image.exists() and not marker.exists(), 'image already exists; never overwrite prior evidence'
        if args.copy_from_image:
            assert args.phase == 'legacy-load'
            assert args.copy_from_image.resolve() != args.image.resolve()
            args.image.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(args.copy_from_image, args.image)
            origin = dict(kind='copy', path=str(args.copy_from_image.resolve()), sha256=digest(args.copy_from_image.read_bytes()))
        else:
            assert args.phase == 'save'
            create_image(args.image)
            origin = dict(kind='new blank FAT16')
        native.atomic_json(marker, dict(owner='verify_story_scroll_cold.py', image=str(args.image.resolve()), origin=origin))
    assert args.image.is_file() and marker.is_file(), 'only images created/copied by this tool are accepted'
    owner = json.loads(marker.read_text(encoding='utf8'))
    assert owner['owner'] == 'verify_story_scroll_cold.py' and owner['image'] == str(args.image.resolve())
    return owner


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--phase', choices=('save', 'load', 'legacy-load'), required=True)
    parser.add_argument('--rom', type=Path, required=True)
    parser.add_argument('--map', type=Path, required=True)
    parser.add_argument('--expected-sha', required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--create', action='store_true')
    parser.add_argument('--copy-from-image', type=Path)
    parser.add_argument('--slot', type=int)
    parser.add_argument('--target', type=int, default=974)
    parser.add_argument('--fallback-span', type=int, default=40)
    parser.add_argument('--language', choices=('cn', 'bi'), default='bi')
    parser.add_argument('--save-report', type=Path, help='PASS save-phase report binding the actual saved viewport ranges')
    parser.add_argument('--core', type=Path, default=SPIKE / 'desmume_fat_test.dll')
    parser.add_argument('--emulator-tools', type=Path, default=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools'))
    parser.add_argument('--text-pack', type=Path, default=ROOT / 'nds/nitrofs/story/text.bin')
    parser.add_argument('--text-manifest', type=Path, default=ROOT / 'nds/artifacts/story_text_manifest.json')
    parser.add_argument('--cpu-mode', default='interpreter', choices=('interpreter',))
    args = parser.parse_args()
    args.language = int(args.language == 'bi')
    args.slot = (0 if args.phase == 'legacy-load' else 1) if args.slot is None else args.slot
    assert 0 <= args.slot <= 2, 'this experiment uses the first row of empty manual slots'
    assert not (args.create and args.copy_from_image)
    assert args.phase != 'load' or args.save_report, 'v2 load requires --save-report from its actual save phase'
    environment_image = os.environ.get('ZATO_MPCF_IMAGE')
    assert environment_image and Path(environment_image).resolve() == args.image.resolve(), 'parent process must preset ZATO_MPCF_IMAGE to this owned image before Python starts'
    rom_data = args.rom.read_bytes()
    assert len(args.expected_sha) == 64 and digest(rom_data) == args.expected_sha.lower(), 'candidate ROM SHA mismatch'
    fat_at, fat_bytes = struct.unpack_from('<2I', rom_data, 0x48)
    assert fat_bytes % 8 == 0 and fat_at + fat_bytes <= len(rom_data), 'ROM FAT bounds'
    spans = [struct.unpack_from('<2I', rom_data, at) for at in range(fat_at, fat_at + fat_bytes, 8)]
    embedded = []
    for path in (args.text_pack, ROOT / 'nds/nitrofs/story/script.bin'):
        payload = path.read_bytes()
        matches = sum(end - start == len(payload) and rom_data[start:end] == payload for start, end in spans)
        assert matches == 1, ('candidate NitroFS payload differs from the oracle input', str(path), matches)
        embedded.append(dict(path=str(path.resolve()), bytes=len(payload), sha256=digest(payload)))
    assert not args.output.exists() or not any(args.output.iterdir()), 'use a new phase output directory'
    args.output.mkdir(parents=True, exist_ok=True)
    owner = owned_image(args)
    identity = dict(rom=str(args.rom.resolve()), rom_sha256=digest(rom_data), embedded=embedded,
        map_sha256=digest(args.map.read_bytes()), core_sha256=digest(args.core.read_bytes()),
        tool_sha256=digest(Path(__file__).read_bytes()), text_sha256=digest(args.text_pack.read_bytes()),
        verifier_sha256=digest((ROOT / 'nds/tools/verify_story_rom.py').read_bytes()),
        image=str(args.image.resolve()), owner=owner, phase=args.phase)
    probe = None
    try:
        probe = Probe(args)
        probe.boot_title()
        details = probe.save() if args.phase == 'save' else probe.load(args.phase == 'legacy-load')
        result = dict(result='PASS', identity=identity, core_frames=probe.n.emu.frame_count,
            method='physical keys, read-only RAM, actual owned MPCF FAT and first presentation ACK; no savestates or RAM writes',
            image_sha256=digest(args.image.read_bytes()), details=details)
        native.atomic_json(args.output / 'report.json', result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except BaseException as error:
        native.atomic_json(args.output / 'report.json', dict(result='FAIL', identity=identity,
            error=str(error), traceback=traceback.format_exc(), state=probe.state if probe else None,
            first_ack=probe.first if probe else None))
        raise
    finally:
        if probe:
            probe.n.close()


if __name__ == '__main__':
    main()
