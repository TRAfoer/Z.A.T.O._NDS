"""Source-backed regressions for the passive ep2d3 preview extraction."""
import json
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

import anim_story


class StoryExtractionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.story = anim_story.build_story()

    def at(self, line):
        return next(e for e in self.story['events'] if e['line'] == line)

    def test_preserves_actual_label_order_and_entire_requested_span(self):
        story = self.story
        source = anim_story.read_compiled(anim_story.DEFAULT_GAME / 'ep2d3.rpyc')
        block = next(anim_story.attrs(n)['block'] for n in source
                     if n.cls == 'renpy.ast.Label' and anim_story.attrs(n).get('name') == 'ep2d3')
        expected = [i for i, n in enumerate(block)
                    if 240 <= anim_story.attrs(n).get('linenumber', 0) <= 815]
        self.assertEqual([e['source_index'] for e in story['events']], expected)
        says = [e for e in story['events'] if e['kind'] == 'say']
        self.assertEqual(len(says), sum(n.cls == 'renpy.ast.Say' for n in block
                         if 240 <= anim_story.attrs(n).get('linenumber', 0) <= 815))
        self.assertEqual(says[0]['line'], 240)
        self.assertEqual(says[-1]['line'], 815)
        self.assertTrue(all(e['text'] is not None and e['translation_id'] for e in says))
        self.assertTrue(all(e['source_file'] == 'game/ep2d3.rpy' for e in story['events']))

    def test_all_atl_cycles_preserve_frames_and_exact_durations(self):
        sequences = self.story['sequences']
        self.assertEqual(set(sequences), {'hands', 'crack', 'windo', 'ghost', 'tface', 'deface', 'ilooked', 'iss'})
        self.assertEqual([f['image'] for f in sequences['hands']['frames']],
            ['hands1', 'carfix_red', 'hands2', 'hands3', 'hands4', 'carfix_red', 'hands5', 'carfix_red', 'hands6'])
        for name, count, duration in [('hands', 9, .03), ('crack', 6, .1), ('windo', 6, .1),
                ('ghost', 5, .1), ('tface', 6, .1), ('deface', 6, .1), ('ilooked', 9, .05), ('iss', 10, .05)]:
            self.assertTrue(sequences[name]['repeat'])
            self.assertEqual(len(sequences[name]['frames']), count)
            self.assertEqual([f['duration'] for f in sequences[name]['frames']], [duration] * count)
        self.assertEqual(sequences['iss']['frames'][-1]['image'], 'is10')

    def test_effect_layers_mirror_and_ease_target_are_structured(self):
        self.assertEqual(self.at(617)['properties']['xzoom'], -1)
        self.assertEqual(self.at(619)['behind'], ['carfix_red'])
        hands = self.at(620)
        self.assertEqual(hands['at'], ['shaking'])
        self.assertEqual(hands['properties']['anchor'], [.5, .5])
        self.assertEqual(hands['properties']['pos'], [.5, .5])
        self.assertEqual(hands['properties']['zoom'], 1.2)
        self.assertEqual(hands['properties']['transitions'], [
            {'warper': 'easein', 'duration': 10.0, 'properties': {'zoom': 1.3}, 'source_line': 624}])
        self.assertEqual(self.at(656)['behind'], ['bodyshop'])
        self.assertEqual(self.at(656)['properties']['blend'], 'add')

    def test_hard_waits_remain_distinct_from_text_tags_and_transitions(self):
        for line, seconds in [(604, .5), (625, .45), (695, 7.0)]:
            self.assertEqual(self.at(line)['kind'], 'wait')
            self.assertEqual(self.at(line)['seconds'], seconds)
            self.assertTrue(self.at(line)['hard'])
        self.assertEqual(self.at(609)['seconds'], 1)
        self.assertFalse(self.at(609)['hard'])
        self.assertIn('{w=0.3}', self.at(629)['text'])
        self.assertIn('{nw}', self.at(629)['text'])
        self.assertEqual(self.at(691)['expr'], 'Pause(4)')
        self.assertEqual(self.at(793)['expr'], 'Dissolve(1.5)')
        self.assertEqual(self.at(795)['expr'], 'Dissolve(1.5)')

    def test_effect_trigger_order_and_audio_commands_are_retained(self):
        operations = [(e['kind'], e['line'], e.get('image')) for e in self.story['events']
                      if 599 <= e['line'] <= 686 and e['kind'] in ('show', 'scene', 'hide')]
        self.assertEqual(operations, [
            ('scene', 603, 'iss'), ('scene', 605, 'black'), ('hide', 606, 'iss'),
            ('show', 614, 'carfix_red'), ('show', 615, 'ghost'), ('show', 616, 'tface'),
            ('show', 617, 'deface'), ('show', 619, 'crack'), ('show', 620, 'hands'), ('hide', 626, 'hands'),
            ('hide', 647, 'carfix_red'), ('hide', 648, 'ghost'), ('hide', 649, 'tface'),
            ('hide', 650, 'crack'), ('hide', 651, 'deface'), ('show', 653, 'ilooked'),
            ('show', 654, 'deface'), ('show', 655, 'bodyshop'), ('show', 656, 'crack'),
            ('hide', 683, 'ilooked'), ('hide', 684, 'deface'), ('hide', 685, 'bodyshop'), ('hide', 686, 'crack')])
        self.assertEqual(self.at(599)['value'], 'play sound "audio/glitch.mp3"')
        self.assertEqual(self.at(613)['value'], 'play music "audio/paradox.mp3" volume 1.4')
        self.assertEqual(self.at(694)['value'], 'play music "audio/heart.ogg" fadein 5.0')

    def test_translations_match_comment_and_speaker_despite_nvl_ref_offset(self):
        oil = self.at(697)
        self.assertEqual(oil['original'], 'It smells of oil.')
        self.assertEqual(oil['text'], '有股汽油的味道，')
        self.assertEqual(oil['translation_source_line'], 696)
        self.assertEqual(oil['translation_line'], 1437)
        self.assertEqual(self.at(627)['attributes'], ['red'])
        self.assertFalse([d for d in self.story['diagnostics'] if d['code'] == 'missing_translation'])

    def test_literal_overlays_opening_state_and_checkpoint_lines(self):
        self.assertEqual(self.at(690)['image'], 'text')
        self.assertEqual(self.at(690)['literal'], '{b}[[JAMMED SIGNAL C]{b}')
        self.assertIn('"{b}[[JAMMED SIGNAL C]{b}"', self.at(690)['image_expression'])
        self.assertTrue(any(e.get('image') == 'carfix' for e in self.story['opening_setup']))
        self.assertEqual(self.story['checkpoints'], {'before': 240, 'effect': 599, 'mirror': 653, 'after': 688, 'window': 789})
        self.assertEqual(self.story['characters']['a']['image'], 'as')
        self.assertIn('red', self.story['characters']['a']['attributes'])
        self.assertEqual(len(self.story['source_sha256']['game/ep2d3.rpyc']), 64)
        json.dumps(self.story, ensure_ascii=False)

    def test_unresolved_is10_is_reported_without_alias(self):
        missing = [d for d in self.story['diagnostics'] if d['code'] == 'unresolved_image' and d.get('image') == 'is10']
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]['candidate'], 'images/anim/10.png')
        self.assertFalse(missing[0]['resolved'])

    def test_translation_parser_never_executes_code_and_reports_missing_text(self):
        rows = anim_story.parse_translations('# game/ep2d3.rpy:1\ntranslate chinese test:\n    # a "Hello"\n    a "你好"\n    $ __import__("os").system("bad")\n')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['text'], '你好')
        with patch.object(anim_story, 'parse_translations', return_value=[]):
            story = anim_story.build_story()
        says = [e for e in story['events'] if e['kind'] == 'say']
        self.assertTrue(all(e['text'] is None for e in says))
        self.assertEqual(len([d for d in story['diagnostics'] if d['code'] == 'missing_translation']), len(says))

    def test_say_transition_suffix_is_inert_and_retains_both_languages(self):
        for transition in ('hpunch','vpunch'):
            rows=anim_story.parse_translations(f'# game/test.rpy:12\ntranslate chinese suffix:\n    # a red "Bump! with \\"quotes\\"" with {transition}\n    a red "撞了一下！" with {transition}\n')
            self.assertEqual(len(rows),1,'Say with suffix was silently lost')
            self.assertEqual(rows[0]['original'],'Bump! with "quotes"')
            self.assertEqual(rows[0]['text'],'撞了一下！')
            self.assertEqual(rows[0]['original_with_'],transition)
            self.assertEqual(rows[0]['translated_with_'],transition)
        self.assertIsNone(anim_story._parse_say_statement('a "Hello" with __import__("os").system("bad")'))
        self.assertIsNone(anim_story._parse_say_statement('a "Hello" trailing_command'))

    def test_all_19_real_say_transitions_and_11951_translations_are_retained(self):
        total=matched=0
        transitions=[]
        for name in ['script','ep1']+[f'ep2d{i}' for i in range(1,8)]+['ep3']:
            label='start' if name=='script' else name
            objects=anim_story.read_compiled(anim_story.DEFAULT_GAME/(name+'.rpyc'))
            block=next(anim_story.attrs(n)['block'] for n in objects if n.cls=='renpy.ast.Label' and anim_story.attrs(n).get('name')==label)
            rows=anim_story.parse_translations((anim_story.DEFAULT_GAME/'tl/chinese'/(name+'.rpy')).read_text(encoding='utf-8-sig'))
            matches=anim_story._match_translations(block,rows)
            for index,node in enumerate(block):
                if node.cls!='renpy.ast.Say':continue
                total+=1
                if index in matches:matched+=1
                state=anim_story.attrs(node)
                if state.get('with_') is not None:
                    with self.subTest(file=name,line=state['linenumber']):
                        self.assertTrue(index in matches,'real translated Say with transition was lost')
                        event=anim_story._event(node,index)
                        transition=anim_story._expression(state['with_'])
                        self.assertEqual(event['with_'],transition)
                        self.assertEqual(matches[index]['original_with_'],transition)
                        transitions.append(transition)
        self.assertEqual(total,11951)
        self.assertEqual(matched,total)
        self.assertEqual(sorted(transitions),['hpunch']*2+['vpunch']*17)

    def test_frozen_ep2d3_slice_is_byte_for_byte_equivalent_as_json(self):
        canonical=json.dumps(self.story,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
        self.assertEqual(hashlib.sha256(canonical).hexdigest(),'65b7332cc16347a0f1a6262b00c79a32ec741624cb5d66f42cebd881f2779976')


if __name__ == '__main__':
    unittest.main()
