"""Independent straight-alpha tiles, real C decode/draw and malformed inputs."""
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HEADER = struct.Struct('<8s8I')
FRAME = struct.Struct('<4I4H2h2HI32s128s')


def fnv(data):
    value = 2166136261
    for byte in data:
        value = ((value ^ byte)*16777619) & 0xffffffff
    return value


def literal_lz(data):
    return b'\x10'+len(data).to_bytes(3, 'little')+b''.join(b'\0'+data[i:i+8] for i in range(0,len(data),8))


def fixture(words, alpha, width, height, fmt=2, x=0, y=0, bad_hash=False, stride=None):
    cols, rows = (width+7)//8, (height+7)//8
    palette = [0, 31, 31<<5, 31<<10]+list(range(4,16))
    tiles = []
    for ty in range(rows):
        for tx in range(cols):
            colors, opacity = [0]*64, bytearray(64)
            for py in range(8):
                for px in range(8):
                    sx, sy = tx*8+px, ty*8+py
                    if sx<width and sy<height:
                        index = sy*width+sx
                        colors[py*8+px] = words[index]
                        opacity[py*8+px] = alpha[index]
            if fmt==0:
                tile = struct.pack('<64H', *colors)
            elif fmt==1:
                tile = bytearray(40)
                for pos,color in enumerate(colors):
                    if opacity[pos]:
                        index = palette.index(color & 0x7fff)
                        tile[pos//2] |= index << ((pos&1)*4)
                        tile[32+pos//8] |= 1 << (pos&7)
            else:
                tile = struct.pack('<64H', *colors)+opacity
            tiles.append(bytes(tile))
    raw = b''.join(tiles)
    dictionary = literal_lz(raw)
    tilemap = literal_lz(struct.pack('<'+'H'*len(tiles), *range(len(tiles))))
    at = HEADER.size+16+FRAME.size+4
    bank = struct.pack('<4I', at, len(dictionary), len(raw), stride or {0:128,1:40,2:192}[fmt])
    image = b''.join(struct.pack('<HB',word,a) if fmt==2 else struct.pack('<H',word) for word,a in zip(words,alpha))
    frame = FRAME.pack(0,at+len(dictionary),len(tilemap),cols*rows*2,width,height,cols,rows,x,y,1,fmt,fnv(image)^bad_hash,
                       bytes(32) if fmt!=1 else struct.pack('<16H',*palette),b'fixture')
    return HEADER.pack(b'ZATONDP1',1,1,1,1,1,0,0,FRAME.size)+bank+frame+bytes(4)+dictionary+tilemap


HARNESS = r'''
#include "asset_pack.h"
#include <stdlib.h>
#include <string.h>
extern bool pack_decode_alpha(AssetPack*,BankCache*,unsigned,uint16_t*,uint8_t*,uint32_t) __attribute__((weak));
static void need(int ok,AssetPack*p,const char*what){if(!ok){fprintf(stderr,"FAIL %s: %s\n",what,p->error);exit(2);}}
static void save(const char*folder,const char*name,const void*data,size_t bytes){
 char path[1024];snprintf(path,sizeof(path),"%s/%s",folder,name);FILE*f=fopen(path,"wb");if(!f||fwrite(data,1,bytes,f)!=bytes)exit(4);fclose(f);
}
int main(int argc,char**argv){
 if(argc!=4)return 3;
 AssetPack p={0};BankCache cache={.id=-1};uint8_t*memory=NULL;
 if(strstr(argv[2],"memory")){
  FILE*f=fopen(argv[1],"rb");if(!f)return 5;fseek(f,0,SEEK_END);long n=ftell(f);rewind(f);memory=malloc((size_t)n);
  if(!memory||fread(memory,1,(size_t)n,f)!=(size_t)n)return 6;
  fclose(f);need(pack_open_memory(&p,memory,(uint32_t)n),&p,"open memory");
 }else{
#ifndef ZATO_EMBEDDED
  need(pack_open(&p,argv[1]),&p,"open file");
#else
  return 7;
#endif
 }
 unsigned pixels=(unsigned)p.frames[0].width*p.frames[0].height,capacity=strstr(argv[2],"short")?pixels-1:pixels;
 uint16_t*rgb=malloc(pixels*2),*screen=malloc(256*192*2);uint8_t*alpha=malloc(pixels);if(!rgb||!alpha||!screen)return 8;
 bool ok;uint32_t hash=0;
 if(strstr(argv[2],"draw"))ok=pack_draw(&p,&cache,0,screen,&hash);
 else if(strstr(argv[2],"alpha")){
  need(pack_decode_alpha!=NULL,&p,"pack_decode_alpha API exists");ok=pack_decode_alpha(&p,&cache,0,rgb,alpha,capacity);
 }else ok=pack_decode(&p,&cache,0,rgb,capacity);
 if(strstr(argv[2],"reject"))need(!ok,&p,"reject malformed data");
 else{
  need(ok,&p,"decode/draw");
  if(strstr(argv[2],"draw")){save(argv[3],"screen.bin",screen,256*192*2);save(argv[3],"hash.bin",&hash,4);}
  else{save(argv[3],"rgb.bin",rgb,pixels*2);if(strstr(argv[2],"alpha"))save(argv[3],"alpha.bin",alpha,pixels);}
 }
 bank_cache_clear(&cache);pack_close(&p);free(memory);free(rgb);free(alpha);free(screen);return 0;
}
'''


class AssetPackFormat2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name)
        source = cls.folder/'probe.c'
        source.write_text(HARNESS)
        cls.executables = {}
        for embedded in (False,True):
            exe = cls.folder/('embedded.exe' if embedded else 'file.exe')
            p = subprocess.run([shutil.which('gcc') or 'gcc','-std=c11','-O2','-Wall','-Wextra','-Werror',
                                *(['-DZATO_EMBEDDED'] if embedded else []),'-I'+str(ROOT/'nds/source'),
                                str(source),str(ROOT/'nds/source/asset_pack.c'),'-o',str(exe)],capture_output=True,text=True)
            if p.returncode:raise AssertionError(p.stderr)
            cls.executables[embedded] = exe

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_native(self,data,mode,embedded=False):
        path = self.folder/'fixture.zat'
        path.write_bytes(data)
        p = subprocess.run([str(self.executables[embedded]),str(path),mode,str(self.folder)],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)

    def test_format2_alpha_planes_and_legacy_wrapper_verify_the_same_source(self):
        alphas = bytes([0,1,64,128,200,248,255]*15)[:99]
        words = [0 if not a else [0x8000,0xffff,0x801f,0x83e0,0xfc00,0xc210,0x9ce7][i%7] for i,a in enumerate(alphas)]
        data = fixture(words,alphas,11,9)
        for embedded,mode in [(False,'alpha'),(False,'memory_alpha'),(True,'memory_alpha')]:
            with self.subTest(embedded=embedded,mode=mode):
                self.run_native(data,mode,embedded)
                self.assertEqual((self.folder/'rgb.bin').read_bytes(),struct.pack('<99H',*words))
                self.assertEqual((self.folder/'alpha.bin').read_bytes(),alphas)
        self.run_native(data,'decode')
        self.assertEqual((self.folder/'rgb.bin').read_bytes(),struct.pack('<99H',*words))

    def test_format2_black_blend_rounding_crop_and_viewport_hash(self):
        width,height,x,y = 17,9,-4,188
        alpha = bytes([0,1,64,128,200,248,255]*(width*height//7+1))[:width*height]
        words = [0 if not a else [0xffff,0x8000,0x801f,0x83e0,0xfc00,0xbdef,0xc210][i%7] for i,a in enumerate(alpha)]
        expected = [0]*(256*192)
        for sy in range(height):
            for sx in range(width):
                dx,dy = sx+x,sy+y
                if 0<=dx<256 and 0<=dy<192:
                    source,a = words[sy*width+sx],(alpha[sy*width+sx]+4)>>3
                    expected[dy*256+dx] = (0x8000 | sum(((((source>>shift)&31)*a+16)>>5)<<shift for shift in (0,5,10))) if a else 0
        wanted = struct.pack('<'+'H'*len(expected),*expected)
        for embedded,mode in [(False,'draw'),(False,'memory_draw'),(True,'memory_draw')]:
            self.run_native(fixture(words,alpha,width,height,x=x,y=y),mode,embedded)
            self.assertEqual((self.folder/'screen.bin').read_bytes(),wanted)
            self.assertEqual(struct.unpack('<I',(self.folder/'hash.bin').read_bytes())[0],fnv(wanted))

    def test_alpha_only_bad_hash_is_rejected_even_by_original_decode_and_cropped_draw(self):
        data = fixture([0xffff]*9,bytes([128]*9),9,1,x=-8)
        # Rebuild with a different alpha plane but retain the original full hash.
        changed = bytearray(fixture([0xffff]*9,bytes([200]+[128]*8),9,1,x=-8))
        changed[88:92] = data[88:92]
        for mode in ['reject_decode','reject_alpha','reject_draw','memory_reject_draw']:
            self.run_native(changed,mode)

    def test_format2_rejects_noncanonical_pixels_bad_geometry_stride_hash_and_capacity(self):
        valid = fixture([0xffff],b'\xff',1,1)
        corrupt = []
        for offset,value in [(52,128),(52,40),(48,193),(48,262272)]:
            bad = bytearray(valid);struct.pack_into('<I',bad,offset,value);corrupt.append(bad)
        for offset,value in [(86,3),(76,2),(78,2)]:
            bad = bytearray(valid);struct.pack_into('<H',bad,offset,value);corrupt.append(bad)
        # Individually valid old dictionary shape paired with the new format.
        bad = bytearray(fixture([0xffff]*64,bytes([255]*64),8,8,fmt=0))
        struct.pack_into('<H',bad,86,2);corrupt.append(bad)
        bad = bytearray(valid)
        map_offset = struct.unpack_from('<I',bad,60)[0]
        struct.pack_into('<H',bad,map_offset+5,1);corrupt.append(bad)
        corrupt += [fixture([0xffff],b'\xff',1,1,bad_hash=True),fixture([0x8000],b'\0',1,1),fixture([0x001f],b'\x80',1,1)]
        for i,data in enumerate(corrupt):
            with self.subTest(case=i):
                self.run_native(data,'reject_decode')
                self.run_native(data,'memory_reject_draw',True)
        self.run_native(valid,'reject_short_alpha')

    def test_old_format0_and_format1_rgb_hash_and_draw_are_unchanged(self):
        for fmt in (0,1):
            alpha = bytes(255 if i%3 else 0 for i in range(64))
            words = ([0,0x8000,0xffff,0x1234,0xfc00,0x83e0,0x801f,0x7fff]*8 if fmt==0
                     else [([0,31,31<<5,31<<10][i%4]|0x8000) if alpha[i] else 0 for i in range(64)])
            data = fixture(words,alpha,8,8,fmt=fmt,x=253,y=-4)
            self.run_native(data,'decode')
            self.assertEqual((self.folder/'rgb.bin').read_bytes(),struct.pack('<64H',*words))
            self.run_native(data,'memory_alpha',True)
            self.assertEqual((self.folder/'rgb.bin').read_bytes(),struct.pack('<64H',*words))
            self.assertEqual((self.folder/'alpha.bin').read_bytes(),bytes(255 if c&0x8000 else 0 for c in words))
            self.run_native(data,'draw')
            expected = [0]*(256*192)
            for sy in range(4,8):
                for sx in range(3):expected[(sy-4)*256+253+sx] = words[sy*8+sx]
            self.assertEqual((self.folder/'screen.bin').read_bytes(),struct.pack('<'+'H'*len(expected),*expected))


if __name__=='__main__':
    unittest.main()
