"""Independent pixels for story-only shadow/underline flag semantics."""
from pathlib import Path
from types import SimpleNamespace
import sys,unittest
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'nds/tools'))
from story_typography import StoryTypographyRenderer,StoryTypographyCompiler,SHADOW,UNDERLINE,INDEX_MASK,MAX_GLYPHS
from anim_text import SceneText

def glyph(char='M',language='cn',**kw):
    result=dict(char=char,language=language,scale=1.,bold=True,italic=False,color=(224,160,80));result.update(kw);return result

class FixedRenderer(StoryTypographyRenderer):
    def glyph(self,char,font=12,scale=1.,bold=False,italic=False):
        return Image.fromarray(np.array([[255,0],[0,255]],dtype=np.uint8)),2

class TypographyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.renderer=StoryTypographyRenderer();cls.original=SceneText()
    def test_bold_does_not_dilate_or_resize_the_underlying_mask(self):
        normal,advance=self.original.glyph('M',8,1.,False,False)
        bold,styled_advance=self.renderer.glyph('M',8,1.,True,False)
        np.testing.assert_array_equal(np.asarray(bold),np.asarray(normal))
        self.assertEqual((bold.size,styled_advance),(normal.size,advance))
    def test_italic_is_applied_to_native_mask_before_integer_enlargement(self):
        native,advance=self.original.glyph('M',8,1.,False,True)
        expected=np.repeat(np.repeat(np.asarray(native),2,axis=0),2,axis=1)
        actual,styled_advance=self.renderer.glyph('M',8,1.5,True,True)
        np.testing.assert_array_equal(np.asarray(actual),expected);self.assertEqual(advance*2,styled_advance)
    def test_all_authored_enlargements_use_two_by_two_native_pixel_blocks(self):
        for font,char in ((8,'M'),(12,'啊')):
            native,advance=self.original.glyph(char,font,1.,False,False)
            expected=np.repeat(np.repeat(np.asarray(native),2,axis=0),2,axis=1)
            for scale in (1+5/30,1+7/30,1+10/30,1+20/30,2.):
                mask,actual_advance=self.renderer.glyph(char,font,scale)
                np.testing.assert_array_equal(np.asarray(mask),expected,err_msg=f'{font}/{scale}')
                self.assertEqual(actual_advance,advance*2)
    def test_shrink_uses_original_ten_pixel_design_instead_of_resized_twelve(self):
        import json,zipfile
        sys.path.insert(0,str(ROOT/'experiments/font_lab'));from font_core import parse_bdf
        archive=json.loads((ROOT/'experiments/font_lab/out/analysis.json').read_text(encoding='utf8'))['font_archive']
        with zipfile.ZipFile(archive) as z:source=parse_bdf(z.read('fusion-pixel-10px-monospaced-zh_hans.bdf'))
        mask,advance=self.renderer.glyph('我',12,25/30)
        np.testing.assert_array_equal(np.asarray(mask),source.bitmap(ord('我'))*255)
        self.assertEqual(advance,source.glyphs[ord('我')]['advance'])
    def test_eight_pixel_subtitles_keep_native_readability_floor(self):
        for scale in (.5,20/30,25/30):
            expected,advance=self.original.glyph('M',8)
            actual,new_advance=self.renderer.glyph('M',8,scale)
            np.testing.assert_array_equal(np.asarray(actual),np.asarray(expected));self.assertEqual(new_advance,advance)
    def test_chinese_shadow_pixels_are_gray_then_original_foreground(self):
        r=FixedRenderer();image=Image.new('RGB',(5,5));g=glyph('A','cn')
        r.paint_glyph(image,g,1,1,font=8)
        self.assertEqual(image.getpixel((1,1)),g['color']);self.assertEqual(image.getpixel((2,2)),g['color'])
        self.assertEqual(image.getpixel((3,3)),(96,96,96));self.assertEqual(image.getpixel((2,1)),(0,0,0))
        self.assertEqual(r.metrics(g,8),(3,3,2))
    def test_english_underline_has_original_color_and_unchanged_canvas(self):
        r=FixedRenderer();image=Image.new('RGB',(5,5));g=glyph('A','en')
        r.paint_glyph(image,g,1,1,font=8)
        for p in ((1,1),(2,2),(1,3),(2,3)):self.assertEqual(image.getpixel(p),g['color'])
        self.assertEqual(image.getpixel((3,3)),(0,0,0));self.assertEqual(r.metrics(g,8),(2,3,2))
    def test_language_is_explicit_even_for_latin_punctuation_and_cyrillic(self):
        r=self.renderer
        self.assertEqual(r.style_flags(glyph('A','cn')),SHADOW)
        self.assertEqual(r.style_flags(glyph('，','cn')),SHADOW)
        self.assertEqual(r.style_flags(glyph('Ж','en')),UNDERLINE)
        g=glyph();g['bold']=False;self.assertEqual(r.style_flags(g),0)
        with self.assertRaisesRegex(ValueError,'language'):r.style_flags(glyph(language='unknown'))
    def test_compiler_reuses_one_bare_bitmap_for_all_language_bold_styles(self):
        c=StoryTypographyCompiler(self.renderer);plain=glyph();plain['bold']=False
        first,advance=c.glyph(plain,8);data=bytes(c.atlas)
        cn,ca=c.glyph(glyph(language='cn'),8);en,ea=c.glyph(glyph(language='en'),8)
        self.assertEqual((first,cn,en),(0,SHADOW,UNDERLINE));self.assertEqual((ca,ea),(advance,advance))
        self.assertEqual(len(c.glyphs),1);self.assertEqual(bytes(c.atlas),data)
        self.assertEqual(c.glyph_advances,[advance])
        mask,_=self.original.glyph('M',8,1.,False,False)
        self.assertEqual(len(data),(mask.width*mask.height+7)//8)
        np.testing.assert_array_equal(np.unpackbits(np.frombuffer(data,dtype=np.uint8))[:mask.width*mask.height].reshape(mask.height,mask.width),np.asarray(mask)!=0)
    def test_scaled_italic_measure_keeps_advance_and_accounts_for_underline(self):
        g=glyph(language='en',scale=1.5,italic=True);m=self.renderer.measure(g,8)
        native,advance=self.original.glyph('M',8,1.,False,True)
        expected=np.repeat(np.repeat(np.asarray(native),2,axis=0),2,axis=1)
        np.testing.assert_array_equal(np.asarray(m.mask),expected)
        self.assertEqual((m.advance,m.ink_width,m.ink_height),(advance*2,native.width*2,native.height*2+1))
    def test_last_latin_underline_stops_at_advance_not_padded_canvas(self):
        g=glyph('M','en');mask,advance=self.renderer.glyph('M',8)
        self.assertEqual((mask.width,advance),(8,4))
        image=Image.new('RGB',(16,16));self.renderer.paint_glyph(image,g,2,1,8)
        for x in range(2,2+advance):self.assertEqual(image.getpixel((x,1+mask.height)),g['color'])
        for x in range(2+advance,2+mask.width):self.assertEqual(image.getpixel((x,1+mask.height)),(0,0,0))
    def test_same_empty_bitmap_with_different_advance_gets_distinct_directory_entries(self):
        class Spaces(FixedRenderer):
            def glyph(self,char,font=12,scale=1.,bold=False,italic=False):return Image.new('L',(8,8)),4 if char==' ' else 8
        c=StoryTypographyCompiler(Spaces());a,first=c.glyph(glyph(' ','en'),8);b,second=c.glyph(glyph('　','en'),8)
        self.assertNotEqual(a&INDEX_MASK,b&INDEX_MASK);self.assertEqual(c.glyph_advances,[4,8])
    def test_layout_cache_distinguishes_language_even_with_identical_raw_text(self):
        r=FixedRenderer();cn=SimpleNamespace(raw='A',glyphs=[glyph('A','cn')]);en=SimpleNamespace(raw='A',glyphs=[glyph('A','en')])
        self.assertEqual(r.layout(cn)[0][0]['ink_width'],3)
        self.assertEqual(r.layout(en)[0][0]['ink_width'],2)
    def test_newline_is_no_ink_and_has_no_style_or_atlas_entry(self):
        c=StoryTypographyCompiler(self.renderer);g=glyph('\n','en')
        self.assertEqual(c.glyph(g,8),(65535,0));self.assertEqual(len(c.glyphs),0)
        self.assertEqual(self.renderer.metrics(g,8),(0,0,0))
    def test_exact_styled_border_fits_and_does_not_write_outside(self):
        r=FixedRenderer();image=Image.new('RGB',(3,3));g=glyph('A','cn')
        r.paint_glyph(image,g,0,0,8)
        self.assertEqual(image.getpixel((2,2)),(96,96,96))
        plan=SimpleNamespace(raw='A',glyphs=[g]);entries=r.layout(plan,x=0,y=0,width=3,bottom=3,font=8)
        self.assertEqual((entries[0][0]['ink_width'],entries[0][0]['height']),(3,3))
        with self.assertRaisesRegex(ValueError,'bounds'):r.layout(plan,x=0,y=0,width=2,bottom=3,font=8)
    def test_flagged_id_layout_checks_extended_shadow_and_underline_bounds(self):
        r=FixedRenderer();plan=SimpleNamespace(raw='AA',glyphs=[glyph('A','cn'),glyph('A','cn')])
        pages=r.layout(plan,x=0,y=0,width=4,bottom=3,font=8)
        self.assertEqual(len(pages),2);self.assertEqual(pages[0][0]['ink_width'],3)
        for entries in pages:
            for e in entries:self.assertLessEqual(e['x']+e['ink_width'],4);self.assertLessEqual(e['y']+e['height'],3)
        c=StoryTypographyCompiler(r);commands,n=c.layout(SimpleNamespace(raw='A',glyphs=[glyph('A','en')]))
        self.assertEqual(commands[0][0]&~INDEX_MASK,UNDERLINE);self.assertEqual(n,1)
    def test_glyph_count_limit_rejects_new_mask_without_mutating_atlas(self):
        c=StoryTypographyCompiler(self.renderer);c.glyphs=[None]*MAX_GLYPHS
        with self.assertRaisesRegex(ValueError,'16384'):c.glyph(glyph(),8)
        self.assertEqual(len(c.glyphs),MAX_GLYPHS);self.assertEqual(c.atlas,bytearray())

if __name__=='__main__':unittest.main()
