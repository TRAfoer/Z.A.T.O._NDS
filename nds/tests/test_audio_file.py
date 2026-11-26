from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class AudioFileTests(unittest.TestCase):
    def test_shipping_mixer_file_and_hardware_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);(out/'audio').mkdir();(out/'video').mkdir()
            stereo=(struct.pack('<hBBhBB',12000,0,0,-8000,0,0).ljust(512,b'\0'))*12
            (out/'audio/music.bin').write_bytes(stereo)
            mono=struct.pack('<hBB',1000,0,0).ljust(512,b'\0');(out/'audio/effect.bin').write_bytes(mono)
            def entry(name,channels,frames,payload):
                return struct.pack('<64s48sIIHHHHIIII',name.encode(),f'audio/{name}.bin'.encode(),frames,22050,channels,512,505 if channels==2 else 1017,0,0,len(payload),0,frames)
            (out/'audio/index.bin').write_bytes(struct.pack('<8sIIII',b'ZATAUD02',2,1,1,144)+entry('music',2,6060,stereo)+entry('effect',1,1017,mono))
            fmt=struct.pack('<HHIIHHHH',0x11,2,22050,22050*512//505,512,4,2,505)
            chunks=b'fmt '+struct.pack('<I',len(fmt))+fmt+b'JUNK'+struct.pack('<I',3)+b'abc\0'+b'data'+struct.pack('<I',len(stereo))+stereo
            (out/'video/clip.wav').write_bytes(b'RIFF'+struct.pack('<I',len(chunks)+4)+b'WAVE'+chunks)
            (out/'video/bad.wav').write_bytes(b'RIFF'+bytes(100))
            executable=out/'check.exe'
            subprocess.run([shutil.which('gcc') or 'gcc','-std=gnu11','-Wall','-Wextra','-Werror','-DZATO_NITROFS=1','-I',str(ROOT/'tests/stubs'),str(ROOT/'tests/validate_audio_file.c'),str(ROOT/'source/ima_adpcm.c'),'-o',str(executable)],check=True)
            subprocess.run([str(executable),str(out)],check=True)
            soundtrack=ROOT.parent/'experiments/video_lab/out/op_soundtrack_ima.wav'
            if soundtrack.exists():
                shutil.copyfile(soundtrack,out/'video/actual.wav')
                pcm=out/'reference.pcm'
                with pcm.open('wb') as stream:
                    subprocess.run(['ffmpeg','-v','error','-i',str(soundtrack),'-f','s16le','-c:a','pcm_s16le','-'],stdout=stream,check=True)
                subprocess.run([str(executable),str(out),str(pcm)],check=True)


if __name__=='__main__':unittest.main()
