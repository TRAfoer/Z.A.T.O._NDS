"""Independent Steam-icon source, alpha/quantisation and NDS banner checks."""
from pathlib import Path
import hashlib,importlib.util,struct,tempfile,unittest
import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[2]
SOURCE=Path('C:/Program Files (x86)/Steam/steamapps/common/Z.A.T.O.  I Love the World and Everything In It/game/gui/icon.ico')
def crc16(data):
    c=0xffff
    for value in data:
        c^=value
        for _ in range(8):c=(c>>1)^(0xa001 if c&1 else 0)
    return c

def rom_fixture(icon,title='ZATO Story Chain\nMainline / Native NDS\nZATO NDS',version=1):
    """Write tiles independently, with a deliberately permuted palette."""
    a=np.asarray(icon);palette=icon.getpalette();used=sorted(set(a.flat));order=[0]+list(reversed([i for i in used if i]))
    reverse={old:new for new,old in enumerate(order)};indices=np.vectorize(reverse.get)(a)
    size={1:0x840,2:0x940,3:0xa40,0x103:0x23c0}[version];banner=bytearray(size);struct.pack_into('<H',banner,0,version)
    for y in range(32):
        for x in range(32):
            at=0x20+((y//8)*4+x//8)*32+(y%8)*4+(x%8)//2
            banner[at]|=int(indices[y,x])<<(4*(x&1))
    for new,old in enumerate(order):
        r,g,b=palette[old*3:old*3+3];struct.pack_into('<H',banner,0x220+new*2,(r>>3)|((g>>3)<<5)|((b>>3)<<10))
    for i in range(8 if version>=3 else 7 if version==2 else 6):
        encoded=title.encode('utf-16le');banner[0x240+i*256:0x240+i*256+len(encoded)]=encoded
    for slot,(minimum,start,end) in enumerate([(1,0x20,0x840),(2,0x20,0x940),(3,0x20,0xa40),(0x103,0x1240,0x23c0)]):
        if version>=minimum:struct.pack_into('<H',banner,2+slot*2,crc16(banner[start:end]))
    rom=bytearray(0x400);struct.pack_into('<I',rom,0x68,0x400);rom.extend(banner);return rom

class StoryIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=ROOT/'nds/tools/prepare_story_icon.py';assert path.exists(),'Steam-to-NDS icon converter is missing'
        spec=importlib.util.spec_from_file_location('story_icon',path);cls.tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.tool)
    def test_alpha_zero_and_fifteen_opaque_rgb15_colours_preserve_source(self):
        before=hashlib.sha256(SOURCE.read_bytes()).digest()
        with Image.open(SOURCE) as opened:original=opened.ico.getimage((32,32)).convert('RGBA')
        icon,meta=self.tool.convert_icon(SOURCE);a=np.asarray(icon);self.assertEqual(icon.size,(32,32));self.assertEqual(icon.mode,'P')
        self.assertTrue(np.array_equal(a==0,np.asarray(original)[:,:,3]<128));self.assertLessEqual(len(set(a.flat)),16)
        self.assertTrue(all(v%8==0 for v in icon.getpalette()));self.assertEqual(meta['source_frame_size'],[32,32]);self.assertEqual(meta['resized'],False)
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).digest(),before)
        again,_=self.tool.convert_icon(SOURCE);self.assertEqual(icon.tobytes(),again.tobytes());self.assertEqual(icon.getpalette(),again.getpalette())
    def test_opaque_black_is_visible_and_alpha_threshold_is_binary(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.png';im=Image.new('RGBA',(32,32),(10,20,30,0));im.putpixel((1,1),(0,0,0,255));im.putpixel((2,1),(240,20,50,127));im.putpixel((3,1),(240,20,50,128));im.save(path)
            icon,_=self.tool.convert_icon(path);self.assertNotEqual(icon.getpixel((1,1)),0);self.assertEqual(icon.getpixel((2,1)),0);self.assertNotEqual(icon.getpixel((3,1)),0)
    def test_banner_tiled_pixels_alpha_palette_title_all_versions_and_crc(self):
        icon,_=self.tool.convert_icon(SOURCE)
        for version in (1,2,3,0x103):
            with self.subTest(version=version):
                report=self.tool.verify_rom(rom_fixture(icon,version=version),icon,expected_title='ZATO Story Chain\nMainline / Native NDS\nZATO NDS')
                self.assertEqual(report['result'],'PASS');self.assertEqual(report['banner_version'],version);self.assertEqual(report['pixels_verified'],1024)
        self.assertEqual(self.tool.crc16(b'123456789'),0x4b37)
    def test_verifier_rejects_bad_pointer_version_crc_pixel_palette_and_title(self):
        icon,_=self.tool.convert_icon(SOURCE);original=rom_fixture(icon);bad=[]
        b=bytearray(original);struct.pack_into('<I',b,0x68,len(b));bad.append(b)
        b=bytearray(original);struct.pack_into('<H',b,0x400,99);bad.append(b)
        b=bytearray(original);b[0x402]^=1;bad.append(b)
        for at in (0x420,0x622):
            b=bytearray(original);b[at]^=1;struct.pack_into('<H',b,0x402,crc16(b[0x420:0xc40]));bad.append(b)
        for b in bad:
            with self.assertRaises(ValueError):self.tool.verify_rom(b,icon)
        with self.assertRaises(ValueError):self.tool.verify_rom(original,icon,expected_title='wrong title')
    def test_makefile_icon_is_explicit_rom_dependency(self):
        text=(ROOT/'nds/Makefile').read_text();self.assertIn('GAME_ICON := icon/steam.gif',text);self.assertIn('$(ROM): $(GAME_ICON)',text)

if __name__=='__main__':unittest.main()
