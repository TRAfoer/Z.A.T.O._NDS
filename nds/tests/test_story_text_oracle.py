"""Concurrent validator executions must not share writable executables/cases."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import importlib.util
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]


class StoryTextOracleIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('isolated_story_text_oracle',ROOT/'nds/tests/story_text_oracle.py')
        cls.oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.oracle)

    def require_fix(self):self.assertTrue(hasattr(self.oracle,'_compile_validator'),'isolated native compiler helper is missing')

    def test_real_compilers_and_readers_use_distinct_private_executables(self):
        self.require_fix()
        with tempfile.TemporaryDirectory() as folder:
            dirs=[Path(folder)/str(i) for i in range(2)]
            for d in dirs:d.mkdir()
            def run(directory):
                exe=self.oracle._compile_validator(directory)
                self.assertEqual(exe.parent,directory)
                native=subprocess.run([str(exe),str(ROOT/'nds/nitrofs/story/text.bin')],capture_output=True,text=True,check=True)
                return exe,native.stdout
            with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,dirs))
            self.assertNotEqual(results[0][0],results[1][0])
            for _,output in results:self.assertIn('PASS story text:11975 records',output)

    def test_compile_failure_preserves_real_gcc_stderr(self):
        self.require_fix()
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)
            with patch.object(self.oracle,'ROOT',directory):
                with self.assertRaisesRegex(RuntimeError,'(?s)compile failed.*fatal error.*No such file'):
                    self.oracle._compile_validator(directory)

    def test_two_real_partial_oracles_do_not_share_cases_results_or_corrupt_inputs(self):
        self.require_fix()
        report=json.loads((ROOT/'nds/artifacts/story_text_manifest.json').read_text(encoding='utf8'))
        ir=json.loads((ROOT/'nds/artifacts/full_story_ir.json').read_text(encoding='utf8'))
        context=self.oracle.context_for(ir,report,self.oracle.p.DEFAULT_GAME)
        context=dict(context,records=context['records'][:1])
        with tempfile.TemporaryDirectory() as folder:
            dirs=[Path(folder)/str(i) for i in range(2)]
            def run(directory):return self.oracle.verify_pack(report,ir,context,self.oracle.p.DEFAULT_GAME,artifacts=directory)
            with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,dirs))
            for result in results:
                self.assertEqual(result['result'],'PASS');self.assertEqual(result['records'],1)
                self.assertGreaterEqual(len(result['malformed_rejected']),4)
            self.assertEqual(results[0]['case_sha256'],results[1]['case_sha256'])
            self.assertEqual((dirs[0]/'story_text_results.bin').read_bytes(),(dirs[1]/'story_text_results.bin').read_bytes())


if __name__=='__main__':unittest.main()
