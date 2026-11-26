"""Independent source masks, native C decoding and strict producer inputs."""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,sys,tempfile,unittest
from PIL import Image
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'nds/tools'))
import pack_credits_badge as pack

class CreditsBadgeTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory(prefix='zato_credits_badge_');self.folder=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def save(self,name,size,points=()):
        image=Image.new('RGBA',size)
        for x,y in points:image.putpixel((x,y),(255,255,255,255))
        path=self.folder/name;image.save(path);return path
    def expected(self):
        mask=Image.new('L',(147,43));mask.paste(Image.open(pack.TRADER).getchannel('A'),(0,0));mask.paste(Image.open(pack.GPT).getchannel('A'),(104,0));return mask.tobytes()
    def test_actual_sources_native_concat_dense_bits_and_zero_tail(self):
        bits,report=pack.pack_badge();self.assertEqual(len(bits),791)
        decoded=bytes(255 if bits[i//8]&(128>>(i%8)) else 0 for i in range(6321))
        self.assertEqual(decoded,self.expected());self.assertEqual(bits[-1]&127,0)
        for y in range(43):self.assertEqual(decoded[y*147+96:y*147+104],bytes(8),'native8px gap must stay transparent')
    def test_concat_edge_cross_row_byte_and_last_pixel(self):
        left=self.save('left.png',(96,43),[(95,0),(0,1),(95,42)])
        right=self.save('right.png',(43,43),[(0,0),(42,0),(0,1),(42,42)])
        bits,_=pack.pack_badge(left,right)
        self.assertTrue(bits[11]&1);self.assertEqual(bits[12],0);self.assertTrue(bits[13]&128);self.assertEqual(bits[18]&0x30,0x30)
        self.assertEqual(bits[-1],128);self.assertEqual(sum(byte.bit_count() for byte in bits),7)
    def test_real_c_header_decodes_to_original_png_alpha(self):
        cc=os.environ.get('CC') or shutil.which('gcc') or shutil.which('clang')
        if not cc:self.skipTest('host C compiler unavailable')
        header=self.folder/'credits_badge.h';meta=self.folder/'metadata.json';pack.build(header=header,metadata=meta)
        source=self.folder/'probe.c';source.write_text('#include "credits_badge.h"\n#include <stdio.h>\nint main(void){if(CREDITS_BADGE_WIDTH!=147||CREDITS_BADGE_HEIGHT!=43||CREDITS_BADGE_BYTES!=791||sizeof(credits_badge_bits)!=791||(credits_badge_bits[790]&127))return 2;for(unsigned i=0;i<147*43;i++)putchar(credits_badge_bits[i/8]&(128u>>(i%8))?255:0);return 0;}\n',encoding='ascii')
        exe=self.folder/'probe.exe';r=subprocess.run([cc,'-std=c11','-O2','-Wall','-Wextra','-Werror',str(source),'-o',str(exe)],capture_output=True)
        self.assertEqual(r.returncode,0,r.stderr.decode());r=subprocess.run([str(exe)],capture_output=True);self.assertEqual(r.returncode,0);self.assertEqual(r.stdout,self.expected())
    def test_partial_alpha_visible_gray_and_wrong_dimensions_rejected_atomically(self):
        for pixel,size in [((255,255,255,127),(96,43)),((128,128,128,255),(96,43)),((255,0,0,255),(96,43)),((255,255,255,255),(95,43))]:
            image=Image.new('RGBA',size);image.putpixel((0,0),pixel);path=self.folder/'bad.png';image.save(path)
            header=self.folder/'old.h';header.write_bytes(b'keep header');meta=self.folder/'old.json';meta.write_bytes(b'keep metadata')
            with self.assertRaises(ValueError):pack.build(path,pack.GPT,header,meta)
            self.assertEqual(header.read_bytes(),b'keep header');self.assertEqual(meta.read_bytes(),b'keep metadata')
    def test_deterministic_metadata_source_hash_and_transparent_hidden_rgb(self):
        left=self.save('left.png',(96,43));right=self.save('right.png',(43,43));im=Image.open(left).convert('RGBA');im.putpixel((0,0),(96,96,96,0));im.save(left)
        before=left.read_bytes();bits,report=pack.pack_badge(left,right);self.assertEqual(bits,bytes(791));self.assertEqual(left.read_bytes(),before)
        self.assertEqual(report['sources'][0]['sha256'],hashlib.sha256(before).hexdigest())
        header=self.folder/'badge.h';meta=self.folder/'metadata.json';a=pack.build(header=header,metadata=meta);first=header.read_bytes();b=pack.build(header=header,metadata=meta)
        self.assertEqual(a,b);self.assertEqual(first,header.read_bytes());self.assertEqual(json.loads(meta.read_text(encoding='utf8')),b)

if __name__=='__main__':unittest.main()
