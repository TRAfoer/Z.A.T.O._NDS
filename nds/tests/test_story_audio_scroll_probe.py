"""The PCM observer must recover replayed source positions independently."""
from pathlib import Path
import importlib.util
import unittest
from types import SimpleNamespace
import numpy as np

ROOT=Path(__file__).resolve().parents[1]


class ScrollPCMObserverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('scroll_pcm_analysis',ROOT/'tools/analyze_story_scroll_pcm.py')
        cls.tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.tool)
        spec=importlib.util.spec_from_file_location('scroll_pcm_capture',ROOT/'tools/verify_story_audio_scroll.py')
        cls.capture=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.capture)

    def test_source_position_is_recovered_despite_output_gain_and_dc(self):
        source=np.random.default_rng(17).normal(0,1000,(12000,2))
        for actual in (3000,3512,1976,2488):
            observed=source[actual:actual+512]*.49+np.array([31,-74])
            result=self.tool.match_window(source,observed,expected=4000)
            self.assertEqual(result['position'],actual)
            self.assertGreater(result['correlation'],.999999)
        # A wave replay is observable as a backwards jump, even though every
        # PCM sample is nonzero and no producer underrun need have happened.
        before=self.tool.match_window(source,source[3512:4024],expected=4000)
        replay=self.tool.match_window(source,source[1976:2488],expected=4512)
        self.assertEqual(replay['position']-before['position'],-1536)

    def test_digital_silence_has_no_invented_source_match(self):
        self.assertIsNone(self.tool.match_window(np.ones((10000,2)),np.zeros((512,2)),4000))

    def test_profile_v2_tick_conversion_and_real_commit_event_gaps(self):
        audio=dict.fromkeys('magic version clock_hz timer_div service_calls last_service max_service_gap max_service_work callback_calls last_callback max_callback_gap max_callback_work max_callback_frames music_min_ready sfx_min_ready music_empty_frames sfx_empty_frames timer_wraps'.split(),0)
        audio.update(magic=0x4650415a,version=1,clock_hz=33513982,timer_div=1024,max_service_gap=1000)
        render=dict.fromkeys('magic version prepare_calls last_prepare max_prepare max_upper max_lower max_commit max_scroll text_bytes text_peak gfx_bytes max_flow'.split(),0)
        render.update(magic=0x5052465a,version=2,max_flow=400)
        rows=[dict(core=i,stage='test',committed=1+(i>=4)+(i>=6),callbacks=1+(i>=4),ready=3072,enabled=1,paused=0,
                   audio_perf=dict(audio,last_service=i*500,last_callback=(i//3)*1500),render_perf=render) for i in range(1,7)]
        probe=SimpleNamespace(pcm=bytes(32),rate=44100,trace=rows,
            markers=[dict(stage='test',core=1,pcm_frame=0)],n=SimpleNamespace(emu=SimpleNamespace(frame_count=7)))
        metric=self.capture.metrics(probe)[0]
        self.assertEqual(metric['max_commit_gap_core_frames'],3)
        self.assertEqual(metric['max_callback_gap_core_frames'],3)
        self.assertAlmostEqual(metric['audio_perf_ms']['max_service_gap'],1000*1024*1000/33513982)
        self.assertAlmostEqual(metric['render_perf_ms']['max_flow'],400*1024*1000/33513982)


if __name__=='__main__':unittest.main()
