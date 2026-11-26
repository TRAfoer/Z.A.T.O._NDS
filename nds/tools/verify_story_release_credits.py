"""Incremental release proof: physical Title -> Credits navigation and pixels.

Reads only the current ARM StoryMenuState prefix. Reference planes are
the actual C renderer outputs bound by credits_update.json to the ROM menu.
This checks the font attribution view, not unrelated menu or FAT behavior.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import struct
import time
import traceback

import numpy as np

from verify_scene_package import files
from verify_story_menu_rom import Probe, ROOT
from verify_story_rom import atomic_json


PREFIX = ('mode', 'return_mode', 'selection', 'page', 'focus', 'scroll')
# ARM object readelf reports Tag_ABI_enum_size: small. story_menu_version's
# ldrb/ldr instructions confirm mode0/return1/selection4/page8/focus12/scroll16.
PREFIX_LAYOUT = struct.Struct('<BB2x4I')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class CreditsProbe(Probe):
    def __init__(self, args):
        header = (ROOT / 'source/story_menu.h').read_text(encoding='utf8')
        assert re.search(r'StoryMenuMode\s+mode\s*,\s*return_mode\s*;\s*'
                         r'unsigned\s+selection\s*,\s*page\s*,\s*focus\s*,\s*scroll\s*;', header), 'Current menu prefix changed'
        self.reference_meta = json.loads((args.reference / 'credits_update.json').read_text(encoding='utf8'))
        self.row = self.reference_meta['preview_scroll_row']
        assert self.row == 51, 'This incremental proof targets the reviewed row51 font credit'
        embedded_menu = files(args.rom)['story/menu.bin']
        assert hashlib.sha256(embedded_menu).hexdigest() == self.reference_meta['new_menu_sha256'], 'ROM menu differs from the C preview input'
        self.expected = {}
        for screen in ('upper', 'lower'):
            name = f'credits_{screen}.rgb15'
            path = args.reference / name
            assert digest(path) == self.reference_meta['preview_planes'][name], ('C reference changed', name)
            raw = path.read_bytes()
            assert len(raw) == 98304, (name, len(raw))
            self.expected[screen] = np.frombuffer(raw, dtype='<u2').reshape(192, 256)
        super().__init__(args)
        self.menu_address, self.menu_bytes = self.session.symbols.section('menu', 'story_shell')
        assert self.menu_bytes >= PREFIX_LAYOUT.size
        self.report.update(scope='Incremental final-ROM font attribution only: real OP skip, Title -> Credits, row51, complete dual-screen C-reference pixels. No full-story, general-menu or save claim.',
                           method='Physical libretro keys, read-only RAM, no savestates or RAM writes.',
                           identity=dict(rom=str(args.rom.resolve()), rom_sha256=self.sha,
                                         map_sha256=digest(args.map), tool_sha256=digest(Path(__file__)),
                                         menu_sha256=hashlib.sha256(embedded_menu).hexdigest(),
                                         reference_metadata_sha256=digest(args.reference / 'credits_update.json'),
                                         reference_planes=self.reference_meta['preview_planes'],
                                         menu_header_sha256=digest(ROOT / 'source/story_menu.h')),
                           menu_prefix=dict(offsets=dict(zip(PREFIX, (0, 1, 4, 8, 12, 16))),
                                            scroll_offset=16, observed_struct_bytes=self.menu_bytes,
                                            abi='ARM small enums; confirmed by final story_menu.c.o readelf/objdump'),
                           target_row=self.row, credit=self.reference_meta['font_credit'], scroll_actions=[])

    def menu_prefix(self):
        value = dict(zip(PREFIX, PREFIX_LAYOUT.unpack(self.session.read(self.menu_address, PREFIX_LAYOUT.size))))
        public = self.shell()
        assert all(value[key] == public[key] for key in ('mode', 'selection', 'page')), ('private prefix does not bind public menu', value, public)
        return value

    def run(self):
        try:
            for _ in range(10000):
                video = self.session.last.get('video') if self.session.last else None
                keys = ('A',) if video and video['active'] and video['ready'] and self.session.emu.frame_count % 20 == 10 else ()
                shell = self.step(keys)
                if shell['magic'] == 0x4c48535a and shell['active'] and shell['mode'] == 0:
                    break
            else:
                raise AssertionError(('OP skip did not reach Title', self.session.last))
            self.release()
            self.report['title'] = dict(shell=self.shell(), prefix=self.menu_prefix())
            self.open_title_item(4, 6)
            assert self.menu_prefix()['scroll'] == 0
            while self.menu_prefix()['scroll'] < self.row:
                before = self.menu_prefix()['scroll']
                key = 'R' if self.row - before >= 20 else 'DOWN'
                self.press(key, lambda s: self.menu_prefix()['scroll'] != before)
                after = self.menu_prefix()['scroll']
                assert before < after <= self.row, ('unexpected credits scroll', key, before, after)
                self.report['scroll_actions'].append(dict(key=key, before=before, after=after))
            assert self.menu_prefix()['mode'] == 6 and self.menu_prefix()['scroll'] == self.row
            # One core frame can stop during a slow ARM9 redraw. Wait for two
            # actual native commits before the video callback latency guard.
            before_commit = self.session.last['s']['frames']
            for _ in range(1200):
                self.step()
                if self.session.last['s']['frames'] >= before_commit + 2:
                    break
            else:
                raise AssertionError('credits view did not reach two native commits')
            self.session.capture(self.args.screenshot)
            frame = np.asarray(self.session.emu.frame.convert('RGB'))
            for number, screen in enumerate(('upper', 'lower')):
                words = self.expected[screen]
                address, size = self.session.symbols.section(screen, 'story_runtime')
                assert size == 98304
                np.testing.assert_array_equal(np.frombuffer(self.session.read(address, size), dtype='<u2').reshape(192, 256), words,
                                              err_msg=f'{screen} native framebuffer versus actual C credits renderer')
                expected = np.stack((words & 31, words >> 5 & 31, words >> 10 & 31), axis=2)
                np.testing.assert_array_equal(frame[number * 192:(number + 1) * 192] >> 3, expected,
                                              err_msg=f'{screen} actual displayed pixels versus C credits renderer')
                self.report['pixel_checks'].append(dict(screen=screen, result='PASS', pixels=49152,
                                                       native_rgb15_sha256=hashlib.sha256(words.tobytes()).hexdigest()))
            assert digest(self.args.rom) == self.sha, 'ROM changed during verification'
            self.report.update(result='PASS', core_frames=self.session.emu.frame_count,
                               wall_seconds=round(time.monotonic() - self.start, 3),
                               native_commits_before=before_commit, native_commits_after=self.session.last['s']['frames'],
                               screenshot=str(self.args.screenshot.resolve()), screenshot_sha256=digest(self.args.screenshot),
                               final_prefix=self.menu_prefix(), final_shell=self.shell(), audio=self.session.last['a'])
            return self.report
        except BaseException as error:
            self.report.update(result='FAIL', error=str(error), traceback=traceback.format_exc(), state=self.session.last)
            raise
        finally:
            atomic_json(self.args.output, self.report)
            self.session.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('rom', 'map'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--expected-sha', required=True)
    p.add_argument('--reference', type=Path, default=ROOT.parent / 'experiments/video_lab/out/text_restored')
    p.add_argument('--output', type=Path, default=ROOT / 'artifacts/op_font16_release_credits.json')
    p.add_argument('--screenshot', type=Path, default=ROOT / 'artifacts/op_font16_release_credits.png')
    p.add_argument('--emulator-tools', type=Path, default=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools'))
    p.add_argument('--cpu-mode', choices=('interpreter',), default='interpreter')
    args = p.parse_args()
    os.environ.pop('ZATO_MPCF_IMAGE', None)
    report = CreditsProbe(args).run()
    print(json.dumps(dict(result=report['result'], rom_sha256=report['rom_sha256'],
                          row=report['target_row'], pixel_checks=report['pixel_checks'],
                          screenshot=report['screenshot']), indent=2))


if __name__ == '__main__':
    main()
