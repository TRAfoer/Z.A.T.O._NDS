"""Native text pack contract and independent desktop-preview pixel oracle."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class SceneTextTests(unittest.TestCase):
    def test_ep2d3_pack_preserves_dialogue_and_native_pixels(self):
        path = ROOT / 'nds/tools/pack_scene_text.py'
        self.assertTrue(path.is_file(), 'scene text packer is not implemented')
        spec = importlib.util.spec_from_file_location('scene_text_packer_test', path)
        packer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(packer)
        report = packer.build(verify=True)
        self.assertEqual(report['say_count'], 278)
        self.assertEqual(report['native_verification']['result'], 'PASS')
        self.assertGreater(report['native_verification']['frames'], 600)
        self.assertGreater(report['native_verification'].get('page_probes',0), 600,
                           'native manual-page caps have not been verified')
        self.assertLess(report['bytes'], 500 * 1024)
        self.assertEqual(report['line_to_id']['608'], report['text_records'][int(report['line_to_id']['608'])]['id'])
        self.assertFalse(report['text_records'][report['line_to_id']['608']]['portrait'])
        self.assertIn('690', report['literal_line_to_id'])


if __name__ == '__main__':
    unittest.main()
