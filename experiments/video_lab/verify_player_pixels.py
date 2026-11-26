"""Whole-screen pixel comparison for the real full-length native player ROM."""
from pathlib import Path
import json
import numpy as np
from PIL import Image
from experiments.video_lab import convert_palette as palette


def main():
    out=palette.op.DEFAULT_OUTPUT/'player_final';samples=out/'pixel_oracle'
    worker=palette.Encoder(out/'native')
    try:checked=palette.verify_native(worker.executable,palette.op.DEFAULT_OUTPUT/'palette/refinement/op_palette64_f30.zpv',samples)
    finally:worker.close()
    rows=[]
    for number in (0,436,873,1309,1745):
        pixels=palette.tile_pixels((samples/f'{number:05}.tiles').read_bytes(),64)
        colors=np.frombuffer((samples/f'{number:05}.pal').read_bytes(),dtype='<u2')
        values=colors[pixels]&32767;expected=np.zeros((192,256,3),dtype=np.uint8)
        for c,shift in enumerate((0,5,10)):
            v=(values>>shift)&31;expected[24:168,:,c]=(v<<3)|(v>>2)
        actual=np.asarray(Image.open(out/f'frame_{number:05}.png').convert('RGB'))[:192]
        diff=np.abs(actual.astype(np.int16)-expected.astype(np.int16))
        quantized_equal=bool(np.array_equal(actual>>3,expected>>3))
        rows.append(dict(frame=number,max_rgb_difference=int(diff.max()),mean_absolute_error=float(diff.mean()),rgb555_pixels_exact=quantized_equal,black_borders=not bool(actual[:24].any() or actual[168:].any())))
        Image.fromarray(expected).save(samples/f'{number:05}.png')
    report=dict(result='PASS' if all(r['rgb555_pixels_exact'] and r['black_borders'] for r in rows) else 'FAIL',method='Actual full native video player ROM captures versus independently reconstructed RGB555 palette lookup. Every pixel of the 256x192 screen including both bars. Exact source 5bit channel values; up to 2/255 in displayed RGB8 comes from libretro RGB565/PIL channel expansion.',native_all_frames_and_seeks=checked,rom_sha256=json.loads((out/'verification.json').read_text())['rom_sha256'],frames=rows)
    (out/'pixel_verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
