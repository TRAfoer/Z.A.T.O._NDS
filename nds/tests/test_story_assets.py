"""Real asset_pack.c proof: old498 masters exact; seven approved family masters."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[2]
CHANGED={172,173,174,175,176,246,247}
HEADER=struct.Struct('<8s8I');BANK=struct.Struct('<4I');FRAME=struct.Struct('<4I4H2h2HI32s128s')


def tables(data):
    h=HEADER.unpack_from(data)
    banks=[BANK.unpack_from(data,40+i*16) for i in range(h[3])]
    at=40+h[3]*16
    frames=[FRAME.unpack_from(data,at+i*196) for i in range(h[2])]
    lists=data[at+h[2]*196:at+h[2]*196+(h[4]+h[5])*2]
    return h,banks,frames,lists


def words(rgba):
    q=(rgba[:,:,:3].astype(np.uint16)*31+127)//255
    return np.where(rgba[:,:,3]>=128,q[:,:,0]|q[:,:,1]<<5|q[:,:,2]<<10|0x8000,0).astype('<u2')


def literal_lz(data):
    return b'\x10'+len(data).to_bytes(3,'little')+b''.join(b'\0'+data[i:i+8] for i in range(0,len(data),8))


class StoryAssetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.writer=ROOT/'nds/tools/pack_story_assets.py';cls.present=cls.writer.is_file()
        if not cls.present:return
        cls.temp=tempfile.TemporaryDirectory();cls.tmp=Path(cls.temp.name)
        spec=importlib.util.spec_from_file_location('pack_story_assets_test',cls.writer)
        cls.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.module)
        cls.old=(ROOT/'nds/nitrofs/assets.zat').read_bytes()
        cls.protected={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'nds/nitrofs/assets.zat',ROOT/'nds/storyfs/assets.zat',ROOT/'nds/nitrofs/story/text.bin',ROOT/'nds/zato_ep2d3_scene_nitrofs.nds']}
        cls.pack=cls.tmp/'story.zat';cls.manifest=cls.tmp/'manifest.json'
        cls.report=cls.module.build(output=cls.pack,manifest=cls.manifest)
        cc=os.environ.get('CC') or shutil.which('gcc') or shutil.which('clang')
        if not cc:raise unittest.SkipTest('host C compiler unavailable')
        cls.exe=cls.tmp/'validate_story_assets.exe'
        subprocess.run([cc,'-std=c11','-O2','-Wall','-Wextra','-Werror','-I'+str(ROOT/'nds/source'),str(ROOT/'nds/source/asset_pack.c'),str(ROOT/'nds/tests/validate_story_assets.c'),'-o',str(cls.exe)],check=True)
        meta=json.loads((ROOT/'nds/artifacts/assets_manifest.json').read_text(encoding='utf8'))
        families=json.loads((ROOT/'experiments/story_lab/out/palette_families/report.json').read_text(encoding='utf8'))
        selected={v['frame_id']:(f['arrays'],i) for f in families['new_families'] for i,v in enumerate(f['variants'])}
        cls.gold=cls.tmp/'masters.gold'
        with cls.gold.open('wb') as out:
            out.write(b'ZATAGOLD'+struct.pack('<I',505))
            for frame in meta['frames']:
                id=frame['id']
                if id in selected:
                    path,index=selected[id]
                    with np.load(path) as a:expected=np.where(a['mask'],a['palettes'][index][a['indices']]|0x8000,0).astype('<u2')
                else:
                    path=ROOT/'nds'/frame['master'] if frame['master'] else ROOT/'experiments/asset_lab/out/full_asset_audit/converted'/frame['name']
                    with Image.open(path) as image:expected=words(np.array(image.convert('RGBA')))
                out.write(struct.pack('<3I',id,expected.shape[1],expected.shape[0]));out.write(expected.tobytes())

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls,'temp'):cls.temp.cleanup()

    def require_writer(self):self.assertTrue(self.present,'independent story asset writer is not implemented')

    def test_family_maps_share_offsets_and_other498_masters_are_native_exact(self):
        self.require_writer()
        data=self.pack.read_bytes();oldh,oldb,oldf,oldlists=tables(self.old);h,banks,frames,lists=tables(data)
        self.assertEqual(h,oldh);self.assertEqual(lists,oldlists)
        self.assertTrue(all(b[2]<=262144 for b in banks))
        for id,(old,new) in enumerate(zip(oldb,banks)):
            if id not in (42,46):
                self.assertEqual(old[1:],new[1:])
                self.assertEqual(self.old[old[0]:old[0]+old[1]],data[new[0]:new[0]+new[1]])
        for id,(old,new) in enumerate(zip(oldf,frames)):
            self.assertEqual(old[4:12],new[4:12]);self.assertEqual(old[-1],new[-1])
            if id not in CHANGED:self.assertEqual(old[12:14],new[12:14])
        for ids in ([172,173,174,175,176],[246,247]):
            self.assertEqual(len({frames[i][:4] for i in ids}),1)
            for id in ids:self.assertEqual(struct.unpack('<16H',frames[id][13])[15],0x2108)
        run=subprocess.run([str(self.exe),str(self.pack),str(self.gold)],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr);print(run.stdout.strip())

    def test_exact_size_report_determinism_and_protected_sources_unchanged(self):
        self.require_writer()
        second=self.tmp/'second.zat';report=self.module.build(output=second,manifest=self.tmp/'second.json')
        self.assertEqual(second.read_bytes(),self.pack.read_bytes())
        self.assertEqual(report['package_bytes'],self.pack.stat().st_size)
        self.assertEqual(report['bytes_saved'],len(self.old)-self.pack.stat().st_size)
        self.assertGreater(report['bytes_saved'],40000)
        self.assertEqual(report['changed_frame_ids'],sorted(CHANGED))
        for path,sha in self.protected.items():self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),sha)

    def test_real_native_decoder_rejects_truncation_invalid_bank_map_and_hash(self):
        self.require_writer()
        source=self.pack.read_bytes();h,banks,frames,lists=tables(source);cases=[]
        for length in (0,39,40+h[3]*16+h[2]*196-1,len(source)-1):cases.append(source[:length])
        for offset,value in [(12,4097),(40+8,262145),(40+12,41),(40+h[3]*16,999),(40+h[3]*16+4,len(source)+4),(40+h[3]*16+32,0)]:
            bad=bytearray(source);struct.pack_into('<I',bad,offset,value);cases.append(bad)
        # A valid LZ stream with an invalid tile number must reach map bounds checks.
        id=172;at=40+h[3]*16+id*196;raw=bytearray(frames[id][3]);struct.pack_into('<H',raw,0,65535)
        blob=literal_lz(raw);bad=bytearray(source);struct.pack_into('<2I',bad,at+4,len(bad),len(blob));bad.extend(blob);cases.append(bad)
        for i,bad in enumerate(cases):
            path=self.tmp/f'bad{i}.zat';path.write_bytes(bad)
            run=subprocess.run([str(self.exe),'reject',str(path)],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,f'bad{i}: '+run.stdout+run.stderr)

    def test_writer_refuses_changed_shared_stream_or_overwriting_old_source(self):
        self.require_writer()
        family=self.tmp/'families';shutil.copytree(ROOT/'experiments/story_lab/out/palette_families',family)
        index=family/'library/common.idx4';data=bytearray(index.read_bytes());data[0]^=1;index.write_bytes(data)
        with self.assertRaises(ValueError):self.module.build(families=family,output=self.tmp/'bad.zat',manifest=self.tmp/'bad.json')
        self.assertFalse((self.tmp/'bad.zat').exists())
        with self.assertRaises(ValueError):self.module.build(output=ROOT/'nds/nitrofs/assets.zat',manifest=self.tmp/'forbidden.json')


if __name__=='__main__':unittest.main()
