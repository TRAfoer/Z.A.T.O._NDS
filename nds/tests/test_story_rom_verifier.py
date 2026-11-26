"""The RAM observer defers only transient mid-commit pc/command snapshots."""
from pathlib import Path
import sys,unittest,struct,json
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from verify_story_rom import CommitSampler,FullVerifier,ManualController,NativeSession,NONE,presentation_entries,GlyphCoverage,WireRecords

class CommitSamplerTests(unittest.TestCase):
    events=[{'name':'TEXT','a':0},{'name':'TEXT','a':1},{'name':'END','a':0}]
    def state(self,pc,n,frame):return {'s':{'pc':pc,'commands':n},'core_frame':frame}
    def test_transient_pc_store_before_command_store_is_retried(self):
        s=CommitSampler();self.assertTrue(s.accept(self.state(1,1,10),self.events,[0,1,2]))
        self.assertFalse(s.accept(self.state(2,1,11),self.events,[0,1,2]))
        self.assertTrue(s.accept(self.state(2,2,12),self.events,[0,1,2]));self.assertEqual(s.deferred,1)
    def test_persistent_pc_mismatch_is_a_failure(self):
        s=CommitSampler()
        for frame in range(10,13):self.assertFalse(s.accept(self.state(2,1,frame),self.events,[0,1,2]))
        with self.assertRaisesRegex(AssertionError,'persistent compiled instruction mismatch'):
            s.accept(self.state(2,1,13),self.events,[0,1,2])
    def test_jump_target_is_checked(self):
        s=CommitSampler();self.assertTrue(s.accept(self.state(2,1,1),[{'name':'JUMP','a':2}], [0]))

class ScreenshotCacheTests(unittest.TestCase):
    def test_repeated_portrait_does_not_compare_an_unrelated_old_video_frame(self):
        verifier=FullVerifier.__new__(FullVerifier)
        verifier.text={'records':[{'portrait_id':1,'char_count':3,'mode':1}]}
        verifier.portrait_names={1:'neutral'};verifier.capture_keys={'asya_neutral'}
        verifier.session=SimpleNamespace(active_layers=lambda:[])
        verifier.gfx={'images':[]}
        state={'s':{'text_id':0,'special_group':NONE,'visible':3,'pc':1},
               'f':{'date_id':NONE,'literal_id':NONE,'blocked':True,'source_auto':False,'pc':1},
               'transition':{'duration':0}}
        self.assertIs(verifier.captures(state),state)

