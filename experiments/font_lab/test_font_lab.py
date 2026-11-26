from pathlib import Path
import unittest
import numpy as np
from font_core import parse_bdf,extract_translation,visible_text,encode_ids,decode_ids,pack_bitmap,unpack_bitmap


class FontTests(unittest.TestCase):
    def test_bdf_baseline_and_halfwidth_advance(self):
        source=b'''STARTFONT 2.1\nFONTBOUNDINGBOX 8 8 0 -1\nFONT_ASCENT 7\nFONT_DESCENT 1\nCHARS 1\nSTARTCHAR test\nENCODING 65\nDWIDTH 4 0\nBBX 3 2 1 -1\nBITMAP\nA0\n40\nENDCHAR\nENDFONT\n'''
        font=parse_bdf(source);bitmap=font.bitmap(65)
        self.assertEqual(font.glyphs[65]['advance'],4)
        expected=np.zeros((8,8),dtype=np.uint8);expected[6,1]=expected[6,3]=expected[7,2]=1
        np.testing.assert_array_equal(bitmap,expected)

    def test_12px_bitpacking_does_not_pad_each_row(self):
        bitmap=np.arange(144).reshape(12,12)%2
        packed=pack_bitmap(bitmap);self.assertEqual(len(packed),18)
        np.testing.assert_array_equal(unpack_bitmap(packed,12,12),bitmap)

    def test_quoted_translation_excludes_comments_old_and_python(self):
        text='''translate chinese test_id:\n    # a "English"\n    a happy "中{w=0.3}文"\ntranslate chinese strings:\n    old "Save"\n    new "保存[[槽]"\ntranslate chinese python:\n    thing = "not display text"\n'''
        records=extract_translation(text,'fixture.rpy','chinese')
        self.assertEqual([r['text'] for r in records],['中{w=0.3}文','保存[[槽]'])
        self.assertEqual([r['kind'] for r in records],['dialogue','ui'])

    def test_visible_text_retains_literal_escapes_not_tags_or_interpolations(self):
        self.assertEqual(visible_text('甲{b}乙{/b}[[丙]{{丁}[name]'),'甲乙[丙]{丁}')

    def test_source_line_comments_may_have_translator_notes(self):
        text='# game/script.rpy:95\ntranslate chinese first:\n    "前句"\n# game/script.rpy:96 存疑\ntranslate chinese next:\n    "后句"\n'
        records=extract_translation(text,'fixture.rpy','chinese')
        self.assertEqual(records[1]['source_ref'],{'file':'script.rpy','line':96})

    def test_all_symbol_codecs_roundtrip_boundaries(self):
        ids=[0,1,223,224,255,256,4095,4319]
        for mode in ['u16','vbyte','packed']:
            data=encode_ids(ids,mode,13)
            self.assertEqual(decode_ids(data,len(ids),mode,13),ids)
        with self.assertRaises(ValueError):decode_ids(b'\xe0',1,'vbyte',12)
        with self.assertRaises(ValueError):decode_ids(b'\xff\xff',1,'vbyte',12)


if __name__=='__main__':unittest.main()
