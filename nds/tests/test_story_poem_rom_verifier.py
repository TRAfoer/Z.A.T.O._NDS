"""Hand-placed pixels validate the independent actual-ROM poem observer."""
from pathlib import Path
import importlib.util
import unittest
import numpy as np
from PIL import Image
from test_story_poem import fixture

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('poem_rom_observer', ROOT / 'nds/tools/verify_story_poem_rom.py')
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


class PoemRomVerifierTests(unittest.TestCase):
    def test_literal_planes_and_pages_use_independent_known_mask_positions(self):
        pack = observer.PackedPoems(fixture())
        upper, lower = pack.expected(0, 0)
        self.assertEqual(int(lower[35, 31]), 0xffff)
        self.assertEqual(int(lower[35, 60]), 0x8000)
        self.assertEqual(int(upper[24, 9]), 0xffff)
        self.assertEqual(int(upper[24, 10]), 0xffff)
        self.assertEqual(int(upper[38, 19]), 0xffff)
        self.assertEqual(int(upper[39, 19]), 0xffff)
        self.assertEqual(int(upper[35, 31]), 0x8000)
        upper, lower = pack.expected(0, 1)
        self.assertEqual(int(upper[24, 29]), 0xffff)
        self.assertEqual(int(lower[35, 60]), 0xffff)
        self.assertEqual(int(lower[10, 4]), 0xffff)

    def test_crc_change_cannot_be_accepted_as_an_oracle(self):
        data = bytearray(fixture())
        data[-1] ^= 1
        with self.assertRaises(AssertionError):
            observer.PackedPoems(data).record(0)

    def test_rgb_screenshot_quantization_preserves_ds_channel_order(self):
        pixels = np.zeros((384, 256, 3), dtype=np.uint8)
        pixels[10, 20] = (255, 0, 0)
        pixels[202, 20] = (0, 255, 0)
        pixels[250, 20] = (0, 0, 255)
        actual = observer.screenshot_rgb15(Image.fromarray(pixels))
        self.assertEqual((int(actual[10, 20]), int(actual[202, 20]), int(actual[250, 20])), (31, 31 << 5, 31 << 10))


if __name__ == '__main__':
    unittest.main()
