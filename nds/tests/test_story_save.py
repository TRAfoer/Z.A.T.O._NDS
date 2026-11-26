from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import struct
import zlib
ROOT=Path(__file__).resolve().parents[1]

class ChapterSkipPreferenceTests(unittest.TestCase):
    """Real C store against independently encoded FORMAT1 global banks."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='zato_chapter_skip_')
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.path=self.folder/'game.sav'
        source=self.folder/'probe.c'
        source.write_text(r'''#include <stdio.h>
#include <stddef.h>
#include <string.h>
#include "story_save.h"
_Static_assert(offsetof(StorySaveGlobals,unlocks)==1496,"existing globals ABI");
_Static_assert(offsetof(StorySaveGlobals,language)==1504,"existing globals ABI");
_Static_assert(offsetof(StorySaveGlobals,play_ticks)==1516,"existing globals ABI");
_Static_assert(offsetof(StorySaveGlobals,chapter_skip)==1520,"append globals ABI");
int main(int argc,char **argv){
    if(argc!=3)return 2;
    unsigned char id[32];for(unsigned i=0;i<32;i++)id[i]=(unsigned char)i;
    int opened=story_save_open(argv[1],id);
    printf("open=%d available=%d",opened,story_save_available());
    if(opened){
        StorySaveGlobals*g=story_save_globals();
        if(!strcmp(argv[2],"language")){
            g->language=0;printf(" commit=%d",story_save_commit_globals());
        }
        if(!strcmp(argv[2],"enable")||!strcmp(argv[2],"disable")||!strcmp(argv[2],"invalid")){
            g->chapter_skip=!strcmp(argv[2],"enable")?1:!strcmp(argv[2],"invalid")?2:0;
            printf(" commit=%d",story_save_commit_globals());
        }
        printf(" language=%u music=%u sfx=%u ticks=%u unlock0=%u unlock1=%u seen=%u chapter_skip=%u",
            g->language,g->music_q12,g->sfx_q12,g->play_ticks,
            g->unlocks[0],g->unlocks[1],g->seen[1493],g->chapter_skip);
    }
    putchar('\n');story_save_close();return 0;
}''',encoding='utf8')
        self.exe=self.folder/'probe.exe'
        build=subprocess.run([shutil.which('gcc') or 'gcc','-std=gnu11','-Wall','-Wextra','-Werror',
            '-I',str(ROOT/'source'),str(source),str(ROOT/'source/story_save.c'),'-o',str(self.exe)],capture_output=True,text=True)
        self.assertEqual(build.returncode,0,build.stdout+build.stderr)

    @staticmethod
    def fixture(chapter_skip=0):
        data=bytearray(524288)
        for bank,seq in ((0,7),(8192,8)):
            data[bank:bank+8]=b'ZATSAV01'
            struct.pack_into('<2I',data,bank+8,1,524288)
            data[bank+16:bank+48]=bytes(range(32))
            struct.pack_into('<5I',data,bank+48,seq,1,3072,2048,99)
            data[bank+68+1493]=0x40
            struct.pack_into('<3I',data,bank+1564,0x12345678,8,chapter_skip)
            struct.pack_into('<I',data,bank+8188,zlib.crc32(data[bank:bank+8188]))
        data[-16:]=b'legacy-tail-data'
        return bytes(data)

    def run_store(self,mode='open'):
        result=subprocess.run([str(self.exe),str(self.path),mode],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return dict(item.split('=') for item in result.stdout.split())

    def test_old_format1_zero_reserved_loads_without_rewrite(self):
        old=self.fixture();self.path.write_bytes(old)
        fields=self.run_store()
        self.assertEqual(fields,dict(open='1',available='1',language='1',music='3072',sfx='2048',
            ticks='99',unlock0=str(0x12345678),unlock1='8',seen='64',chapter_skip='0'))
        self.assertEqual(self.path.read_bytes(),old)

    def test_new_save_defaults_off_and_toggle_persists_across_processes(self):
        self.assertEqual(self.run_store()['chapter_skip'],'0')
        data=self.path.read_bytes()
        self.assertEqual(len(data),524288)
        for base in (0,8192):
            self.assertEqual(struct.unpack_from('<I',data,base+1572)[0],0)
        self.assertEqual(self.run_store('enable')['commit'],'1')
        self.assertEqual(self.run_store()['chapter_skip'],'1')
        self.assertEqual(self.run_store('disable')['commit'],'1')
        self.assertEqual(self.run_store()['chapter_skip'],'0')
        data=self.path.read_bytes()
        for base,seq,value in ((0,3,0),(8192,2,1)):
            self.assertEqual(struct.unpack_from('<I',data,base+8)[0],1,'container stays FORMAT1')
            self.assertEqual(data[base+16:base+48],bytes(range(32)),'story fingerprint stays unchanged')
            self.assertEqual(struct.unpack_from('<I',data,base+48)[0],seq)
            self.assertEqual(struct.unpack_from('<I',data,base+1572)[0],value)
            self.assertEqual(zlib.crc32(data[base:base+8188]),struct.unpack_from('<I',data,base+8188)[0])

    def test_invalid_ram_setting_refuses_commit_without_file_write(self):
        old=self.fixture();self.path.write_bytes(old)
        self.assertEqual(self.run_store('invalid')['commit'],'0')
        self.assertEqual(self.path.read_bytes(),old)
        self.assertEqual(self.run_store()['chapter_skip'],'0')

    def test_enabled_wire_preference_survives_unrelated_commit(self):
        self.path.write_bytes(self.fixture(1))
        self.assertEqual(self.run_store('language')['commit'],'1')
        data=self.path.read_bytes()
        self.assertEqual(struct.unpack_from('<I',data,48)[0],9)
        self.assertEqual(struct.unpack_from('<I',data,1572)[0],1,
            'committing another setting must retain the enabled chapter preference')
        self.assertEqual(zlib.crc32(data[:8188]),struct.unpack_from('<I',data,8188)[0])
        self.assertEqual(self.run_store()['chapter_skip'],'1')

    def test_crc_valid_out_of_range_chapter_preference_is_rejected_untouched(self):
        for value in (2,0xffffffff):
            with self.subTest(value=value):
                invalid=self.fixture(value);self.path.write_bytes(invalid)
                self.assertEqual(self.run_store(),dict(open='0',available='0'))
                self.assertEqual(self.path.read_bytes(),invalid)

    def test_invalid_latest_bank_recovers_older_disabled_generation(self):
        data=bytearray(self.fixture())
        struct.pack_into('<I',data,8192+1572,2)
        struct.pack_into('<I',data,8192+8188,zlib.crc32(data[8192:8192+8188]))
        self.path.write_bytes(data)
        self.assertEqual(self.run_store()['chapter_skip'],'0')
        self.assertEqual(self.path.read_bytes(),data)

class StorySaveStorageTests(unittest.TestCase):
    def test_real_c_container_faults_and_repair(self):
        with tempfile.TemporaryDirectory(prefix='zato_store_') as folder:
            exe=Path(folder)/'validate.exe'
            build=subprocess.run([shutil.which('gcc') or 'gcc','-std=gnu11','-Wall','-Wextra','-Werror',str(ROOT/'tests/validate_story_save.c'),'-o',str(exe)],capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stdout+build.stderr)
            run=subprocess.run([str(exe),str(Path(folder)/'game.sav')],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            self.assertEqual((Path(folder)/'game.sav').stat().st_size,524288)
            data=(Path(folder)/'game.sav').read_bytes()
            for base in (0,8192):
                self.assertEqual(data[base:base+8],b'ZATSAV01')
                self.assertEqual(zlib.crc32(data[base:base+8188]),struct.unpack_from('<I',data,base+8188)[0])
            base=24576+115*4096;record=bytearray(data[base:base+4096])
            self.assertEqual(record[:8],b'ZATSLOT1')
            self.assertEqual(struct.unpack_from('<I',record,44)[0],115)
            self.assertEqual(struct.unpack_from('<2I',record,80),(768,3072))
            self.assertEqual(record[1024:],b'\xa7'*3072,'CRC must live in metadata, never overwrite thumbnail tail')
            checksum=struct.unpack_from('<I',record,252)[0];record[252:256]=bytes(4)
            self.assertNotEqual(zlib.crc32(record),checksum,'C intentionally corrupted this checkpoint')
            record[300]^=1;self.assertEqual(zlib.crc32(record),checksum)
            print(run.stdout.strip())

    def test_fat_paths_mount_failure_and_no_nitro_relative_saves(self):
        with tempfile.TemporaryDirectory(prefix='zato_fat_path_') as folder:
            d=Path(folder);(d/'fat.h').write_text('#include <stdbool.h>\nbool fatInitDefault(void);\n',encoding='utf8')
            (d/'probe.c').write_text('''#include <assert.h>
#include <stdbool.h>
#include <string.h>
#include "story_save.h"
#include "story_save_fat.h"
bool fatInitDefault(void){return false;}
int main(void){
char out[512];char*a[]={"fat:/games/Story.NDS"};
assert(story_save_fat_path(out,sizeof(out),1,a)&&!strcmp(out,"fat:/games/Story.sav"));
char*b[]={"sd:/a/b.nds"};assert(story_save_fat_path(out,sizeof(out),1,b)&&!strcmp(out,"sd:/a/b.sav"));
char*c[]={"nitro:/game.nds"};assert(story_save_fat_path(out,sizeof(out),1,c)&&!strcmp(out,"fat:/ZATO_Story_Chain.sav"));
char*e[]={"fat:/../game.nds"};assert(story_save_fat_path(out,sizeof(out),1,e)&&!strcmp(out,"fat:/ZATO_Story_Chain.sav"));
assert(story_save_fat_path(out,sizeof(out),0,0)&&!strcmp(out,"fat:/ZATO_Story_Chain.sav"));
assert(!story_save_fat_path(out,3,1,a));
unsigned char id[32]={1};assert(!story_save_fat_open(1,a,id));assert(!story_save_available()&&*story_save_error());
return 0;}''',encoding='utf8')
            exe=d/'probe.exe'
            build=subprocess.run([shutil.which('gcc') or 'gcc','-std=gnu11','-Wall','-Wextra','-Werror','-I',str(d),'-I',str(ROOT/'source'),str(d/'probe.c'),str(ROOT/'source/story_save.c'),str(ROOT/'source/story_save_fat.c'),'-o',str(exe)],capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stdout+build.stderr)
            result=subprocess.run([str(exe)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=='__main__':unittest.main()
