"""Independent upper-only movie geometry and native ZPV reconstruction."""
from pathlib import Path
import importlib.util,struct,unittest
from types import SimpleNamespace
import numpy as np
ROOT=Path(__file__).resolve().parents[2]

class DualVideoToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=ROOT/'nds/tools/verify_story_dual_video_rom.py';assert path.exists(),'dual video verifier is missing'
        spec=importlib.util.spec_from_file_location('dual_video_tool',path);cls.tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.tool)
    def test_upper_movie_with_black_lower_display_and_bars(self):
        top=np.zeros((192,256,3),dtype=np.uint8);y,x=np.indices((144,256));top[24:168]=np.stack([x&31,y&31,(x+y)&31],axis=2)*8
        bottom=np.zeros_like(top);frame=np.concatenate([top,bottom])
        self.assertEqual(self.tool.check_dual_pixels(frame,top>>3)['lower_pixels'],49152)
        self.assertFalse(bottom.any())
        for location in [(192,0,0),(0,0,1)]:
            wrong=frame.copy();wrong[location]^=8
            with self.assertRaises(AssertionError):self.tool.check_dual_pixels(wrong,top>>3)
        with self.assertRaises(AssertionError):self.tool.check_dual_pixels(frame,np.zeros_like(top))
    def test_lz10_reference_handles_overlap_and_rejects_bounds(self):
        self.assertEqual(self.tool.lz10(bytes.fromhex('10060000104142430002'),6),b'ABCABC')
        for value in [b'',bytes.fromhex('10060000800000'),bytes.fromhex('1006000010414243')]:
            with self.assertRaises((ValueError,IndexError)):self.tool.lz10(value,6)
    def test_observer_accepts_index_loading_before_video_metadata_publication(self):
        probe=self.tool.Probe.__new__(self.tool.Probe);probe.decoder=SimpleNamespace(count=1746)
        state={'a':{'errors':0,'music_underruns':0,'sfx_underruns':0},'s':{'errors':0},'video':{'magic':0x4449565a,'errors':0,'requested_seek':0xffffffff,'total_frames':0,'active':0,'decoded':0,'displayed':0}}
        self.assertIs(probe.observe(state),state)
    def test_file_playback_keeps_catalog_selection_but_has_only_one_start(self):
        probe=self.tool.Probe.__new__(self.tool.Probe);probe.decoder=SimpleNamespace(count=1746);probe.seen=set();probe.timeline=[];probe.last_progress=0
        state={'a':{'errors':0,'music_underruns':0,'sfx_underruns':0,'music_starts':1,'sfx_starts':0,'music_index':0},'s':{'errors':0},'video':{'magic':0x4449565a,'errors':0,'requested_seek':0xffffffff,'total_frames':1746,'active':1,'decoded':2,'displayed':1,'frame':0,'audio_frame':1,'commits':1,'paused':0}}
        self.assertIs(probe.observe(state),state)
    def test_independent_tile_palette_key_and_xor_decoder(self):
        n=18432;first=bytearray(n)
        for y in range(144):
            for x in range(256):
                at=((y//8)*32+x//8)*32+(y%8)*4+(x%8)//2;first[at]|=((x//8+y//8)%16)<<(4*(x&1))
        difference=bytes([0x11])*n;second=bytes(a^b for a,b in zip(first,difference));colours=np.arange(16,dtype='<u2')*1024
        def fnv(data):
            value=2166136261
            for b in data:value=((value^b)*16777619)&0xffffffff
            return value
        palette_at=88+72;payload_at=palette_at+32;size=payload_at+n*2
        header=struct.pack('<8s20I',b'ZATPAL01',1,256,144,256,192,0,24,1,2,2_000_000,16,4,n,1,36,88,palette_at,payload_at,size,n)
        index=struct.pack('<9I',payload_at,n,n,9,0,0,0,fnv(first),0)+struct.pack('<9I',payload_at+n,n,n,10,0,0,22050,fnv(second),0)
        decoder=self.tool.ZPVFrameDecoder(header+index+colours.tobytes()+first+difference)
        for frame in (0,1):
            actual=decoder.upper(frame);y,x=np.indices((144,256));indices=(x//8+y//8)%16
            if frame:indices^=1
            words=colours[indices];expected=np.zeros((192,256,3),dtype=np.uint8);expected[24:168]=np.stack([words&31,(words>>5)&31,(words>>10)&31],axis=2)
            np.testing.assert_array_equal(actual,expected)

if __name__=='__main__':unittest.main()
