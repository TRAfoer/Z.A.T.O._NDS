"""Regression: preserve detached white effects and outline only the Box actor."""
import numpy as np
from PIL import Image
from asset_model import Family, STRIPPED_BOX
from pixel_passes import remove_white_edge


def main():
    # A white-only detached effect is art, unlike the white frame around a body.
    a = np.zeros((16, 20, 4), dtype=np.uint8)
    a[4:14, 2:12] = (255, 255, 255, 255)
    a[5:13, 3:11] = (80, 90, 60, 255)
    a[2, 15:19] = (255, 255, 255, 255)
    stripped, removed = remove_white_edge(Image.fromarray(a))
    assert not removed[2, 15:19].any(), 'Detached white effect was erased as an outline'
    assert np.array_equal(np.array(stripped)[2, 15:19], a[2, 15:19])
    assert removed[4, 2], 'Old body outline was retained'

    from pixel_passes import box_with_white_edge
    bare, final, outline, fx = box_with_white_edge(stripped, (10, 8))
    assert np.array(fx)[1, 7:10, 3].all(), 'Thin effect vanished during Box alpha reduction'
    assert not np.array(fx)[0, :, 3].any()
    assert not outline[:2, 7:].any(), 'Effect received an extra outline'
    assert outline.any() and np.all(np.array(final)[outline] == 255)
    assert np.array_equal(np.array(final)[np.array(fx)[:, :, 3] > 0], np.array(fx)[np.array(fx)[:, :, 3] > 0])

    for framing in ['crop572_288', 'crop576_144']:
        for key in ['marina_crossed', 'marina_raised']:
            family = Family(key, sampling=STRIPPED_BOX, framing=framing)
            for i, path in enumerate(family.paths):
                with Image.open(path) as source:
                    clean, _ = remove_white_edge(source)
                working = clean.crop(family.crop_boxes[i])
                factor = 2 if framing == 'crop572_288' else 4
                aligned = Image.new('RGBA', (family.size[0] * factor, family.size[1] * factor))
                aligned.paste(working, (0, 0))
                content_height = (working.height + factor - 1) // factor
                _, final, outline, fx = box_with_white_edge(aligned, family.size, content_height)
                assert np.array_equal(np.array(final), family.arrays[i])
                assert np.all(np.array(final)[outline] == 255)
                if framing == 'crop572_288':
                    assert not family.arrays[i][286:, :, 3].any()
                for method in ['RGB15', 'Shared 16', 'Shared 256']:
                    frame = family.frames(method)[i]
                    assert np.all(frame.rgb[outline] == 255), 'Quantization changed white outline'
                    if path.stem == 'mcshock':
                        # Source effect bbox (266,70)-(306,110), wholly separate from the head.
                        region = (slice(35, 55), slice(133, 153)) if factor == 2 else (slice(17, 28), slice(66, 77))
                        effect_mask = np.array(fx)[:, :, 3] > 0
                        assert effect_mask[region].any(), 'Real shock effect missing'
                        assert frame.mask[effect_mask].all(), 'Final RGB15/indexed alpha lost the effect'
                        assert np.all(frame.rgb[effect_mask] == 255), 'Final format changed effect white'
    print('PASS detached effects preserved, thin Box effects survive, body-only white outline, both framings / 17 actors / three formats')


if __name__ == '__main__':
    main()
