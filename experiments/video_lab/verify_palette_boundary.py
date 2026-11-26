"""Actual ARM9 Bmp8 proof: noninitial palette[0] is a color, borders stay black."""
from pathlib import Path
import json
import argparse
import subprocess
import sys
import numpy as np
from PIL import Image
from experiments.video_lab import convert_palette as palette


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--colors',type=int,choices=(16,48,64));args=parser.parse_args()
    out=palette.op.DEFAULT_OUTPUT/'palette/bmp8_boundary';out.mkdir(parents=True,exist_ok=True)
    if args.colors is None:
        for colors in (16,48,64):subprocess.run([sys.executable,'-m','experiments.video_lab.verify_palette_boundary','--colors',str(colors)],check=True)
        records=[json.loads((out/str(colors)/'verification.json').read_text())['cases'][0] for colors in (16,48,64)]
        result=dict(result='PASS',cases=records)
        (out/'verification.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps(result,indent=2));return
    records=[]
    for colors in (args.colors,):
        folder=out/str(colors);folder.mkdir(exist_ok=True)
        pixels=(np.arange(144*256).reshape(144,256)%colors).astype(np.uint8)
        first=np.arange(colors,dtype='<u2')|0x8000
        later=first.copy();later[0]=0x801f
        encoder=palette.Encoder(folder/'native')
        try:
            raw=palette.tile_bytes(pixels,colors)
            data,_=palette.build_pack([raw]*6,[first,later],colors=colors,fps=2,duration_us=3_000_000,encoder=encoder)
            path=folder/'boundary.zpv';path.write_bytes(data)
            palette.verify_native(encoder.executable,path,folder/'samples')
        finally:encoder.close()
        report=palette.run_native_probe(folder,[path])
        actual=np.asarray(Image.open(folder/'native_palette_probe.png').convert('RGB'))[:192]
        values=later[pixels]&32767
        expected=np.zeros((192,256,3),dtype=np.uint8)
        for channel,shift in enumerate((0,5,10)):
            v=(values>>shift)&31;expected[24:168,:,channel]=(v<<3)|(v>>2)
        difference=np.abs(actual.astype(np.int16)-expected.astype(np.int16))
        assert not actual[:24].any() and not actual[168:].any(), 'Black borders changed'
        assert difference.max()<=1, f'Actual NDS pixels mismatch: {difference.max()}'
        Image.fromarray(expected).save(folder/'expected.png')
        records.append(dict(colors=colors,later_family_zero_rgb555=int(later[0]),frame=report['preview_case']['frame'],
            frame_palette=1,rom_sha256=report['rom_sha256'],max_rgb_difference=int(difference.max()),
            whole_256x192_mean_absolute_error=float(difference.mean()),black_borders=True,result='PASS'))
    result=dict(result='PASS',method='Standalone real ARM9 ROM with Main BG3 Bmp8 bank B. Compared every displayed pixel against independent RGB555 palette lookup, including both 24px bars. Frame 5 uses a nonfirst GOP with red source index zero.',cases=records)
    (out/str(args.colors)/'verification.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
