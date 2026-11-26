"""Deterministic audio transport tests; no output device or source conversion."""
from pathlib import Path
import json
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

try:
    from .anim_audio import SceneAudio
except ImportError:
    from anim_audio import SceneAudio


RATE = 22050


class SceneAudioTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.manifest = {
            "rate": RATE,
            "source_aliases": {"song.mp3": "song.ogg", "song.ogg": "song.ogg",
                               "clink.mp3": "clink.mp3"},
            "assets": [
                {"group": "music", "source": "song.ogg", "file": "audio/music/000.wav",
                 "frames": 6, "rate": RATE, "channels": 2,
                 "loop_start_frame": 1, "loop_end_frame": 4,
                 "aliases": ["song.ogg", "song.mp3"],
                 "alias_gains": {"song.ogg": 1.0, "song.mp3": 1.4}},
                {"group": "sfx", "source": "clink.mp3", "file": "audio/sfx/000.wav",
                 "frames": 3, "rate": RATE, "channels": 1,
                 "loop_start_frame": 0, "loop_end_frame": 3,
                 "aliases": ["clink.mp3"], "alias_gains": {"clink.mp3": 1.0}},
                {"group": "music", "source": "clink.mp3", "file": "audio/music/001.wav",
                 "frames": 3, "rate": RATE, "channels": 2,
                 "loop_start_frame": 0, "loop_end_frame": 3,
                 "aliases": ["clink.mp3"], "alias_gains": {"clink.mp3": 1.0}},
            ],
        }
        self.path = self.root / "audio_manifest.json"
        self.path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.pcm = {
            "audio/music/000.wav": np.column_stack((np.arange(1, 7) / 10,
                                                    -np.arange(1, 7) / 10)).astype("float32"),
            "audio/sfx/000.wav": np.array([.2, .3, .4], dtype="float32"),
            "audio/music/001.wav": np.full((3, 2), .75, dtype="float32"),
        }
        self.decoded = []

    def audio(self, **kwargs):
        def decode(path):
            name = path.relative_to(self.root / "nitrofs").as_posix()
            self.decoded.append(name)
            return self.pcm[name]
        kwargs.setdefault("start_stream", False)
        kwargs.setdefault("decoder", decode)
        audio = SceneAudio(manifest_path=self.path, nitrofs_root=self.root / "nitrofs",
                           **kwargs)
        self.addCleanup(audio.close)
        return audio

    def test_prepare_is_lazy_group_specific_and_deduplicates_source_aliases(self):
        audio = self.audio()
        self.assertEqual(self.decoded, [])
        audio.prepare([("music", "audio/song.mp3"), ("music", "song.ogg")])
        self.assertEqual(self.decoded, ["audio/music/000.wav"])
        self.assertTrue(audio.play("music", "audio/song.mp3"))
        self.assertEqual(self.decoded, ["audio/music/000.wav"])
        audio.prepare([("sfx", "audio/clink.mp3"), ("music", "clink.mp3")])
        self.assertEqual(self.decoded, ["audio/music/000.wav", "audio/sfx/000.wav",
                                       "audio/music/001.wav"])

    def test_intro_plays_once_then_exact_loop_without_tail_or_silent_padding(self):
        audio = self.audio(master_gain=1)
        audio.play("music", "song.ogg")
        out = audio.render(10)
        np.testing.assert_allclose(out[:, 0], [.1, .2, .3, .4, .2, .3, .4, .2, .3, .4])
        np.testing.assert_allclose(out[:, 1], [-.1, -.2, -.3, -.4, -.2, -.3, -.4, -.2, -.3, -.4])
        self.assertEqual(audio.status["active"]["music"]["position"], 1)
        np.testing.assert_allclose(audio.render(2)[:, 0], [.2, .3])

    def test_sfx_duplicates_mono_and_stops_at_eof_without_looping(self):
        audio = self.audio(master_gain=1)
        audio.play("sfx", "audio/clink.mp3")
        np.testing.assert_allclose(audio.render(5), [[.2, .2], [.3, .3], [.4, .4], [0, 0], [0, 0]])
        self.assertNotIn("sfx", audio.status["active"])

    def test_music_offset_preserves_intro_and_wraps_only_inside_approved_loop(self):
        audio = self.audio(master_gain=1)
        for frames, wanted in [(0, [.1, .2, .3]), (2, [.3, .4, .2]),
                               (4, [.2, .3, .4]), (9, [.4, .2, .3])]:
            with self.subTest(offset_frames=frames):
                self.assertTrue(audio.play("music", "song.ogg", offset_seconds=frames / RATE))
                np.testing.assert_allclose(audio.render(3)[:, 0], wanted)

    def test_offset_advances_fade_using_total_elapsed_time_before_loop_wrapping(self):
        audio = self.audio(master_gain=1)
        self.pcm["audio/music/000.wav"][:] = .8
        self.assertTrue(audio.play("music", "song.ogg", fadein=4 / RATE,
                                   offset_seconds=2 / RATE))
        np.testing.assert_allclose(audio.render(3)[:, 0], [.4, .6, .8])
        self.assertTrue(audio.play("music", "song.ogg", fadein=4 / RATE,
                                   offset_seconds=9 / RATE))
        np.testing.assert_allclose(audio.render(2)[:, 0], [.8, .8])

    def test_sfx_offset_skips_elapsed_samples_and_expired_sfx_replaces_previous_voice(self):
        audio = self.audio(master_gain=1)
        self.assertTrue(audio.play("sfx", "clink.mp3", offset_seconds=1 / RATE))
        np.testing.assert_allclose(audio.render(3)[:, 0], [.3, .4, 0])
        for frames in (3, 8):
            with self.subTest(offset_frames=frames):
                audio.play("sfx", "clink.mp3")
                self.assertTrue(audio.play("sfx", "clink.mp3", offset_seconds=frames / RATE))
                self.assertNotIn("sfx", audio.status["active"])
                self.assertFalse(audio.render(4).any())

    def test_invalid_offset_is_reported_without_replacing_active_voice(self):
        audio = self.audio(master_gain=1)
        audio.play("music", "song.ogg")
        for offset in (-1, float("nan"), float("inf")):
            with self.subTest(offset=offset):
                self.assertFalse(audio.play("music", "song.ogg", offset_seconds=offset))
        np.testing.assert_allclose(audio.render(1), [[.1, -.1]])
        self.assertTrue(any("offset_seconds" in error for error in audio.status["errors"]))

    def test_gain_above_one_and_alias_gain_are_preserved_before_master_gain(self):
        audio = self.audio()
        audio.play("music", "song.mp3", volume=1.4)
        np.testing.assert_allclose(audio.render(1), [[.098, -.098]])

    def test_voices_sum_before_clipping_and_int16_conversion_never_wraps(self):
        audio = self.audio(master_gain=1)
        self.pcm["audio/music/001.wav"][:] = [[.75, -.75]]
        self.pcm["audio/sfx/000.wav"][:] = [.75, -.75, .75]
        audio.play("music", "clink.mp3", volume=1.4)
        audio.play("sfx", "clink.mp3", volume=1.4)
        np.testing.assert_array_equal(audio.render(2, dtype="int16"), [[32767, 0], [0, -32768]])

    def test_fade_in_and_out_are_sample_based_and_stop_exactly(self):
        audio = self.audio(master_gain=1)
        self.pcm["audio/music/000.wav"][:] = .8
        audio.play("music", "song.ogg", fadein=4 / RATE)
        np.testing.assert_allclose(audio.render(5)[:, 0], [0, .2, .4, .6, .8])
        audio.stop("music", fadeout=4 / RATE)
        np.testing.assert_allclose(audio.render(6)[:, 0], [.8, .6, .4, .2, 0, 0])
        self.assertNotIn("music", audio.status["active"])

    def test_pause_freezes_cursor_and_fade_but_muting_advances_them(self):
        audio = self.audio(master_gain=1)
        audio.play("music", "song.ogg")
        audio.render(1)
        audio.pause()
        self.assertFalse(audio.render(20).any())
        self.assertEqual(audio.status["active"]["music"]["position"], 1)
        audio.resume()
        audio.enabled = False
        self.assertFalse(audio.render(2).any())
        self.assertEqual(audio.status["active"]["music"]["position"], 3)
        audio.enabled = True
        np.testing.assert_allclose(audio.render(1), [[.4, -.4]])

    def test_reset_discards_both_voices_and_fades_and_replays_from_start(self):
        audio = self.audio(master_gain=1)
        audio.play("music", "song.ogg", fadein=1)
        audio.play("sfx", "clink.mp3")
        audio.pause()
        audio.reset()
        self.assertEqual(audio.status["active"], {})
        audio.play("music", "song.ogg")
        np.testing.assert_allclose(audio.render(1), [[.1, -.1]])

    def test_errors_are_visible_and_never_interrupt_gui_callers(self):
        audio = self.audio()
        self.assertFalse(audio.play("music", "audio/missing.ogg"))
        self.assertTrue(audio.status["errors"])
        self.assertFalse(audio.play("other", "song.ogg"))
        self.assertFalse(audio.play("music", "song.ogg", volume=float("nan")))
        self.pcm["audio/music/000.wav"] = np.zeros((2, 2), dtype="float32")
        self.assertFalse(audio.play("music", "song.ogg"))
        self.assertEqual(audio.status["active"], {})

    def test_one_stream_handles_callback_status_failure_and_close(self):
        streams = []

        class Stream:
            def __init__(self, **kwargs):
                self.options = kwargs
                self.started = self.closed = False
                streams.append(self)

            def start(self):
                self.started = True

            def stop(self):
                self.started = False

            def close(self):
                self.closed = True

        audio = self.audio(stream_factory=Stream, start_stream=True)
        audio.play("music", "song.ogg")
        audio.play("sfx", "clink.mp3")
        self.assertEqual(len(streams), 1)
        self.assertEqual(streams[0].options["channels"], 2)
        self.assertEqual(streams[0].options["latency"], "low")
        self.assertEqual(audio.status["requested_latency"], "low")
        self.assertIsNone(audio.status["stream_latency_seconds"])
        out = np.empty((2, 2), dtype="float32")
        streams[0].options["callback"](out, 2, None, "output underflow")
        np.testing.assert_allclose(out, [[.15, .05], [.25, .05]], atol=1e-7)
        self.assertEqual(audio.status["callback"]["count"], 1)
        self.assertIsNone(audio.status["callback"]["current_time"])
        self.assertIsNone(audio.status["callback"]["output_buffer_dac_time"])
        self.assertTrue(any("underflow" in e for e in audio.status["errors"]))
        audio.close()
        self.assertTrue(streams[0].closed)

    def test_device_clock_diagnostics_distinguish_mixed_position_from_presentation(self):
        class Stream:
            latency = .092

            def __init__(self, **kwargs):
                self.callback = kwargs["callback"]

            def start(self): pass
            def stop(self): pass
            def close(self): pass

        audio = self.audio(stream_factory=Stream, start_stream=True)
        audio.play("music", "song.ogg")
        out = np.empty((2, 2), dtype="float32")
        audio._stream.callback(out, 2,
                               SimpleNamespace(currentTime=12.0, outputBufferDacTime=12.092),
                               "")
        status = audio.status
        self.assertEqual(status["stream_latency_seconds"], .092)
        self.assertEqual(status["position_clock"], "mixed")
        self.assertEqual(status["active"]["music"]["position"], 2)
        self.assertEqual(status["callback"]["current_time"], 12.0)
        self.assertEqual(status["callback"]["output_buffer_dac_time"], 12.092)
        audio._stream.callback(out, 2, None, "")
        self.assertIsNone(audio.status["callback"]["current_time"])
        self.assertIsNone(audio.status["callback"]["output_buffer_dac_time"])

    def test_output_device_failure_is_reported_while_transport_remains_usable(self):
        def fail(**kwargs):
            raise RuntimeError("no output device")
        audio = self.audio(stream_factory=fail, start_stream=True)
        self.assertTrue(audio.play("music", "song.ogg"))
        self.assertTrue(any("no output device" in e for e in audio.status["errors"]))
        self.assertFalse(audio.status["stream_active"])
        self.assertTrue(audio.status["active"])

    def test_prepared_play_does_not_wait_for_an_unrelated_background_decode(self):
        decoding = threading.Event()
        release = threading.Event()
        played = threading.Event()

        def decode(path):
            name = path.relative_to(self.root / "nitrofs").as_posix()
            if name == "audio/sfx/000.wav":
                decoding.set()
                release.wait(2)
            return self.pcm[name]

        audio = self.audio(decoder=decode)
        audio.prepare([("music", "song.ogg")])
        preparing = threading.Thread(target=lambda: audio.prepare([("sfx", "clink.mp3")]))
        playing = threading.Thread(target=lambda: (audio.play("music", "song.ogg"), played.set()))
        preparing.start()
        try:
            self.assertTrue(decoding.wait(1))
            playing.start()
            self.assertTrue(played.wait(.3), "Prepared play blocked on another asset's decoder")
        finally:
            release.set()
            preparing.join(2)
            if playing.ident is not None:
                playing.join(2)

    def test_device_failure_wall_clock_keeps_muted_positions_but_pause_freezes(self):
        now = [0.0]

        def fail(**kwargs):
            raise RuntimeError("no output device")

        with patch(f"{SceneAudio.__module__}.time.monotonic", side_effect=lambda: now[0]):
            audio = self.audio(stream_factory=fail, start_stream=True)
            audio.play("music", "song.ogg", fadein=1)
            audio.play("sfx", "clink.mp3")
            now[0] = .5
            self.assertNotIn("sfx", audio.status["active"])
            self.assertAlmostEqual(audio.status["active"]["music"]["fade_gain"], .5)
            audio.pause()
            now[0] = 2
            self.assertAlmostEqual(audio.status["active"]["music"]["fade_gain"], .5)
            audio.resume()
            audio.enabled = False
            now[0] = 2.5
            self.assertAlmostEqual(audio.status["active"]["music"]["fade_gain"], 1)

    def test_fadeout_interrupts_fadein_from_its_current_amplitude(self):
        audio = self.audio(master_gain=1)
        self.pcm["audio/music/000.wav"][:] = .8
        audio.play("music", "song.ogg", fadein=4 / RATE)
        audio.render(2)
        audio.stop("music", fadeout=2 / RATE)
        np.testing.assert_allclose(audio.render(4)[:, 0], [.4, .2, 0, 0])

    def test_int16_input_is_normalized_and_ima_block_padding_is_excluded(self):
        audio = self.audio(master_gain=1)
        self.pcm["audio/sfx/000.wav"] = np.array([-32768, 16384, 32767, -32768], dtype=np.int16)
        audio.play("sfx", "clink.mp3")
        np.testing.assert_array_equal(audio.render(4, dtype="int16"),
                                      [[-32768, -32768], [16384, 16384], [32767, 32767], [0, 0]])


if __name__ == "__main__":
    unittest.main()
