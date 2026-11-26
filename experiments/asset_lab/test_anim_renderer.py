"""Special ep2d3 compositor regressions; never write to the original game."""
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageDraw

from anim_renderer import AnimRenderer, DEFAULT_GAME


def layer(name, **properties):
    return dict(name=name, start=0.0, properties=properties, at=[], behind=[])


class SyntheticRendererTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.game = Path(self.temp.name)
        self.images = self.game / 'images'
        self.images.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def save(self, name, rgba):
        rgba.save(self.images / (name + '.png'))

    def test_coverage_survives_and_matches_composite_then_box(self):
        thin = Image.new('RGBA', (1280, 720))
        ImageDraw.Draw(thin).line((101, 30, 101, 690), fill='white', width=1)
        self.save('thin_line', thin)
        renderer = AnimRenderer(self.game, sequences={})
        image = renderer.render([layer('thin_line')], 0, mode='rgba')
        expected = Image.alpha_composite(Image.new('RGBA', thin.size, 'black'), thin)
        expected = expected.convert('RGB').resize((256, 144), Image.Resampling.BOX)
        actual = np.array(image)[24:168]
        self.assertGreater(actual.max(), 0)
        self.assertLessEqual(np.abs(actual.astype(int) - np.array(expected).astype(int)).max(), 1)
        old = renderer.render([layer('thin_line')], 0, mode='old')
        self.assertFalse(np.array(old).any())
        self.assertTrue(renderer.metadata['composition']['special_segment'])

    def test_original_underscores_and_alpha_and_add_blending(self):
        self.save('bg_red', Image.new('RGBA', (1280, 720), (20, 40, 60, 255)))
        self.save('effect', Image.new('RGBA', (1280, 720), (100, 20, 40, 128)))
        r = AnimRenderer(self.game, sequences={})
        normal = r.render([layer('bg_red'), layer('effect')], 0, mode='rgba')
        add = r.render([layer('bg_red'), layer('effect', blend='add')], 0, mode='rgba')
        # Independent source-over arithmetic permits Pillow's one-level
        # premultiply/unpremultiply rounding during RGBA Box downsampling.
        self.assertLessEqual(np.abs(np.array(normal.getpixel((20, 40))) - (60, 30, 50)).max(), 1)
        self.assertEqual(add.getpixel((20, 40)), (70, 50, 80))
        self.assertEqual(r.metadata['missing'], [])

    def test_behind_places_effect_below_named_background(self):
        self.save('bg', Image.new('RGBA', (1280, 720), 'red'))
        self.save('crack', Image.new('RGBA', (1280, 720), 'white'))
        r = AnimRenderer(self.game, sequences={})
        crack = layer('crack')
        crack['behind'] = ['bg']
        image = r.render([layer('bg'), crack], 0, mode='rgba')
        self.assertEqual(image.getpixel((0, 24)), (255, 0, 0))
        self.assertEqual([x['name'] for x in r.metadata['layers']], ['crack', 'bg'])
        preordered = r.render([crack, layer('bg')], 0, mode='rgba')
        self.assertEqual(image.tobytes(), preordered.tobytes())

    def test_time_is_relative_with_no_tick_accumulation_and_missing_is_explicit(self):
        self.save('is1', Image.new('RGBA', (1280, 720), 'red'))
        self.save('10', Image.new('RGBA', (1280, 720), 'green'))
        sequences = {'iss': {'frames': [{'image': 'is1', 'duration': 0.05},
                                       {'image': 'is10', 'duration': 0.05}], 'repeat': True}}
        r = AnimRenderer(self.game, sequences=sequences)
        event = layer('iss')
        event['start'] = 5
        missing = r.render([event], 5.075, mode='rgba')
        self.assertEqual(missing.getpixel((0, 24)), (255, 0, 0))
        self.assertEqual(r.metadata['missing'][0]['image'], 'is10')
        self.assertEqual(r.metadata['missing'][0]['fallback'], 'is1')
        self.assertAlmostEqual(r.metadata['sequenceperiods']['iss'], .1)
        self.assertEqual(r.render([event], 1005.001, mode='rgba').getpixel((0, 24)), (255, 0, 0))
        candidate = AnimRenderer(self.game, sequences=sequences, aliases={'is10': '10'})
        self.assertEqual(candidate.render([event], 5.075, mode='rgba').getpixel((0, 24)), (0, 128, 0))
        self.assertEqual(candidate.metadata['aliases_used'][0]['image'], 'is10')

    def test_mirror_and_source_ast_hands_transform(self):
        original = Image.new('RGBA', (1280, 720))
        ImageDraw.Draw(original).rectangle((0, 0, 99, 719), fill='white')
        self.save('face', original)
        r = AnimRenderer(self.game, sequences={})
        mirrored = r.render([layer('face', xzoom=-1)], 0, mode='rgba')
        self.assertEqual(mirrored.getpixel((255, 24)), (255, 255, 255))
        self.assertEqual(mirrored.getpixel((0, 24)), (0, 0, 0))
        hands = layer('hands', anchor=(.5, .5), pos=(.5, .5), zoom=1.2,
                      zoom_easein={'from': 1.2, 'to': 1.3, 'duration': 10})
        hands['at'] = ['shaking']
        r.render([hands], .075)
        transform = r.metadata['layers'][0]['transform']
        self.assertAlmostEqual(transform['xoffset_source'], 0.)
        self.assertAlmostEqual(transform['yoffset_source'], 0.)
        self.assertGreater(transform['zoom'], 1.2)
        self.assertLess(transform['zoom'], 1.3)

    def test_reference_and_rgb15_share_special_scene_coordinates(self):
        self.save('bg', Image.new('RGBA', (1280, 720), (120, 90, 50, 255)))
        r = AnimRenderer(self.game, sequences={})
        reference, output = r.render_pair([layer('bg')], 0)
        self.assertEqual(reference.size, (256, 192))
        self.assertEqual(reference.mode, 'RGB')
        self.assertEqual(output.getpixel((0, 23)), (0, 0, 0))
        self.assertEqual(output.getpixel((0, 168)), (0, 0, 0))
        self.assertTrue(np.array(output)[24:168].any())
        self.assertLessEqual(np.abs(np.array(reference).astype(int) - np.array(output).astype(int)).max(), 4)

    def test_alpha_zero_does_not_add_rgb_and_fallback_black_is_explicit(self):
        self.save('background', Image.new('RGBA', (1280, 720), (20, 40, 60, 255)))
        self.save('transparent', Image.new('RGBA', (1280, 720), (255, 255, 255, 0)))
        r = AnimRenderer(self.game, sequences={}, missing_policy='black')
        image = r.render([layer('background'), layer('transparent', blend='add')], 0, mode='rgba')
        self.assertEqual(image.getpixel((0, 24)), (20, 40, 60))
        missing = r.render([layer('unknown')], 0)
        self.assertFalse(np.array(missing).any())
        self.assertEqual(r.metadata['missing'][0]['fallback'], 'black')

    def test_native_sprite_bounds_use_original_default_center_bottom(self):
        self.save('narrow', Image.new('RGBA', (100, 720), 'white'))
        r = AnimRenderer(self.game, sequences={})
        reference, output = r.render_pair([layer('narrow')], 0, mode='rgba')
        self.assertEqual(output.getpixel((0, 24)), (0, 0, 0))
        self.assertEqual(output.getpixel((118, 24)), (255, 255, 255))
        self.assertEqual(output.getpixel((137, 167)), (255, 255, 255))
        self.assertEqual(output.getpixel((138, 24)), (0, 0, 0))
        self.assertEqual(reference.tobytes(), output.tobytes())


