"""Measure RGB15 ROM payloads; zlib estimates only, no NDS runtime claim."""
from pathlib import Path
import json
import struct
import zlib
import numpy as np
from asset_model import Family, STRIPPED_BOX, rgb15_bytes


def tiles(raw, size):
    w, h = size
    words = np.frombuffer(raw, dtype='<u2').reshape(h, w)
    padded = np.pad(words, ((0, (-h) % 8), (0, (-w) % 8)))
    return [padded[y:y + 8, x:x + 8].astype('<u2').tobytes()
            for y in range(0, padded.shape[0], 8) for x in range(0, padded.shape[1], 8)]


def measure(key, sampling, framing):
    family = Family(key, sampling=sampling, framing=framing)
    rows, base = [], None
    for frame in family.frames('RGB15'):
        raw = rgb15_bytes(frame.rgb, frame.mask)
        current = tiles(raw, family.size)
        if base is None:
            base = current
        changes = [i for i, (a, b) in enumerate(zip(base, current)) if a != b]
        patch = b''.join(struct.pack('<H', i) + current[i] for i in changes)
        restored = base.copy()
        for offset in range(0, len(patch), 130):
            i = struct.unpack_from('<H', patch, offset)[0]
            restored[i] = patch[offset + 2:offset + 130]
        assert restored == current, 'RGB15 tile patch roundtrip failed'
        rows.append(dict(name=frame.name, raw_bytes=len(raw), zlib_bytes=len(zlib.compress(raw, 9)),
                         changed_tiles=len(changes), delta_bytes=len(patch),
                         delta_zlib_bytes=len(zlib.compress(patch, 9)) if patch else 0))
    base_zlib = len(zlib.compress(b''.join(base), 9))
    return dict(key=key, sampling=sampling, framing=framing, size=list(family.size), frames=rows,
                raw_total=sum(r['raw_bytes'] for r in rows),
                full_zlib_total=sum(r['zlib_bytes'] for r in rows),
                base_tiles_zlib_bytes=base_zlib,
                base_plus_deltas_zlib_total=base_zlib + sum(r['delta_zlib_bytes'] for r in rows[1:]))


def main():
    results = [measure(key, sampling, framing)
               for framing in ['crop572_288', 'crop576_144']
               for sampling in ['Box', STRIPPED_BOX]
               for key in ['marina_crossed', 'marina_raised']]
    out = Path(__file__).resolve().parent / 'out' / 'rgb15_storage.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict(
        format='A1B5G5R5 little-endian, binary alpha threshold 128; transparent words zero',
        compression='zlib level 9, host-side estimate',
        delta='8x8 tiles; 2-byte tile index + 128-byte replacement; all expressions relative to first pose frame',
        excludes='headers, archive alignment, decoder code, hardware allocation padding; GPU memory is decompressed',
        measurements=results), ensure_ascii=False, indent=2), encoding='utf-8')
    for result in results:
        print(json.dumps({k: v for k, v in result.items() if k != 'frames'}, ensure_ascii=False))
    print(out)


if __name__ == '__main__':
    main()