class ManualInputTests(unittest.TestCase):
    def test_scrolling_driver_waits_for_real_viewport_coverage_before_A_edge(self):
        driver=ManualController();record={'fields':[0,4,1,0,1,1],'limits':[4],'pauses':[]}
        session=SimpleNamespace(records={8192:record})
        def state(frame,covered):
            s=dict(text_id=8192,page=0,pc=12177,visible=4,frames=frame,ticks=frame,text_mode=2,scroll_covered=covered)
            f=dict(s,blocked=True,waiting=False,source_auto=False,paused=False,pause_index=0,bilingual=False,local=10,scrolling=True)
            return {'s':s,'f':f,'transition':{'duration':0}}
        self.assertEqual(driver.keys(session,state(10,0)),())
        self.assertEqual(driver.keys(session,state(11,0)),())
        self.assertEqual(driver.keys(session,state(12,4)),())
        self.assertEqual(driver.keys(session,state(13,4)),('A',))
    def test_title_start_holds_A_until_ack_then_releases_through_input_irq(self):
        session=NativeSession.__new__(NativeSession);calls=[]
        states=iter([{'shell':{'active':1,'mode':0},'input_vblanks':1},
            {'shell':{'active':0,'mode':0},'input_vblanks':2},
            {'shell':{'active':0,'mode':0},'input_vblanks':2},
            {'shell':{'active':0,'mode':0},'input_vblanks':3}])
        def step(keys=()):calls.append(tuple(keys));return next(states)
        session.step=step
        result=session.enter_story({'shell':{'active':1,'mode':0}})
        self.assertEqual(result['input_vblanks'],3)
        self.assertEqual(calls,[('A',),('A',),(),()])
    def test_legacy_rom_boot_does_not_invent_title_input(self):
        session=NativeSession.__new__(NativeSession);state={'s':{}}
        session.step=lambda *_:self.fail('legacy boot must not press title keys')
        self.assertIs(session.enter_story(state),state)
    def test_paired_page_uses_bilingual_cap_instead_of_cn_only_layout(self):
        driver=ManualController();record={'fields':[0,2,2,0,1,2],'limits':[2],
            'limits_cn':[2],'limits_bi':[1,2],'pauses':[]}
        session=SimpleNamespace(records={0:record})
        s={'text_id':0,'page':0,'pc':1,'visible':1,'frames':10,'ticks':10}
        f=dict(s,blocked=True,waiting=False,source_auto=False,paused=False,pause_index=0,bilingual=True,local=1)
        state={'s':s,'f':f,'transition':{'duration':0}}
        self.assertEqual(driver.keys(session,state),())
        s['frames']=11
        self.assertEqual(driver.keys(session,state),('A',))
    def test_one_A_per_committed_page_even_while_cue_loading_keeps_old_page_visible(self):
        driver=ManualController();record={'fields':[0,2,2,0,1,1],'limits':[2],'pauses':[]}
        session=SimpleNamespace(records={0:record,1:record})
        def state(frame,id=0,pc=1):
            s={'text_id':id,'page':0,'pc':pc,'visible':2,'frames':frame,'ticks':frame}
            f=dict(s,blocked=True,waiting=False,source_auto=False,paused=False,pause_index=0,bilingual=False,local=2)
            return {'s':s,'f':f,'transition':{'duration':0}}
        self.assertEqual(driver.keys(session,state(10)),())
        self.assertEqual(driver.keys(session,state(11)),('A',))
        for frame in range(12,30):self.assertEqual(driver.keys(session,state(frame)),('A',))
        self.assertEqual(driver.keys(session,state(30,1,2)),())
        self.assertEqual(driver.keys(session,state(31,1,2)),())
        self.assertEqual(driver.keys(session,state(32,1,2)),('A',))
    def test_release_requires_an_actual_input_irq_sample(self):
        driver=ManualController();driver.outstanding=(0,0,1)
        state={'s':{'text_id':1,'page':0,'pc':2,'frames':20},'f':{},'transition':{},'input_vblanks':77}
        self.assertEqual(driver.keys(None,state),())
        state['s']['frames']=21;self.assertEqual(driver.keys(None,state),())
        self.assertEqual(driver.release_vblank,77)
    def test_odd_snapshot_retries_keep_the_same_held_key(self):
        session=NativeSession.__new__(NativeSession);calls=[]
        session.emu=SimpleNamespace(run=lambda frames,keys:calls.append(tuple(keys)))
        audio={'errors':0,'music_underruns':0,'sfx_underruns':0}
        states=iter([{'a':audio,'coherent':False},{'a':audio,'coherent':False},{'a':audio,'coherent':True}])
        session.snapshot=lambda:next(states)
        self.assertTrue(session.step(('A',))['coherent']);self.assertEqual(calls,[('A',),('A',),('A',)])

class PresentationRingTests(unittest.TestCase):
    def test_scroll_first_ack_carries_its_own_view_not_a_later_status(self):
        raw=bytearray(1936);struct.pack_into('<4I',raw,0,0x5054535a,32,1,0)
        struct.pack_into('<15I',raw,16,1,500,974,1,85,17,59,14065,32,75,172,335,2,1,85)
        head,entries=presentation_entries(raw,0)
        self.assertEqual(head,1);self.assertEqual(entries[0]['scroll'],2)
        self.assertEqual(entries[0]['scroll_manual'],1);self.assertEqual(entries[0]['scroll_covered'],85)
        self.assertTrue(entries[0]['flags']&32)
    def raw(self,head,serials):
        data=bytearray(struct.pack('<4I',0x5054535a,32,head,0)+bytes(1024))
        for serial in serials:struct.pack_into('<8I',data,16+((serial-1)&31)*32,serial,12,0,0,2,2,3,99)
        return data
    def test_wrapped_slots_preserve_all_new_entries(self):
        head,entries=presentation_entries(self.raw(34,[32,33,34]),31)
        self.assertEqual(head,34);self.assertEqual([e['serial'] for e in entries],[32,33,34])
    def test_overrun_is_strict_failure(self):
        with self.assertRaisesRegex(AssertionError,'overrun'):presentation_entries(self.raw(33,range(2,34)),0)
    def test_head_cannot_publish_partial_entry(self):
        with self.assertRaisesRegex(AssertionError,'partial presentation'):presentation_entries(self.raw(1,[]),0)

