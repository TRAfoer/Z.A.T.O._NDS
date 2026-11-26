"""Trilingual poetry source identity, grouped pagination and body policy."""
from pathlib import Path
import importlib,inspect,json,struct,sys,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'nds/tools'))
sys.path.insert(0,str(ROOT/'experiments/story_lab'))
sys.path.insert(0,str(ROOT/'experiments/asset_lab'))
from anim_player import RichText
import pack_story_text as pack
from paired_layout import layout_cn,layout_paired
from story_typography import StoryTypographyRenderer

def glyphs(text,font):
    plan=RichText(text,base_cps=0)
    for g in plan.glyphs:g['font']=font
    return plan.glyphs
def measure(g,font):return font,font,font

class PoemProducerTests(unittest.TestCase):
    def layout(self):
        self.assertTrue((ROOT/'nds/tools/poem_layout.py').is_file(),'dedicated poem producer is missing')
        return importlib.import_module('poem_layout').poem_pages
    def test_eight_groups_balance_four_plus_four_and_center_both_screens(self):
        layout=self.layout();ru=glyphs('\n'.join('Р' for _ in range(8)),12);en=glyphs('\n'.join('E' for _ in range(8)),8);cn=glyphs('\n'.join('中' for _ in range(8)),12)
        result=layout(ru,en,cn,measure)
        self.assertEqual(len(result['lower_pages']),2);self.assertEqual(len(result['upper_pages']),2)
        self.assertEqual([len([g for g in result['groups'] if g['page']==i]) for i in range(2)],[4,4])
        for page in result['lower_pages']:
            ink=[e for e in page if e['glyph']['char']!='\n'];self.assertEqual([e['y'] for e in ink],[66,82,98,114]);self.assertTrue(all(e['x']==122 for e in ink))
        ink=[e for e in result['upper_pages'][0] if e['glyph']['char']!='\n'];self.assertEqual([e['y'] for e in ink],[46,60,72,86,98,112,124,138])
        self.assertEqual(sorted(e['index'] for p in result['upper_pages'] for e in p),list(range(len(ru)+len(en))))
        self.assertEqual(sorted(e['index'] for p in result['lower_pages'] for e in p),list(range(len(cn))))
    def test_fourteen_groups_keep_two_blank_lines_and_five_five_four_pages(self):
        layout=self.layout();lines=['中']*14;lines[4]=lines[9]='';cn=glyphs('\n'.join(lines),12);ru=glyphs('\n'.join(x.replace('中','Р') for x in lines),12);en=glyphs('\n'.join(x.replace('中','E') for x in lines),8)
        result=layout(ru,en,cn,measure);self.assertEqual([sum(g['page']==p for g in result['groups']) for p in range(3)],[5,5,4])
        self.assertEqual(len(result['groups']),14)
        self.assertEqual(sorted(e['index'] for p in result['upper_pages'] for e in p),list(range(len(ru)+len(en))))
        self.assertEqual(sum(e['glyph']['char']=='\n' for p in result['lower_pages'] for e in p),13)
    def test_line_mismatch_and_overwide_line_are_diagnostics_not_silent_wrap(self):
        layout=self.layout()
        with self.assertRaises(ValueError):layout(glyphs('Р\nР',12),glyphs('E',8),glyphs('中',12),measure)
        with self.assertRaises(ValueError):layout(glyphs('Р'*21,12),glyphs('E',8),glyphs('中',12),measure)
    def test_ordinary_defaults_expand_without_changing_explicit_upper_rectangle(self):
        for func in (layout_cn,layout_paired,StoryTypographyRenderer.layout):
            params=inspect.signature(func).parameters
            self.assertEqual((params['x'].default,params['y'].default,params['width'].default),(20,30,216))
        p=RichText('中',base_cps=0);r=StoryTypographyRenderer();e=r.layout(p,x=8,y=24,width=240,bottom=168)[0][0]
        self.assertEqual((e['x'],e['y']),(8,24))
    def test_source_pairing_keeps_ids_and_all_three_original_streams(self):
        ir=json.loads((ROOT/'nds/artifacts/full_story_ir.json').read_text(encoding='utf8'));records,*_=pack.prepare_records(ir)
        for first,last,chapter,ru_line,en_line,count in [(11952,11953,'ep1',2864,2871,8),(11960,11961,'ep2d7',2123,2143,14)]:
            for id,stage,line in [(first,'russian',ru_line),(last,'english',en_line)]:
                record=records[id];self.assertIn('poem',record,'four source literals are not linked to trilingual poems')
                poem=record['poem'];self.assertEqual(poem['source_record_ids'],[first,last]);self.assertEqual(poem['stage'],stage)
                self.assertEqual((record['event']['chapter'],record['event']['line']),(chapter,line))
                self.assertIs(record['plan'],poem['chinese_plan']);self.assertEqual(record['original_plan'].text,poem['russian_plan'].text+poem['english_plan'].text)
                self.assertEqual(len(poem['russian_plan'].text.split('\n')),count);self.assertEqual(len(poem['english_plan'].text.split('\n')),count);self.assertEqual(len(poem['chinese_plan'].text.split('\n')),count)
                self.assertEqual(record['timeline'],[0]*len(record['plan'].glyphs));self.assertEqual(record['duration'],0);self.assertEqual(record['pauses'],[])
    def test_real_source_four_record_compile_matches_kind4_wire_contract(self):
        ir=json.loads((ROOT/'nds/artifacts/full_story_ir.json').read_text(encoding='utf8'))
        records,names,literals,locations,dates=pack.prepare_records(ir)
        selected=[r for r in records if 'poem' in r]
        before={path:path.read_bytes() for path in (pack.DEST,pack.MANIFEST)}
        with patch.object(pack,'prepare_records',return_value=(selected,names,literals,locations,dates)):
            context=pack.compile_records(ir)
        self.assertEqual([r['id'] for r in context['details']],[11952,11953,11960,11961])
        for record,detail,(stored,flag,size,crc) in zip(selected,context['details'],context['payload']):
            raw=stored if flag else pack.unlz10(stored);f=struct.unpack_from('<24H',raw)
            self.assertEqual(len(raw),size);self.assertEqual(f[16],4);self.assertEqual(f[6],3)
            self.assertEqual(f[4],f[5]);self.assertEqual(f[4],2 if record['id']<11960 else 3)
            self.assertEqual((f[2],f[3],f[7],f[8],f[15]),(0,0,0,0,0));self.assertEqual(f[18:24],(0,)*6)
            self.assertEqual(f[1],len(record['poem']['chinese_plan'].glyphs))
            self.assertEqual(f[14],sum(len(record['poem'][key].glyphs) for key in ('russian_plan','english_plan')))
            self.assertEqual(detail['upper_kind'],4)
        for path,data in before.items():self.assertEqual(path.read_bytes(),data,'unit compile changed canonical pack')

if __name__=='__main__':unittest.main()
