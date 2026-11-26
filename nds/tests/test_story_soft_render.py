"""Real C layers against untiled straight-alpha RGB15 reference images."""
from pathlib import Path
import os, shutil, struct, subprocess, tempfile, unittest, zlib
import numpy as np
from test_story_gfx import literal_lz, write_cases, blank

ROOT = Path(__file__).resolve().parents[2]
Q = 65536


def fixture():
    yy, xx = np.indices((8, 16))
    colors = [np.full((8, 16), 0x8000 | 7 | 18 << 5 | 26 << 10, dtype='<u2'),
              (0x8000 | (xx * 2) | ((yy * 4) << 5) | (((xx + yy) % 32) << 10)).astype('<u2'),
              (0x8000 | (31 - xx) | ((31 - yy * 3) << 5) | (((xx * 3 + yy) % 32) << 10)).astype('<u2'),
              np.full((8, 16), 0x8000 | 31 | 5 << 5 | 12 << 10, dtype='<u2')]
    alphas = [np.full((8, 16), 255, dtype='u1'),
              np.tile(np.array([0, 1, 3, 4, 7, 8, 63, 64, 127, 128, 199, 200, 247, 248, 254, 255], dtype='u1'), (8, 1)),
              np.tile(np.array([255, 248, 200, 128, 64, 1, 0, 255, 0, 64, 128, 200, 255, 248, 1, 0], dtype='u1'), (8, 1)),
              np.where((xx + yy) % 3, 255, 0).astype('u1')]
    colors = [np.where(a, c, 0).astype('<u2') for c, a in zip(colors, alphas)]
    banks = [bytearray(), bytearray()]; maps = []
    for frame, (c, a) in enumerate(zip(colors, alphas)):
        bank = 1 if frame in (1, 2) else 0
        stride = 192 if bank else 128
        ids = []
        for x in (0, 8):
            ids.append(len(banks[bank]) // stride)
            banks[bank].extend(c[:, x:x+8].tobytes())
            if bank: banks[bank].extend(a[:, x:x+8].tobytes())
        maps.append(struct.pack('<2H', *ids))
    payload = bytearray(); start = 40 + 32 + 4 * 196 + 8
    def append(raw):
        while (start + len(payload)) % 4: payload.append(0)
        at = start + len(payload); data = literal_lz(raw); payload.extend(data)
        return at, len(data)
    records = []
    for data, stride in zip(banks, (128, 192)):
        at, n = append(data); records.append(struct.pack('<4I', at, n, len(data), stride))
    frame_records = []
    for i, mapping in enumerate(maps):
        at, n = append(mapping)
        frame_records.append(struct.pack('<4I4H2h2HI32s128s', int(i in (1, 2)), at, n, 4,
            16, 8, 2, 1, 120, 92, 2 if i else 1, 2 if i in (1, 2) else 0, 0,
            bytes(32), f'alpha{i}'.encode().ljust(128, b'\0')))
    assets = struct.pack('<8s8I', b'ZATONDP1', 1, 4, 2, 1, 3, 0, 0, 196) + b''.join(records) + b''.join(frame_records) + struct.pack('<4H', 0, 1, 2, 3) + payload
    images = [(0, i, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0) for i in range(4)]
    seq = []
    for old, current in ((1, 2), (3, 1), (2, 3)):
        images.append((1, len(seq), 2, 0, 0, 0, 0, 0, 0, 0, 0, 0))
        seq.extend([(old, 6, 0, 0), (current, 0, 12, 0)])
    base = (4096, 0, Q, Q, 0, Q//2, Q//2, Q//2, Q//2, 1, -1, 0)
    programs = [base]
    for alpha in (0, 1024, 2048, 4096):
        for blend in (0, 1, 2): programs.append((alpha, blend, *base[2:]))
    programs += [(*base[:9], -1, *base[10:]), (4096, 0, Q*3//2, Q*3//2, *base[4:]),
                 (*base[:7], 0, 0, *base[9:11], 1)]
    geometry = [(32, 16*Q, 8*Q, 0, 0, 192*Q, 0, 0)] * 4
    io = 64; so = io + len(images)*48; po = so + len(seq)*16; go = po + len(programs)*48
    data = b''.join(struct.pack('<12i', *x) for x in images) + b''.join(struct.pack('<4I', *x) for x in seq) + b''.join(struct.pack('<12i', *x) for x in programs) + b''.join(struct.pack('<8i', *x) for x in geometry)
    catalog = struct.pack('<8s14I', b'ZSTGFX01', 1, 64+len(data), len(images), len(seq), len(programs), 4, io, so, po, go, len(assets), zlib.crc32(data), 0, 0) + data
    return assets, catalog, colors, alphas, programs


def reference(colors, alphas, old, current, mix, program, *, background=True):
    result = blank()
    if background: result[92:100, 120:136] = colors[0]
    p = program; z = p[2] / Q
    ox, oy = 120 + 16*(1-z)/2, 92 + 8*(1-z)/2
    if p[11] & 1: ox, oy = -8*z, -4*z
    for y in range(192):
        sy = int(np.floor((y + .5 - oy) / z))
        if not 0 <= sy < 8: continue
        for x in range(256):
            sx = int(np.floor((x + .5 - ox) / z))
            if p[9] < 0: sx = 15 - sx
            if not 0 <= sx < 16: continue
            c, a = int(colors[current][sy, sx]), int(alphas[current][sy, sx])
            o, oa = (int(colors[old][sy, sx]), int(alphas[old][sy, sx])) if old is not None else (0, 0)
            caq = (((a+4)//8) * p[0] + 16)//32
            oaq = (((oa+4)//8) * p[0] + 16)//32
            alpha = (caq*mix + oaq*(4096-mix) + 2048)//4096
            if not alpha: continue
            value = 0x8000
            for shift in (0, 5, 10):
                dest = (int(result[y, x]) >> shift) & 31
                prem = (caq*((c>>shift)&31)*mix + oaq*((o>>shift)&31)*(4096-mix) + 2048)//4096
                if p[1] == 1: out = min(31, dest+(prem+2048)//4096)
                elif p[1] == 2: out = (dest*((31*(4096-alpha)+prem+15)//31)+2048)//4096
                else: out = (dest*(4096-alpha)+prem+2048)//4096
                value |= out << shift
            result[y, x] = value
    return result


class SoftRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.tmp = Path(cls.temp.name)
        cc = os.environ.get('CC') or shutil.which('gcc') or shutil.which('clang')
        if not cc: raise unittest.SkipTest('host C compiler unavailable')
        cls.exe = cls.tmp/'soft_render.exe'
        subprocess.run([cc, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-I'+str(ROOT/'nds/source'),
            str(ROOT/'nds/source/story_gfx.c'), str(ROOT/'nds/tests/validate_story_gfx.c'), '-o', str(cls.exe)], check=True)
        cls.a, cls.c, cls.colors, cls.alphas, cls.programs = fixture()
        cls.ap = cls.tmp/'alpha.zat'; cls.ap.write_bytes(cls.a)
        cls.cp = cls.tmp/'gfx.bin'; cls.cp.write_bytes(cls.c)
    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()
    def run_cases(self, actions):
        path = self.tmp/'alpha.cases'; write_cases(path, actions)
        r = subprocess.run([str(self.exe), str(self.ap), str(self.cp), str(path)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout+r.stderr)
    def case(self, image, current, old, tick, mix, p, background=True):
        actions = [((0, 0, 0, 0, 0, 0, 0, 0), None)]
        if background: actions.append(((1, 0, 0, 0, 0, 0, -1, 0), None))
        actions.append(((1, 0, image, 1, 0, p, -1, 0), None))
        actions.append(((3, tick, 0, 0, 0, 0, 0, 0), reference(self.colors, self.alphas, old, current, mix, self.programs[p], background=background)))
        return actions
    def test_plain_alpha_extremes_color_and_unaligned_clipping(self):
        actions = []
        for p in (0, 13, 14, 15):
            for background in (False, True): actions += self.case(1, 1, None, 0, 4096, p, background)
        self.run_cases(actions)
    def test_global_alpha_additive_multiply(self):
        actions = []
        for p in range(1, 13): actions += self.case(1, 1, None, 0, 4096, p)
        self.run_cases(actions)
    def test_dissolve_soft_to_soft_and_binary_both_directions(self):
        actions = []
        for image, prior, current in ((4, 1, 2), (5, 3, 1), (6, 2, 3)):
            for p in range(13):
                # All dissolve weights used by the native 16-step animation.
                for tick in (6, 9, 12, 17):
                    mix = ((tick-6)*16//12)*256
                    actions += self.case(image, current, prior, tick, mix, p)
        self.run_cases(actions)


if __name__ == '__main__': unittest.main()
