"""Storage compaction preserves complete tiled payloads and frame metadata."""
from pathlib import Path
import hashlib,struct,sys,unittest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'nds/tools'))
from compact_asset_storage import compact_storage
from pack_assets import HEADER,BANK,FRAME,unlz10

def literal(raw):return b'\x10'+len(raw).to_bytes(3,'little')+b''.join(b'\0'+raw[i:i+8] for i in range(0,len(raw),8))

def fixture():
    red=struct.pack('<64H',*[0x801f]*64);blue=struct.pack('<64H',*[0xfc00]*64);green=struct.pack('<64H',*[0x83e0]*64)
    indexed=bytes(32)+bytes([255])*8
    soft=red+bytes(range(64));soft2=blue+bytes(range(192,256))
    raw_banks=[red+green+red+blue,indexed+bytes(40),indexed+bytes([17])*40,soft+soft2]
    strides=[128,40,40,192];maps=[(0,[0,2]),(0,[3,0]),(2,[0,0]),(3,[0,1])]
    lists=struct.pack('<4H',0,2,1,3);base=HEADER.size+4*BANK.size+4*FRAME.size+len(lists);payload=bytearray()
    def append(blob):
        while (base+len(payload))%4:payload.append(0)
        at=base+len(payload);payload.extend(blob);return at
    bank_records=[]
    for raw,stride in zip(raw_banks,strides):
        blob=literal(raw);bank_records.append(BANK.pack(append(blob),len(blob),len(raw),stride))
    frames=[]
    for id,(bid,ids) in enumerate(maps):
        raw=struct.pack('<2H',*ids);blob=literal(raw);name=(f'frame{id}'.encode()+b'\0preserved').ljust(127,b'X')+b'\0'
        frames.append(FRAME.pack(bid,append(blob),len(blob),4,16,8,2,1,-3+id,70+id,0 if id%2==0 else 2,[0,0,1,2][id],0x12340000+id,bytes(range(32)),name))
    return HEADER.pack(b'ZATONDP1',1,4,4,2,2,0,0,FRAME.size)+b''.join(bank_records)+b''.join(frames)+lists+payload

def parse(data):
    h=HEADER.unpack_from(data);banks=[BANK.unpack_from(data,HEADER.size+i*BANK.size) for i in range(h[3])];at=HEADER.size+h[3]*BANK.size
    frames=[FRAME.unpack_from(data,at+i*FRAME.size) for i in range(h[2])];end=at+h[2]*FRAME.size
    return h,banks,frames,data[end:end+(h[4]+h[5])*2]

def expanded_tiles(data):
    h,banks,frames,_=parse(data);raw=[unlz10(data[b[0]:b[0]+b[1]]) for b in banks];result=[]
    for f in frames:
        mapping=unlz10(data[f[1]:f[1]+f[2]]);ids=struct.unpack('<'+'H'*(len(mapping)//2),mapping);stride=banks[f[0]][3]
        result.append(tuple(raw[f[0]][i*stride:(i+1)*stride] for i in ids))
    return result


class AssetStorageCompactionTests(unittest.TestCase):
    def test_dead_banks_dead_and_duplicate_tiles_and_shared_maps(self):
        original=fixture();result,report=compact_storage(original);h,banks,frames,lists=parse(result)
        self.assertEqual(h[3],3);self.assertEqual(banks[0][2],256);self.assertEqual(banks[1][2],40)
        self.assertEqual(frames[0][1:4],frames[2][1:4],'identical index maps may share payload across banks')
        self.assertEqual(expanded_tiles(original),expanded_tiles(result));self.assertGreater(report['bytes_saved'],0)
    def test_every_frame_field_after_storage_header_and_stage_actor_lists_unchanged(self):
        original=fixture();result,_=compact_storage(original);old=parse(original);new=parse(result)
        self.assertEqual(old[0][:3]+old[0][4:],new[0][:3]+new[0][4:]);self.assertEqual(old[3],new[3])
        for a,b in zip(old[2],new[2]):self.assertEqual(a[4:],b[4:])
        old_bank=old[1][3];new_bank=new[1][new[2][3][0]]
        self.assertEqual(original[old_bank[0]:old_bank[0]+old_bank[1]],result[new_bank[0]:new_bank[0]+new_bank[1]],'untrimmed bank compressed bytes must be copied')
    def test_invalid_ranges_shape_lz_and_tile_ids_are_atomic_errors(self):
        good=fixture();h,banks,frames,_=parse(good);invalid=[good[:n] for n in (0,39,len(good)-1)]
        at=HEADER.size+h[3]*BANK.size
        for offset,value in [(HEADER.size,len(good)+1),(at+4,len(good)+1),(at+30,7)]:
            bad=bytearray(good);struct.pack_into('<I' if offset!=at+30 else '<H',bad,offset,value);invalid.append(bad)
        bad=bytearray(good);bad[frames[0][1]+5:frames[0][1]+7]=b'\xff\xff';invalid.append(bad)
        bad=bytearray(good);bad[banks[0][0]]=0x11;invalid.append(bad)
        bad=bytearray(good);bad.extend(bytes(300001));struct.pack_into('<I',bad,at+8,300001);invalid.append(bad)
        for bad in invalid:
            before=bytes(bad)
            with self.assertRaises(ValueError):compact_storage(bad)
            self.assertEqual(bytes(bad),before)
    def test_deterministic_idempotent_report_and_hashes(self):
        source=fixture();a,report=compact_storage(source);b,again=compact_storage(a)
        self.assertEqual(a,b);self.assertEqual(again['bytes_saved'],0)
        self.assertEqual(report['old_sha256'],hashlib.sha256(source).hexdigest());self.assertEqual(report['new_sha256'],hashlib.sha256(a).hexdigest())


if __name__=='__main__':unittest.main()
