"""Independent split-poem wire fixture; real streamed C reader and rasterizer."""
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[2]


def commands(gids, xs, ys, pages):
    return (struct.pack('<' + 'H' * len(gids), *gids) + bytes(xs) + bytes(ys)
            + bytes(pages) + b'\0\0')


def fixture(change=None, kind=4):
    # CN and original RU/EN are independent streams with the same page count.
    cn = commands([0, 0], [31, 60], [35, 35], [0, 1])
    original = commands([1, 2, 1, 2], [9, 19, 29, 39], [24, 38, 24, 38], [0, 0, 1, 1])
    f = [1, 2, 0, 0, 2, 2, 3, 0, 0, 48, 54, 54, 2, 54 + len(cn), 4, 0, kind, 0,
         0, 0, 0, 0, 0, 0]
    raw = bytearray(struct.pack('<24H', *f) + struct.pack('<3H', 1, 2, 0) + cn + original)
    if change:
        change(raw, f)
    glyph_at = 148
    atlas_at = glyph_at + 3 * 8
    header_at = (atlas_at + 3 + 3) & ~3
    commands_at = header_at + 8
    end = commands_at + 106 * 10
    h = [2, end + len(raw), 0, 1, 0, 3, 1, 0, 136, glyph_at, atlas_at, 3,
         header_at, commands_at, 106, 0, 1, 11, 95, 0, end, 0, end,
         len(raw), 160, len(raw), 0, end - 136, 0, 4, 12, 0]
    shared = bytearray(struct.pack('<8s32I', b'ZATSTX02', *h))
    shared.extend(struct.pack('<IHHI', end, len(raw) | 0x8000, len(raw), zlib.crc32(raw)))
    for at, width, height in [(0, 1, 1), (1, 2, 1), (2, 1, 2)]:
        shared.extend(struct.pack('<IHH', at, width, (width << 8) | height))
    shared.extend(b'\x80\xc0\xc0')
    shared.extend(bytes(header_at - len(shared)))
    shared.extend(struct.pack('<II', 0, 0))
    shared.extend(struct.pack('<HBBHHBB', 0, 8, 178, 0xffff, 0, 0, 1))
    shared.extend(struct.pack('<HBBHHBB', 0, 0, 0, 0xffff, 0, 0, 1) * 105)
    struct.pack_into('<I', shared, 16, zlib.crc32(shared))
    return shared + raw


HARNESS = r'''
#include "story_text.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
extern int story_text_is_poem(unsigned) __attribute__((weak));
extern void story_text_draw_lower_poem(unsigned,unsigned,int,int,uint16_t*) __attribute__((weak));
static void need(int yes,const char*s){if(!yes){fprintf(stderr,"FAIL %s (%s)\n",s,story_text_error());exit(2);}}
static uint16_t guarded[256*192+2],other[256*192];
static void blank(uint16_t*p){for(unsigned i=0;i<256*192;i++)p[i]=0x8842;}
int main(int argc,char**argv){
 need(argc==3,"arguments");need(story_text_open(argv[1]),"open tiny package");
 if(!strcmp(argv[2],"reject")){need(!story_text_select(0,1),"reject malformed split-poem stream");story_text_close();return 0;}
 need(story_text_is_poem&&story_text_draw_lower_poem,"split-poem API exists");
 need(!story_text_is_poem(0),"uncached existing ID is not loaded by getter");
 need(story_text_select(0,1),"accept split-poem record");
 unsigned reads=story_text_read_calls();
 if(!strcmp(argv[2],"legacy")){
  need(!story_text_is_poem(0),"ordinary literal stays ordinary");
  blank(guarded+1);story_text_draw_upper_bilingual(0,2,0,0,guarded+1);
  need(guarded[1+35*256+31]==0xffff&&guarded[1+24*256+9]==0x8842,"legacy CN upper plane");
  blank(guarded+1);story_text_draw_upper_bilingual(0,2,0,1,guarded+1);
  need(guarded[1+24*256+9]==0xffff&&guarded[1+35*256+31]==0x8842,"legacy original upper plane");
 }else{
  need(story_text_is_poem(0)&&!story_text_is_poem(99),"explicit cached kind getter");
  guarded[0]=0x1234;guarded[256*192+1]=0x5678;
  blank(guarded+1);story_text_draw_upper_bilingual(0,0,0,0,guarded+1);
  need(guarded[1+24*256+9]==0xffff&&guarded[1+24*256+10]==0xffff,"RU upper plane white ink");
  need(guarded[1+38*256+19]==0xffff&&guarded[1+39*256+19]==0xffff,"English upper alternating line");
  need(guarded[1+35*256+31]==0x8842,"CN does not leak to upper");
  blank(other);story_text_draw_upper_bilingual(0,0,0,1,other);
  need(!memcmp(guarded+1,other,sizeof(other)),"BI flag cannot replace split-poem original");
  blank(other);story_text_draw_upper(0,0,99,other);
  need(other[24*256+29]==0xffff&&other[38*256+39]==0xffff&&other[24*256+9]==0x8842,"upper page clamps without mixing pages");
  story_text_draw_lower_poem(0,0,1,0,guarded+1);
  need(guarded[1+35*256+31]==0xffff&&guarded[1+10*256+4]==0xffff,"CN lower plane and frame");
  need(guarded[1+24*256+9]==0x8000&&guarded[1+38*256+19]==0x8000,"original does not leak to lower");
  story_text_draw_lower_poem(0,99,1,1,guarded+1);
  need(guarded[1+35*256+60]==0xffff&&guarded[1+35*256+31]==0x8000,"lower page clamps without mixing pages");
  need(guarded[1+178*256+8]==0xffff,"requested footer retained");
  story_text_draw_lower_poem(0,0,0,0,guarded+1);
  for(unsigned i=0;i<256*192;i++)need(guarded[i+1]==0x8000,"hidden poem lower is clear");
  story_text_draw_lower_poem(99,0,1,0,guarded+1);
  need(guarded[1+35*256+31]==0x8000&&!story_text_is_poem(99),"uncached getters and draw do not load");
  need(guarded[0]==0x1234&&guarded[256*192+1]==0x5678,"both screen guards intact");
 }
 need(story_text_read_calls()==reads,"all cached getters and draws perform zero I/O");
 story_text_close();need(!story_text_is_poem(0),"close clears poem presence");return 0;
}
'''


class StoryPoemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='zato_poem_')
        cls.folder = Path(cls.temp.name)
        src = cls.folder / 'poem.c'
        src.write_text(HARNESS)
        cls.exe = cls.folder / 'poem.exe'
        run = subprocess.run([shutil.which('gcc'), '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                              '-I' + str(ROOT / 'nds/source'), str(src), str(ROOT / 'nds/source/story_text.c'),
                              str(ROOT / 'nds/source/asset_pack.c'), '-o', str(cls.exe)], capture_output=True, text=True)
        if run.returncode:
            raise AssertionError(run.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def check(self, data, mode='valid'):
        path = self.folder / 'poem.bin'
        path.write_bytes(data)
        run = subprocess.run([str(self.exe), str(path), mode], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_independent_upper_original_lower_cn_pages_bi_and_zero_io(self):
        self.check(fixture())

    def test_legacy_literal_language_switch_is_unchanged(self):
        self.check(fixture(kind=2), 'legacy')

    def test_reject_missing_original_stream(self):
        def empty_original(raw, f):
            struct.pack_into('<H', raw, 28, 0)
            raw[f[13]:] = b'\0\0'
        self.check(fixture(empty_original), 'reject')

    def test_reject_poem_source_timeline(self):
        self.check(fixture(lambda b, f: struct.pack_into('<H', b, 4, 1)), 'reject')

    def test_reject_mismatched_plane_page_counts(self):
        self.check(fixture(lambda b, f: struct.pack_into('<H', b, 10, 3)), 'reject')

    def test_reject_noncentered_poem(self):
        self.check(fixture(lambda b, f: struct.pack_into('<H', b, 12, 0)), 'reject')

    def test_reject_out_of_range_cn_page(self):
        self.check(fixture(lambda b, f: b.__setitem__(f[11] + f[12] * 4, 2)), 'reject')

    def test_reject_out_of_range_original_page(self):
        self.check(fixture(lambda b, f: b.__setitem__(f[13] + f[14] * 4, 2)), 'reject')

    def test_reject_original_ink_outside_screen(self):
        self.check(fixture(lambda b, f: b.__setitem__(f[13] + f[14] * 2, 255)), 'reject')

    def test_reject_cn_ink_outside_screen(self):
        self.check(fixture(lambda b, f: b.__setitem__(f[11] + f[12] * 3, 192)), 'reject')

    def test_reject_unknown_kind(self):
        self.check(fixture(kind=5), 'reject')


if __name__ == '__main__':
    unittest.main()
