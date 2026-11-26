"""Package-tagged expanded body area, with old-package viewport compatibility."""
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib
from test_story_text_format4 import fixture

ROOT = Path(__file__).resolve().parents[2]


def policy_fixture(policy):
    def alter(raw, f):
        # Two native8px rows separated by136px fit the new viewport exactly.
        struct.pack_into('<H', raw, f[11], 0)
        raw[f[11] + 2 * 3] = 30
        raw[f[11] + 2 * 3 + 1] = 166
    data = fixture(alter)
    struct.pack_into('<H', data, 154, 0x0108)
    struct.pack_into('<I', data, 8 + 31 * 4, policy)
    end = struct.unpack_from('<I', data, 8 + 22 * 4)[0]
    struct.pack_into('<I', data, 16, 0)
    struct.pack_into('<I', data, 16, zlib.crc32(data[:end]))
    return data


HARNESS = r'''
#include "story_text.h"
#include <stdio.h>
#include <stdlib.h>
static void need(int yes,const char*s){if(!yes){fprintf(stderr,"FAIL %s (%s)\n",s,story_text_error());exit(2);}}
int main(int argc,char**argv){
 need(argc==3,"arguments");unsigned policy=(unsigned)atoi(argv[2]);
 if(policy>1){need(!story_text_open(argv[1]),"unknown layout policy rejected");return 0;}
 need(story_text_open(argv[1])&&story_text_select(0,0),"open native8 ordinary record");
 unsigned before=story_text_read_calls();uint16_t pixels[256*192];StoryTextViewport v;
 need(story_text_legacy_layout_compatible(0)==!policy,"legacy classification follows package reflow policy");
 story_text_draw_lower_scroll(0,2,0,0,1,0,pixels,&v);
 need(pixels[(policy?30:35)*256+30]==0xffff,"scroll origin follows package policy");
 need(v.cn_end==(policy?2u:1u),"draw coverage follows expanded viewport");
 need(story_text_scroll_for_visible(0,2,0)==(policy?0u:1u),"anchor query uses same viewport as drawing");
 need(story_text_scroll_follow(0,2,0,0,1)==(policy?0u:1u),"follow uses same viewport as drawing");
 need(story_text_read_calls()==before,"policy queries and draw have no I/O");story_text_close();return 0;
}
'''


class StoryTextLayoutPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='zato_body_policy_')
        cls.folder = Path(cls.temp.name)
        source = cls.folder / 'policy.c'
        source.write_text(HARNESS)
        cls.exe = cls.folder / 'policy.exe'
        run = subprocess.run([shutil.which('gcc'), '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                              '-I' + str(ROOT / 'nds/source'), str(source), str(ROOT / 'nds/source/story_text.c'),
                              str(ROOT / 'nds/source/asset_pack.c'), '-o', str(cls.exe)], capture_output=True, text=True)
        if run.returncode:
            raise AssertionError(run.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def check(self, policy):
        path = self.folder / 'policy.bin'
        path.write_bytes(policy_fixture(policy))
        run = subprocess.run([str(self.exe), str(path), str(policy)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_old_package_retains_native_legacy_layout_and_y35(self):
        self.check(0)

    def test_new_package_reflows_native_legacy_layout_and_uses_y30(self):
        self.check(1)

    def test_unknown_layout_policy_is_rejected(self):
        self.check(2)


if __name__ == '__main__':
    unittest.main()
