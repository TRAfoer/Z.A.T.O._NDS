"""Run with experiments/audio_lab/.venv/Scripts/python.exe."""
from pathlib import Path
import sys
import tempfile
import unittest
import wave
import librosa
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'experiments/audio_lab'))
import music_loops as analysis


class MusicAnalysisTests(unittest.TestCase):
    def test_click_track_detects_tempo_and_uses_detected_phrase_boundaries(self):
        duration = 100
        y = librosa.clicks(times=np.arange(.5, duration, .5), sr=analysis.SR,
                          length=duration*analysis.SR)
        tempo, beats, onset, chroma, rms = analysis.detect_features(y)
        self.assertLess(abs(float(np.asarray(tempo).ravel()[0])-120), 2)
        candidates = list(analysis.choose_candidate(duration, beats, onset, chroma, rms))
        self.assertTrue(candidates)
        chosen = max(candidates, key=lambda c:c['score'])
        self.assertEqual(chosen['beat_count']%16, 0)
        self.assertTrue(52<=chosen['stored_seconds']<=68)
        for key in ['source_loop_start_seconds','source_loop_end_seconds']:
            self.assertLess(np.min(np.abs(beats-chosen[key])), 1e-8)

    def test_sparse_irregular_beats_are_flagged_instead_of_claiming_bars(self):
        n = int(120*analysis.SR/analysis.HOP)+1
        candidates = list(analysis.choose_candidate(120, np.array([1.,2.,4.,9.]),
            np.zeros(n), np.ones((12,n)), np.ones(n)))
        self.assertTrue(candidates)
        chosen=max(candidates,key=lambda c:c['score'])
        self.assertIsNone(chosen['assumed_meter'])
        self.assertIn('weak beat',chosen['confidence'])

    def test_already_minute_long_song_retains_complete_source(self):
        previous=analysis.DEFAULT_GAME
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'audio').mkdir()
            path=root/'audio/minute.wav'
            samples=librosa.clicks(times=np.arange(.5,64,.5),sr=analysis.SR,length=64*analysis.SR)
            with wave.open(str(path),'wb') as f:
                f.setnchannels(1);f.setsampwidth(2);f.setframerate(analysis.SR)
                f.writeframes((samples*32767).astype('<i2').tobytes())
            try:
                analysis.DEFAULT_GAME=root
                result=analysis.analyze(dict(source='minute.wav',seconds=64))
            finally:
                analysis.DEFAULT_GAME=previous
        self.assertFalse(result['edited'])
        self.assertNotIn('edit',result)


if __name__=='__main__':unittest.main()
