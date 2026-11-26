"""Selective straight-alpha actor repair; unrelated asset streams stay exact."""
from pathlib import Path
import importlib.util,json,struct,sys,tempfile,unittest
import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'nds/tools'))
import pack_story_assets as writer

IDS={150,330,375,407,451,452,453,454,455,456,459}

def load_converter():
    path=ROOT/'nds/tools/convert_story_soft_alpha.py'
    if not path.exists():return None
    spec=importlib.util.spec_from_file_location('soft_converter_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def planes(tiles,width,height):
    cols=(width+7)//8;rows=(height+7)//8
    values=np.frombuffer(b''.join(tiles),dtype='u1').reshape(rows,cols,192)
    words=values[:,:,:128].copy().view('<u2').reshape(rows,cols,8,8).transpose(0,2,1,3).reshape(rows*8,cols*8)
    alpha=values[:,:,128:].reshape(rows,cols,8,8).transpose(0,2,1,3).reshape(rows*8,cols*8)
    return words[:height,:width],alpha[:height,:width],words,alpha

class SoftActorTests(unittest.TestCase):
    def converter(self):
        module=load_converter();self.assertIsNotNone(module,'straight-alpha selective converter is missing');return module

    def test_rgba_box_keeps_partial_alpha_and_original_white_without_outline(self):
        module=self.converter();a=np.zeros((8,10,4),dtype='u1');a[:]=(0,0,0,96);a[2:6,2:8]=(255,255,255,255)
        original=Image.fromarray(a);result=module.actor_rgba(original)
        expected=original.resize((5,4),Image.Resampling.BOX)
        self.assertEqual(result.size,(5,4));self.assertEqual(result.tobytes(),expected.tobytes())
        self.assertEqual(result.getpixel((0,0)),(0,0,0,96));self.assertEqual(result.getpixel((2,1)),(255,255,255,255))

    def test_halfbody_crop_remains_572_source_pixels_and_two_target_rows_blank(self):
        module=self.converter();a=np.full((720,450,4),255,dtype='u1');a[:,:,3]=127
        result=np.asarray(module.actor_rgba(Image.fromarray(a)))
        self.assertEqual(result.shape,(288,225,4));self.assertTrue(np.all(result[:286,:,3]==127));self.assertFalse(result[286:].any())

    def test_tile192_alpha_exact_rgb15_rounding_transparent_zero_and_padding(self):
        module=self.converter();a=np.zeros((9,11,4),dtype='u1');a[:,:,:3]=(91,182,255);a[:,:,3]=np.arange(99,dtype='u1').reshape(9,11)*2
        a[0,1]=(0,0,0,1);a[0,2]=(0,0,0,255)
        tiles=module.encode_tiles(Image.fromarray(a));self.assertEqual(len(tiles),4);self.assertTrue(all(len(t)==192 for t in tiles))
        words,alpha,padded_words,padded_alpha=planes(tiles,11,9)
        q=(a[:,:,:3].astype(np.uint16)*31+127)//255
        expected=np.where(a[:,:,3],q[:,:,0]|(q[:,:,1]<<5)|(q[:,:,2]<<10)|0x8000,0).astype('<u2')
        np.testing.assert_array_equal(words,expected);np.testing.assert_array_equal(alpha,a[:,:,3])
        self.assertEqual(int(words[0,1]),0x8000);self.assertEqual(int(words[0,2]),0x8000)
        self.assertFalse(padded_words[9:].any());self.assertFalse(padded_words[:,11:].any());self.assertFalse(padded_alpha[9:].any());self.assertFalse(padded_alpha[:,11:].any())
        interleaved=np.empty((9,11,3),dtype='u1');interleaved[:,:,0]=words&255;interleaved[:,:,1]=words>>8;interleaved[:,:,2]=alpha
        self.assertEqual(module.pixel_hash(Image.fromarray(a)),writer.fnv(interleaved.tobytes()))

    def test_source_pixel_oracle_uses_true_q5_coverage_and_q12_sequence_global_blend(self):
        sys.path.insert(0,str(ROOT/'nds/tests'));from story_gfx_oracle import Oracle
        class TinySource(Oracle):
            def __init__(self):self.frames=[dict(format=2),dict(format=2)]
            def pixels(self,frame):return np.full((1,1),0x801f if frame==0 else 0x83e0,dtype=np.uint16)
            def alpha32(self,frame):return np.full((1,1),12 if frame==0 else 23,dtype=np.int64)
        source=TinySource();p=[4096,0,65536,65536,0,0,0,0,0,1,-1,0]
        for prior,mix,global_alpha,blend in [(None,4096,4096,0),(1,1536,2301,0),(1,1536,2301,1),(1,1536,2301,2)]:
            dest=np.full((192,256),0xfc00,dtype=np.uint16);p[0]=global_alpha;p[1]=blend
            source.paint(dest,0,prior,mix,p,(0,0,1))
            ca=(12*global_alpha+16)>>5;oa=(23*global_alpha+16)>>5 if prior is not None else 0
            alpha=(ca*mix+oa*(4096-mix)+2048)>>12;want=0x8000
            for shift in (0,5,10):
                c=(0x801f>>shift)&31;o=(0x83e0>>shift)&31 if prior is not None else 0;d=(0xfc00>>shift)&31
                prem=(ca*c*mix+oa*o*(4096-mix)+2048)>>12
                if blend==1:value=min(31,d+((prem+2048)>>12))
                elif blend==2:value=(d*((31*(4096-alpha)+prem+15)//31)+2048)>>12
                else:value=(d*(4096-alpha)+prem+2048)>>12
                want|=value<<shift
            self.assertEqual(int(dest[0,0]),want,(prior,mix,global_alpha,blend));self.assertTrue(np.all(dest[1:]==0xfc00))

    def test_selective_package_preserves_all494_other_pixels_and_original_compressed_banks(self):
        self.assertIn('soft_alpha',__import__('inspect').signature(writer.build).parameters,'writer has no selective alpha repair')
        with tempfile.TemporaryDirectory() as folder:
            tmp=Path(folder);report=writer.build(output=tmp/'new.zat',manifest=tmp/'new.json',soft_alpha=True,soft_masters=tmp/'masters')
            writer.build(output=tmp/'baseline.zat',manifest=tmp/'baseline.json')
            new=writer.SourcePack(tmp/'new.zat');old=writer.SourcePack(tmp/'baseline.zat')
            self.assertEqual(set(report['soft_alpha']['frame_ids']),IDS);self.assertEqual(len(new.banks),len(old.banks)+7)
            self.assertEqual(new.list_bytes,old.list_bytes)
            for id,(before,after) in enumerate(zip(old.frames,new.frames)):
                self.assertEqual(before[4:11],after[4:11]);self.assertEqual(before[-1],after[-1])
                if id not in IDS:
                    self.assertEqual(before[11:14],after[11:14]);self.assertEqual(old.tiles(id),new.tiles(id))
                else:
                    self.assertEqual(after[11],2);self.assertEqual(after[13],bytes(32))
                    with Image.open(report['frames'][id]['master']) as image:
                        words,alpha,*_=planes(new.tiles(id),after[4],after[5]);np.testing.assert_array_equal(alpha,np.asarray(image)[:,:,3])
                        self.assertGreater(len(np.unique(alpha)),16 if id==150 else 100)
                    if id==150:
                        # Unpack the old approved sixteen-color master from its
                        # actual tile directory, not the new encoder's result.
                        t=np.frombuffer(b''.join(old.tiles(id)),dtype='u1').reshape(after[7],after[6],40)
                        indices=np.empty((*t.shape[:2],64),dtype='u1');indices[:,:,::2]=t[:,:,:32]&15;indices[:,:,1::2]=t[:,:,:32]>>4
                        indices=indices.reshape(after[7],after[6],8,8).transpose(0,2,1,3).reshape(after[7]*8,after[6]*8)[:after[5],:after[4]]
                        visible=np.unpackbits(t[:,:,32:],axis=2,bitorder='little').reshape(after[7],after[6],8,8).transpose(0,2,1,3).reshape(after[7]*8,after[6]*8)[:after[5],:after[4]]>0
                        expected=np.frombuffer(before[13],dtype='<u2')[indices]|0x8000
                        np.testing.assert_array_equal(words[visible],expected[visible]);self.assertFalse(alpha[~visible].any())
            for i,before in enumerate(old.banks):
                after=new.banks[i];self.assertEqual(before[1:],after[1:]);self.assertEqual(old.data[before[0]:before[0]+before[1]],new.data[after[0]:after[0]+after[1]])
            self.assertLessEqual(max(b[2] for b in new.banks),262144)
            self.assertEqual(report['soft_alpha']['tile_bytes'],192)

    def test_catalog_geometry_same_for_new_master_paths_and_format2_closed_list(self):
        self.assertIn('soft_alpha',__import__('inspect').signature(writer.build).parameters,'writer has no selective alpha repair')
        import pack_story_gfx as catalog
        with tempfile.TemporaryDirectory() as folder:
            tmp=Path(folder);writer.build(output=tmp/'new.zat',manifest=tmp/'new.json',soft_alpha=True,soft_masters=tmp/'masters')
            old=ROOT/'nds/nitrofs/story/gfx.bin';old_bytes=old.read_bytes()
            result=catalog.build(catalog.story.DEFAULT_GAME,tmp/'new.zat',tmp/'gfx.bin',tmp/'gfx.json',tmp/'new.json')
            data=(tmp/'gfx.bin').read_bytes();offset=struct.unpack_from('<I',data,44)[0];old_offset=struct.unpack_from('<I',old_bytes,44)[0]
            self.assertEqual(data[offset:],old_bytes[old_offset:]);self.assertEqual(result['image_count'],525)
            meta=json.loads((tmp/'new.json').read_text(encoding='utf8'));meta['frames'][329]['format']=2;(tmp/'bad.json').write_text(json.dumps(meta),encoding='utf8')
            with self.assertRaises(ValueError):catalog.build(catalog.story.DEFAULT_GAME,tmp/'new.zat',tmp/'badgfx.bin',tmp/'badgfx.json',tmp/'bad.json')

if __name__=='__main__':unittest.main()
