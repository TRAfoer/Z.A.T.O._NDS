import unittest
from pathlib import Path
import subprocess
import sys
import numpy as np
from unittest.mock import patch
from iss_text import IssText
from anim_renderer import AnimRenderer


class IssTextTests(unittest.TestCase):
    def test_sparse_words_follow_measured_anchors(self):
        effect=IssText()
        first=np.asarray(effect.render(0))
        ys,xs=np.nonzero(first[:,:,:3].max(axis=2))
        self.assertGreater(len(xs),0)
        self.assertEqual((int(xs.min()),int(ys.min())),(58,34))
        self.assertEqual([f['words'] for f in effect.metadata['frames'][:4]],[1,2,5,14])

    def test_ten_steps_accumulate_without_flickering_existing_words(self):
        effect=IssText();previous=np.zeros((144,256),dtype=bool);counts=[]
        for i in range(10):
            a=np.asarray(effect.render(i))
            self.assertEqual(a.shape,(144,256,4))
            self.assertTrue(np.all(a[:,:,3]==255))
            visible=a[:,:,0]>0
            self.assertTrue(np.all(visible[previous]))
            counts.append(int(np.count_nonzero(visible)));previous=visible
        self.assertTrue(all(b>a for a,b in zip(counts,counts[1:])),counts)

    def test_overlaps_brighten_and_earliest_pixels_dim_in_the_tail(self):
        effect=IssText();first=np.asarray(effect.render(0));last=np.asarray(effect.render(9))
        first_ink=first[:,:,0]>0
        self.assertGreater(int(last[:,:,0].max()),int(first[:,:,0].max()))
        self.assertTrue(np.any(last[:,:,0][first_ink]<first[:,:,0][first_ink]))
        self.assertTrue(np.all(last[:,:,0][first_ink]>0))

    def test_repeated_renders_are_deterministic_and_font_based(self):
        a,b=IssText(),IssText()
        self.assertEqual(a.render(8).tobytes(),b.render(8).tobytes())
        self.assertTrue(a.metadata['font_based'])
        self.assertFalse(a.metadata['reads_original_textures'])
        self.assertEqual(a.metadata['word'],'is')

    def test_text_effect_never_loads_is_textures_and_keeps_script_timing(self):
        renderer=AnimRenderer(iss_mode='text')
        layers=[dict(name='iss',start=5.,properties={},at=[],behind=[])]
        with patch.object(renderer,'_texture',side_effect=AssertionError('texture loaded')):
            renderer.render(layers,5.049)
            self.assertEqual(renderer.metadata['iss_text']['step'],1)
            renderer.render(layers,5.05)
            self.assertEqual(renderer.metadata['iss_text']['step'],2)
            renderer.render(layers,5.475)
            self.assertEqual(renderer.metadata['iss_text']['step'],10)
            self.assertFalse(renderer.metadata['missing'])
            renderer.render(layers,5.5)
            self.assertEqual(renderer.metadata['iss_text']['step'],1)

    def test_other_effects_remain_pixel_identical(self):
        text=AnimRenderer(iss_mode='text');original=AnimRenderer(iss_mode='texture')
        layers=[dict(name='ghost',start=0,properties={},at=[],behind=[]),
                dict(name='deface',start=0,properties={'xzoom':-1},at=[],behind=[])]
        self.assertEqual(text.render(layers,.2).tobytes(),original.render(layers,.2).tobytes())

    def test_renderer_text_mode_can_be_imported_as_a_package(self):
        code="from experiments.asset_lab.anim_renderer import AnimRenderer; r=AnimRenderer(iss_mode='text'); assert r.render([dict(name='iss',start=0)],0).size==(256,192)"
        result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[2],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':unittest.main()
