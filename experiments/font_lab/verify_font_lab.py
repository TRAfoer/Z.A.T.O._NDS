from pathlib import Path
import hashlib
import json
import struct
import subprocess
import tempfile
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent/'out'


def unpack_tiles(data):
    result=np.zeros((192,256),dtype=np.uint8)
    for y in range(192):
        for x in range(256):
            at=((y//8)*32+x//8)*32+(y%8)*4+(x%8)//2
            result[y,x]=(data[at]>>((x&1)*4))&15
    return result


def main():
    exe=OUT/'validate_font_text.exe';checks=[]
    for size in [8,10,12]:
        tags=[f'preview_{size}px']+(['preview_12px_bilingual'] if size==12 else [])
        for tag in tags:
            for ui in [False,True]:
                suffix='_ui' if ui else '';font=8 if ui else size
                target=OUT/f'{tag}{suffix}.tiles4'
                result=subprocess.check_output([str(exe),str(OUT/f'font_{font}px.zfn'),str(OUT/f'{tag}{suffix}.commands'),str(target)],text=True)
                pixels=unpack_tiles(target.read_bytes());expected=np.load(OUT/f'{tag}_expected_{"ui" if ui else "main"}.npy')
                np.testing.assert_array_equal(pixels,expected)
                checks.append(dict(kind='font_pixels',name=tag+suffix,result='PASS',bytes=target.stat().st_size))
                print(result.strip(),flush=True)
    for path in sorted(OUT.glob('text_*.ztx')):
        target=path.with_suffix('.native_records')
        result=subprocess.check_output([str(exe),str(path),str(target)],text=True)
        expected=OUT/('expected_bilingual_text_records.bin' if 'bilingual' in path.name else 'expected_text_records.bin')
        assert target.read_bytes()==expected.read_bytes()
        checks.append(dict(kind='text_records',name=path.name,result='PASS',sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
        print(result.strip(),flush=True)
    rejected=[]
    with tempfile.TemporaryDirectory(dir=OUT,prefix='bad_font_') as temp:
        original=(OUT/'font_12px.zfn').read_bytes()
        for name,bad in [('truncated',original[:-1]),('bad_crc',original[:60]+bytes(b^1 for b in original[60:64])+original[64:]),
                         ('bad_magic',b'BADFONT!'+original[8:])]:
            path=Path(temp)/(name+'.zfn');path.write_bytes(bad)
            result=subprocess.run([str(exe),str(path),str(OUT/'preview_12px.commands'),str(Path(temp)/'out')],capture_output=True)
            assert result.returncode!=0;rejected.append(name)
        path=Path(temp)/'bad.commands';bad=bytearray((OUT/'preview_12px.commands').read_bytes());struct.pack_into('<I',bad,0,0xffffffff);path.write_bytes(bad)
        result=subprocess.run([str(exe),str(OUT/'font_12px.zfn'),str(path),str(Path(temp)/'out')],capture_output=True)
        assert result.returncode!=0;rejected.append('invalid_glyph_id')
    summary=json.loads((OUT/'analysis.json').read_text(encoding='utf-8'))
    assert not next(f for f in summary['fonts'] if f['height']==12)['missing']
    (OUT/'verification.json').write_text(json.dumps(dict(result='PASS',checks=checks,rejected=rejected,
        note='Native C links actual demo LZ10 decoder. All4bpp preview pixels and all records compare exactly to independent Python/BDF/corpus reference. Not a physical NDS or gameplay integration test.'),indent=2)+'\n')
    print(f'PASS {len(checks)} complete pixel/text checks; {len(rejected)} malformed-input checks')


if __name__=='__main__':main()
