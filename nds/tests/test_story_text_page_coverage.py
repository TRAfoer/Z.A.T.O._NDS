"""Every newly authored glyph must reach a committed page, including fast NW."""
from pathlib import Path
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'nds/tools'))
from pack_assets import unlz10

HARNESS=r'''
#include "story_flow.h"
#include "story_text.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
static bool select_text(void*c,unsigned id,unsigned slot){(void)c;return story_text_select(id,slot)!=0;}
static const SceneTextInfo*info(void*c,unsigned id){(void)c;return story_text_info(id);}
static const SceneTextPause*pause_at(void*c,unsigned id,unsigned at){(void)c;return story_text_pause(id,at);}
static unsigned visible(void*c,unsigned id,uint32_t tick){(void)c;return story_text_visible(id,tick);}
static unsigned page(void*c,unsigned id,unsigned count,int bi){(void)c;return story_text_page(id,count,bi);}
static unsigned limit(void*c,unsigned id,unsigned p,int bi){(void)c;return story_text_page_limit(id,p,bi);}
int main(int argc,char**argv){
 assert((argc==4||argc==5) && story_text_open(argv[1]));unsigned id=(unsigned)strtoul(argv[2],NULL,10);int bi=atoi(argv[3]);
 assert(story_text_select(id,0));const SceneTextInfo*t=story_text_info(id);
 StoryEvent events[]={{.op=ST_TEXT,.flags=1|(t->mode<<1),.a=(int32_t)id,.d=(int32_t)t->duration_ticks},{.op=ST_END}};
 StoryChapter chapter={.first_pc=0,.end_pc=2,.first_text=id,.text_count=1};
 StoryScript script={.count=2,.chapter_count=1,.text_count=11975,.events=events,.chapters=&chapter};
 StoryFlowOps ops={.select_text=select_text,.info=info,.pause=pause_at,.visible=visible,.page=page,.limit=limit};StoryFlow flow;
 assert(story_flow_init(&flow,&script,&ops,NULL));if(bi)story_flow_toggle_language(&flow);
 if(argc==5){unsigned first=flow.page;for(unsigned i=0;i<64;i++)story_flow_tick(&flow,1);assert(!flow.done&&flow.page==first);}
 unsigned frames=0;uint16_t screen[256*192];
 while(!flow.done && frames<4096){
  assert(flow.blocked && !flow.failed);
  unsigned reads=story_text_read_calls();for(unsigned i=0;i<256*192;i++)screen[i]=0x8000;
  if(t->mode==3)story_text_draw_upper(id,flow.visible,flow.page,screen);
  else story_text_draw_lower(id,flow.visible,flow.page,bi,1,1,screen);
  unsigned english=story_text_english_visible(id,flow.visible,flow.page),ink=0;
  for(unsigned y=24;y<168;y++)for(unsigned x=8;x<248;x++)if(screen[y*256+x]==0xca52)ink++;
  assert(story_text_read_calls()==reads);
  printf("%u %u %u %u %u %u %u\n",frames,flow.local,flow.visible,flow.page,flow.pause_index,english,ink);
  story_flow_mark_presented(&flow);story_flow_tick(&flow,1);frames++;
 }
 assert(flow.done && !flow.failed);story_text_close();return 0;
}
'''


