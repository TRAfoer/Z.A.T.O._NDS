"""ep2d3 source coverage, checkpoint/phase semantics, and native loader bounds."""
from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'experiments/asset_lab'))
from anim_story import build_story


def synthetic():
    # WAIT 12 ticks (hard) followed by END and one checkpoint at WAIT.
    payload=struct.pack('<HHI4i',3,0,1,12,1,0,0)+struct.pack('<HHI4i',13,0,2,0,0,0,0)
    payload+=struct.pack('<I24s',0,b'fixture\0')
    return bytearray(struct.pack('<8s8I',b'ZSCENE01',1,2,1,24,40,88,116,zlib.crc32(payload))+payload)


class SceneScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.tmp=Path(cls.temp.name)
        spec=importlib.util.spec_from_file_location('scene_script_packer_test',ROOT/'nds/tools/pack_scene_script.py')
        cls.packer=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.packer)
        cls.story=build_story()
        text=json.loads((ROOT/'nds/artifacts/scene_text_manifest.json').read_text(encoding='utf8'))
        gfx=json.loads((ROOT/'nds/artifacts/scene_gfx_manifest.json').read_text(encoding='utf8'))
        audio=json.loads((ROOT/'nds/artifacts/audio_manifest.json').read_text(encoding='utf8'))
        cls.audio=audio
        cls.gfx={g['name']:g['id'] for g in gfx['groups']}
        cls.events,cls.checkpoints=cls.packer.compile_story(cls.story,text['line_to_id'],text['literal_line_to_id'],cls.gfx,audio['assets'])
        cls.text=text
        cls.exe=cls.tmp/'validate_scene_script.exe'
        cc=os.environ.get('CC') or shutil.which('gcc') or shutil.which('clang')
        if not cc:raise unittest.SkipTest('host C compiler unavailable')
        subprocess.run([cc,'-std=c11','-O2','-Wall','-Wextra','-Werror',
                        '-I'+str(ROOT/'nds/source'),str(ROOT/'nds/source/scene_script.c'),
                        str(ROOT/'nds/tests/validate_scene_script.c'),'-o',str(cls.exe)],check=True)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def run_native(self,pack,argument):
        result=subprocess.run([str(self.exe),str(pack),str(argument)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_all_474_source_events_have_behavior_or_precomposed_layer_coverage(self):
        self.assertEqual(Counter(e['kind'] for e in self.story['events']),
                         {'say':278,'command':109,'hide':25,'show':23,'wait':19,'with':18,'scene':2})
        compiled_lines={e['line'] for e in self.events}
        omitted=[e for e in self.story['events'] if e['line'] not in compiled_lines]
        # These updates belong to already composited banks, with no blocking
        # event between the updates; their pixel behavior is tested by gfx tests.
        self.assertEqual([e['line'] for e in omitted],
                         [606,615,616,617,619,647,648,649,650,654,655,656,683,684,686])
        self.assertTrue(all(e['kind'] in ('show','hide') for e in omitted))
        self.assertEqual(len(self.events),474-len(omitted)+3)
        texts=[e for e in self.events if e['name']=='TEXT']
        self.assertEqual(len(texts),278)
        self.assertEqual([e['a'] for e in texts],
                         [int(self.text['line_to_id'][str(e['line'])]) for e in self.story['events'] if e['kind']=='say'])

    def test_automatic_text_markers_do_not_block_on_a_button(self):
        source={e['line']:e for e in self.story['events'] if e['kind']=='say'}
        for event in self.events:
            if event['name']=='TEXT':
                self.assertEqual(bool(event['flags']&1),'{nw}' in source[event['line']]['text'],
                                 f'automatic flag missing at {event["line"]}')

    def test_graphic_phase_restores_hands_red_and_each_loop_origin(self):
        key_lines={603:('iss',3,1),605:('black',0,0),614:('red',1,1),
                   620:('hands',1,1),626:('red',1,0),651:('black',0,0),
                   653:('mirror',2,1),685:('black',0,0),792:('window',4,1),794:('black',0,0)}
        gfx={e['line']:e for e in self.events if e['name']=='GFX'}
        self.assertEqual(gfx[0]['a'],self.gfx['carfix'])
        for line,(name,phase,reset) in key_lines.items():
            self.assertEqual((gfx[line]['a'],gfx[line]['b'],gfx[line]['c']),
                             (self.gfx[name],phase,reset))

    def test_checkpoints_address_first_event_at_or_after_source_line(self):
        self.assertEqual([c['line'] for c in self.checkpoints],[240,564,573,599,613,653,688,777])
        for checkpoint in self.checkpoints:
            pc=checkpoint['pc'];line=checkpoint['line']
            self.assertGreaterEqual(self.events[pc]['line'],line)
            self.assertTrue(all(e['line']<line for e in self.events[:pc]))

    def test_every_command_wait_and_transition_preserves_source_line_and_parameters(self):
        compiled={e['line']:e for e in self.events if e['line']}
        for source in self.story['events']:
            line=source['line'];kind=source['kind']
            if kind=='wait':
                self.assertEqual((compiled[line]['name'],compiled[line]['a'],compiled[line]['b']),
                                 ('WAIT',round(source['seconds']*60),int(source['hard'])))
            elif kind=='with':
                if line==691:expected=('WAIT',240)
                else:expected=('TRANSITION',{557:120,566:60,569:120,793:90,795:90}.get(line,30))
                self.assertEqual((compiled[line]['name'],compiled[line]['a']),expected)
            elif kind=='command':
                command=source['value']
                if command.startswith('play music '):op='MUSIC'
                elif command.startswith('play sound '):op='SFX'
                elif command.startswith('stop music'):op='STOP_MUSIC'
                elif command.startswith('stop sound'):op='STOP_SFX'
                elif command.startswith('window '):op='WINDOW'
                elif command=='nvl clear':op='CLEAR'
                else:op='WINDOW'
                self.assertEqual(compiled[line]['name'],op,f'command lost at {line}')
        # Hand-checked source gain and fade cues catch wrong channel units.
        self.assertEqual((compiled[246]['b'],compiled[246]['c']),(2048,154350))
        self.assertEqual((compiled[613]['b'],compiled[613]['c']),(2867,0))
        self.assertEqual((compiled[694]['b'],compiled[694]['c']),(2048,110250))
        for line in (573,599,689,791):self.assertEqual(compiled[line]['b'],2048)

    def test_native_loader_accepts_actual_and_hand_written_script(self):
        fixture=self.tmp/'fixture.bin';fixture.write_bytes(synthetic())
        self.run_native(fixture,2)
        path=ROOT/'nds/nitrofs/scene/script.bin'
        self.assertTrue(path.exists(),'run pack_scene_script.py before validating actual binary')
        count=struct.unpack_from('<I',path.read_bytes(),12)[0]
        self.run_native(path,count)

    def test_native_loader_rejects_truncation_overflow_and_invalid_records(self):
        source=synthetic();bad=[source[:n] for n in (0,8,39,40,63,87,88,115)]
        for offset,value in [(8,2),(12,0xffffffff),(16,0),(16,33),(20,23),
                             (24,39),(24,0xfffffffc),(28,40),(28,0xfffffffc),(32,117)]:
            item=source.copy();struct.pack_into('<I',item,offset,value);bad.append(item)
        for offset,value in [(40,0),(40,14),(88,2)]:
            item=source.copy();struct.pack_into('<I',item,offset,value)
            struct.pack_into('<I',item,36,zlib.crc32(item[40:]));bad.append(item)
        item=source.copy();item[-24:]=b'x'*24
        struct.pack_into('<I',item,36,zlib.crc32(item[40:]));bad.append(item)
        item=source.copy();item[-1]^=1;bad.append(item)
        # Valid checksums do not authorize illegal flags/negative durations,
        # hard-pause booleans, or a script lacking its final END.
        for offset,fmt,value in [(42,'H',1),(48,'i',-1),(52,'i',2),(64,'H',3)]:
            item=source.copy();struct.pack_into('<'+fmt,item,offset,value)
            struct.pack_into('<I',item,36,zlib.crc32(item[40:]));bad.append(item)
        for index,data in enumerate(bad):
            path=self.tmp/f'bad{index}.bin';path.write_bytes(data)
            self.run_native('reject',path)


if __name__=='__main__':unittest.main()
