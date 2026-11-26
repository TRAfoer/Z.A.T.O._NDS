"""Real reader/scroll work between cooperative service calls, with GCC trace-pc.

Basic-block counts are deterministic work proxies, not NDS cycle estimates.
The separately compiled harness/callback is never coverage-instrumented.
"""
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = r'''
#include "story_text.h"
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
static uint64_t blocks,last_service,longest;
static unsigned services,active;
void __sanitizer_cov_trace_pc(void){if(active)blocks++;}
static void service(void){
 if(active){uint64_t gap=blocks-last_service;if(gap>longest)longest=gap;last_service=blocks;services++;}
}
static void start(void){blocks=last_service=longest=services=0;active=1;}
static void finish(unsigned id,const char*phase){
 active=0;if(blocks-last_service>longest)longest=blocks-last_service;
 printf("{\"id\":%u,\"phase\":\"%s\",\"blocks\":%llu,\"service_calls\":%u,\"max_silent_blocks\":%llu,\"resident\":%lu,\"peak\":%lu}\n",
 id,phase,(unsigned long long)blocks,services,(unsigned long long)longest,(unsigned long)story_text_resident_bytes(),(unsigned long)story_text_peak_bytes());
}
static void need(int ok,const char*what){if(!ok){fprintf(stderr,"%s: %s\n",what,story_text_error());exit(2);}}
int main(int argc,char**argv){
 need(argc>=3,"pack and IDs");story_text_set_service(service);start();need(story_text_open(argv[1]),"open");finish(0,"open");
 static uint16_t screen[49152];
 for(int a=2;a<argc;a++){
  unsigned id=(unsigned)strtoul(argv[a],NULL,10);start();need(story_text_select(id,0),"select");finish(id,"select");
  const SceneTextInfo*t=story_text_info(id);unsigned visible=t->char_count;
  for(unsigned bi=0;bi<2;bi++){
   char name[64];start();unsigned max=story_text_scroll_max(id,visible,bi);snprintf(name,sizeof(name),"layout_%u",bi);finish(id,name);
   start();(void)story_text_scroll_for_visible(id,visible,bi);snprintf(name,sizeof(name),"for_visible_%u",bi);finish(id,name);
   start();(void)story_text_scroll_follow(id,visible,bi,0,t->new_char_start);snprintf(name,sizeof(name),"follow_%u",bi);finish(id,name);
   unsigned reads=story_text_read_calls();
   for(unsigned row=0;row<=max;row++){
    StoryTextViewport v;start();story_text_draw_lower_scroll(id,visible,row,bi,1,0,screen,&v);
    snprintf(name,sizeof(name),"draw_%u_row%u",bi,row);finish(id,name);
   }
   need(story_text_read_calls()==reads,"draw/query zero IO");
  }
 }
 need(story_text_select(7619,0)&&story_text_select(10389,1),"two pinned maximum row tables");
 for(unsigned bi=0;bi<2;bi++)for(unsigned i=0;i<2;i++){
  unsigned id=i?10389:7619;const SceneTextInfo*t=story_text_info(id);(void)story_text_scroll_max(id,t->char_count,bi);
 }
 start();finish(0,"two_slot_layout_resident");story_text_close();need(story_text_resident_bytes()==0,"close releases memory");return 0;
}
'''


def compile_probe(folder, coverage=True):
    gcc = shutil.which('gcc') or 'gcc'
    flags = ['-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-I'+str(ROOT/'nds/source')]
    objects = []
    for name in ['story_text', 'asset_pack']:
        output = folder/(name+'.o')
        command = [gcc, *flags, *(['-fsanitize-coverage=trace-pc'] if coverage else []), '-c', str(ROOT/'nds/source'/(name+'.c')), '-o', str(output)]
        p = subprocess.run(command, capture_output=True, text=True)
        if p.returncode: raise AssertionError(p.stderr)
        objects.append(output)
    source = folder/'probe.c'
    source.write_text(HARNESS)
    output = folder/'probe.exe'
    p = subprocess.run([gcc, *flags, str(source), *map(str, objects), '-o', str(output)], capture_output=True, text=True)
    if p.returncode: raise AssertionError(p.stderr)
    return output


def run_probe(exe, ids):
    p = subprocess.run([str(exe), str(ROOT/'nds/nitrofs/story/text.bin'), *map(str, ids)], capture_output=True, text=True)
    if p.returncode: raise AssertionError(p.stdout+p.stderr)
    return [json.loads(line) for line in p.stdout.splitlines()]


class StoryTextServiceBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name)
        cls.rows = run_probe(compile_probe(cls.folder), [0, 197, 974, 989, 7619, 8192, 9158, 10389, 11231, 11902])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_bounded_service_work_for_loaded_layout_queries_and_redraw(self):
        # A 12px row contains at most a few thousand basic blocks of glyph
        # work. Keep cooperative checkpoints below this fixed work ceiling,
        # independent of total record/line count or scroll origin.
        measured = [r for r in self.rows if r['phase']!='two_slot_layout_resident']
        worst = max(measured, key=lambda r:r['max_silent_blocks'])
        self.assertLessEqual(worst['max_silent_blocks'], 20000, worst)

    def test_row_cache_remains_small_with_two_pinned_records(self):
        row = next(r for r in self.rows if r['phase']=='two_slot_layout_resident')
        self.assertLess(row['resident'], 540000)

    def test_checksum_startup_and_sentence_load_do_not_dominate_cpu_work(self):
        opened = next(r for r in self.rows if r['phase']=='open')
        selected = max((r for r in self.rows if r['phase']=='select'), key=lambda r:r['blocks'])
        self.assertLess(opened['blocks'], 2500000, opened)
        self.assertLess(selected['blocks'], 80000, selected)


if __name__=='__main__':
    unittest.main()
