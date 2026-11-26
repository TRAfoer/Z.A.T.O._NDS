import tempfile
import unittest
from pathlib import Path

import numpy as np

from experiments.video_lab import convert_palette as pal
from experiments.video_lab import lossless_packet_opt as opt
from experiments.video_lab.restore_op_text import fast_packets


class LosslessPacketTests(unittest.TestCase):
    def test_only_small_uncompressed_deltas_replace_optional_keys(self):
        rng=np.random.default_rng(1320)
        a=rng.integers(0,64,size=36864,dtype=np.uint8)
        b=a.copy();b[140:143]^=7
        c=rng.integers(0,64,size=36864,dtype=np.uint8)
        d=c.copy();d[3000:3300]^=1
        frames=[v.tobytes() for v in (a,a,b,c,c,c,d,d)]
        palettes=[(0x8000+np.arange(64,dtype='<u2'))]*2
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            encoder=pal.Encoder(directory)
            try:
                data,_=pal.build_pack(frames,palettes,colors=64,fps=2,duration_us=4000000,encoder=encoder)
            finally:
                encoder.close()
            data,_=fast_packets(data,[],key_frames={n:frames[n] for n in range(6)})
            result,report=opt.optimize_packets(data,max_delta_bytes=32)
            old=pal.inspect_pack(data);new=pal.inspect_pack(result)
            self.assertEqual([r['frame'] for r in report['replacements']],[1,2,5])
            self.assertEqual(new['index'][1]['length'],4)
            self.assertEqual(new['index'][4]['flags'],pal.RAW|pal.KEY)
            self.assertEqual(new['index'][5]['key'],4)
            self.assertEqual(new['index'][6]['key'],4)
            self.assertEqual([r['hash'] for r in old['index']],[r['hash'] for r in new['index']])
            self.assertEqual(list(opt.decoded_tiles(result)),frames)
            self.assertEqual(opt.optimize_packets(result,max_delta_bytes=32)[0],result)
            file=directory/'opt.zpv';file.write_bytes(result)
            self.assertEqual(pal.verify_native(encoder.executable,file,directory/'checked')['seek_checks'],8)


if __name__=='__main__':unittest.main()
