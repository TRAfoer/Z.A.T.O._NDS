"""Source-font/integer enlargement policy, independent from old scene scaling."""
from pathlib import Path
from types import SimpleNamespace
import sys,unittest
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'nds/tools'))
from story_typography import StoryTypographyRenderer,StoryTypographyCompiler,SHADOW,UNDERLINE
from anim_text import SceneText
from portrait_preview import load_font

class IntegerTypographyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.renderer=StoryTypographyRenderer();cls.original=SceneText()
    def test_every_enlargement_is_an_exact_double_of_the_native_italic_mask(self):
        for font,char in ((8,'M'),(12,'我'),(12,'M')):
            for italic in (False,True):
                native,advance=self.original.glyph(char,font,1.,False,italic)
                expected=np.repeat(np.repeat(np.asarray(native),2,axis=0),2,axis=1)
                for scale in (1.001,1.167,1.233,1.333,1.5,1.667,2.,3.):
                    actual,width=self.renderer.glyph(char,font,scale,True,italic)
                    np.testing.assert_array_equal(np.asarray(actual),expected,err_msg=str((font,char,italic,scale)))
                    self.assertEqual(width,advance*2)
    def test_unscaled_glyphs_keep_legacy_pixels_and_advances(self):
        for font,char in ((8,'M'),(12,'我'),(12,'赝')):
            for italic in (False,True):
                expected,advance=self.original.glyph(char,font,1.,False,italic)
                actual,width=self.renderer.glyph(char,font,1.,True,italic)
                np.testing.assert_array_equal(np.asarray(actual),np.asarray(expected));self.assertEqual(width,advance)
    def test_shrink_uses_native10_and_never_shrinks_the_native8_floor(self):
        glyphs,advances,ids=load_font(10)
        actual,width=self.renderer.glyph('图',12,.833)
        np.testing.assert_array_equal(np.asarray(actual),glyphs[ids['图']]*255);self.assertEqual(width,advances['图'])
        base,advance=self.original.glyph('M',8)
        actual,width=self.renderer.glyph('M',8,.5)
        np.testing.assert_array_equal(np.asarray(actual),np.asarray(base));self.assertEqual(width,advance)
    def test_native10_shrink_steps_down_to_exact_native8_design(self):
        glyphs,advances,ids=load_font(8)
        self.assertEqual(self.renderer.resolve_font('图',10,.833),(8,1))
        actual,width=self.renderer.glyph('图',10,.833)
        np.testing.assert_array_equal(np.asarray(actual),glyphs[ids['图']]*255)
        self.assertEqual(width,advances['图'])
    def test_missing_native10_uses_real_native8_or12_and_records_fallback(self):
        self.assertTrue(hasattr(self.renderer,'resolve_font'),'native source-font resolution is missing')
        self.assertEqual(self.renderer.resolve_font('犸',12,.5),(8,1))
        self.assertEqual(self.renderer.resolve_font('Ж',12,.5),(12,1))
        from pack_story_text import extend_fonts
        from story_typography import font_archive
        from font_core import parse_bdf
        import zipfile
        r=StoryTypographyRenderer();g=dict(char='Ж',font=12,scale=.5,language='en')
        extend_fonts(r,[dict(plan=SimpleNamespace(glyphs=[g]))],{})
        with zipfile.ZipFile(font_archive()) as z:source=parse_bdf(z.read('fusion-pixel-12px-monospaced-zh_hans.bdf'))
        actual,width=r.glyph('Ж',12,.5)
        np.testing.assert_array_equal(np.asarray(actual),source.bitmap(ord('Ж'))*255)
        self.assertEqual(width,source.glyphs[ord('Ж')]['advance'])
        self.assertTrue(any(x['char']=='Ж' and x['native_font']==12 for x in self.renderer.size_fallbacks.values()))
    def test_styles_add_one_native_display_pixel_after_integer_enlargement(self):
        for language,flag in (('cn',SHADOW),('en',UNDERLINE)):
            g=dict(char='M',language=language,bold=True,italic=True,scale=1.5,color=(255,255,255))
            m=self.renderer.measure(g,8);self.assertEqual(m.mask.size,(16,16));self.assertEqual(m.ink_height,17)
            c=StoryTypographyCompiler(self.renderer);gid,advance=c.glyph(g,8)
            self.assertEqual(gid&0xc000,flag);self.assertEqual(advance,8)
            self.assertEqual(len(c.atlas),32)
    def test_font_extension_uses_resolved_native10_instead_of_nominal12(self):
        self.assertTrue(hasattr(self.renderer,'resolve_font'),'native source-font resolution is missing')
        from pack_story_text import extend_fonts
        import zipfile
        from font_core import parse_bdf
        import json
        archive=Path(json.loads((ROOT/'experiments/font_lab/out/analysis.json').read_text(encoding='utf8'))['font_archive'])
        with zipfile.ZipFile(archive) as z:font=parse_bdf(z.read('fusion-pixel-10px-monospaced-zh_hans.bdf'))
        r=StoryTypographyRenderer();char=next(chr(cp) for cp in sorted(font.glyphs) if 0x4e00<=cp<=0x9fff and chr(cp) not in r.fonts[10][2])
        g=dict(char=char,scale=.833,language='cn',font=12,bold=False,italic=False)
        report=extend_fonts(r,[dict(plan=SimpleNamespace(glyphs=[g]))],{})
        self.assertIn(char,report['additions']['10']);self.assertNotIn(char,report['additions'].get('12',[]))
        bitmap,advance=r.glyph(char,12,.833);np.testing.assert_array_equal(np.asarray(bitmap),font.bitmap(ord(char))*255)
        self.assertEqual(advance,font.glyphs[ord(char)]['advance'])

if __name__=='__main__':unittest.main()
