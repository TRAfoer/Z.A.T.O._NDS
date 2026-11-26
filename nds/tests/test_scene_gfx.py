"""Real C loader bounds and independent compositor-pixel/time regression tests."""
from pathlib import Path
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'experiments/asset_lab'))
from anim_renderer import AnimRenderer


def fnv(data):
    h = 2166136261
    for b in data:
        h = ((h ^ b) * 16777619) & 0xffffffff
    return h


def synthetic():
    # Two hand-checked solid-color tiles and two alternating maps, one bank.
    tiles = struct.pack('<64H', *([0x801f]*64)) + struct.pack('<64H', *([0x83e0]*64))
    maps = struct.pack('<576H', *([0]*576)) + struct.pack('<576H', *([1]*576))
    payload = tiles + maps
    header = struct.pack('<8s8I', b'ZATOGFX1', 1,256,144,24,2,1,128+len(payload),0)
    # Looping group and clamped group share maps, distinguish 6-tick boundaries.
    groups = struct.pack('<8I',0,0,0,2,6,1,0,0) + struct.pack('<8I',1,0,0,2,6,0,0,0)
    bank = struct.pack('<6I',128,len(payload),2,2,fnv(payload),0)
    return bytearray(header + groups + bank + payload)


def write_golden(path, records):
    with path.open('wb') as out:
        out.write(struct.pack('<8sI', b'ZATOGOLD',len(records)))
        for group,tick,frame,screen in records:
            out.write(struct.pack('<3I',group,tick,frame))
            out.write(screen.astype('<u2').tobytes())


class SceneGfxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls.temp.name)
        cls.exe = cls.tmp/'validate_scene_gfx.exe'
        cc = os.environ.get('CC') or shutil.which('gcc') or shutil.which('clang')
        if not cc:
            raise unittest.SkipTest('host C compiler unavailable')
        subprocess.run([cc,'-std=c11','-O2','-Wall','-Wextra','-Werror',
                        '-I'+str(ROOT/'nds/source'),str(ROOT/'nds/source/scene_gfx.c'),
                        str(ROOT/'nds/tests/validate_scene_gfx.c'),'-o',str(cls.exe)],check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def validate(self, pack, gold):
        result = subprocess.run([str(self.exe),str(pack),str(gold)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return result.stdout

    def test_time_loop_clamp_stage_and_no_frame_io(self):
        pack=self.tmp/'synthetic.bin'; pack.write_bytes(synthetic())
        records=[]
        for group,tick,frame in [(0,0,0),(0,5,0),(0,6,1),(0,11,1),(0,12,0),
                                 (0,0xffffffff,0),(1,12,1),(1,0xffffffff,1)]:
            screen=np.full((192,256),0x8000,dtype='<u2')
            screen[24:168] = [0x801f,0x83e0][frame]
            records.append((group,tick,frame,screen))
        gold=self.tmp/'synthetic.gold'; write_golden(gold,records)
        print(self.validate(pack,gold).strip())

    def test_corrupt_sizes_indices_and_truncation_are_rejected(self):
        source=synthetic()
        bad=[]
        for length in (0,8,39,64,127,len(source)-1): bad.append(source[:length])
        for offset,value in [(8,2),(12,255),(24,0xffffffff),(28,0xffffffff),
                             (32,len(source)+1),(44,9),(52,3),(56,0),
                             (64,9),(104,0),(108,0xffffffff),(112,65536),(116,3)]:
            item=source.copy();struct.pack_into('<I',item,offset,value);bad.append(item)
        # Bad tile index with a recomputed bank integrity hash must still fail.
        item=source.copy();struct.pack_into('<H',item,128+256,2)
        struct.pack_into('<I',item,120,fnv(item[128:]));bad.append(item)
        item=source.copy();item[-1]^=1;bad.append(item)
        for i,data in enumerate(bad):
            pack=self.tmp/f'bad{i}.bin';pack.write_bytes(data)
            self.validate('reject',pack)

    def test_packer_cli_generates_resident_budget_and_required_groups(self):
        pack=self.tmp/'generated.bin';manifest=self.tmp/'generated.json'
        result=subprocess.run([sys.executable,str(ROOT/'nds/tools/pack_scene_gfx.py'),
                               '--output',str(pack),'--manifest',str(manifest)],
                              capture_output=True,text=True,cwd=ROOT)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        data=json.loads(manifest.read_text(encoding='utf8'))
        groups={g['name']:g for g in data['groups']}
        self.assertEqual(set(groups),{'black','carfix','red','hands','mirror','iss','window'})
        for name,count,step,loop in [('black',1,1,False),('carfix',1,1,False),
                                   ('red',30,6,True),('hands',27,1,False),
                                   ('mirror',36,3,True),('iss',10,3,True),('window',6,6,True)]:
            self.assertEqual((groups[name]['frame_count'],groups[name]['step_ticks'],groups[name]['loop']),
                             (count,step,loop))
        self.assertLessEqual(data['memory']['resident_image_heap_bytes'],2400*1024)
        self.assertLess(pack.stat().st_size,13*1000*1000)
        existing=ROOT/'nds/nitrofs/scene/gfx.bin'
        if existing.exists():self.assertEqual(pack.read_bytes(),existing.read_bytes(),'repacked scene graphics must be deterministic')

    @unittest.skipUnless((ROOT/'nds/nitrofs/scene/gfx.bin').exists(),'scene graphics pack not generated')
    def test_actual_bank_truncation_and_final_map_bounds_are_rejected(self):
        source=bytearray((ROOT/'nds/nitrofs/scene/gfx.bin').read_bytes())
        group_count,bank_count=struct.unpack_from('<2I',source,24)
        for bank_id in range(bank_count):
            record=40+group_count*32+bank_id*24
            offset,size,tiles,maps=struct.unpack_from('<4I',source,record)
            pack=self.tmp/f'actual_short{bank_id}.bin'
            pack.write_bytes(source[:offset+size-1]);self.validate('reject',pack)
            # Corrupt the actual bank's final tile index and repair its FNV.
            # This catches index validation accidentally stopping one map early.
            item=source.copy();struct.pack_into('<H',item,offset+size-2,tiles)
            struct.pack_into('<I',item,record+16,fnv(item[offset:offset+size]))
            pack=self.tmp/f'actual_index{bank_id}.bin'
            pack.write_bytes(item);self.validate('reject',pack)

    @unittest.skipUnless((ROOT/'nds/nitrofs/scene/gfx.bin').exists(),'scene graphics pack not generated')
    def test_actual_pack_matches_independent_python_compositor(self):
        # The expectation uses the existing compositor and source story directly,
        # never the packer's own image/tile helpers or stored frame hashes.
        story=json.loads((ROOT/'experiments/asset_lab/out/anim_preview/story.json').read_text(encoding='utf8'))
        manifest=json.loads((ROOT/'nds/artifacts/scene_gfx_manifest.json').read_text(encoding='utf8'))
        events={e['line']:e for e in story['events'] if e['kind']=='show'}
        def events_layers(lines):
            return [dict(events[line],name=events[line]['image'],start=0) for line in lines]
        red=events_layers((614,615,616,617,619))
        layers={'black':[dict(name='black',start=0,properties={})],
                'carfix':[dict(name='carfix',start=0,properties={})],
                'red':red,'hands':red+events_layers((620,)),
                'mirror':events_layers((653,654,655,656)),
                'iss':[dict(name='iss',start=0,properties={})],
                'window':events_layers((792,))}
        renderer=AnimRenderer(iss_mode='text')
        records=[]
        for g in manifest['groups']:
            count,step=g['frame_count'],g['step_ticks']
            ticks=set(range(0,count*step)) | {count*step,count*step+1,0xffffffff}
            for tick in sorted(ticks):
                frame=(tick//step)%count if g['loop'] else min(tick//step,count-1)
                image=renderer.render(layers[g['name']],frame*step/60)
                q=(np.asarray(image,dtype=np.uint16)*31+127)//255
                screen=(q[:,:,0]|(q[:,:,1]<<5)|(q[:,:,2]<<10)|0x8000).astype('<u2')
                records.append((g['id'],tick,frame,screen))
        gold=self.tmp/'actual.gold';write_golden(gold,records)
        print(self.validate(ROOT/'nds/nitrofs/scene/gfx.bin',gold).strip())


if __name__=='__main__': unittest.main()
