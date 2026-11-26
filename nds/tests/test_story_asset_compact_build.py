"""Production CLI emits only referenced storage while keeping approved tiles."""
from pathlib import Path
import json,subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'nds/tools'))
import pack_story_assets as writer


class CompactStoryBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='zato_compact_build_');cls.folder=Path(cls.temp.name)
        cls.candidate=cls.folder/'candidate.zat';cls.manifest=cls.folder/'candidate.json'
        result=subprocess.run([sys.executable,str(ROOT/'nds/tools/pack_story_assets.py'),
            '--output',str(cls.candidate),'--manifest',str(cls.manifest),'--soft-masters',str(cls.folder/'masters')],capture_output=True,text=True,cwd=ROOT)
        if result.returncode:raise AssertionError(result.stdout+result.stderr)
        cls.new=writer.SourcePack(cls.candidate)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def test_production_cli_drops_orphan_banks_and_unreferenced_tiles(self):
        used={frame[0] for frame in self.new.frames}
        self.assertEqual(used,set(range(len(self.new.banks))),'production output still contains unused dictionaries')
        for bank,record in enumerate(self.new.banks):
            used_tiles=set()
            for i,frame in enumerate(self.new.frames):
                if frame[0]!=bank:continue
                self.new.tiles(i)
                used_tiles.update(writer.np.frombuffer(self.new.maps[i],dtype='<u2').tolist())
            self.assertEqual(used_tiles,set(range(record[2]//record[3])),f'bank{bank} still stores unused tiles')
    def test_compaction_preserves_all_approved_frame_tiles_and_metadata(self):
        baseline=self.folder/'baseline.zat'
        writer.build(output=baseline,manifest=self.folder/'baseline.json',soft_alpha=True,soft_masters=self.folder/'masters')
        old=writer.SourcePack(baseline)
        self.assertEqual(old.list_bytes,self.new.list_bytes)
        self.assertEqual(len(old.frames),505)
        for i,(before,after) in enumerate(zip(old.frames,self.new.frames)):
            self.assertEqual(before[4:],after[4:],f'frame{i} visible metadata changed')
            self.assertEqual(old.tiles(i),self.new.tiles(i),f'frame{i} tile bytes changed')
        report=json.loads(self.manifest.read_text(encoding='utf8'))
        self.assertEqual(report['package_bytes'],self.candidate.stat().st_size)
        self.assertLess(self.candidate.stat().st_size,baseline.stat().st_size)
        self.assertEqual(report['bank_count'],len(self.new.banks))
        for i,frame in enumerate(self.new.frames):
            self.assertEqual((report['frames'][i]['bank'],report['frames'][i]['map_offset'],report['frames'][i]['map_bytes']),tuple(frame[:3]))


if __name__=='__main__':unittest.main()
