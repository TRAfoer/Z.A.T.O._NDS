"""Check actual ROM input and composited pixels through installed libretro tools.

Only the test runner uses the existing emulator utility; the ROM runtime and
build have no dependency on the reference project.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import struct
import sys
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
FIELDS = 'magic frames ready stage actor visible stage_id actor_id assets stages actors errors stage_hash actor_hash loads reserved mount_attempted mount_ok fallbacks fallback_reason'.split()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--emulator-tools', type=Path, default=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools'))
    parser.add_argument('--name', default='zato_asset_demo_nitrofs')
    parser.add_argument('--rom', type=Path)
    parser.add_argument('--expect-backend', type=int, choices=[1, 2])
    parser.add_argument('--runtime-fallback', action='store_true')
    parser.add_argument('--with-audio', action='store_true')
    args = parser.parse_args()
    sys.path.insert(0, str(args.emulator_tools))
    from nds_emulator import Emulator
    manifest = json.loads((ROOT / 'artifacts/assets_manifest.json').read_text(encoding='utf-8'))
    mapping = (ROOT / f'build/{args.name}.map').read_text()
    offset = int(re.search(r'0x([0-9a-fA-F]+)\s+g_demo_status\s', mapping)[1], 16) - 0x02000000
    audio_offset = int(re.search(r'0x([0-9a-fA-F]+)\s+g_audio_status\s', mapping)[1], 16) - 0x02000000 if args.with_audio else None
    rom = args.rom.resolve() if args.rom else ROOT / f'{args.name}.nds'
    embedded = args.name.endswith('_r4')
    adaptive = 'adaptive' in args.name
    nitrofs = 'nitrofs' in args.name
    filesystem = adaptive or nitrofs
    backend = args.expect_backend or (2 if filesystem else 1)
    active_backend = 2 if args.runtime_fallback else backend
    header = rom.read_bytes()[:512]
    if embedded:
        assert header[0x12] == 0, 'Legacy build must be DS-only'
        assert struct.unpack_from('<I', header, 0x84)[0] == 0x200, 'Legacy header must be 512 bytes'
        fnt_offset, fnt_size, _, fat_size = struct.unpack_from('<4I', header, 0x40)
        # ndstool emits a canonical empty root even without -d. No file entries
        # or file allocation table may be present in this build.
        assert fat_size == 0 and fnt_size == 9, 'Legacy build must not contain filesystem files'
        with rom.open('rb') as file:
            file.seek(fnt_offset)
            assert file.read(fnt_size) == bytes.fromhex('080000000000010000'), 'Expected an empty ROM filesystem root'
    if filesystem:
        assert header[0x12] == 0 and struct.unpack_from('<I', header, 0x84)[0] == 0x200
    if nitrofs:
        package = (ROOT / 'nitrofs/assets.zat').read_bytes()
        contents = rom.read_bytes()
        arm9_offset, _, _, arm9_bytes = struct.unpack_from('<4I', contents, 0x20)
        fat_offset, fat_bytes = struct.unpack_from('<2I', contents, 0x48)
        assert fat_bytes >= 8 and fat_bytes % 8 == 0
        files = [struct.unpack_from('<2I', contents, at) for at in range(fat_offset, fat_offset + fat_bytes, 8)]
        assert sum(contents[start:end] == package for start, end in files if end - start == len(package)) == 1
        assert contents.count(package) == 1
        assert package not in contents[arm9_offset:arm9_offset + arm9_bytes], 'Asset pack must not be embedded in ARM9'
        assert arm9_bytes < 256 * 1024, 'Unexpectedly large ARM9 binary'
    emu = Emulator(args.emulator_tools / 'emulator/desmume_libretro.dll', rom)
    cache, checks = {}, []
    out = ROOT / 'artifacts' / ('screens_' + rom.stem if filesystem else 'screens_r4' if embedded else 'screens')
    out.mkdir(exist_ok=True)

    def state(validate=True):
        result = dict(zip(FIELDS, struct.unpack_from('<20I', emu.memory(), offset)))
        if not validate:
            return result
        assert result['magic'] == 0x4f54415a and result['ready'] == 1 and result['errors'] == 0, result
        assert result['reserved'] == active_backend, f'Unexpected resource backend: {result}'
        if filesystem:
            assert result['mount_attempted'] == 1
        if nitrofs:
            assert result['mount_ok'] == 1 and result['fallbacks'] == 0
        return result

    def words(asset_id):
        if asset_id not in cache:
            frame = manifest['frames'][asset_id]
            master = ROOT.parent / 'experiments/asset_lab/out/full_asset_audit/converted' / Path(frame['name']).with_suffix('.png')
            if frame.get('master'):
                master = ROOT / frame['master']
            with Image.open(master) as opened:
                a = np.array(opened.convert('RGBA'))
            q = (a[:, :, :3].astype(np.uint16) * 31 + 127) // 255
            image = q[:, :, 0] | (q[:, :, 1] << 5) | (q[:, :, 2] << 10)
            image = np.where(a[:, :, 3] >= 128, image | 0x8000, 0).astype(np.uint16)
            screen = np.zeros((192, 256), dtype=np.uint16)
            x, y = frame['x'], frame['y']
            sx, sy = max(0, -x), max(0, -y)
            dx, dy = max(0, x), max(0, y)
            w, h = min(frame['width'] - sx, 256 - dx), min(frame['height'] - sy, 192 - dy)
            screen[dy:dy + h, dx:dx + w] = image[sy:sy + h, sx:sx + w]
            cache[asset_id] = screen
        return cache[asset_id]

    def check(label=None):
        s = state()
        assert s['stage_id'] == manifest['stages'][s['stage']]
        assert s['actor_id'] == manifest['actors'][s['actor']]
        background, actor = words(s['stage_id']), words(s['actor_id'])
        composed = np.where((actor & 0x8000) != 0, actor, background) if s['visible'] else background
        expected = np.stack([composed & 31, (composed >> 5) & 31, (composed >> 10) & 31], axis=2)
        actual = np.array(emu.frame)[:192] >> 3
        assert np.array_equal(actual, expected), f'Pixel mismatch: {label}, state={s}, pixels={np.count_nonzero(np.any(actual != expected,axis=2))}'
        checks.append(dict(label=label, state=s, pixel_match='PASS'))
        if label:
            emu.frame.save(out / (label + '.png'))
        return s

    def press(key, label=None, held=3, backend_after=None):
        nonlocal active_backend
        previous = state()
        if backend_after is not None:
            active_backend = backend_after
        emu.run(held, [key])
        emu.run(14)
        for _ in range(100):
            current = state(False)
            if current['ready'] and current['frames'] >= previous['frames'] + 2:
                break
            emu.run(5)
        else:
            raise AssertionError(f'ROM did not finish loading after {key}: {state(False)}')
        result = check(label)
        if key in ('RIGHT', 'LEFT'):
            assert result['stage'] == (previous['stage'] + (1 if key == 'RIGHT' else -1)) % previous['stages']
            assert result['actor'] == previous['actor'] and result['actor_hash'] == previous['actor_hash']
        if key in ('A', 'B'):
            assert result['actor'] == (previous['actor'] + (1 if key == 'A' else -1)) % previous['actors']
            assert result['stage'] == previous['stage'] and result['stage_hash'] == previous['stage_hash']
        if key == 'R':
            assert result['visible'] == 1 - previous['visible']
            assert result['loads'] == previous['loads'], 'Visibility must not reload either layer'
        return result

    try:
        emu.run(240)
        initial = check('01_library_marina')
        if args.with_audio:
            emu.press('L', held=3, settle=30)
        assert (initial['assets'], initial['stages'], initial['actors']) == (505, 169, 171)
        first_stage = manifest['frames'][initial['stage_id']]
        assert (first_stage['width'], first_stage['height'], first_stage['x'], first_stage['y']) == (245, 102, 5, 45)
        assert first_stage['source_crop'] == [150, 91, 1130, 497]
        press('RIGHT', '02_library2_marina', backend_after=1 if args.runtime_fallback else None)
        press('LEFT')
        press('A', '03_next_expression')
        press('B')
        press('R', '04_actor_hidden', held=40)  # Holding R must not toggle repeatedly.
        press('A')  # Selection still works while hidden.
        press('R', '05_actor_restored')
        press('B')
        captures = {'library5.png': '06_library5', 'vents_red.png': '07_vents_red',
                    'tea-smile.png': '08_cg_tea', 'doit.png': '09_cg_doit'}
        for i in range(initial['stages']):
            next_stage = (state()['stage'] + 1) % initial['stages']
            asset_id = manifest['stages'][next_stage]
            name = Path(manifest['frames'][asset_id]['name']).name
            press('RIGHT', captures.get(name))
            if (i + 1) % 40 == 0:
                print(f'PASS {i + 1}/{initial["stages"]} actual ROM scenery frames', flush=True)
        assert state()['stage'] == initial['stage'], 'Scenery wraparound failed'
        for i in range(initial['actors']):
            next_actor = (state()['actor'] + 1) % initial['actors']
            asset_id = manifest['actors'][next_actor]
            name = manifest['frames'][asset_id]['name']
            label = '10_mcshock_effect' if name == 'images/sprites/marina/coat/mcshock.png' else None
            press('A', label)
            if (i + 1) % 40 == 0:
                print(f'PASS {i + 1}/{initial["actors"]} actual ROM actors', flush=True)
        assert state()['actor'] == initial['actor'], 'Actor wraparound failed'
        press('B', '11_reverse_actor')
        press('A')
        press('R', '12_background_only')
        press('R')
        assert state()['stage'] == initial['stage'] and state()['actor'] == initial['actor']
        audio_state = None
        if args.with_audio:
            audio_state = list(struct.unpack_from('<22I', emu.memory(), audio_offset))
            assert audio_state[7] == 1 and audio_state[15] > 0 and audio_state[20] > 0
            assert audio_state[12] == audio_state[13] == audio_state[14] == 0, audio_state
        report = dict(result='PASS', rom_bytes=rom.stat().st_size, rom_sha256=hashlib.sha256(rom.read_bytes()).hexdigest(),
                      resource_backend='NitroFS' if backend == 2 else 'embedded RAM fallback',
                      header='DS-only, 512 bytes', final_state=state(),
                      music_during_graphics=args.with_audio, audio_state=audio_state,
                      scenery_frames=initial['stages'], actor_frames=initial['actors'], checks=checks,
                      validation='Actual ROM: exact 5-bit upper-screen pixels, centered 1/4 BG masters, hardware transparency, independent inputs, visibility, wraparound',
                      limitation='Emulator verified; physical NDS performance not yet measured')
        report_name = f'{rom.stem}_verification.json' if filesystem else 'rom_verification_r4.json' if embedded else 'rom_verification.json'
        (ROOT / 'artifacts' / report_name).write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(f'PASS actual ROM: {len(checks)} input/pixel checks; {rom.stat().st_size:,} B single-file ROM', flush=True)
    finally:
        emu.close()


if __name__ == '__main__':
    main()
