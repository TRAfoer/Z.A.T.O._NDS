"""Synthetic format4 packet and independent RGB15 BI pixel checks."""
from pathlib import Path
import struct,sys,unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from verify_story_bi_rom import decode_record4,revealed_english,PacketPixelOracle,SHADOW,UNDERLINE,punctuation_hang_rows,language_switch_due

def columns(gids,x,y,page):
    return struct.pack('<'+'H'*len(gids),*gids)+bytes(x)+bytes(y)+bytes(page)+b'\0\0'

def paired_record():
    normal=columns([SHADOW,0],[31,43],[35,35],[0,0])
    english=columns([UNDERLINE,UNDERLINE],[31,35],[49,49],[0,0])
    bi=normal;f=[0]*24;f[1]=f[12]=f[14]=f[19]=f[21]=2;f[2]=2;f[4]=f[5]=1
    f[9]=48;f[10]=f[11]=50;f[13]=50+len(normal);f[18]=f[13]+len(english);f[20]=f[18]+len(bi);f[22]=f[20]+4
    return struct.pack('<24H',*f)+b'\0\0'+normal+english+bi+struct.pack('<3H',1,2,2)

def glyph_packet():
    h=[0]*32;h[0]=2;h[5]=1;h[9]=136;h[10]=144;h[29]=4
    return struct.pack('<8s32I',b'ZATSTX02',*h)+struct.pack('<IHH',0,4,(2<<8)|2)+b'\x84'

class StoryBIProbeTests(unittest.TestCase):
    def test_immediate_bilingual_phase_has_no_numeric_anchor_comparison(self):
        self.assertFalse(language_switch_due(0,None,True))
        self.assertFalse(language_switch_due(9139,9140,False))
        self.assertTrue(language_switch_due(9140,9140,False))
        self.assertFalse(language_switch_due(0xffffffff,9140,False))
    def test_header_has_continuous_line_name_gap_and_portrait_gap(self):
        data=bytearray(glyph_packet());h=list(struct.unpack_from('<32I',data,8))
        h[12]=len(data);h[13]=h[12]+8;h[20]=h[13]+10
        struct.pack_into('<32I',data,8,*h)
        data.extend(struct.pack('<II',0,1));data.extend(struct.pack('<HBBHHBB',0,44,4,0xffff,0,0,0))
        data.extend(struct.pack('<H',0x83e0)*3600)
        oracle=PacketPixelOracle(bytes(data));f=[0]*24
        screen=oracle.header(dict(fields=f))
        self.assertEqual(screen[10,40],0xffff);self.assertEqual(screen[10,43],0x8000)
        self.assertEqual(screen[10,48],0xffff);self.assertEqual(screen[4,44],0xffff)
        self.assertEqual(screen[11,4],0xffff);self.assertEqual(screen[23,251],0xffff)
        f[15]=1;screen=oracle.header(dict(fields=f))
        self.assertEqual(screen[10,190],0xffff);self.assertEqual(screen[10,191],0x8000)
        self.assertTrue(np.all(screen[:60,196:]==0x83e0))
    def test_english_reveals_at_each_CN_threshold_not_only_after_whole_CN(self):
        r=decode_record4(paired_record())
        self.assertEqual(r['caps'],[2]);self.assertEqual(r['thresholds'],[1,2])
        self.assertEqual([len(revealed_english(r,0,v)) for v in (0,1,2)],[0,1,2])
    def test_extension_offsets_caps_and_compact_planes_are_checked(self):
        raw=paired_record()
        for at,value in [(18*2,65535),(19*2,3),(21*2,1),(22*2,91),(23*2,1)]:
            bad=bytearray(raw);struct.pack_into('<H',bad,at,value)
            with self.assertRaises(AssertionError):decode_record4(bad)
        bad=bytearray(raw);struct.pack_into('<H',bad,len(raw)-2,1)
        with self.assertRaises(AssertionError):decode_record4(bad)
    def test_packed_height_and_advance_and_shadow_foreground_order(self):
        oracle=PacketPixelOracle(glyph_packet());mask,advance=oracle.glyph(SHADOW)
        self.assertEqual((mask.shape,advance),((2,4),2))
        screen=np.full((192,256),0x8000,dtype=np.uint16)
        oracle.paint(screen,dict(gid=SHADOW,x=1,y=1,color=0x801f))
        self.assertEqual(screen[1,1],0x801f);self.assertEqual(screen[2,2],0x801f)
        self.assertEqual(screen[3,3],0xb18c)
    def test_underline_uses_advance_and_preserves_foreground_color(self):
        oracle=PacketPixelOracle(glyph_packet());screen=np.full((192,256),0x8000,dtype=np.uint16)
        oracle.paint(screen,dict(gid=UNDERLINE,x=1,y=1,color=0x83e0))
        self.assertEqual(list(screen[3,1:5]),[0x83e0,0x83e0,0x8000,0x8000])
    def test_body_partial_frame_contains_current_english_but_no_future_glyph(self):
        oracle=PacketPixelOracle(glyph_packet());r=decode_record4(paired_record())
        first=oracle.body(r,0,1);complete=oracle.body(r,0,2)
        self.assertEqual(first[49,31],0xca52);self.assertEqual(first[49,35],0x8000)
        self.assertEqual(complete[49,35],0xca52)
    def test_two_punctuation_may_hang_but_no_third_or_ordinary_letter(self):
        oracle=PacketPixelOracle(glyph_packet());commands=[dict(gid=0,x=i*2,y=0,page=0,index=i) for i in range(3)]
        rows=punctuation_hang_rows(commands,oracle,'A。。',3,8)
        self.assertEqual(rows[0]['indices'],[1,2])
        with self.assertRaisesRegex(AssertionError,'nonpunctuation'):punctuation_hang_rows(commands,oracle,'ABC',3,8)
        commands.append(dict(gid=0,x=6,y=0,page=0,index=3))
        with self.assertRaisesRegex(AssertionError,'more than two'):punctuation_hang_rows(commands,oracle,'A。。。',3,10)

if __name__=='__main__':unittest.main()
