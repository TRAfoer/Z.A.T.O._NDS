from pathlib import Path
import importlib.util,json,sys,unittest,struct,zlib,shutil,subprocess,tempfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'nds/tools'))
from pack_story_script import parse_transition,parse_control,parse_assignment,compile_controls,compile_story,encode

class StoryControlTests(unittest.TestCase):
    def test_transition_names_and_authored_duration(self):
        self.assertEqual(parse_transition('None'),dict(kind='transition',type=0,ticks=0))
        self.assertEqual(parse_transition('dissolve'),dict(kind='transition',type=1,ticks=30))
        self.assertEqual(parse_transition('Dissolve (1.0)'),dict(kind='transition',type=1,ticks=60))
        self.assertEqual(parse_transition('Pause(19)'),dict(kind='wait',ticks=1140,hard=False))
        self.assertEqual(parse_transition('hpunch')['ticks'],17) # source .275 s, ceil to VBlank
        for name in ('ease','easeoutleft','easeinright','easeoutright','easeinleft','fade','vpunch'):
            self.assertEqual(parse_transition(name)['kind'],'transition')
        for bad in ('Dissolve(foo)','Dissolve(1+1)','eval("dissolve")','unknown','Dissolve(-1)'):
            with self.assertRaises(ValueError):parse_transition(bad)

    def test_closed_control_grammar_preserves_indefinite_and_parenthesized_pause(self):
        self.assertEqual(parse_control('pause (1.0)'),dict(op='WAIT',a=60,b=0))
        self.assertEqual(parse_control('pause'),dict(op='WAIT',a=-1,b=0))
        self.assertEqual(parse_control('nvl clear'),dict(op='CLEAR'))
        self.assertEqual(parse_control('window hide'),dict(op='WINDOW',a=0))
        self.assertEqual(parse_control('show screen date7'),dict(op='DATE',name='date7',show=True))
        self.assertEqual(parse_control('hide screen date3'),dict(op='DATE',name='date3',show=False))
        for bad in ('pause foo','pause(-1)','show screen anything','window auto','nvl clear; exec(1)'):
            with self.assertRaises(ValueError):parse_control(bad)

    def test_literal_assignment_whitelist(self):
        self.assertEqual(parse_assignment('quick_menu = False'),dict(op='QUICK',a=0))
        self.assertEqual(parse_assignment('persistent.game_completed = True'),dict(op='UNLOCK',name='game_completed',a=1))
        self.assertEqual(parse_assignment('persistent.hug_unlocked = True'),dict(op='UNLOCK',name='hug_unlocked',a=1))
        for bad in ('persistent.hug_unlocked = f()','foo = True','persistent.other = True','quick_menu = 1','quick_menu = True\nfoo()'):
            with self.assertRaises(ValueError):parse_assignment(bad)

    @unittest.skipUnless((ROOT/'nds/artifacts/full_story_ir.json').exists(),'full IR not generated')
    def test_every_full_source_control_is_converted_without_a_fallback(self):
        ir=json.loads((ROOT/'nds/artifacts/full_story_ir.json').read_text(encoding='utf-8'))
        cues=json.loads((ROOT/'nds/artifacts/full_story_audio_cues.json').read_text(encoding='utf-8'))
        result=compile_controls(ir,cues)
        self.assertEqual(len(result['audio_bindings']),399)
        self.assertEqual(len(result['unlocks']),36)
        self.assertEqual(result['attached_transitions'],19)
        self.assertEqual(result['counts']['python'],46) # 36 unlock + 10 quick_menu; hard waits already lifted
        self.assertEqual(result['counts']['with'],1438)
        self.assertEqual(result['counts']['jump'],9)
        self.assertEqual(result['counts']['return'],1)
        self.assertFalse(result['diagnostics'])

    def test_full_story_instruction_coverage_with_explicit_mock_subsystem_ids(self):
        ir=json.loads((ROOT/'nds/artifacts/full_story_ir.json').read_text(encoding='utf-8'))
        cues=json.loads((ROOT/'nds/artifacts/full_story_audio_cues.json').read_text(encoding='utf-8'))
        special=json.loads((ROOT/'nds/artifacts/scene_gfx_manifest.json').read_text(encoding='utf-8'))
        source=[e for c in ir['labels'] for e in c['events']]
        tags={name:i for i,name in enumerate(sorted({e['image'].split()[0] for e in source if e['kind'] in ('show','hide','scene')}))}
        gfx=dict(tag_to_id=tags,show_keys={f"{Path(e['source_file']).stem}:{e['line']}":dict(image_id=0,tag_id=tags[e['image'].split()[0]],placement=-1,program_id=0,behind_tag=-1) for e in source if e['kind'] in ('show','scene')})
        literals={str(e['source_event_id']):11951+i for i,e in enumerate(e for e in source if e['kind']=='show' and e['image']=='text')}
        text=dict(literal_event_ids=literals,date_ids={f'date{i}':11967+i for i in range(1,8)})
        events,chapters,coverage=compile_story(ir,compile_controls(ir,cues),text,gfx,special)
        self.assertEqual(len(chapters),10)
        self.assertEqual([e['a'] for e in events if e['name']=='TEXT'],list(range(11951)))
        self.assertEqual(len(coverage),17606)
        self.assertEqual(sum(e['status']=='promoted_init' for e in coverage),24)
        self.assertEqual(sum(e['status']=='unreachable_after_jump' for e in coverage),8)
        self.assertEqual(len([e for e in events if e['name']=='LITERAL']),17)
        self.assertEqual(len([e for e in events if e['name']=='DATE']),14)
        for row in events:
            if row['name']=='JUMP':self.assertEqual(row['a'],chapters[row['b']]['first_pc'])
        packed=encode(events,chapters,11975,17574)
        self.assertEqual(packed[:8],b'ZSTORY01')
        self.assertEqual(struct.unpack_from('<I',packed,36)[0],zlib.crc32(packed[64:]))

class StoryNativeLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.folder=Path(cls.temp.name)
        source=cls.folder/'probe.c';cls.exe=cls.folder/'probe.exe'
        source.write_text('''#include "story_script.h"
int main(int argc,char**argv){StoryScript s={0};if(argc<2||argc>3)return 9;
if(!story_script_open(&s,argv[1]))return s.data||s.events||s.chapters||s.count?8:2;
if(argc==2&&(s.chapter_count!=1||s.text_count!=1||s.source_count!=2||s.count!=2))return 7;
if(argc==3&&(s.chapter_count!=10||s.text_count!=11975||s.source_count!=17574||s.count<17000))return 5;
story_script_close(&s);return s.data||s.count?6:0;}
''')
        compiled=subprocess.run([shutil.which('gcc'),'-std=c11','-O2','-Wall','-Wextra','-Werror',
            '-I'+str(ROOT/'nds/source'),str(ROOT/'nds/source/story_script.c'),str(source),'-o',str(cls.exe)],capture_output=True,text=True)
        if compiled.returncode:raise AssertionError(compiled.stderr)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def pack(self,events=None):
        events=events or [(1,0,72,0,0,0,0),(19,0,75,0,0,0,0)]
        payload=b''.join(struct.pack('<HHI4i',*e) for e in events)
        co=64+len(payload)
        payload+=struct.pack('<4I16s',0,len(events),0,1,b'start\0')
        return bytearray(struct.pack('<8s14I',b'ZSTORY01',1,len(events),1,24,64,co,64+len(payload),zlib.crc32(payload),1,2,0,0,0,0)+payload)
    def check(self,data,expected):
        path=self.folder/'test.bin';path.write_bytes(data)
        self.assertEqual(subprocess.run([str(self.exe),str(path)]).returncode,expected)
    def test_hand_written_native_script(self):self.check(self.pack(),0)
    @unittest.skipUnless((ROOT/'nds/nitrofs/story/script.bin').exists(),'final subsystem packs not generated')
    def test_actual_full_source_script_native_load(self):
        p=subprocess.run([str(self.exe),str(ROOT/'nds/nitrofs/story/script.bin'),'full'])
        self.assertEqual(p.returncode,0)
    def test_corrupt_native_script_rejected_and_state_cleared(self):
        base=self.pack()
        for n in (0,39,63,64,len(base)-1):self.check(base[:n],2)
        bad=base.copy();bad[-1]^=1;self.check(bad,2)
        for offset,value in ((12,0xffffffff),(16,0),(20,25),(24,65),(28,0),(40,0),(48,1)):
            bad=base.copy();struct.pack_into('<I',bad,offset,value);self.check(bad,2)
        for ev in [
            [(1,0,72,1,0,0,0),(19,0,75,0,0,0,0)],
            [(5,0,72,-2,0,0,0),(19,0,75,0,0,0,0)],
            [(18,0,72,999,0,0,0),(19,0,75,0,0,0,0)],
            [(1,0,72,0,0,0,0),(6,0,75,1,30,0,0)]]:
            self.check(self.pack(ev),2)

if __name__=='__main__':unittest.main()