class GlyphCoverageTests(unittest.TestCase):
    def test_scroll_visible_count_cannot_count_glyphs_outside_actual_viewport(self):
        coverage=GlyphCoverage({0:self.record([{0,1},{2,3}])})
        entry={**self.entry(1,4,19),'cn_first':2,'cn_end':4,'en_first':0,'en_end':0}
        coverage.present(entry)
        with self.assertRaisesRegex(AssertionError,'unpresented new Chinese glyphs'):coverage.finish(0)
        coverage.present({**entry,'cn_first':0,'cn_end':2})
        coverage.finish(0);self.assertEqual(coverage.glyphs,4)
    def test_full_bilingual_coverage_requires_each_new_english_glyph_on_its_page(self):
        record={'fields':[0,2,1,0,1,1],'pages':[set()],'pages_bi':[set()],
            'commands_en':[{'gid':0,'page':0,'index':2},{'gid':1,'page':0,'index':3},{'gid':2,'page':0,'index':4}],
            'english_thresholds':[0,1,2],'manifest':{'new_en_start':1}}
        coverage=GlyphCoverage({0:record});coverage.bilingual=True
        coverage.present(self.entry(0,1,11))
        with self.assertRaisesRegex(AssertionError,'English'):coverage.finish(0)
        coverage.present(self.entry(0,2,11));coverage.finish(0)
        self.assertEqual(coverage.english_glyphs,2)
    def test_format4_wire_keeps_both_layouts_and_english_projection(self):
        from nds.tests.test_story_text_format4 import fixture
        metadata={'source_line':1,'char_count':2,'duration_ticks':1,'pause_count':0,
            'pages_cn':1,'pages_bi':2,'mode':0,'continuation':0,'new_char_start':0}
        record=WireRecords(bytes(fixture()),{'records':[metadata]}).get(0)
        self.assertEqual(record.get('limits_bi'),[1,2])
        self.assertEqual(record.get('english_thresholds'),[1,1,2])
        self.assertEqual(record.get('pages_bi'),[{0},{1}])
    def record(self,pages):return {'fields':[0,4,0,0,2],'pages':pages}
    def entry(self,page,visible,flags=3):return {'text_id':0,'page':page,'visible':visible,'flags':flags}
    def test_final_char_count_does_not_hide_missing_prior_page(self):
        coverage=GlyphCoverage({0:self.record([{0,1},{2,3}])})
        coverage.present(self.entry(1,4))
        with self.assertRaisesRegex(AssertionError,'unpresented new Chinese glyphs'):coverage.finish(0)
        coverage.present(self.entry(0,2));coverage.finish(0);self.assertEqual(coverage.glyphs,4)
    def test_continuation_requires_only_new_tail_and_fast_batch_is_allowed(self):
        coverage=GlyphCoverage({0:self.record([set(),{2,3}])})
        coverage.present(self.entry(1,4));coverage.finish(0);self.assertEqual(coverage.glyphs,2)
    def test_hidden_body_does_not_count_but_centered_upper_does(self):
        coverage=GlyphCoverage({0:self.record([{0,1},set()])})
        coverage.present(self.entry(0,2,1))
        with self.assertRaises(AssertionError):coverage.finish(0)
        coverage.present(self.entry(0,2,5));coverage.finish(0)
    def test_real_auto_continuation_new_page_start_is_preserved(self):
        root=Path(__file__).resolve().parents[1]
        manifest=json.loads((root/'artifacts/story_text_manifest.json').read_text(encoding='utf8'))
        wire=WireRecords((root/'nitrofs/story/text.bin').read_bytes(),manifest)
        pages=wire.get(4298)['pages']
        self.assertFalse(pages[0]);self.assertEqual(pages[1],set(range(356,360)))

if __name__=='__main__':unittest.main()
