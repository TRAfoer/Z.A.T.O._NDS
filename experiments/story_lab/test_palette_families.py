"""Palette-only structural proof, separate from subjective quantization quality."""
from pathlib import Path
import hashlib
import importlib.util
import tempfile
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[2]


class PaletteFamilyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=ROOT/'experiments/story_lab/palette_families.py'
        if not path.is_file():return
        spec=importlib.util.spec_from_file_location('palette_families_test_module',path)
        cls.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.module)

    def test_equivalent_shared_plane_ignores_slot_names_but_cannot_hide_17_classes(self):
        self.assertTrue(hasattr(self,'module'),'palette family audit is not implemented')
        proof=self.module.equivalent_classes([np.array([[0x8001,0x8002,0x8001]],dtype=np.uint16),np.array([[0x8005,0x8006,0x8005]],dtype=np.uint16)])
        self.assertTrue(proof['palette_only_exact']);self.assertEqual(proof['minimum_classes'],2)
        a=np.array([list(range(0x8000,0x8010))+[0x8000]],dtype=np.uint16)
        b=np.array([list(range(0x8100,0x8110))+[0x8101]],dtype=np.uint16)
        proof=self.module.equivalent_classes([a,b]);self.assertEqual(proof['minimum_classes'],17);self.assertFalse(proof['palette_only_exact'])
        proof=self.module.equivalent_classes([a,np.where(np.arange(17)[None,:]==0,0,b).astype(np.uint16)])
        self.assertFalse(proof['alpha_equal']);self.assertFalse(proof['palette_only_exact'])

    def test_two_selected_families_get_one_shared_plane_and_fixed_gray_slot(self):
        self.assertTrue(hasattr(self,'module'),'palette family audit is not implemented')
        pack=ROOT/'nds/nitrofs/assets.zat';before=hashlib.sha256(pack.read_bytes()).hexdigest()
        parent=ROOT/'experiments/story_lab/out';parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='palette_family_test_',dir=parent) as folder:
            report=self.module.build(output=folder,scan_all=False)
            self.assertEqual(hashlib.sha256(pack.read_bytes()).hexdigest(),before)
            self.assertEqual(report['existing']['library']['equivalence']['minimum_classes'],217)
            self.assertEqual(report['existing']['vents']['equivalence']['minimum_classes'],144)
            for family in report['new_families']:
                with np.load(family['arrays']) as arrays:
                    self.assertTrue(arrays['edge'].any())
                    self.assertTrue(np.all(arrays['indices'][arrays['edge']]==15))
                    self.assertEqual(arrays['palettes'].shape[1],16)
                    self.assertTrue(np.all(arrays['palettes'][:,15]==0x2108))
                    words=[np.where(arrays['mask'],pal[arrays['indices']]|0x8000,0).astype('<u2') for pal in arrays['palettes']]
                    self.assertTrue(self.module.equivalent_classes(words)['palette_only_exact'])
                    for i,variant in enumerate(family['variants']):
                        self.assertEqual(hashlib.sha256(words[i].tobytes()).hexdigest(),variant['native_word_sha256'])
                        from PIL import Image
                        saved=np.array(Image.open(variant['master']).convert('RGBA'))
                        restored=np.where(saved[:,:,3]>=128,self.module.rgb_words(saved[:,:,:3])|0x8000,0).astype('<u2')
                        np.testing.assert_array_equal(restored,words[i])
                    self.assertTrue(family['index_and_mask_roundtrip'])
                    self.assertTrue(family['alpha_unchanged'])


if __name__=='__main__':unittest.main()
