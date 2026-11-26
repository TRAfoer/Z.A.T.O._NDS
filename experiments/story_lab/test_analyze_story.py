"""Source-backed, non-executing full-story intermediate representation checks."""
from pathlib import Path
import importlib.util
import json
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]


class FullStoryTests(unittest.TestCase):
    def test_every_mainline_translation_and_static_jump_is_retained(self):
        path=ROOT/'experiments/story_lab/analyze_story.py'
        self.assertTrue(path.is_file(),'full-story text IR is not implemented')
        spec=importlib.util.spec_from_file_location('full_story_analyzer_test',path)
        analyzer=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(analyzer)
        with patch('pickle.load',side_effect=AssertionError('unsafe pickle execution')),patch('pickle.loads',side_effect=AssertionError('unsafe pickle execution')):
            result=analyzer.build_ir()
        self.assertEqual(result['summary']['say_count'],11951)
        self.assertEqual(result['summary']['translated_count'],11951)
        self.assertEqual(result['summary']['runtime_event_count'],17574)
        self.assertEqual(result['summary']['unreachable_event_count'],8)
        self.assertEqual(result['summary']['say_with_transitions'],{'hpunch':2,'vpunch':17})
        self.assertEqual(result['flow']['mainline_chain'],['start','ep1']+[f'ep2d{i}' for i in range(1,8)]+['ep3'])
        says=[event for label in result['labels'] for event in label['events'] if event['kind']=='say']
        self.assertEqual([event['say_id'] for event in says],list(range(11951)))
        self.assertEqual([event['global_text_id'] for event in says],list(range(11951)))
        events=[event for label in result['labels'] for event in label['events']]
        self.assertEqual([event['source_event_id'] for event in events],list(range(17606)))
        self.assertEqual([event['runtime_event_id'] for event in events if event['runtime_eligible']],list(range(17574)))
        self.assertTrue(all(not event['runtime_eligible'] for event in events if event['kind']=='init' or event['unreachable_after_jump']))
        self.assertTrue(all(event['text'] is not None and event['translation_id'] for event in says))
        self.assertTrue(all(len(event['display']['timeline_ticks'])==event['display']['char_count'] for event in says))
        self.assertFalse(result['summary']['missing_translations'])
        full608=next(e for e in says if e['chapter']=='ep2d3' and e['line']==608)
        self.assertEqual(full608['display']['attributes'],[])
        self.assertEqual(result['frozen_ep2d3']['canonical_json_sha256'],'65b7332cc16347a0f1a6262b00c79a32ec741624cb5d66f42cebd881f2779976')
        for event in says:
            if event['who']=='extend':
                self.assertTrue(event['display']['continuation'])
                self.assertGreater(event['display']['new_char_start'],0)
        json.dumps(result,ensure_ascii=False)


if __name__=='__main__':unittest.main()
