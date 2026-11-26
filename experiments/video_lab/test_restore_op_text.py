"""Catches wrapping, missing glyphs, cropped ink and off-by-one cue activation."""
import unittest
import json
import tempfile
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFont
from experiments.video_lab import restore_op_text as restore
from experiments.video_lab import convert_palette as pal


class RestorationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.font = restore.BitmapText()

    def test_preview_only_shows_video_on_upper_screen(self):
        rgb=np.full((144,256,3),(96,160,224),np.uint8)
        frame=restore.dual(rgb)
        np.testing.assert_array_equal(frame[24:168],rgb)
        self.assertFalse(frame[:24].any() or frame[168:].any())

    def test_large16_uses_original_gb16fs_bits_without_resampling(self):
        glyph,advance=self.font.glyph('温',16)
        # Literal source gb16fs GL code0x4e42; independently pinned rows.
        rows=bytes.fromhex('0008 43fc 3208 1208 83f8 6208 2208 0bf8 1000 27fc e4a4 24a4 24a4 24a4 2ffe 2000')
        expected=np.unpackbits(np.frombuffer(rows,np.uint8)).reshape(16,16)*255
        np.testing.assert_array_equal(np.asarray(glyph),expected)
        self.assertEqual(advance,16)
        self.assertEqual(set(np.unique(np.asarray(glyph))),{0,255})

    def black_card_cues(self):
        frame=np.zeros((144,256,3),np.uint8)
        frames=np.broadcast_to(frame,(1746,144,256,3))
        return frame,restore.prepare_cues(json.loads(restore.CUES.read_text(encoding='utf8')),frames,self.font)

    def test_original_large_card_changes_have_staggered_glyphs_and_a_faint_neutral_trail(self):
        frame,cues=self.black_card_cues()
        cue=next(c for c in cues if c['id']=='gentle_world')
        reference,base=restore.restore_frame(frame,907,[cue])
        # Inspect the first three actual 16px cells independently. A whole-card
        # translation would move all three together. A fade alone moves none.
        regions=[np.s_[13:31,x:x+16] for x in (8,24,40)]
        patterns=[]
        trails=[]
        for n in range(908,912):
            rgb,ink=restore.restore_frame(frame,n,[cue])
            patterns.append(tuple(not np.array_equal(ink[r],base[r]) for r in regions))
            trails.extend(rgb[~ink,0][rgb[~ink,0]>0].tolist())
            self.assertTrue(np.all(rgb[:,:,0]==rgb[:,:,1]) and np.all(rgb[:,:,0]==rgb[:,:,2]))
            self.assertGreaterEqual(int(rgb[ink].min()),160)
        self.assertTrue(any(0<sum(p)<3 for p in patterns),patterns)
        self.assertGreater(len(set(patterns)),1)
        self.assertTrue(trails and max(trails)<=96)
        for n in (897,906,916,921):
            np.testing.assert_array_equal(restore.restore_frame(frame,n,[cue])[0],reference)

    def test_each_cue_preserves_all_native_ink_and_a_majority_of_steady_frames(self):
        frame,cues=self.black_card_cues()
        for cue in cues:
            h,w=cue['mask'].shape;x,y=cue['x'],cue['y']
            base=np.zeros((144,256),bool);base[y:y+h,x:x+w]=cue['mask']>0
            native_pixels=sum(np.count_nonzero(self.font.glyph(ch,cue['metrics']['size'])[0])
                              for line in cue['lines'] for ch in line)
            self.assertEqual(int(base.sum()),native_pixels)
            steady=0
            for n in range(cue['start'],cue['end']):
                rgb,mask=restore.restore_frame(frame,n,[cue])
                self.assertEqual(int(mask.sum()),native_pixels,(cue['id'],n))
                if np.array_equal(mask,base) and not rgb[~mask].any() and len(np.unique(rgb[mask]))==1:
                    steady+=1
                if cue['id']=='wound' and n>=1480: np.testing.assert_array_equal(mask,base)
            self.assertGreater(steady,(cue['end']-cue['start'])//2,cue['id'])

    def test_large_sentence_keeps16_size_and_original_two_lines(self):
        result=self.font.render_lines(['既然忘了，','大概也没那么重要吧。'],248,16,line_step=20)
        self.assertEqual(result['size'],16)
        self.assertEqual(len(result['lines']),2)
        self.assertLessEqual(result['mask'].width,248)

    def test_all_original_large_cards_use16_and_fit_their_panels(self):
        source=np.zeros((144,256,3),np.uint8)
        frames=np.broadcast_to(source,(1746,144,256,3))
        cues=restore.prepare_cues(json.loads(restore.CUES.read_text(encoding='utf8')),frames,self.font)
        large={'gentle_world','ungrateful','forgot_matter','forgot_real','dream_question','dream_statement',
               'bird','eyes','cannot','animals','knife','wound'}
        for cue in cues:
            if cue['id'] not in large:continue
            h,w=cue['mask'].shape
            self.assertEqual(cue['metrics']['size'],16,cue['id'])
            self.assertTrue(4<=cue['x'] and cue['x']+w<=252)
            self.assertTrue(0<=cue['y'] and cue['y']+h<=144)
            if cue['id'] in ('bird','cannot'):self.assertGreaterEqual(cue['x'],172)
            if cue['id'] in ('eyes','animals'):self.assertLessEqual(cue['x']+w,90)
            if cue['id']=='knife':self.assertLess(int(np.where(cue['mask'])[0].max())+cue['y'],108)

    def test_long_chinese_sentence_stays_one_line_in_lower_screen_crop(self):
        result = self.font.render_lines(['我大声呼喊，但声音在墙上撞了个粉碎。'], 188, 12)
        self.assertEqual(result['lines'], ['我大声呼喊，但声音在墙上撞了个粉碎。'])
        self.assertEqual(len(result['widths']), 1)
        self.assertLessEqual(result['mask'].width, 188)
        self.assertIn(result['size'], (8, 10, 12))
        self.assertTrue(np.asarray(result['mask']).any())

    def test_four_explicit_lines_are_preserved_and_not_resampled(self):
        lines = ['“...的，可......你......牙..”', '“还....抽...”',
                 '“......味，谁......亲...啊，...？”', '“...哈......笑......开......”']
        result = self.font.render_lines(lines, 188, 10, line_step=10)
        self.assertEqual(result['lines'], lines)
        self.assertEqual(len(result['widths']), 4)
        self.assertLessEqual(result['mask'].height, 40)
        self.assertEqual(set(np.unique(np.asarray(result['mask']))), {0, 255})

    def test_missing_glyph_is_an_error_instead_of_silent_box(self):
        with self.assertRaisesRegex(ValueError, 'glyph'):
            self.font.render_lines(['\U0010ffff'], 188, 12)

    def test_unfit_text_is_rejected_without_truncation_or_added_lines(self):
        with self.assertRaisesRegex(ValueError, 'fit'):
            self.font.render_lines(['界' * 60], 188, 12)

    def test_frame_intervals_are_half_open_and_keep_single_frame_flashes(self):
        self.assertEqual(restore.active_ranges([0, 1, 1, 0, 1, 0, 1]),
                         [(1, 3), (4, 5), (6, 7)])

    def test_source_interval_and_neutral_text_survive_frame_restoration(self):
        cue = {'start': 3, 'end': 5, 'clear_rect': [40, 60, 200, 84],
               'clean': 'black', 'x': 40, 'y': 66, 'mask': np.ones((10, 18), np.uint8)*255,
               'alpha': np.ones(2), 'outline': False, 'value': 224}
        source = np.full((144, 256, 3), (16, 32, 64), np.uint8)
        for n in (2, 5):
            rgb, mask = restore.restore_frame(source, n, [cue])
            np.testing.assert_array_equal(rgb, source)
            self.assertFalse(mask.any())
        rgb, mask = restore.restore_frame(source, 3, [cue])
        np.testing.assert_array_equal(rgb[66:76, 40:58], np.full((10, 18, 3), 224, np.uint8))
        self.assertEqual(int(mask.sum()), 180)
        np.testing.assert_array_equal(rgb[:60], source[:60])

    def test_knife_cut_has_no_horizontal_mirror_caption(self):
        # Source frame1443 cuts to the knife shot. The only caption there is
        # vertical lettering on the right; a previous horizontal cue is stale.
        source = np.full((144,256,3), (64,16,48), np.uint8)
        frames = np.broadcast_to(source, (1746,144,256,3))
        data = json.loads(restore.CUES.read_text(encoding='utf8'))
        cues = restore.prepare_cues(data, frames, self.font)
        rgb, mask = restore.restore_frame(source,1443,cues)
        self.assertFalse(mask[60:86,54:190].any())

    def test_final_code_glitch_is_preserved_without_title_caption(self):
        # Source frame1731 is the hard cut from the logo to full-frame code.
        # Neither the horizontal title line nor inpainting may cross this cut.
        source = np.full((144,256,3), (16,32,64), np.uint8)
        frames = np.broadcast_to(source, (1746,144,256,3))
        data = json.loads(restore.CUES.read_text(encoding='utf8'))
        cues = restore.prepare_cues(data, frames, self.font)
        rgb, mask = restore.restore_frame(source,1731,cues)
        self.assertFalse(mask.any())
        np.testing.assert_array_equal(rgb,source)

    def test_credit_entry_glitch_precedes_readable_credit_letters(self):
        source = np.full((144,256,3), (16,32,64), np.uint8)
        frames = np.broadcast_to(source, (1746,144,256,3))
        cues = restore.prepare_cues(json.loads(restore.CUES.read_text(encoding='utf8')),frames,self.font)
        for n in (48,49,50):
            rgb,mask = restore.restore_frame(source,n,cues)
            self.assertFalse(mask.any())
            np.testing.assert_array_equal(rgb,source)

    def test_original_four_line_dialogue_does_not_gain_a_closing_quote(self):
        # Transcription fixture comes from Chinese video12.5s, whose third
        # line intentionally lacks the closing quote present in the script.
        data=json.loads(restore.CUES.read_text(encoding='utf8'))
        cue=next(c for c in data['cues'] if c['id']=='broken_dialogue')
        self.assertEqual(cue['lines'][2],'“......味，谁......亲...啊，...？')

    def test_broken_dialogue_does_not_erase_lower_band_border(self):
        source=np.zeros((144,256,3),np.uint8);source[93]=64;source[94]=128
        frames=np.broadcast_to(source,(1746,144,256,3))
        cues=restore.prepare_cues(json.loads(restore.CUES.read_text(encoding='utf8')),frames,self.font)
        rgb,_=restore.restore_frame(source,375,cues)
        np.testing.assert_array_equal(rgb[93:95],source[93:95])

    def test_text_card_clear_preserves_incoming_bottom_wipes(self):
        source=np.zeros((144,256,3),np.uint8)
        source[114:]=[128,64,32]
        frames=np.broadcast_to(source,(1746,144,256,3))
        cues=restore.prepare_cues(json.loads(restore.CUES.read_text(encoding='utf8')),frames,self.font)
        for n,y in [(1077,135),(1131,114)]:
            rgb,_=restore.restore_frame(source,n,cues)
            np.testing.assert_array_equal(rgb[y:],source[y:])

    def test_fading_neutral_caption_cannot_choose_a_tinted_palette_color(self):
        colors=pal.rgb555(np.array([[0,0,0],[64,64,64],[96,96,96],[80,72,88]],np.uint8))
        lookup=restore.neutral_lookup(colors)
        for level in range(32):
            index=lookup[level|(level<<5)|(level<<10)]
            rgb=pal.rgb_from_555(colors[index])
            self.assertEqual(int(rgb[0]),int(rgb[1]))
            self.assertEqual(int(rgb[1]),int(rgb[2]))

    def test_palette_training_includes_single_frame_color_flashes(self):
        frames=np.zeros((60,144,256,3),np.uint8)
        frames[3]=[0,255,0]  # between the old every8-frame samples
        palette=restore.neutral_palettes(frames)[0]
        rgb=pal.rgb_from_555(palette)
        self.assertTrue(np.any((rgb[:,0]==0)&(rgb[:,1]==255)&(rgb[:,2]==0)))

    def test_mirror_repair_does_not_smooth_purple_stripes_outside_ink(self):
        source=np.full((144,256,3),(46,20,47),np.uint8)
        source[(np.arange(144)//4)%2==1]=[106,39,90]
        source[71:79,80:180]=[128,128,128]
        frames=np.broadcast_to(source,(1746,144,256,3))
        cues=restore.prepare_cues(json.loads(restore.CUES.read_text(encoding='utf8')),frames,self.font)
        rgb,_=restore.restore_frame(source,1428,cues)
        np.testing.assert_array_equal(rgb[65:67,54:197],source[65:67,54:197])
        np.testing.assert_array_equal(rgb[82:86,54:197],source[82:86,54:197])

    def test_brief_bird_eyes_and_wound_dialogue_is_not_omitted(self):
        source=np.zeros((144,256,3),np.uint8)
        frames=np.broadcast_to(source,(1746,144,256,3))
        cues=restore.prepare_cues(json.loads(restore.CUES.read_text(encoding='utf8')),frames,self.font)
        for n,rect in [(1290,(170,30,224,105)),(1311,(34,25,90,115)),(1470,(34,25,65,120))]:
            rgb,mask=restore.restore_frame(source,n,cues)
            x0,y0,x1,y1=rect
            self.assertTrue(mask[y0:y1,x0:x1].any(),f'Source dialogue at frame{n} is missing')

    def test_wound_over_art_repairs_only_old_ink_from_clean_neighbor(self):
        source=np.zeros((144,256,3),np.uint8);source[94:]=[64,128,32]
        source[108,47]=[200,200,200]
        old=np.zeros((88,20),np.uint8);old[80,7]=255
        reference=np.full((88,20,3),(32,64,128),np.uint8)
        cue=dict(start=1468,end=1483,clear_rect=[40,28,60,116],clean='reference_ink',
                 x=42,y=31,mask=np.ones((2,2),np.uint8)*255,alpha=np.ones(15),
                 old_ink=old,reference_rgb=reference,reference_cutoffs={'1482':94},value=224)
        rgb,_=restore.restore_frame(source,1482,[cue])
        np.testing.assert_array_equal(rgb[108,47],[32,64,128])
        np.testing.assert_array_equal(rgb[100,45],source[100,45])
        np.testing.assert_array_equal(rgb[94:,60:],source[94:,60:])

    def test_fast_packets_keep_native_pixels_and_following_delta_seeks(self):
        with tempfile.TemporaryDirectory() as temporary:
            out=Path(temporary);encoder=pal.Encoder(out)
            try:
                frames=[]
                for i in range(6):
                    pixels=np.zeros((144,256),np.uint8);pixels[10:20,10+i:20+i]=i+1
                    frames.append(pal.tile_bytes(pixels,16))
                palette=[np.arange(16,dtype='<u2')|0x8000]*2
                original,_=pal.build_pack(frames,palette,colors=16,fps=2,duration_us=3000000,encoder=encoder)
                fast,meta=restore.fast_packets(original,[(1,3)])
                info=pal.inspect_pack(fast)
                self.assertTrue(info['index'][1]['flags']&pal.RAW)
                self.assertTrue(info['index'][2]['flags']&pal.RAW)
                self.assertEqual(meta['changed_frames'],[1,2])
                path=out/'fast.zpv';path.write_bytes(fast)
                result=pal.verify_native(encoder.executable,path,out/'checked')
                self.assertEqual(result['frames'],6)
                self.assertEqual(result['seek_checks'],6)
                self.assertEqual((out/'checked/00005.tiles').read_bytes(),frames[5])
                keypack,keys=restore.fast_packets(original,[(1,3)],key_frames={1:frames[1]})
                keyinfo=pal.inspect_pack(keypack)
                self.assertEqual(keyinfo['index'][1]['flags'],pal.KEY|pal.RAW)
                self.assertEqual(keyinfo['index'][2]['key'],1)
                path.write_bytes(keypack)
                self.assertEqual(pal.verify_native(encoder.executable,path,out/'key_checked')['seek_checks'],6)
                self.assertEqual((out/'key_checked/00005.tiles').read_bytes(),frames[5])
            finally:encoder.close()


if __name__ == '__main__':
    unittest.main()
