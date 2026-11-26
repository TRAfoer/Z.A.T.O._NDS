from pathlib import Path
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class StoryBuildReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=ROOT/'tools/story_build_report.py'
        spec=importlib.util.spec_from_file_location('story_build_report',path)
        cls.reporter=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.reporter)

    def test_development_report_measures_finished_file_without_blocking_size(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'development.nds'
            with path.open('wb') as file:file.truncate(64_000_001)
            report=self.reporter.report_package(path)
            self.assertEqual(report['rom_bytes'],64_000_001)
            self.assertEqual(report['remaining_bytes'],-1)
            self.assertFalse(report['strict_budget'])
            self.assertFalse(report['within_strict_budget'])
            result=subprocess.run([sys.executable,str(ROOT/'tools/story_build_report.py'),str(path)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['rom_bytes'],64_000_001)

    def test_explicit_strict_budget_rejects_exact_boundary_and_accepts_below(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.nds'
            with path.open('wb') as file:file.truncate(64_000_000)
            with self.assertRaises(ValueError):self.reporter.report_package(path,strict_budget=True)
            result=subprocess.run([sys.executable,str(ROOT/'tools/story_build_report.py'),str(path),'--strict-budget'],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            with path.open('r+b') as file:file.truncate(63_999_999)
            self.assertTrue(self.reporter.report_package(path,strict_budget=True)['within_strict_budget'])

    def test_script_forces_rebuild_and_keeps_explicit_development_options(self):
        script=(ROOT/'build_story.ps1').read_text()
        self.assertIn('make -B -j4 ',script,'Switching STORY_OP must rebuild C and repack FAT even with unchanged timestamps')
        self.assertIn('[switch]$StrictBudget',script)
        self.assertIn('[switch]$UseExistingPacks',script)
        self.assertIn('tools/story_build_report.py',script)

    def test_build_verifier_distinguishes_undefined_runtime_movie_references(self):
        path=ROOT/'tools/verify_story_build_modes.py';spec=importlib.util.spec_from_file_location('build_mode_verifier',path)
        verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier)
        self.assertEqual(verifier.runtime_video_references(' U story_video_open\n U g_story_video_status\n T story_video_close\n U memcpy\n'),['g_story_video_status','story_video_open'])
        self.assertEqual(verifier.runtime_video_references(' U memcpy\n'),[])

    def test_build_verifier_rejects_partial_movie_payloads(self):
        path=ROOT/'tools/verify_story_build_modes.py';spec=importlib.util.spec_from_file_location('build_mode_payload_verifier',path)
        verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier)
        self.assertEqual(verifier.check_movie_files({'movie/op.zpv':b'video','movie/op.wav':b'audio'},True),['movie/op.wav','movie/op.zpv'])
        self.assertEqual(verifier.check_movie_files({'story/text.bin':b'text'},False),[])
        with self.assertRaises(AssertionError):verifier.check_movie_files({'movie/op.zpv':b'video'},True)
        with self.assertRaises(AssertionError):verifier.check_movie_files({'movie/op.wav':b'audio'},False)


if __name__=='__main__':unittest.main()
