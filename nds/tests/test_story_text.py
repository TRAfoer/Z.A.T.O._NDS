"""Streamed whole-story pack contract, independent preview goldens, cache I/O."""
from pathlib import Path
import importlib.util
import unittest

ROOT=Path(__file__).resolve().parents[2]


class StoryTextTests(unittest.TestCase):
    def test_all_story_records_use_bounded_explicit_caches_and_lossless_pixels(self):
        path=ROOT/'nds/tools/pack_story_text.py'
        self.assertTrue(path.is_file(),'streamed full-story text packer is not implemented')
        spec=importlib.util.spec_from_file_location('story_text_packer_test',path)
        packer=importlib.util.module_from_spec(spec);spec.loader.exec_module(packer)
        report=packer.build(verify=True)
        self.assertEqual(report['say_count'],11951)
        self.assertEqual(report['literal_count'],17)
        self.assertEqual(report['date_count'],7)
        self.assertEqual(report['record_format'],4)
        self.assertEqual(report['wire_record_header_bytes'],48)
        self.assertEqual(report['cache_record_header_bytes'],96)
        self.assertEqual(report['bytes'],Path(report['path']).stat().st_size)
        self.assertLessEqual(report['max_expanded_record_bytes'],131072)
        self.assertEqual(report['verification']['result'],'PASS')
        self.assertEqual(report['verification']['getter_draw_read_calls'],0)
        self.assertEqual(report['missing_glyphs'],[])


if __name__=='__main__':unittest.main()
