import unittest
from anim_player import RichText
from anim_text import SceneText


class SceneTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.renderer = SceneText()

    def test_long_dialogue_pages_without_losing_characters(self):
        plan = RichText('甲乙丙丁'*200)
        layout = self.renderer.layout(plan, portrait=True)
        self.assertGreater(len(layout), 1)
        self.assertEqual(''.join(x['glyph']['char'] for p in layout for x in p), plan.text)

    def test_portrait_avoidance_and_page_bounds(self):
        layout = self.renderer.layout(RichText('甲'*150), portrait=True)
        for page in layout:
            for item in page:
                self.assertLessEqual(item['y']+item['height'], 173)
                if item['y'] < 60: self.assertLessEqual(item['x']+item['width'], 188)

    def test_original_ui_frame_and_footer_render_at_native_size(self):
        im, _ = self.renderer.lower(None)
        self.assertEqual(im.size, (256,192))
        self.assertEqual(im.getpixel((4,100)), (255,255,255))
        self.assertEqual(im.getpixel((251,100)), (255,255,255))

    def test_ordinary_auto_reading_displays_the_second_page(self):
        plan=RichText('{cps=0}'+'甲'*180)
        display=dict(who=None,text=plan,raw=plan.raw,original='',revealed=True,attributes=[])
        _,first=self.renderer.lower(display,auto=True,auto_delay=1.,elapsed=.5)
        _,second=self.renderer.lower(display,auto=True,auto_delay=1.,elapsed=1.5)
        self.assertGreaterEqual(first['pages'],2)
        self.assertEqual((first['shown_page'],second['shown_page']),(0,1))

    def test_auto_pages_follow_the_actual_forced_text_completion_time(self):
        plan=RichText('{cps=1}'+'甲'*180)
        display=dict(who=None,text=plan,raw=plan.raw,original='',revealed=True,attributes=[],start=0.,ready_at=1.)
        _,page=self.renderer.lower(display,auto=True,auto_delay=1.,elapsed=2.5)
        self.assertEqual(page['shown_page'],1)
        display['time_shift']=.5
        _,page=self.renderer.lower(display,auto=True,auto_delay=1.,elapsed=1.75)
        self.assertEqual(page['shown_page'],1)


if __name__ == '__main__': unittest.main()
