from pathlib import Path
import struct
import tempfile
import unittest
import numpy as np
from experiments.video_lab import convert_palette as palette


class PaletteFormatTests(unittest.TestCase):
    def test_flat_families_pad_short_pillow_palettes_and_reserve_black(self):
        samples=np.full((8,36,64,3),200,dtype=np.uint8)
        result=palette.family_palettes(samples,256,1)
        self.assertEqual(len(result[0]),256)
        self.assertEqual(int(result[0][0]),0x8000)

    def test_native_tile_order_round_trips_both_bit_depths(self):
        for colors in (16,32,48,64,256):
            pixels=(np.arange(144*256).reshape(144,256)%colors).astype(np.uint8)
            raw=palette.tile_bytes(pixels,colors)
            self.assertEqual(len(raw),18432 if colors==16 else 36864)
            np.testing.assert_array_equal(palette.tile_pixels(raw,colors),pixels)

    def test_skip_runs_apply_xor_and_reject_overflow(self):
        before=bytes(100)
        after=bytes([1])+bytes(49)+bytes([7,8])+bytes(48)
        packet=palette.skip_runs(bytes(a^b for a,b in zip(before,after)))
        self.assertEqual(palette.apply_skip(before,packet),after)
        self.assertLess(len(packet),20)
        self.assertEqual(palette.apply_skip(after,palette.skip_runs(bytes(100))),after)
        with self.assertRaises(ValueError):
            palette.apply_skip(before,struct.pack('<HH',101,0))

    def test_existing_c_lz10_decoder_verifies_all_frames_and_bounded_keyframe_seek(self):
        with tempfile.TemporaryDirectory() as temporary:
            out=Path(temporary)
            worker=palette.Encoder(out)
            try:
                raw=[]
                for i in range(6):
                    pixels=np.zeros((144,256),dtype=np.uint8)
                    pixels[20:28,10+i:18+i]=i+1
                    raw.append(palette.tile_bytes(pixels,16))
                palettes=[np.arange(16,dtype='<u2')|0x8000]*2
                data,meta=palette.build_pack(raw,palettes,colors=16,fps=2,duration_us=3_000_000,encoder=worker)
                path=out/'test.zpv';path.write_bytes(data)
                result=palette.verify_native(worker.executable,path,out/'samples')
                self.assertEqual(result['frames'],6)
                self.assertEqual(result['seek_checks'],6)
                self.assertLessEqual(meta['max_seek_frames'],4)
                self.assertEqual((out/'samples/00005.tiles').read_bytes(),raw[5])
                corrupt=bytearray(data)
                struct.pack_into('<I',corrupt,palette.HEADER.size,len(data)+4)
                with self.assertRaises(ValueError):palette.inspect_pack(corrupt)
            finally:worker.close()

    def test_bitmap8_preserves_nonblack_zero_in_later_family_and_black_borders(self):
        with tempfile.TemporaryDirectory() as temporary:
            out=Path(temporary);worker=palette.Encoder(out)
            try:
                for colors in (16,48,64):
                    pixels=(np.arange(144*256).reshape(144,256)%colors).astype(np.uint8)
                    raw=palette.tile_bytes(pixels,colors)
                    first=np.arange(colors,dtype='<u2')|0x8000
                    later=first.copy();later[0]=0x801f
                    data,_=palette.build_pack([raw]*6,[first,later],colors=colors,fps=2,duration_us=3_000_000,encoder=worker)
                    path=out/f'boundary{colors}.zpv';path.write_bytes(data)
                    samples=out/f'samples{colors}';palette.verify_native(worker.executable,path,samples)
                    bitmap=np.frombuffer((samples/'00005.bmp8').read_bytes(),dtype=np.uint8).reshape(192,256)
                    gpu=np.frombuffer((samples/'00005.gpu_pal').read_bytes(),dtype='<u2')
                    np.testing.assert_array_equal(bitmap[24:168],pixels+1)
                    self.assertFalse(bitmap[:24].any() or bitmap[168:].any())
                    self.assertEqual(gpu[0],0)
                    np.testing.assert_array_equal(gpu[1:colors+1],later)
            finally:worker.close()


if __name__=='__main__':unittest.main()
