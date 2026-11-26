"""Source-index preserving kinsoku and simultaneous paired-line contract."""
from pathlib import Path
import importlib.util
import unittest

ROOT=Path(__file__).resolve().parents[2]


def glyphs(text,language='cn',bold=False):
    return [dict(char=c,color=(255,255,255),scale=1.,bold=bold,italic=False,language=language) for c in text]


def measure(g,font=12):
    advance=font if ord(g['char'])>255 else font//2
    extra=int(g.get('bold',False))
    return (advance+extra,font+extra,advance)


class PairedLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=ROOT/'experiments/story_lab/paired_layout.py'
        if not path.exists():return
        spec=importlib.util.spec_from_file_location('paired_layout_test',path)
        cls.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.module)

    def need(self):self.assertTrue(hasattr(self,'module'),'paired source-aware layout is not implemented')

    def test_chinese_hangs_two_closing_marks_inside_reserved_pocket(self):
        self.need();g=glyphs('甲'*16+'，。'+'乙',bold=True);pages=self.module.layout_cn(g,measure)
        first=[e for e in pages[0] if e['y']==35]
        self.assertEqual(''.join(e['glyph']['char'] for e in first),'甲'*16+'，。')
        self.assertLessEqual(max(e['x']+e['ink_width'] for e in first),248)
        self.assertEqual([e['index'] for p in pages for e in p],list(range(len(g))))

    def test_three_marks_do_not_create_three_hanging_marks_or_drop_source(self):
        self.need();g=glyphs('甲'*16+'，。！'+'乙');pages=self.module.layout_cn(g,measure)
        lines={}
        for p in pages:
            for e in p:lines.setdefault((e['page'],e['y']),[]).append(e)
        for entries in lines.values():
            self.assertLessEqual(sum(e['x']+e['ink_width']>225 for e in entries),2)
            self.assertNotIn(entries[0]['glyph']['char'],'，。！')
        self.assertEqual([e['index'] for p in pages for e in p],list(range(len(g))))

    def test_portrait_blocks_only_first_two_physical_lines(self):
        self.need();pages=self.module.layout_cn(glyphs('甲'*80,bold=True),measure,portrait=True)
        for e in pages[0]:
            if e['y']<60:self.assertLessEqual(e['x']+e['ink_width'],190)
        third=[e for e in pages[0] if e['y']==65]
        self.assertGreater(max(e['x']+e['ink_width'] for e in third),190)

    def test_pairs_alternate_and_reveal_first_and_last_at_cn_endpoints(self):
        self.need();cn=glyphs('甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉。');en=glyphs("Please don't split our words, or lose their punctuation.",'en')
        result=self.module.layout_paired(cn,en,measure)
        self.assertEqual(sorted(e['index'] for p in result['cn_pages'] for e in p),list(range(len(cn))))
        self.assertEqual(sorted(e['index'] for p in result['en_pages'] for e in p),list(range(len(en))))
        for pair in result['pairs']:
            self.assertEqual(pair['en_y']-pair['cn_y'],14)
            c=[i for i in pair['cn_indices'] if cn[i]['char']!='\n'];e=[i for i in pair['en_indices'] if en[i]['char']!='\n']
            if c and e:
                self.assertEqual(result['en_reveal'][e[0]],c[-1]+1 if len(e)==1 else c[0]+1)
                self.assertEqual(result['en_reveal'][e[-1]],c[-1]+1)
        self.assertTrue(any(0<v<len(cn) for v in result['en_reveal']))

    def test_one_english_ink_character_finishes_with_last_chinese_character(self):
        self.need();result=self.module.layout_paired(glyphs('甲乙丙丁'),glyphs('I','en'),measure)
        self.assertEqual(result['en_reveal'],[4])

    def test_joint_capacity_exact_tie_chooses_later_break(self):
        self.need()
        from fractions import Fraction
        self.assertEqual(self.module._nearest([(2,2),(3,3)],15,Fraction(1,6)),3)

    def test_prepared_records_keep_event_partitions_for_extend_and_nvl(self):
        self.need()
        import json,sys
        sys.path.insert(0,str(ROOT/'nds/tools'))
        import pack_story_text as packer
        ir=json.loads((ROOT/'nds/artifacts/full_story_ir.json').read_text(encoding='utf8'))
        records,*_=packer.prepare_records(ir)
        self.assertEqual(len(records),11975)
        for record in records:
            if record['kind']!='say':continue
            self.assertIn('segments',record)
            self.assertIn('english_plan',record)
            self.assertEqual(record['segments'][-1]['cn_start'],record['new_char_start'])
            self.assertEqual(record['segments'][-1]['cn_end'],len(record['plan'].glyphs))
            self.assertEqual(record['segments'][-1]['en_end'],len(record['english_plan'].glyphs))
            self.assertEqual(record['segments'][-1]['source_event_id'],record['event']['source_event_id'])

    def test_english_apostrophes_and_words_remain_whole(self):
        self.need();en=glyphs("I don't think we're changing anyone's words. "+'Something '*12,'en')
        result=self.module.layout_paired(glyphs('甲'*60),en,measure)
        rows=[''.join(en[i]['char'] for i in p['en_indices']) for p in result['pairs']]
        for word in ("don't","we're","anyone's"):
            self.assertEqual(sum(word in row for row in rows),1,(word,rows))

    def test_hard_newlines_and_styles_are_not_deleted(self):
        self.need();cn=glyphs('甲乙\n丙丁');en=glyphs('First line.\nSecond line.','en',bold=True)
        result=self.module.layout_paired(cn,en,measure)
        self.assertEqual([e['glyph'] for p in result['cn_pages'] for e in p],cn)
        self.assertEqual([e['glyph'] for p in result['en_pages'] for e in p],en)
        left=next(p for p in result['pairs'] if 1 in p['cn_indices']);right=next(p for p in result['pairs'] if 3 in p['cn_indices'])
        self.assertNotEqual((left['page'],left['cn_y']),(right['page'],right['cn_y']))

    def test_blank_hardline_consumes_other_languages_whitespace_and_newline(self):
        self.need();cn=glyphs('\n甲');en=glyphs(' \nOne','en')
        result=self.module.layout_paired(cn,en,measure)
        self.assertEqual(result['pairs'][0]['cn_indices'],[0])
        self.assertEqual(result['pairs'][0]['en_indices'],[0,1])
        self.assertEqual(result['pairs'][1]['cn_indices'],[1])
        self.assertEqual(result['pairs'][1]['en_indices'],[2,3,4])

    def test_authored_newline_before_ellipsis_does_not_gain_a_blank_row(self):
        self.need();pages=self.module.layout_cn(glyphs('甲\n……乙'),measure)
        ellipsis=next(e for p in pages for e in p if e['index']==2)
        self.assertEqual(ellipsis['y'],50)

    def test_source_event_segments_keep_inherited_prefix_positions(self):
        self.need();first=dict(source_event_id=1,cn_start=0,cn_end=4,en_start=0,en_end=7)
        old=self.module.layout_paired(glyphs('甲乙丙丁'),glyphs('One two','en'),measure,segments=[first])
        second=dict(source_event_id=2,cn_start=4,cn_end=8,en_start=7,en_end=18)
        new=self.module.layout_paired(glyphs('甲乙丙丁戊己庚辛'),glyphs('One two three four','en'),measure,segments=[first,second])
        key=lambda e:(e['index'],e['x'],e['y'],e['page'])
        self.assertEqual([key(e) for p in old['cn_pages'] for e in p],[key(e) for p in new['cn_pages'] for e in p if e['index']<4])
        self.assertEqual([key(e) for p in old['en_pages'] for e in p],[key(e) for p in new['en_pages'] for e in p if e['index']<7])
        self.assertTrue(all(p['source_event_id'] in (1,2) for p in new['pairs']))
        self.assertTrue(all(v>=5 for v in new['en_reveal'][7:]))

    def test_short_extend_reuses_both_unfilled_lines_without_moving_prefix(self):
        self.need();first=dict(source_event_id=1,cn_start=0,cn_end=4,en_start=0,en_end=7)
        second=dict(source_event_id=2,cn_start=4,cn_end=8,en_start=7,en_end=18)
        result=self.module.layout_paired(glyphs('甲乙丙丁戊己庚辛'),glyphs('One two three four','en'),measure,segments=[first,second])
        self.assertEqual(len({(p['page'],p['cn_y']) for p in result['pairs']}),1,'short EXTEND must use the available paired row')

    def test_blank_break_does_not_measure_the_other_sides_unconsumed_word(self):
        self.need();old='Word '*9;new='Extraordinary'
        segments=[dict(source_event_id=1,cn_start=0,cn_end=4,en_start=0,en_end=len(old)),dict(source_event_id=2,cn_start=4,cn_end=6,en_start=len(old),en_end=len(old+new))]
        result=self.module.layout_paired(glyphs('甲乙丙丁\n戊'),glyphs(old+new,'en'),measure,segments=segments)
        body=next(e for page in result['cn_pages'] for e in page if e['index']==5)
        self.assertEqual(body['y'],60,'a blank break must attach to the old row, without consuming the next EN word')

    def test_available_ink_word_excludes_leading_whitespace_only_cuts(self):
        self.need();g=glyphs(' One two','en')
        cuts=self.module._candidates(g,0,len(g),measure,8,31,225,248,True)
        self.assertTrue(all(any(not x['char'].isspace() for x in g[:end]) for end,advance in cuts))

    def test_real197_and974_short_extensions_do_not_force_new_pairs(self):
        self.need()
        import json,sys
        sys.path.insert(0,str(ROOT/'nds/tools'));import pack_story_text as p
        from anim_story import _characters,read_compiled
        records,names,*_=p.prepare_records(json.loads((ROOT/'nds/artifacts/full_story_ir.json').read_text(encoding='utf8')))
        renderer=p.StoryRenderer(p.DEFAULT_GAME,_characters(p.DEFAULT_GAME,read_compiled(p.DEFAULT_GAME/'script.rpyc')))
        p.extend_fonts(renderer,records,names)
        for id,old_rows in ((197,5),(974,13)):
            with self.subTest(text_id=id):
                row=records[id];previous=records[id-1]
                old=self.module.layout_paired(previous['plan'].glyphs,previous['english_plan'].glyphs,renderer.metrics,previous['segments'])
                new=self.module.layout_paired(row['plan'].glyphs,row['english_plan'].glyphs,renderer.metrics,row['segments'])
                self.assertLess(len({(q['page'],q['cn_y']) for q in new['pairs']}),old_rows)
                key=lambda e:(e['index'],e['x'],e['y'],e['page'])
                self.assertEqual([key(e) for page in old['cn_pages'] for e in page],[key(e) for page in new['cn_pages'] for e in page if e['index']<len(previous['plan'].glyphs)])
                self.assertEqual(old['en_reveal'],new['en_reveal'][:len(previous['english_plan'].glyphs)])
        row=records[3803]
        result=self.module.layout_paired(row['plan'].glyphs,row['english_plan'].glyphs,renderer.metrics,row['segments'])
        self.assertEqual(len(result['cn_pages']),4)
        boundary=next(q for q in result['pairs'] if q['cn_start']==148)
        self.assertEqual(boundary['en_indices'],[432,433],'space plus newline must share the CN blank hardline')

    def test_bi_caps_are_monotonic_and_cover_en_thresholds_on_each_page(self):
        self.need();result=self.module.layout_paired(glyphs('甲'*300),glyphs('Some longer English text. '*70,'en'),measure,portrait=True)
        self.assertGreater(len(result['cn_pages']),1)
        self.assertEqual(result['bi_limits'],sorted(result['bi_limits']))
        self.assertEqual(result['bi_limits'][-1],300)
        for page,entries in enumerate(result['en_pages']):
            self.assertTrue(all(result['en_reveal'][e['index']]<=result['bi_limits'][page] for e in entries))
        for p in result['cn_pages']+result['en_pages']:
            self.assertTrue(all(e['x']+e['ink_width']<=190 if e['y']<60 else e['x']+e['ink_width']<=248 for e in p))

    def test_real_short_carry_and_single_word_do_not_leave_one_language_alone(self):
        self.need()
        import json,sys
        sys.path.insert(0,str(ROOT/'nds/tools'));import pack_story_text as p
        records,names,*_=p.prepare_records(json.loads((ROOT/'nds/artifacts/full_story_ir.json').read_text(encoding='utf8')))
        renderer=p.StoryRenderer(p.DEFAULT_GAME,p._characters(p.DEFAULT_GAME,p.read_compiled(p.DEFAULT_GAME/'script.rpyc')))
        p.extend_fonts(renderer,records,names)
        metadata=json.loads((ROOT/'nds/artifacts/story_text_manifest.json').read_text(encoding='utf8'))['records']
        for id in (49,78,227,1114,1424,10347,10472):
            with self.subTest(text_id=id):
                record=records[id]
                result=self.module.layout_paired(record['plan'].glyphs,record['english_plan'].glyphs,renderer.metrics,record['segments'],portrait=bool(metadata[id]['portrait_id']))
                rows={}
                for pair in result['pairs']:
                    row=rows.setdefault((pair['page'],pair['cn_y'],pair['en_y']),[[],[]])
                    row[0].extend(i for i in pair['cn_indices'] if not record['plan'].glyphs[i]['char'].isspace())
                    row[1].extend(i for i in pair['en_indices'] if not record['english_plan'].glyphs[i]['char'].isspace())
                self.assertTrue(all(bool(c)==bool(e) for c,e in rows.values()),rows)
                for cn_indices,en_indices in rows.values():
                    if cn_indices and en_indices:
                        en_cap=result['en_reveal'][en_indices[-1]]
                        self.assertEqual(record['timeline'][cn_indices[-1]],record['timeline'][en_cap-1])
                self.assertEqual([e['index'] for page in result['cn_pages'] for e in page],list(range(len(record['plan'].glyphs))))
                self.assertEqual([e['index'] for page in result['en_pages'] for e in page],list(range(len(record['english_plan'].glyphs))))


if __name__=='__main__':unittest.main()
