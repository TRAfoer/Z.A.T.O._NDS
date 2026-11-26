from pathlib import Path
import sys
import unittest
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from music_edit import render_edit


class MusicEditTests(unittest.TestCase):
    def setUp(self):
        self.pcm = np.column_stack((np.sin(np.arange(120000)*.03), np.cos(np.arange(120000)*.02))) * 10000
        self.pcm = self.pcm.astype(np.int16)
        self.edit = dict(intro_end_frame=6000, source_loop_start_frame=20000,
                         source_loop_end_frame=74000, crossfade_frames=20)

    def test_intro_once_then_loop_with_exact_duration(self):
        pcm, start, end = render_edit(self.pcm, self.edit)
        self.assertEqual((len(pcm), start, end), (60000, 6000, 60000))
        np.testing.assert_array_equal(pcm[:5980], self.pcm[:5980])
        np.testing.assert_array_equal(pcm[start:end-20], self.pcm[20000:73980])
        repeated = np.concatenate((pcm, pcm[start:end], pcm[start:end]))
        np.testing.assert_array_equal(repeated[60000:114000], pcm[start:end])
        self.assertLessEqual(np.abs(pcm.astype(np.int32)).max(), 10000)

    def test_crossfade_connects_to_source_preroll_without_shrinking_beat_period(self):
        pcm = np.full((120000, 2), -10000, dtype=np.int16)
        pcm[19980:21000] = 10000
        out, start, end = render_edit(pcm, self.edit)
        self.assertEqual(len(out), 60000)
        np.testing.assert_array_equal(out[end-1], out[start])
        np.testing.assert_array_equal(out[start-1], out[start])

    def test_rejects_out_of_range_or_empty_loop(self):
        for changes in [dict(source_loop_end_frame=200000), dict(source_loop_start_frame=74000),
                        dict(intro_end_frame=-1), dict(crossfade_frames=30000)]:
            with self.assertRaises(ValueError): render_edit(self.pcm, self.edit | changes)

    def test_manual_loop_without_intro_and_without_fade(self):
        edit=self.edit|dict(intro_end_frame=0,source_loop_start_frame=0,crossfade_frames=0)
        pcm,start,end=render_edit(self.pcm,edit)
        self.assertEqual((start,end),(0,74000))
        np.testing.assert_array_equal(pcm,self.pcm[:74000])

    def test_faded_loop_can_begin_at_source_zero(self):
        pcm,start,end=render_edit(self.pcm,self.edit|dict(intro_end_frame=0,source_loop_start_frame=0))
        self.assertEqual((start,end),(0,74000))
        np.testing.assert_array_equal(pcm[-1],pcm[0])


if __name__ == '__main__': unittest.main()
