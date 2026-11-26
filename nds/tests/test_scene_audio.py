"""Host-run the shipping scene audio against hand-checked ADPCM and approved PCM."""
from pathlib import Path
import json
import importlib.util
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SceneAudioVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('verify_scene_audio_rom', ROOT/'tools/verify_scene_audio_rom.py')
        cls.verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.verifier)

    def test_stereo_metrics_keep_channels_and_signed_peak(self):
        metric = self.verifier.audio_metrics(struct.pack('<6h', 3, 4, 0, -4, -32768, 0))
        self.assertEqual(metric['frames'], 3)
        self.assertEqual(metric['channel_peaks'], [32768, 4])
        self.assertEqual(metric['channel_nonzero_samples'], [2, 2])
        self.assertEqual(metric['different_channel_frames'], 3)
        with self.assertRaises(ValueError):
            self.verifier.audio_metrics(b'\0\0')

    def test_map_rejects_non_ram_and_header_preserves_actual_field_order(self):
        self.assertEqual(self.verifier.symbol_offset('0x02000104 g_audio_status ', 'g_audio_status'), 260)
        with self.assertRaises(ValueError):
            self.verifier.symbol_offset('0x08000104 g_audio_status ', 'g_audio_status')
        header = 'typedef struct { uint32_t magic,ready; uint32_t paused,frames; } SceneStatus;\nextern volatile SceneStatus g_scene_status;'
        self.assertEqual(self.verifier.scene_fields(header), ['magic', 'ready', 'paused', 'frames'])


class SceneAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='zato_scene_audio_')
        cls.temp = Path(cls.temporary.name)
        cls.executable = cls.temp / 'validate_scene_audio.exe'
        subprocess.run([shutil.which('gcc') or 'gcc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
            '-DZATO_NITROFS=1', '-I', str(ROOT/'tests/stubs'),
            str(ROOT/'tests/validate_scene_audio.c'), str(ROOT/'source/ima_adpcm.c'),
            '-o', str(cls.executable)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_synthetic_sample_timing_seek_and_legacy_contract(self):
        root = self.temp/'fixture'
        (root/'audio').mkdir(parents=True, exist_ok=True)
        # Index 0 plus zero nibbles has zero delta: each header predictor is the
        # complete block's constant PCM. The oracle is independent of the decoder.
        def block(*predictors):
            return b''.join(struct.pack('<hBB', value, 0, 0) for value in predictors).ljust(512, b'\0')

        rows = []
        for name, channels, frames, loop_start, loop_end, blocks in [
            ('music', 2, 1515, 509, 1400, [block(12000, -8000), block(16000, -6000), block(-20000, 10000)]),
            ('effect', 1, 40, 0, 40, [block(6000)]),
            ('negative', 1, 40, 0, 40, [block(-30000)]),
            ('long_effect', 1, 8192, 0, 8192, [block(6000)] * 9),
        ]:
            path = f'audio/{name}.bin'
            payload = b''.join(blocks)
            (root/path).write_bytes(payload)
            rows.append(struct.pack('<64s48sIIHHHHIIII', name.encode(), path.encode(),
                frames, 22050, channels, 512, 505 if channels == 2 else 1017, 0,
                0, len(payload), loop_start, loop_end))
        (root/'audio/index.bin').write_bytes(struct.pack('<8sIIII', b'ZATAUD02', 2, 1, 3, 144) + b''.join(rows))
        subprocess.run([str(self.executable), '--synthetic', str(root)], check=True)

    def test_all_approved_pcm_at_seven_seek_offsets(self):
        manifest = json.loads((ROOT/'artifacts/audio_manifest.json').read_text(encoding='utf-8'))
        for row in manifest['assets']:
            with self.subTest(group=row['group'], source=row['source']):
                pcm = self.temp/'reference.pcm'
                with pcm.open('wb') as output:
                    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(ROOT/'nitrofs'/row['file']),
                        '-f', 's16le', '-c:a', 'pcm_s16le', '-'], stdout=output, check=True)
                subprocess.run([str(self.executable), '--reference', str(ROOT/'nitrofs'),
                    row['group'], str(row['index']), str(pcm)], check=True)


if __name__ == '__main__':
    unittest.main()
