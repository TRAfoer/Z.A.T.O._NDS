"""Native-oriented OP conversion tests: independent markers, bounds and decoders."""
from io import BytesIO
from pathlib import Path
import json
import math
import struct
import subprocess
import tempfile
import unittest
import wave

from PIL import Image
import numpy as np
from experiments.video_lab import convert_op


def jpeg(color):
    stream = BytesIO()
    Image.new('RGB', (256, 144), color).save(stream, format='JPEG', subsampling=2, quality=80)
    return stream.getvalue()


class VideoPackTests(unittest.TestCase):
    def test_full_container_duration_frame_counts_include_partial_last_frame(self):
        self.assertEqual([convert_op.frame_count(58_197_000, fps) for fps in (15, 20, 30)], [873, 1164, 1746])

    def test_jpeg_frame_split_respects_eoi_inside_segment_and_stuffed_scan(self):
        frame = b'\xff\xd8\xff\xe1\x00\x06A\xff\xd9B\xff\xda\x00\x02\x11\xff\x00\xd9\x22\xff\xd9'
        self.assertEqual(convert_op.split_mjpeg(frame * 2), [frame, frame])
        with self.assertRaises(ValueError):
            convert_op.split_mjpeg(frame[:-1])

    def test_index_is_sample_clocked_centered_and_rejects_out_of_bounds(self):
        frames = [jpeg('red'), jpeg('blue')]
        data = convert_op.pack_video(frames, duration_us=1_000_000, fps=2)
        info = convert_op.inspect_video(data)
        self.assertEqual((info['width'], info['height'], info['screen_height'], info['y']), (256, 144, 192, 24))
        self.assertEqual([row['audio_sample'] for row in info['index']], [0, 11025])
        self.assertEqual([data[row['offset']:row['offset']+row['length']] for row in info['index']], frames)
        corrupt = bytearray(data)
        struct.pack_into('<I', corrupt, convert_op.HEADER.size, len(data) + 1)
        with self.assertRaises(ValueError):
            convert_op.inspect_video(corrupt)
        with self.assertRaises(ValueError):
            convert_op.pack_video(frames[:1], duration_us=1_000_000, fps=2)

    def test_toolchain_tjpgdec_really_decodes_packed_frames_to_ds_rgb555(self):
        if not convert_op.DECODER_DIR.is_dir():
            self.skipTest('Wonderful BlocksDS TJpgDec example is not installed')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'colors.zav'
            path.write_bytes(convert_op.pack_video([jpeg('red'), jpeg('blue')], duration_us=1_000_000, fps=2))
            executable = convert_op.build_native_validator(root)
            result = json.loads(subprocess.check_output([str(executable), str(path), str(root/'samples')], text=True))
            self.assertEqual((result['frames'], result['width'], result['height']), (2, 256, 144))
            self.assertLessEqual(result['work_bytes_max'], 32768)
            rgb = np.frombuffer((root/'samples/00000.rgb555').read_bytes(), dtype='<u2').reshape(192, 256)
            self.assertTrue(np.all(rgb[:24] == 0x8000) and np.all(rgb[168:] == 0x8000))
            center = int(rgb[72, 128])
            self.assertGreater(center & 31, 28)
            self.assertLess((center >> 10) & 31, 3)
            corrupt = bytearray(path.read_bytes())
            struct.pack_into('<I', corrupt, convert_op.HEADER.size+4, 1)
            path.write_bytes(corrupt)
            rejected = subprocess.run([str(executable), str(path), str(root/'rejected')], capture_output=True)
            self.assertEqual(rejected.returncode, 9)

    def test_stereo_soundtrack_keeps_exact_pcm_length_and_real_ima_decoder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frames = 2205
            time = np.arange(frames)/22050
            pcm = np.stack([12000*np.sin(time*2*math.pi*100), 6000*np.cos(time*2*math.pi*200)], axis=1).astype('<i2')
            source = root/'source.wav'
            with wave.open(str(source), 'wb') as output:
                output.setnchannels(2); output.setsampwidth(2); output.setframerate(22050); output.writeframes(pcm.tobytes())
            result = convert_op.convert_audio(source, root)
            self.assertEqual((result['frames'], result['channels'], result['rate']), (2205, 2, 22050))
            self.assertEqual(result['padded_frames'], 2525)
            self.assertTrue(result['runtime_c_equals_ffmpeg'])
            self.assertGreater(result['different_channel_frames'], 2000)


if __name__ == '__main__':
    unittest.main()
