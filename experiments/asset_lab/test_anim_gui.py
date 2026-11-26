"""Hidden Tk integration checks; never target or control another app/window."""
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import patch
from anim_audio import SceneAudio
from anim_preview import AnimPreview,PRESETS


class HiddenPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=tk.Tk();cls.root.withdraw()
        with patch('anim_preview.SceneAudio',lambda enabled=True:SceneAudio(enabled=False,start_stream=False)):
            cls.app=AnimPreview(cls.root,audio=False)
        cls.root.update_idletasks()

    @classmethod
    def tearDownClass(cls):cls.app.close()

    def test_all_scene_and_context_entries_render(self):
        for label in PRESETS:
            with self.subTest(preset=label):
                self.app.preset.set(label);self.app.select()
                self.assertEqual(self.app.top.size,(256,192))
                self.assertEqual(self.app.lower.size,(256,192))
                self.assertFalse(self.app.player.diagnostics)
                self.assertFalse(self.app.audio.status['errors'])

    def test_window_cycle_next_opens_its_own_context(self):
        self.app.preset.set('仅动效 · 窗口循环');self.app.select();self.app.next()
        self.assertEqual(self.app.preset.get(),'前后剧情 · 窗口前后')
        self.assertEqual(self.app.mode,'story')

    def test_return_keeps_audio_and_picture_paused(self):
        self.app.preset.set('前后剧情 · 窗口前后');self.app.select();self.app.pause()
        scale=self.app.scale.get();box=next(b for b in self.app.text.footer_boxes if b[4]=='返回')
        self.app.touch(SimpleNamespace(x=(box[0]+1)*scale,y=185*scale))
        self.assertTrue(self.app.paused)
        self.assertTrue(self.app.audio.status['paused'])

    def test_export_metadata_describes_current_not_old_comparison(self):
        self.app.compare.set(True);self.app.redraw()
        self.assertEqual(self.app.current_metadata['mode'],'rgb15')
        self.app.compare.set(False)

    def test_iss_defaults_to_text_and_can_compare_original_texture(self):
        self.app.iss_text.set(True);self.app.change_iss()
        self.app.preset.set('仅动效 · iss 闪现');self.app.select()
        self.assertIn('iss_text',self.app.current_metadata)
        self.app.iss_text.set(False);self.app.change_iss()
        self.assertNotIn('iss_text',self.app.current_metadata)
        self.app.iss_text.set(True);self.app.change_iss()


if __name__=='__main__':unittest.main()
