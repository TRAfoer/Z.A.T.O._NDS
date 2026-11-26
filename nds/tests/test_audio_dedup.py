"""Protect against merging distinct effects while recognizing transcode copies."""
from pathlib import Path
import sys
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from audio_dedup import waveform_match
from pack_audio import references


class AudioDedupTests(unittest.TestCase):
    def setUp(self):
        self.rate = 8000
        rng = np.random.default_rng(712)
        self.pcm = rng.normal(0, .1, (self.rate * 3, 2))

    def test_recognizes_gain_and_small_transcode_error(self):
        noise = np.random.default_rng(4).normal(0, .001, self.pcm.shape)
        match = waveform_match(self.pcm * 1.412 + noise, self.pcm, self.rate)
        self.assertIsNotNone(match)
        self.assertAlmostEqual(match['gain'], 1.412, places=3)

    def test_rejects_unrelated_equal_duration_sound(self):
        other = np.random.default_rng(19).normal(0, .1, self.pcm.shape)
        self.assertIsNone(waveform_match(self.pcm, other, self.rate))

    def test_rejects_different_stereo_even_when_mono_matches(self):
        a = np.column_stack((self.pcm[:, 0], self.pcm[:, 1]))
        b = a[:, ::-1]
        np.testing.assert_array_equal(a.mean(axis=1), b.mean(axis=1))
        self.assertIsNone(waveform_match(a, b, self.rate))

    def test_rejects_matching_intro_with_changed_ending(self):
        changed = self.pcm.copy()
        changed[-400:] *= -1
        self.assertIsNone(waveform_match(self.pcm, changed, self.rate))

    def test_rejects_different_duration(self):
        self.assertIsNone(waveform_match(self.pcm, self.pcm[:-400], self.rate))

    def test_accepts_encoder_final_sample_difference(self):
        self.assertIsNotNone(waveform_match(self.pcm, self.pcm[:-1], self.rate))

    def test_silence_does_not_match_non_silence(self):
        self.assertIsNone(waveform_match(self.pcm, np.zeros_like(self.pcm), self.rate))

    def test_static_selection_includes_menu_and_ui_but_excludes_unused(self):
        refs = references()
        self.assertIn('menu.ogg', refs['music'])
        self.assertIn('button.mp3', refs['sfx'])
        self.assertEqual(len(refs['music'] | refs['sfx']), 76)
        self.assertFalse({'girl.ogg', 'weight.ogg', 'clink1.mp3', 'metalhit.mp3', 'punch1.mp3'} & (refs['music'] | refs['sfx']))


if __name__ == '__main__':
    unittest.main()
