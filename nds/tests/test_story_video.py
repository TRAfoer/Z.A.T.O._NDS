from pathlib import Path
import shutil
import subprocess
import struct
import tempfile
import unittest
import numpy as np
from experiments.video_lab import convert_palette as palette

ROOT=Path(__file__).resolve().parents[1]


class StoryVideoTests(unittest.TestCase):
    def test_shipping_player_decodes_and_schedules_all_synthetic_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);encoder=palette.Encoder(out)
            try:
                y,x=np.indices((144,256))
                frames=[palette.tile_bytes(((x+3*y+i)%64).astype(np.uint8),64) for i in range(6)]
                first=np.arange(64,dtype='<u2')|0x8000;later=first.copy();later[0]=0x801f
                data,_=palette.build_pack(frames,[first,later],colors=64,fps=2,duration_us=3_000_000,encoder=encoder)
                (out/'movie.zpv').write_bytes(data);(out/'bad.zpv').write_bytes(data[:100])
            finally:encoder.close()
            executable=out/'verify.exe'
            subprocess.run([shutil.which('gcc') or 'gcc','-std=gnu11','-Wall','-Wextra','-Werror','-DZATO_NITROFS=1','-DZATO_VIDEO_VERIFY=1','-I',str(ROOT/'tests/video_stubs'),str(ROOT/'tests/validate_story_video.c'),str(ROOT/'source/asset_pack.c'),'-o',str(executable)],check=True)
            subprocess.run([str(executable),str(out)],check=True)
            for colors in (16,48,128):
                encoder=palette.Encoder(out/'encoder')
                try:
                    frames=[palette.tile_bytes(((x+3*y+i)%colors).astype(np.uint8),colors) for i in range(6)]
                    first=np.arange(colors,dtype='<u2')|0x8000;later=first.copy();later[0]=0x801f
                    other,_=palette.build_pack(frames,[first,later],colors=colors,fps=2,duration_us=3_000_000,encoder=encoder)
                finally:encoder.close()
                (out/'movie.zpv').write_bytes(other);subprocess.run([str(executable),str(out)],check=True)
            invalid=[]
            for at,value in ((8,2),(36,0),(48,256),(68,0),(palette.HEADER.size+12,0x21),(palette.HEADER.size+16,1),(palette.HEADER.size+32,1)):
                corrupt=bytearray(data);struct.pack_into('<I',corrupt,at,value);invalid.append(corrupt)
            corrupt=bytearray(data);info=palette.inspect_pack(data);corrupt[info['payload_offset']]=0;invalid.append(corrupt)
            for corrupt in invalid:
                (out/'movie.zpv').write_bytes(corrupt);subprocess.run([str(executable),str(out),'--reject'],check=True)


if __name__=='__main__':unittest.main()
