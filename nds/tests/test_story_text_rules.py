"""User-rule assertions against the real native reader, independent of pack layout."""
from pathlib import Path
import importlib.util
import json
import subprocess
import struct
import tempfile
import unittest
import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[2]


class NativeStoryTextRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='zato_text_rules_');cls.directory=Path(cls.temp.name)
        spec=importlib.util.spec_from_file_location('native_text_rule_oracle',ROOT/'nds/tests/story_text_oracle.py')
        cls.oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.oracle)
        cls.exe=cls.oracle._compile_validator(cls.directory)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def render(self,visible,record=0,bilingual=True):
        output=self.directory/f'{record}_{visible}_{bilingual}.rgb15'
        subprocess.run([str(self.exe),'--probe',str(ROOT/'nds/nitrofs/story/text.bin'),str(record),str(visible),'0',str(int(bilingual)),str(output)],check=True,capture_output=True,text=True)
        return np.frombuffer(output.read_bytes(),dtype='<u2').reshape(192,256)

    def commands(self,record=0,package=None):
        data=(Path(package) if package is not None else ROOT/'nds/nitrofs/story/text.bin').read_bytes();h=struct.unpack_from('<32I',data,8)
        at,flags,raw_size,checksum=struct.unpack_from('<IHHI',data,h[8]+record*12)
        packet=data[at:at+(flags&32767)];raw=packet if flags&32768 else self.oracle.p.unlz10(packet)
        count=24 if h[29]==4 else 18;f=struct.unpack_from(f'<{count}H',raw)
        def columns(offset,count):
            return [dict(index=i,gid=struct.unpack_from('<H',raw,offset+i*2)[0],x=raw[offset+count*2+i],y=raw[offset+count*3+i],page=raw[offset+count*4+i]) for i in range(count)]
        cn=columns(f[18],f[19]) if h[29]==4 and f[19] else columns(f[11],f[12])
        en=columns(f[13],f[14])
        for entry in en:entry['reveal']=struct.unpack_from('<H',raw,f[20]+entry['index']*2)[0] if h[29]==4 and f[19] else f[1]
        return cn,en

    def test_english_starts_with_partial_chinese_instead_of_after_the_entire_paragraph(self):
        # Say 0 is unstyled CN prose and ASCII original, no portrait. EN's base
        # ink is RGB(150,150,150)=RGB15 0xca52. Footer is disabled and header is
        # outside this rectangle, so gray pixels here are actual English ink.
        body=self.render(1)[24:168,8:248]
        self.assertGreater(int(np.count_nonzero(body==0xca52)),0,
            'Paired English must already reveal while Chinese is only partially visible')

    def test_bilingual_body_rows_alternate_chinese_then_english(self):
        cn,en=self.commands()
        rows={}
        for language,commands in (('cn',cn),('en',en)):
            for command in commands:
                if command['page']==0 and command['gid']!=65535:rows.setdefault(command['y'],set()).add(language)
        order=[next(iter(languages)) if len(languages)==1 else 'mixed' for _,languages in sorted(rows.items())]
        self.assertGreaterEqual(len(order),4)
        self.assertEqual(order,['cn' if i%2==0 else 'en' for i in range(len(order))])

    def test_chinese_bold_uses_gray_offset_shadow_in_real_native_pixels(self):
        # Source Say492 contains CN {b} and EN {b}, with no source color tags.
        body=self.render(0xffffffff,record=492,bilingual=False)[24:168,8:248]
        self.assertGreater(int(np.count_nonzero(body==0xb18c)),0,
            'Chinese bold must cast RGB96 at (+1,+1), not dilate its base mask')

    def test_english_bold_is_a_runtime_underline_flag(self):
        _,english=self.commands(492)
        self.assertTrue(any(c['gid']!=65535 and c['gid']&0x4000 for c in english),
            'English {b} needs underline style instead of a broadened atlas glyph')

    def test_empty_name_has_one_unbroken_top_border_instead_of_dash_glyphs(self):
        pixels=self.render(0,bilingual=False)
        np.testing.assert_array_equal(pixels[10,4:252],np.full(248,0xffff),
            err_msg='Empty name must leave the continuous one-pixel top edge intact')

    def test_real_append_rows_finish_together_after_merging_source_fragments(self):
        manifest=json.loads((ROOT/'nds/artifacts/story_text_manifest.json').read_text(encoding='utf8'))
        ir=json.loads((ROOT/'nds/artifacts/full_story_ir.json').read_text(encoding='utf8'))
        source={e['global_text_id']:e for label in ir['labels'] for e in label['events'] if e['kind']=='say'}
        for id in (78,227,197,974):
            with self.subTest(text_id=id):
                event=source[id];display=event['display'];cg=self.oracle.RichText(display['raw']).glyphs;eg=self.oracle.RichText(display['original'],base_cps=0).glyphs
                chinese,english=self.commands(id);self.assertEqual(len(chinese),len(cg));self.assertEqual(len(english),len(eg))
                rows={(row['page'],row['cn_y'],row['en_y']) for row in manifest['records'][id]['pairs']}
                for page,cy,ey in rows:
                    cn=[c for c in chinese if c['page']==page and c['y']==cy and not cg[c['index']]['char'].isspace()]
                    en=[c for c in english if c['page']==page and c['y']==ey and not eg[c['index']]['char'].isspace()]
                    if not cn or not en:continue
                    last_cn=max(cn,key=lambda c:c['index']);last_en=max(en,key=lambda c:c['index'])
                    cn_tick=display['timeline_ticks'][last_cn['index']]
                    en_tick=display['timeline_ticks'][last_en['reveal']-1] if last_en['reveal'] else 0
                    self.assertEqual(cn_tick,en_tick,f'text{id} physical row{page,cy,ey} ends at different source ticks')

    def assert_balanced_real_rows(self,package=None):
        # These source cases exposed exhausted carry tails, retaining only a
        # space, a sole English word, and the literal all-closing ellipsis run.
        # Pair baselines are discovered from source row proof; existence and
        # exact glyph coverage are checked against the actual wire commands.
        manifest=json.loads((Path(package).parent/'story_text_manifest.json' if package is not None else ROOT/'nds/artifacts/story_text_manifest.json').read_text(encoding='utf8'))
        ir=json.loads((ROOT/'nds/artifacts/full_story_ir.json').read_text(encoding='utf8'))
        source={e['global_text_id']:e for label in ir['labels'] for e in label['events'] if e['kind']=='say'}
        for id in (49,1114,1424,10347,10472,10848):
            with self.subTest(text_id=id):
                event=source[id];display=event['display']
                cg=self.oracle.RichText(display['raw']).glyphs;eg=self.oracle.RichText(display['original'],base_cps=0).glyphs
                chinese,english=self.commands(id,package)
                self.assertEqual([c['index'] for c in chinese],list(range(len(cg))))
                self.assertEqual([c['index'] for c in english],list(range(len(eg))))
                rows={(row['page'],row['cn_y'],row['en_y']) for row in manifest['records'][id]['pairs']}
                for page,cy,ey in rows:
                    cn=[c for c in chinese if c['page']==page and c['y']==cy and not cg[c['index']]['char'].isspace()]
                    en=[c for c in english if c['page']==page and c['y']==ey and not eg[c['index']]['char'].isspace()]
                    self.assertEqual(bool(cn),bool(en),f'text{id} physical row{page,cy,ey} has only one printed language')
                    if cn:
                        last_cn=max(cn,key=lambda c:c['index']);last_en=max(en,key=lambda c:c['index'])
                        self.assertEqual(display['timeline_ticks'][last_cn['index']],display['timeline_ticks'][last_en['reveal']-1],
                            f'text{id} physical row{page,cy,ey} ends at different source ticks')

    def test_real_line_balance_and_hard_newlines_preserve_both_complete_source_streams(self):
        self.assert_balanced_real_rows()


class IndependentStyleOracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('independent_style_oracle',ROOT/'nds/tests/story_text_oracle.py')
        cls.oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.oracle)

    def renderer(self):
        source_glyph=self.oracle.p.SceneText.glyph
        class FontOnly:
            diagnostics=set()
            def glyph(self,*args):return source_glyph(self,*args)
            def layout(self,*args,**kwargs):raise AssertionError('Independent oracle must not call product layout')
        renderer=FontOnly();renderer.fonts={}
        for size in (8,12):
            pixels=np.zeros((size,size),dtype=np.uint8);pixels[0,0]=pixels[2,1]=1
            renderer.fonts[size]=([pixels,np.zeros_like(pixels)],{'A':size,' ':size//2},{'A':0,' ':1})
        return renderer

    def entry(self,language):
        return dict(index=0,x=4,y=5,font=8 if language=='en' else 12,
            glyph=dict(char='A',color=(255,255,255),scale=1.,bold=True,italic=False,language=language))

    def test_chinese_style_keeps_base_ink_and_adds_only_one_pixel_gray_shadow(self):
        renderer=self.renderer();entry=self.entry('cn');image=Image.new('RGB',(32,32))
        self.oracle.paint(renderer,image,[entry],1);pixels=np.asarray(image)
        self.assertEqual(int(np.count_nonzero(np.all(pixels==255,axis=2))),2)
        self.assertEqual(tuple(pixels[5,4]),(255,255,255));self.assertEqual(tuple(pixels[6,5]),(96,96,96))
        self.assertEqual(tuple(pixels[5,5]),(0,0,0),'Bold must not broaden the base mask')

    def test_english_style_draws_original_mask_and_one_complete_baseline(self):
        renderer=self.renderer();entry=self.entry('en');image=Image.new('RGB',(32,32))
        self.oracle.paint(renderer,image,[entry],1);pixels=np.asarray(image)
        self.assertEqual(int(np.count_nonzero(np.all(pixels==255,axis=2))),10)
        np.testing.assert_array_equal(pixels[13,4:12],np.full((8,3),255))
        self.assertEqual(tuple(pixels[6,5]),(0,0,0),'English bold must not cast a Chinese shadow')

    def test_english_underline_uses_advance_instead_of_eight_pixel_canvas(self):
        renderer=self.renderer();renderer.fonts[8][1]['A']=4
        entry=self.entry('en');image=Image.new('RGB',(32,32));self.oracle.paint(renderer,image,[entry],1)
        pixels=np.asarray(image)
        np.testing.assert_array_equal(pixels[13,4:8],np.full((4,3),255))
        self.assertEqual(tuple(pixels[13,8]),(0,0,0),'Advance4 underline must not extend another4 pixels')

    def test_row_thresholds_project_real_ink_indices_and_share_first_last(self):
        cn=[dict(index=i,glyph=dict(char=char)) for i,char in enumerate(' A B  C')]
        en=[dict(index=i,glyph=dict(char=char)) for i,char in enumerate(' xy z ')]
        values=self.oracle.row_thresholds(cn,en,0)
        self.assertEqual(values,[2,2,4,4,7,7])
        self.assertEqual(values[1],2);self.assertEqual(values[4],7)

    def test_single_english_ink_waits_for_last_chinese_ink(self):
        cn=[dict(index=i,glyph=dict(char=char)) for i,char in enumerate(' A B C ')]
        en=[dict(index=i,glyph=dict(char=char)) for i,char in enumerate(' x ')]
        self.assertEqual(self.oracle.row_thresholds(cn,en,0),[6,6,6])

    def test_single_chinese_ink_reveals_english_batch_at_same_tick(self):
        cn=[dict(index=10,glyph=dict(char='甲'))]
        en=[dict(index=i,glyph=dict(char=char)) for i,char in enumerate('word')]
        self.assertEqual(self.oracle.row_thresholds(cn,en,0),[11]*4)

    def test_centered_oracle_uses_ink_extent_without_product_layout(self):
        renderer=self.renderer();renderer.fonts[12][1]['A']=6
        pages=self.oracle.centered_pages(renderer,self.oracle.RichText('AA',base_cps=0))
        self.assertEqual([entry['x'] for entry in pages[0]],[119,125])

    def plans(self,chinese,english):
        cn=self.oracle.RichText(chinese,base_cps=0).glyphs
        en=[dict(g,language='en',font=8,color=(150,150,150)) for g in self.oracle.RichText(english,base_cps=0).glyphs]
        return cn,en

    def test_append_carries_without_relocating_or_retiming_the_visible_prefix(self):
        renderer=self.renderer();cn,en=self.plans('AAA','AAA')
        segments=[dict(source_event_id=1,cn_start=0,cn_end=2,en_start=0,en_end=2),dict(source_event_id=2,cn_start=2,cn_end=3,en_start=2,en_end=3)]
        _,before=self.oracle.independent_lower_layout(renderer,cn[:2],en[:2])
        _,after=self.oracle.independent_lower_layout(renderer,cn,en,segments)
        def key(entry):return entry['index'],entry['x'],entry['y'],entry.get('reveal')
        old_cn=[key(e) for page in before for e in page if 'en_index' not in e]
        old_en=[(e['en_index'],e['x'],e['y'],e['reveal']) for page in before for e in page if 'en_index' in e]
        self.assertEqual(old_cn,[key(e) for page in after for e in page if 'en_index' not in e and e['index']<2])
        self.assertEqual(old_en,[(e['en_index'],e['x'],e['y'],e['reveal']) for page in after for e in page if 'en_index' in e and e['en_index']<2])
        self.assertEqual({e['y'] for page in after for e in page if 'en_index' not in e},{30})
        self.assertTrue(after.pairs[-1]['carried'])

    def test_append_hard_newline_does_not_consume_the_other_language_body(self):
        renderer=self.renderer();cn,en=self.plans('AA\nA','AAA')
        segments=[dict(source_event_id=1,cn_start=0,cn_end=2,en_start=0,en_end=2),dict(source_event_id=2,cn_start=2,cn_end=4,en_start=2,en_end=3)]
        _,pages=self.oracle.independent_lower_layout(renderer,cn,en,segments)
        cn_last=next(e for p in pages for e in p if 'en_index' not in e and e['index']==3)
        en_last=next(e for p in pages for e in p if e.get('en_index')==2)
        self.assertEqual((cn_last['x'],cn_last['y']),(20,55));self.assertEqual((en_last['x'],en_last['y']),(20,69))

    def test_only_two_closing_marks_can_hang_after_a_full_cn_line(self):
        renderer=self.renderer();renderer.fonts[12][2]['，']=0;renderer.fonts[12][1]['，']=12
        cn,en=self.plans('A'*18+'，'*3+'A','A')
        pages,_=self.oracle.independent_lower_layout(renderer,cn,en)
        second_row=[e for e in pages[0] if e['y']==45]
        self.assertEqual(second_row[0]['index'],17)
        self.assertEqual(second_row[0]['glyph']['char'],'A','Closing run must move with preceding body text')
        self.assertEqual(sorted(e['index'] for p in pages for e in p),list(range(len(cn))))

    def test_blank_hard_lines_consume_leading_whitespace_in_both_languages(self):
        renderer=self.renderer();cn,en=self.plans('\nA',' \nA')
        _,pages=self.oracle.independent_lower_layout(renderer,cn,en)
        self.assertEqual([e['glyph']['char'] for e in pages.pairs[0]['en']],[' ','\n'])
        self.assertEqual([(e['glyph']['char'],e['y']) for p in pages for e in p if e['glyph']['char']=='A'],[('A',55),('A',69)])

    def test_carry_keeps_the_whole_english_token_inside_remaining_nominal_width(self):
        renderer=self.renderer();renderer.fonts[8][2][',']=0;renderer.fonts[8][1][',']=4
        cn,en=self.plans('AAAAA','A'*27+',')
        segments=[dict(source_event_id=1,cn_start=0,cn_end=4,en_start=0,en_end=23),dict(source_event_id=2,cn_start=4,cn_end=5,en_start=23,en_end=28)]
        _,pages=self.oracle.independent_lower_layout(renderer,cn,en,segments)
        last=next(e for p in pages for e in p if 'en_index' not in e and e['index']==4)
        self.assertEqual(last['y'],55,'Carry must not borrow the closing-mark pocket for a new EN token')

    def test_leading_english_space_is_not_selected_instead_of_an_available_printed_word(self):
        renderer=self.renderer();cn,en=self.plans('A'*40,' AAAAA')
        _,pages=self.oracle.independent_lower_layout(renderer,cn,en)
        self.assertTrue(any(not e['glyph']['char'].isspace() for e in pages.pairs[0]['en']),
            'A legal complete printed word must win over an empty leading-space cut')

    def test_physical_row_audit_merges_spans_and_catches_late_cn_with_only_en_space(self):
        cn,en=self.plans('AAA','AA ')
        c=[dict(index=i,glyph=g) for i,g in enumerate(cn)]
        e=[dict(index=i+3,en_index=i,glyph=g,reveal=i+1) for i,g in enumerate(en)]
        pages=self.oracle.LayoutPages([c+e],pairs=[dict(page=0,cn_y=35,en_y=49,cn=c[:2],en=e[:2]),dict(page=0,cn_y=35,en_y=49,cn=c[2:],en=e[2:])])
        record=dict(id=78,event=dict(chapter='synthetic',line=1),timeline=[0,0,2])
        with self.assertRaisesRegex(AssertionError,'physical row.*78'):
            self.oracle.physical_row_timing(record,pages)

    def test_physical_row_audit_accepts_different_source_indices_at_same_cps_zero_tick(self):
        cn,en=self.plans('AAA','AA')
        c=[dict(index=i,glyph=g) for i,g in enumerate(cn)]
        e=[dict(index=i+3,en_index=i,glyph=g,reveal=i+1) for i,g in enumerate(en)]
        pages=self.oracle.LayoutPages([c+e],pairs=[dict(page=0,cn_y=35,en_y=49,cn=c,en=e)])
        record=dict(id=79,event=dict(chapter='synthetic',line=1),timeline=[0,0,0])
        self.assertEqual(self.oracle.physical_row_timing(record,pages)['matched_rows'],1)

    def test_balance_retains_printed_glyph_instead_of_only_a_trailing_space(self):
        renderer=self.renderer();cn,en=self.plans('AA ','AA '*12)
        _,pages=self.oracle.independent_lower_layout(renderer,cn,en)
        later=[e for row in pages.pairs[1:] for e in row['cn'] if not e['glyph']['char'].isspace()]
        self.assertTrue(later,'An EN continuation row needs a real retained CN glyph, not trailing whitespace')

    def test_carry_is_rejected_when_a_fresh_pair_can_keep_both_complete_sides(self):
        renderer=self.renderer();cn,en=self.plans('A'*20,'A'*4)
        segments=[dict(source_event_id=1,cn_start=0,cn_end=16,en_start=0,en_end=2),dict(source_event_id=2,cn_start=16,cn_end=20,en_start=2,en_end=4)]
        _,pages=self.oracle.independent_lower_layout(renderer,cn,en,segments)
        first_new=next(e for page in pages for e in page if 'en_index' not in e and e['index']==16)
        self.assertEqual((first_new['x'],first_new['y']),(20,55))

    def test_necessary_single_word_fallback_keeps_both_sides_on_every_row(self):
        renderer=self.renderer();cn,en=self.plans('A'*40,'AAAAA')
        _,pages=self.oracle.independent_lower_layout(renderer,cn,en)
        self.assertTrue(all(any(not e['glyph']['char'].isspace() for e in row['cn']) and any(not e['glyph']['char'].isspace() for e in row['en']) for row in pages.pairs))
        self.assertEqual(sorted(e['en_index'] for p in pages for e in p if 'en_index' in e),list(range(5)))

    def test_all_closing_source_run_can_split_only_for_necessary_line_balance(self):
        renderer=self.renderer()
        renderer.fonts[12][2]['…']=0;renderer.fonts[12][1]['…']=12
        renderer.fonts[8][2]['.']=0;renderer.fonts[8][1]['.']=4
        cn,en=self.plans('…'*22,'.'*34)
        _,pages=self.oracle.independent_lower_layout(renderer,cn,en)
        self.assertTrue(all(row['cn'] and row['en'] for row in pages.pairs))
        self.assertEqual(sorted(e['en_index'] for p in pages for e in p if 'en_index' in e),list(range(34)))


if __name__=='__main__':unittest.main()
