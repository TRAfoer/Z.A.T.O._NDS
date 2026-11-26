import unittest
from pathlib import Path
import tempfile
import numpy as np
from PIL import Image,ImageDraw
from portrait_preview import layout_around_portrait,make_portrait,PORTRAIT


class PortraitLayoutTests(unittest.TestCase):
    def test_first_two_lines_avoid_portrait_then_restore_full_width(self):
        text='甲'*80;lines,widths=layout_around_portrait(text,{'甲':12})
        self.assertEqual(PORTRAIT,(196,0,60,60))
        self.assertEqual(widths[:3],[157,157,194])
        self.assertEqual([len(line) for line in lines[:3]],[13,13,16])
        self.assertEqual(''.join(lines),text)

    def test_no_portrait_keeps_full_width(self):
        lines,widths=layout_around_portrait('甲'*40,{'甲':12},portrait=None)
        self.assertTrue(all(w==194 for w in widths));self.assertEqual(len(lines[0]),16)

    def test_previous_32px_layout_still_supported_explicitly(self):
        _,widths=layout_around_portrait('甲'*80,{'甲':12},portrait=(212,31,32,32))
        self.assertEqual(widths[:3],[173,173,194])

    def test_60px_includes_one_pixel_white_border(self):
        image=Image.new('RGBA',(220,220),(0,0,0,0));ImageDraw.Draw(image).rectangle((13,26,192,205),fill=(80,60,40,255))
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'portrait.png';image.save(source);result,_=make_portrait(source)
        self.assertEqual(result.size,(60,60));a=np.array(result)
        self.assertTrue(np.all(a[0]==255) and np.all(a[-1]==255) and np.all(a[:,0]==255) and np.all(a[:,-1]==255))
        self.assertFalse(np.all(a[1:-1,1:-1]==255))


if __name__=='__main__':unittest.main()
