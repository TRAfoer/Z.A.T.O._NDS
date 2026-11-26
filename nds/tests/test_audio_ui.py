"""Run the shipping UI mixer against independent constant-block/ffmpeg PCM."""
from pathlib import Path
import json
import hashlib
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]


class AudioUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="zato_audio_ui_")
        cls.temp = Path(cls.temporary.name)
        cls.exe = cls.temp / "validate_audio_ui.exe"
        build = subprocess.run([
            shutil.which("gcc") or "gcc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-DZATO_NITROFS=1", "-I", str(ROOT / "tests/stubs"),
            str(ROOT / "tests/validate_audio_ui.c"), str(ROOT / "source/ima_adpcm.c"),
            "-o", str(cls.exe)], text=True, capture_output=True)
        if build.returncode:
            raise AssertionError(build.stdout + build.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def fixture(self, name, *, missing=False, corrupt=False, too_long=False, short=False):
        root = self.temp / name
        (root / "audio").mkdir(parents=True, exist_ok=True)
        rows = []
        music = None
        # No pop in this catalog. Button is deliberately moved to SFX index 2.
        for label, channels, frames, values in [
            ("music", 2, 5050, [(12000, -8000)] * 10),
            ("plot", 1, 10170, [(6000,)] * 10),
            ("unrelated", 1, 1825, [(8000,), (-12000,)]),
            ("absent" if missing else "button.mp3", 1, 30511 if too_long else 2205 if short else 29636,
             [(12000 if i % 2 == 0 else -16000,) for i in range(31 if too_long else 30)]),
        ]:
            payload = b"".join(b"".join(struct.pack("<hBB", x, 0, 0) for x in v)
                                .ljust(512, b"\0") for v in values)
            if corrupt and label == "button.mp3":
                payload = payload[:2] + bytes([89]) + payload[3:]
            path = f"audio/{label}.ima"
            (root / path).write_bytes(payload)
            if channels == 2:
                music = payload
            rows.append(struct.pack("<64s48sIIHHHHIIII", label.encode(), path.encode(),
                        frames, 22050, channels, 512, 505 if channels == 2 else 1017,
                        0, 0, len(payload), 0, frames))
        (root / "audio/index.bin").write_bytes(
            struct.pack("<8sIIII", b"ZATAUD02", 2, 1, 3, 144) + b"".join(rows))
        fmt = struct.pack("<HHIIHHHH", 0x11, 2, 22050, 22050 * 512 // 505, 512, 4, 2, 505)
        chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(music)) + music
        (root / "audio/clip.wav").write_bytes(b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"WAVE" + chunks)
        return root

    def run_case(self, case, root, *args):
        result = subprocess.run([str(self.exe), case, str(root), *map(str, args)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_paused_plot_freezes_while_all_ui_cues_mix_exact_pcm(self):
        self.run_case("paused", self.fixture("paused"))

    def test_trigger_and_callback_zero_io_single_voice_end_mute_and_stop(self):
        self.run_case("controls", self.fixture("controls"))

    def test_active_stereo_plot_and_ui_sum_before_clipping(self):
        self.run_case("mix", self.fixture("mix"))

    def test_restore_and_movie_stop_ui_but_keep_prepared_cache(self):
        self.run_case("restore", self.fixture("restore"))

    def test_prepare_is_optional_idempotent_and_failure_does_not_break_plot(self):
        for label, kwargs in [("missing", {"missing": True}),
                              ("corrupt", {"corrupt": True}),
                              ("too_long", {"too_long": True})]:
            with self.subTest(label=label):
                self.run_case("unavailable", self.fixture(label, **kwargs))
        self.run_case("short", self.fixture("short", short=True))

    def test_prepare_io_failures_are_optional_and_retriable(self):
        for failure in ("open", "seek", "read", "close"):
            with self.subTest(failure=failure):
                self.run_case("io_failure", self.fixture("io_" + failure), failure)

    def test_no_nitrofs_optional_ui_api_is_safe(self):
        source = self.temp / "no_fs.c"
        executable = self.temp / "no_fs.exe"
        source.write_text('#include <assert.h>\n#include "audio_demo.h"\n'
            'int main(void){assert(!audio_ui_prepare());audio_ui_play(AUDIO_UI_MOVE);'
            'audio_ui_stop();return 0;}\n')
        result = subprocess.run([shutil.which("gcc") or "gcc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-I", str(ROOT / "tests/stubs"), "-I", str(ROOT / "source"), str(source),
            str(ROOT / "source/audio_demo.c"), "-o", str(executable)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        subprocess.run([str(executable)], check=True)

    def test_reinitialize_stops_ui_and_does_not_reuse_previous_catalog_cache(self):
        self.run_case("reinit", self.fixture("reinit_old"),
                      self.fixture("reinit_new", missing=True))

    def test_approved_button_original_and_cut_nearest_match_ffmpeg_and_preview(self):
        manifest = json.loads((ROOT / "artifacts/audio_manifest.json").read_text(encoding="utf-8"))
        row = next(row for row in manifest["assets"]
                   if row["group"] == "sfx" and Path(row["source"]).name == "button.mp3")
        source = self.temp / "button.pcm"
        decoded = subprocess.run(["ffmpeg", "-v", "error", "-i",
            str(ROOT / "nitrofs" / row["file"]), "-f", "s16le", "-c:a", "pcm_s16le", "-"],
            capture_output=True)
        self.assertEqual(decoded.returncode, 0, decoded.stderr.decode(errors="replace"))
        source.write_bytes(decoded.stdout[:row["frames"] * 2])
        preview = ROOT / "artifacts/ui_audio_button/move_button_075x_gain512_cut100ms_immediate.wav"
        self.assertEqual(hashlib.sha256(preview.read_bytes()).hexdigest(),
                         "8826317c5b0cf1c9ab22513612e82f3e901b3004d488206d4e84a489f2c826f2")
        golden = self.temp / "approved_move.pcm"
        with wave.open(str(preview), "rb") as stream:
            self.assertEqual((stream.getnchannels(), stream.getsampwidth(),
                              stream.getframerate(), stream.getnframes()), (1, 2, 22050, 36575))
            golden.write_bytes(stream.readframes(stream.getnframes()))
        self.run_case("approved", ROOT / "nitrofs", source, golden)


if __name__ == "__main__":
    unittest.main()
