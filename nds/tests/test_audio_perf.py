"""Real audio C diagnostic clock and deadline counters with simulated timer MMIO."""
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AudioPerfTests(unittest.TestCase):
    def test_uninitialized_no_nitrofs_clock_is_safe_and_legacy_abi_stays_88(self):
        with tempfile.TemporaryDirectory(prefix='zato_audio_no_fs_') as temporary:
            root=Path(temporary);source=root/'main.c';executable=root/'check.exe'
            source.write_text('#include <assert.h>\n#include "audio_demo.h"\n'
                'int main(void){assert(sizeof(AudioStatus)==88);assert(sizeof(AudioPerfStatus)==72);'
                'assert(audio_perf_now()==0);assert(!audio_demo_init());assert(audio_perf_now()==0);return 0;}\n')
            subprocess.run([shutil.which('gcc') or 'gcc','-std=gnu11','-Wall','-Wextra','-Werror',
                '-I',str(ROOT/'tests/stubs'),'-I',str(ROOT/'source'),str(source),
                str(ROOT/'source/audio_demo.c'),'-o',str(executable)],check=True)
            subprocess.run([str(executable)],check=True)

    def test_real_audio_clock_and_deadline_counters(self):
        with tempfile.TemporaryDirectory(prefix='zato_audio_perf_') as temporary:
            root=Path(temporary);(root/'audio').mkdir()
            rows=[]
            for name,channels,frames,blocks in [('music',2,505,1),('effect',1,8192,9)]:
                block=b''.join(struct.pack('<hBB',12000 if c==0 else -8000,0,0) for c in range(channels)).ljust(512,b'\0')
                payload=block*blocks;path=f'audio/{name}.bin';(root/path).write_bytes(payload)
                rows.append(struct.pack('<64s48sIIHHHHIIII',name.encode(),path.encode(),frames,22050,
                    channels,512,505 if channels==2 else 1017,0,0,len(payload),0,frames))
            (root/'audio/index.bin').write_bytes(struct.pack('<8sIIII',b'ZATAUD02',2,1,1,144)+b''.join(rows))
            executable=root/'validate_audio_perf.exe'
            subprocess.run([shutil.which('gcc') or 'gcc','-std=gnu11','-Wall','-Wextra','-Werror',
                '-DZATO_NITROFS=1','-I',str(ROOT/'tests/stubs'),str(ROOT/'tests/validate_audio_perf.c'),
                str(ROOT/'source/ima_adpcm.c'),'-o',str(executable)],check=True)
            subprocess.run([str(executable),str(root)],check=True)


if __name__=='__main__':unittest.main()