def native_record(id,bilingual=False):
    data=(ROOT/'nds/nitrofs/story/text.bin').read_bytes();h=struct.unpack_from('<32I',data,8)
    at,flags,raw_size,crc=struct.unpack_from('<IHHI',data,h[8]+id*12)
    assert h[29]==4,'Paired page coverage requires the actual format4 story package'
    raw=data[at:at+(flags&32767)];raw=raw if flags&32768 else unlz10(raw);f=struct.unpack_from('<24H',raw)
    def commands(at,count,first):
        gids=struct.unpack_from('<'+'H'*count,raw,at)
        return [(first+i,raw[at+count*4+i]) for i,gid in enumerate(gids) if gid!=65535]
    cn=commands(f[18],f[19],0) if bilingual and f[19] else commands(f[11],f[12],0)
    english=[]
    for index,page in commands(f[13],f[14],0):
        gid=struct.unpack_from('<H',raw,f[13]+index*2)[0]
        bitmap,width,packed_height=struct.unpack_from('<IHH',data,h[9]+(gid&0x3fff)*8);height=packed_height&255
        has_ink=bool(gid&0x4000) or any(data[h[10]+bitmap:h[10]+bitmap+(width*height+7)//8])
        english.append(dict(index=index,page=page,reveal=struct.unpack_from('<H',raw,f[20]+index*2)[0] if f[19] else f[1],ink=has_ink))
    return f,cn,english


class StoryTextPageCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.directory=Path(cls.temp.name)
        source=cls.directory/'page_coverage.c';source.write_text(HARNESS,encoding='utf8');cls.exe=cls.directory/'page_coverage.exe'
        cc=os.environ.get('CC') or shutil.which('gcc') or shutil.which('clang')
        if not cc:raise unittest.SkipTest('host C compiler unavailable')
        subprocess.run([cc,'-std=c11','-O2','-Wall','-Wextra','-Werror','-I'+str(ROOT/'nds/source'),str(source),str(ROOT/'nds/source/story_flow.c'),str(ROOT/'nds/source/story_text.c'),str(ROOT/'nds/source/asset_pack.c'),'-o',str(cls.exe)],check=True,capture_output=True,text=True)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def states(self,id,bi=0,withhold=False):
        command=[str(self.exe),str(ROOT/'nds/nitrofs/story/text.bin'),str(id),str(bi)]+(['withhold'] if withhold else [])
        run=subprocess.run(command,check=True,capture_output=True,text=True)
        return [tuple(map(int,line.split())) for line in run.stdout.splitlines()]

    def test_all_four_authored_fast_automatic_multpage_records_present_each_new_glyph(self):
        for id in (4297,4298,4299,9158):
            with self.subTest(text_id=id):
                fields,cn,en=native_record(id);wanted={logical for logical,page in cn if logical>=fields[8]}
                seen=set();states=self.states(id)
                for frame,local,visible,page,pause,english,ink in states:seen|={logical for logical,p in cn if p==page and logical<visible}
                self.assertFalse(wanted-seen,f'text{id} new glyphs never submitted: {sorted(wanted-seen)}; states={states}')
                self.assertEqual(states[-1][2],fields[1])

    def test_bilingual_auto_pages_submit_every_new_english_glyph_at_paired_threshold(self):
        records=json.loads((ROOT/'nds/artifacts/story_text_manifest.json').read_text(encoding='utf8'))['records']
        ids=[r['id'] for r in records if r['kind']=='say' and r['automatic'] and r['pages_bi']>1 and r.get('paired')]
        for id in ids:
            fields,cn,en=native_record(id,True);seen=set();cn_seen=set();states=self.states(id,1)
            record=records[id];new_en=record.get('new_en_start',record['segments'][-1]['en_start'])
            for frame,local,visible,page,pause,english,ink in states:
                printed={entry['index'] for entry in en if entry['page']==page and entry['reveal']<=visible}
                self.assertEqual(english,len(printed),f'text{id} native EN count differs on submitted page{page}')
                if any(entry['index'] in printed and entry['ink'] for entry in en):self.assertGreater(ink,0,f'text{id} reported EN but rendered no gray ink')
                seen|=printed;cn_seen|={logical for logical,p in cn if p==page and logical<visible}
            wanted={entry['index'] for entry in en if entry['index']>=new_en}
            self.assertFalse(wanted-seen,f'text{id} automatic BI skipped new EN glyphs: {sorted(wanted-seen)}')
            wanted_cn={logical for logical,page in cn if logical>=fields[8]}
            self.assertFalse(wanted_cn-cn_seen,f'text{id} automatic BI skipped new paired CN glyphs')

    def test_auto_page_tail_cannot_flip_without_a_real_presentation_ack(self):
        states=self.states(9158,withhold=True)
        fields,cn,en=native_record(9158);cap=min((logical for logical,page in cn if page>0),default=fields[1])
        self.assertEqual(states[0][2:4],(cap,0))

    def test_internal_wait_at_page_boundary_finishes_before_flipping(self):
        states=self.states(4297)
        first_next=next(state for state in states if state[3]==1)
        self.assertGreaterEqual(first_next[4],1,'page flipped before its pending authored wait was acknowledged')


if __name__=='__main__':unittest.main()
