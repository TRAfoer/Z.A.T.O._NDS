"""Focused checks for the temporary pixel processing experiment."""
from pathlib import Path
import argparse
import numpy as np
from PIL import Image
from asset_model import DEFAULT_GAME, GROUPS, Family, STRIPPED_BOX, rgb15_bytes
import zlib
from pixel_passes import remove_white_edge, step_reduce, reconnect_lines, reference_fade, process, export_passes, rgb15
from dual_screen import Renderer, COLOR_MODES, POLICIES

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--framing', choices=['crop572_288', 'crop576_144'], default='crop572_288')
    args = parser.parse_args()
    # Exterior white disappears, but an enclosed white eye is not flood-filled.
    a = np.zeros((12, 12, 4), dtype=np.uint8)
    a[2:10, 2:10] = (255, 255, 255, 255)
    a[3:9, 3:9] = (0, 0, 0, 255)
    a[4:8, 4:8] = (180, 140, 100, 255)
    a[5, 5] = (255, 255, 255, 255)
    stripped, removed = remove_white_edge(Image.fromarray(a))
    assert removed[2, 2] and not removed[5, 5]
    assert tuple(np.array(stripped)[5, 5]) == (255, 255, 255, 255)

    # A 12:4 vote picks an existing red, not a red/blue average.
    a = np.full((4, 4, 4), (200, 10, 10, 255), dtype=np.uint8)
    a[0] = (10, 20, 200, 255)
    step, _ = step_reduce(Image.fromarray(a), (1, 1))
    assert np.array_equal(np.array(step)[0, 0, :3], rgb15(np.array([200, 10, 10])))

    # Only a source-supported one-pixel gap between thin endpoints is repaired.
    a = np.full((3, 5, 4), (200, 180, 150, 255), dtype=np.uint8)
    a[1, 1, :3] = a[1, 3, :3] = 0
    evidence = dict(ink_counts=np.zeros((3, 5), dtype=int), hline=np.zeros((3, 5), dtype=bool),
                    vline=np.zeros((3, 5), dtype=bool), ink_colors=np.zeros((3, 5, 3), dtype=np.uint8), factor=[4, 4])
    evidence['ink_counts'][1, 2] = 2
    evidence['hline'][1, 2] = True
    lines, repairs = reconnect_lines(Image.fromarray(a), evidence)
    assert repairs.sum() == 1 and repairs[1, 2]
    evidence['ink_counts'][1, 2] = 0
    assert not reconnect_lines(Image.fromarray(a), evidence)[1].any()

    # Fade changes neither alpha nor strong original ink, and never darkens.
    evidence['ink_counts'][1, 2] = 2
    faded, changes, _ = reference_fade(lines, Image.fromarray(a), evidence)
    assert changes[1, 2]
    assert np.array_equal(np.array(faded)[:, :, 3], np.array(lines)[:, :, 3])
    assert np.all(np.array(faded)[:, :, :3] >= np.array(lines)[:, :, :3])
    evidence['ink_counts'][:] = 16
    assert not reference_fade(lines, Image.fromarray(a), evidence)[1].any()

    count = 0
    for key in ['marina_crossed', 'marina_raised']:
        family = Family(key, sampling='MultiPass+Fade', framing=args.framing)
        stripped_family = Family(key, sampling=STRIPPED_BOX, framing=args.framing)
        native2 = args.framing == 'crop572_288'
        assert family.size == ((225, 288) if native2 else (113, 144))
        for i, name in enumerate(family.names):
            with Image.open(family.paths[i]) as source:
                passes = process(source, family.size, family.crop_boxes[i])
            assert passes.info['block_factor'] == ([2, 2] if native2 else [4, 4])
            assert passes.info['padded_source_size'] == ([450, 576] if native2 else [452, 576])
            if native2:
                assert passes.info['source_crop'] == [0, 0, 450, 572]
                assert not np.array(passes.final)[286:, :, 3].any()
            assert np.array_equal(np.array(passes.final), family.arrays[i])
            assert np.array_equal(np.array(passes.box_final), stripped_family.arrays[i]), 'Outlined Box pipeline mismatch'
            packed_frame = stripped_family.frames('RGB15')[i]
            raw = rgb15_bytes(packed_frame.rgb, packed_frame.mask)
            assert len(raw) == family.size[0] * family.size[1] * 2
            assert packed_frame.stats['zlib_frame_bytes'] == len(zlib.compress(raw, 9))
            assert np.array_equal(np.array(passes.lines)[:, :, 3], np.array(passes.faded)[:, :, 3])
            assert np.all(np.array(passes.final)[passes.outline] == 255)
            export_passes(passes, Path('out/multi_pass') / name / args.framing)
            for method in ['RGB15', 'Shared 16', 'Shared 256']:
                f = family.frames(method)[i]
                assert np.all(f.rgb[passes.outline] == 255), 'New outline was reduced to a cream/gray color'
                count += 1

    renderer = Renderer()
    for key in ['library', 'vents', 'cg']:
        for color in COLOR_MODES:
            a = renderer.render(key, 0, color, sampling='Box')
            b = renderer.render(key, 0, color, sampling='MultiPass+Fade')
            assert np.array_equal(np.array(a.output), np.array(b.output)), 'Actor pass changed background/CG sampling'
            c = renderer.render(key, 0, color, sampling=STRIPPED_BOX)
            assert np.array_equal(np.array(a.output), np.array(c.output)), 'Stripped Box changed background/CG'
    original = renderer.render('marina_raised', 0, COLOR_MODES[0], sampling=STRIPPED_BOX, framing=args.framing)
    assert np.array_equal(np.array(original.reference), np.array(original.output)), 'Original mode must bypass stripping'
    print('PASS white-edge/eye separation, 4x4 majority, evidence-only line repair, reference-fade invariants')
    print('PASS', count, 'real actor format cases, all 17 pass exports /', args.framing, ', exact new white and unchanged background/CG processing')
    print('PASS all 17 stripped Box outputs, RGB15 word/alpha/compression roundtrips, original-mode bypass')

if __name__ == '__main__':
    main()
