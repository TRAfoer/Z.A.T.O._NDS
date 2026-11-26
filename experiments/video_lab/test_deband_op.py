import unittest
from pathlib import Path
import tempfile
import numpy as np
from experiments.video_lab import convert_palette as p
from experiments.video_lab import deband_op as d

class DebandTests(unittest.TestCase):
    def test_refill_keeps_existing_colors_and_reduces_native_error(self):
        palette=p.rgb555(np.repeat(np.array([0,0,0,255],np.uint8)[:,None],3,axis=1))
        rgb=np.repeat(np.arange(256,dtype=np.uint8)[None,:,None],3,axis=2)
        new,meta=d.refill_palette(palette,rgb[None])
        self.assertEqual(int(new[0]),int(palette[0]));self.assertEqual(int(new[3]),int(palette[3]))
        self.assertGreater(len(set(map(int,new))),len(set(map(int,palette))))
        old_rgb=p.rgb_from_555(palette).astype(float);new_rgb=p.rgb_from_555(new).astype(float)
        err=lambda values:np.min(np.sum((rgb[:,:,None,:].astype(float)-values[None,None])**2,axis=3),axis=2).sum()
        self.assertLess(err(new_rgb),err(old_rgb))

    def test_ordered_pattern_is_fixed_and_protects_ink_and_black(self):
        palette=p.rgb555(np.repeat(np.array([0,64,128,255],np.uint8)[:,None],3,axis=1))
        # Native5-bit nearest-color tie resolves toward64; crossing into
        # gray104 is the actual threshold where the ordered pattern mixes.
        rgb=np.full((8,16,3),104,np.uint8);rgb[:,0]=0
        base=d.lookup(palette)[p.rgb555(rgb)&32767];guard=np.zeros((8,16),bool);guard[:,0:2]=True
        a=d.quantize(rgb,palette,d.lookup(palette),base,guard,16)
        b=d.quantize(rgb,palette,d.lookup(palette),base,guard,16)
        np.testing.assert_array_equal(a,b);np.testing.assert_array_equal(a[guard],base[guard])
        self.assertGreater(len(np.unique(a[:,2:])),1)

    def test_split_stitch_preserves_full_packet_bytes_and_seek_indices(self):
        with tempfile.TemporaryDirectory() as temp:
            enc=p.Encoder(Path(temp))
            try:
                palettes=[np.arange(64,dtype='<u2')|0x8000]*2
                frames=[]
                for n in range(8):
                    a=np.zeros((144,256),np.uint8);a[3:9,20+n:25+n]=n+1;frames.append(p.tile_bytes(a,64))
                data,_=p.build_pack(frames,palettes,colors=64,fps=2,duration_us=4000000,encoder=enc)
                parts=d.split_families(data);self.assertEqual(len(parts),2)
                self.assertEqual(d.stitch(parts,p.inspect_pack(data)),data)
                self.assertEqual(p.inspect_pack(parts[1])['index'][0]['key'],0)
            finally:enc.close()

if __name__=='__main__':unittest.main()