@unittest.skipUnless((DEFAULT_GAME / 'images/anim/def1.png').exists(), 'original read-only assets unavailable')
class OriginalAssetTests(unittest.TestCase):
    def test_all_21_old_blank_frames_retain_coverage_and_visible_rgb15(self):
        r = AnimRenderer()
        names = [f'def{i}' for i in range(1, 7)] + [f'mirror{i}' for i in range(1, 10)] + [f'tface{i}' for i in range(1, 7)]
        for name in names:
            with self.subTest(name=name):
                output = r.render([layer(name)], 0)
                self.assertTrue(np.array(output).any(), name)
                original = Image.open(DEFAULT_GAME / 'images/anim' / (name + '.png')).convert('RGBA')
                reference = Image.alpha_composite(Image.new('RGBA', original.size, 'black'), original)
                reference = np.array(reference.convert('RGB').resize((256, 144), Image.Resampling.BOX))
                q = ((reference.astype(np.uint16) * 31 + 127) // 255 * 255 + 15) // 31
                self.assertLessEqual(np.abs(np.array(output)[24:168].astype(int) - q.astype(int)).max(), 9)
                self.assertFalse(np.array(r.render([layer(name)], 0, mode='old')).any())
        self.assertLessEqual(r.cache_bytes, 64 * 256 * 144 * 4)

    def test_default_atl_sequences_and_is10_diagnostic(self):
        r = AnimRenderer()
        r.render([layer('iss')], .475)
        self.assertEqual(r.metadata['missing'][0]['image'], 'is10')
        self.assertAlmostEqual(r.metadata['sequenceperiods']['hands'], .27)
        self.assertAlmostEqual(r.metadata['sequenceperiods']['ilooked'], .45)
        self.assertTrue(r.metadata['alpha_preserved'])

    def test_exact_hands_boundaries_and_independent_layer_clocks(self):
        r = AnimRenderer()
        hand = layer('hands')
        hand['start'] = 20
        ghost = layer('ghost')
        ghost['start'] = 20.05
        for now, expected in [(20.029, 'hands1'), (20.03, 'carfix_red'),
                              (20.06, 'hands2'), (20.27, 'hands1'),
                              (290.03, 'carfix_red')]:
            with self.subTest(now=now):
                r.render([hand], now)
                self.assertEqual(r.metadata['layers'][0]['requested'], expected)
        r.render([hand, ghost], 20.16)
        self.assertEqual(r.metadata['layers'][0]['requested'], 'carfix_red')
        self.assertEqual(r.metadata['layers'][1]['requested'], 'ghost2')


if __name__ == '__main__':
    unittest.main()
