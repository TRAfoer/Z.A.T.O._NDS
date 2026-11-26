"""Behavioral tests for passive, source-located full-story audio conversion."""
from pathlib import Path
from collections import Counter
import hashlib
import json
import pickle
import struct
import tempfile
import unittest
import zlib

from experiments.story_lab import audio_cues


def manifest_fixture():
    def row(group, index, source, aliases, gains):
        return dict(group=group, index=index, source=source,
            file=f'audio/{group}/{index:03d}.wav', frames=1000, rate=22050,
            channels=2 if group == 'music' else 1, loop_start_frame=123,
            loop_end_frame=999, aliases=aliases, alias_gains=gains)
    return dict(rate=22050, assets=[
        row('music', 0, 'track.ogg', ['track.mp3', 'track.ogg'], {'track.mp3': 1.4, 'track.ogg': 1.0}),
        row('music', 1, 'clink.mp3', ['clink.mp3'], {'clink.mp3': 1.0}),
        row('sfx', 0, 'clink.mp3', ['clink.mp3'], {'clink.mp3': 1.0})])


class AudioCueTests(unittest.TestCase):
    def setUp(self):
        self.catalog = audio_cues.AudioCatalog(manifest_fixture())

    def cue(self, command):
        return audio_cues.compile_command(command, self.catalog,
            chapter='ep1', source_file='game/ep1.rpy', source_line=12)

    def test_alias_script_and_master_gain_are_applied_once_with_frame_fade(self):
        command = 'play music "audio/track.mp3" volume 0.7 fadein 1.5'
        cue = self.cue(command)
        self.assertIsNotNone(cue, 'A recognized source audio command must emit a cue')
        self.assertEqual((cue['chapter'], cue['source_file'], cue['source_line'], cue['source_command']),
            ('ep1', 'game/ep1.rpy', 12, command))
        self.assertEqual((cue['group'], cue['index'], cue['asset_source']), ('music', 0, 'track.ogg'))
        self.assertEqual((cue['script_volume'], cue['alias_gain'], cue['master_gain'], cue['gain_q12']), (0.7, 1.4, 0.5, 2007))
        self.assertEqual(cue['fadein_frames'], 33075)
        self.assertTrue(cue['loop'])
        self.assertEqual((cue['loop_start_frame'], cue['loop_end_frame']), (123, 999))
        self.assertEqual(cue['native_args'], dict(is_music=True, index=0, gain_q12=2007, offset_frames=0, fade_frames=33075))

    def test_sound_without_space_and_shared_music_alias_keep_group_index(self):
        effect = self.cue('play sound"audio/clink.mp3"')
        music = self.cue('play music "audio/clink.mp3"')
        self.assertEqual((effect['group'], effect['index'], effect['gain_q12'], effect['loop']), ('sfx', 0, 2048, False))
        self.assertEqual((music['group'], music['index'], music['loop']), ('music', 1, True))

    def test_native_rounding_uses_half_even_for_gains_and_frame_ties(self):
        cue = self.cue('play music "audio/track.ogg" volume .000244140625 fadein .01 fadeout .03')
        self.assertEqual((cue['gain_q12'], cue['fadein_frames'], cue['fadeout_frames']), (0, 220, 662))
        cue = self.cue('play music "audio/track.ogg" volume .000732421875')
        self.assertEqual(cue['gain_q12'], 2)

    def test_stop_fade_and_explicit_loop_overrides_are_preserved(self):
        cue = self.cue('stop sound fadeout 9.0')
        self.assertEqual((cue['op'], cue['group'], cue['fadeout_frames']), ('stop', 'sfx', 198450))
        self.assertEqual(cue['native_args'], dict(is_music=False, fade_frames=198450))
        cue = self.cue('play sound "audio/clink.mp3" loop fadein .5 volume 0')
        self.assertEqual((cue['loop'], cue['loop_mode'], cue['gain_q12'], cue['fadein_frames']), (True, 'explicit', 0, 11025))
        self.assertEqual(cue['runtime_requirements'], ['sfx_loop'])
        cue = self.cue('play music "audio/track.ogg" noloop fadeout 2')
        self.assertEqual(cue['runtime_requirements'], ['music_once', 'replacement_fadeout'])

    def test_non_audio_is_ignored_but_unknown_audio_never_silently_drops(self):
        self.assertIsNone(self.cue('window hide'))
        for command in ['play music variable', 'play sound ["audio/clink.mp3"]',
                'play music "audio/track.ogg" volume gain',
                'play music "audio/track.ogg" fadein 1 fadein 2',
                'play music "audio/track.ogg" loop noloop',
                'play music "audio/track.ogg" volume -0.1',
                'play music "audio/track.ogg" fadein 1e99',
                'play music "audio/track.ogg" mystery 1',
                'play sound "audio/missing.mp3"', 'queue music "audio/track.ogg"',
                'stop sound fadein 1', 'play voice "audio/clink.mp3"']:
            with self.subTest(command=command), self.assertRaises(audio_cues.AudioCueError) as error:
                self.cue(command)
            diagnostic = error.exception.diagnostic
            self.assertEqual((diagnostic['chapter'], diagnostic['source_line'], diagnostic['source_command']), ('ep1', 12, command))

    def test_ambiguous_alias_is_rejected_without_cross_group_collision(self):
        manifest = manifest_fixture()
        manifest['assets'][1]['aliases'].append('track.ogg')
        manifest['assets'][1]['alias_gains']['track.ogg'] = 1.0
        with self.assertRaises(ValueError):
            audio_cues.AudioCatalog(manifest)

    def test_passive_reduce_never_executes_referenced_callable(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary)/'must_not_exist.txt'
            expression = f'__import__("pathlib").Path({str(marker)!r}).write_text("executed")'
            class Exploit:
                def __reduce__(self):
                    return eval, (expression,)
            payload = zlib.compress(pickle.dumps(Exploit(), protocol=2))
            path = Path(temporary)/'malicious.rpyc'
            path.write_bytes(b'RENPY RPC2' + struct.pack('<III', 1, 34, len(payload)) + b'\0'*12 + payload)
            objects = audio_cues.read_compiled(path)
            self.assertEqual(len(objects), 1)
            self.assertIn(objects[0].cls, ('__builtin__.eval', 'builtins.eval'))
            self.assertFalse(marker.exists())

    def test_full_reachable_main_chain_has_399_source_located_cues_without_mutation(self):
        if not audio_cues.DEFAULT_GAME.is_dir():
            self.skipTest('Original read-only game source is not installed')
        protected = [audio_cues.DEFAULT_MANIFEST, audio_cues.ROOT/'nds/config/audio_edits.json',
            audio_cues.ROOT/'nds/zato_ep2d3_scene_nitrofs.nds']
        before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
        report = audio_cues.build_audio_ir()
        self.assertEqual(report['chapters'], ['start', 'ep1', 'ep2d1', 'ep2d2', 'ep2d3', 'ep2d4', 'ep2d5', 'ep2d6', 'ep2d7', 'ep3'])
        self.assertEqual(report['summary']['cues'], 399)
        self.assertEqual(report['summary']['by_chapter'], dict(start=14, ep1=53, ep2d1=16, ep2d2=21, ep2d3=90, ep2d4=16, ep2d5=95, ep2d6=21, ep2d7=40, ep3=33))
        self.assertEqual(report['diagnostics'], [])
        self.assertTrue(all(c['source_line'] > 0 and c['source_command'] and c['runtime_requirements'] == [] for c in report['cues']))
        audit = json.loads((audio_cues.ROOT/'nds/artifacts/story_source_audit.json').read_text(encoding='utf-8'))
        expected_commands = {command: count for command, count in audit['main_user_statements'].items()
            if command.startswith(('play ', 'stop ', 'queue '))}
        self.assertEqual(Counter(c['source_command'] for c in report['cues']), expected_commands)
        audit_hashes = {row['file']: row['sha256'] for row in audit['files']}
        self.assertTrue(all(row['sha256'] == audit_hashes[row['file']] for row in report['sources']))
        self.assertEqual(before, {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in protected})
        self.assertEqual(json.dumps(report, sort_keys=True), json.dumps(audio_cues.build_audio_ir(), sort_keys=True))

    def test_full_ir_matches_frozen_ep2d3_native_audio_arguments(self):
        if not audio_cues.DEFAULT_GAME.is_dir():
            self.skipTest('Original read-only game source is not installed')
        cues = {(cue['source_line'], cue['op'], cue['group']): cue for cue in audio_cues.build_audio_ir()['cues']
            if cue['chapter'] == 'ep2d3'}
        script = json.loads((audio_cues.ROOT/'nds/artifacts/scene_script_manifest.json').read_text(encoding='utf-8'))
        checked = 0
        for event in script['events']:
            if event['name'] not in ('MUSIC', 'SFX', 'STOP_MUSIC', 'STOP_SFX'):
                continue
            op = 'stop' if event['name'].startswith('STOP_') else 'play'
            group = 'music' if event['name'].endswith('MUSIC') else 'sfx'
            cue = cues[(event['line'], op, group)]
            if op == 'play':
                self.assertEqual((cue['index'], cue['gain_q12'], cue['fadein_frames']), (event['a'], event['b'], event['c']))
            else:
                self.assertEqual(cue['fadeout_frames'], event['a'])
            checked += 1
        self.assertGreater(checked, 20)


if __name__ == '__main__':
    unittest.main()
