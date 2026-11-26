"""Measured image budget for the current prototype, not a final ROM build.

Originals stay read-only. Every PNG/JPEG under game/images and game/gui is
converted. Source-native dimensions are also counted as a separate RGB15 case.
"""
from collections import defaultdict
from io import BytesIO
from pathlib import Path
import csv
import hashlib
import json
import math
import struct
import zlib
import numpy as np
from PIL import Image
from asset_model import DEFAULT_GAME, rgb15, rgb15_bytes, train_palette, nearest, palette_bytes, tiled, mask_tiles
from pixel_passes import remove_white_edge, box_with_white_edge

OUT = Path(__file__).resolve().parent / 'out' / 'full_asset_audit'


def classify(rel):
    parts = rel.parts
    if parts[0] == 'gui' or parts[:3] == ('images', 'cgs', 'minis'):
        return 'ui'
    if parts[:3] == ('images', 'sprites', 'snow'):
        return 'particles'
    if parts[:2] == ('images', 'sprites'):
        return 'actors'
    if parts[:2] == ('images', 'bgs') or rel.name == 'black.jpg':
        return 'backgrounds'
    if parts[:2] == ('images', 'cgs'):
        return 'illustrations'
    if parts[:2] == ('images', 'anim'):
        return 'effects'
    return 'titles_credits'


def transform(source, category, full_body=False):
    w, h = source.size
    if category == 'actors':
        crop_height = min(572, h) if h >= 700 and not full_body else h
        target_h = 288 if h >= 700 and not full_body else math.ceil(crop_height / 2)
        size = (math.ceil(w / 2), target_h)
        clean, _ = remove_white_edge(source)
        aligned = Image.new('RGBA', (size[0] * 2, size[1] * 2))
        aligned.paste(clean.crop((0, 0, w, crop_height)), (0, 0))
        _, result, _, _ = box_with_white_edge(aligned, size, math.ceil(crop_height / 2))
        return result
    size = (max(1, math.ceil(w / 5)), max(1, math.ceil(h / 5)))
    # The only odd-sized full background is a solid black JPEG.
    if category == 'backgrounds' and size[0] > 256:
        factor = min(256 / w, 192 / h)
        size = (round(w * factor), round(h * factor))
    return source.resize(size, Image.Resampling.BOX)


def encode(image, indexed=False):
    a = np.array(image)
    mask = a[:, :, 3] >= 128
    if indexed:
        pal = train_palette([a], 16)
        ids = nearest(a[:, :, :3], pal)
        ids[~mask] = 0
        rgb = pal[ids]
        pixels = np.pad(ids.ravel(), (0, ids.size % 2))
        index_data = (pixels[::2] | (pixels[1::2] << 4)).tobytes()
        palette = palette_bytes(pal)
        alpha = np.packbits(mask.ravel(), bitorder='little').tobytes() if not mask.all() else b''
        payload = palette + index_data + alpha
        color_tiles = tiled(ids, 4)
        # A fixed tile alpha plane makes cross-image dictionary entries comparable.
        tiles = [color + alpha_tile for color, alpha_tile in zip(color_tiles, mask_tiles(mask))]
        decoded_ids = np.empty(len(pixels), dtype=np.uint8)
        stored = np.frombuffer(index_data, dtype=np.uint8)
        decoded_ids[::2], decoded_ids[1::2] = stored & 15, stored >> 4
        assert np.array_equal(decoded_ids[:ids.size].reshape(ids.shape), ids)
        if alpha:
            decoded_mask = np.unpackbits(np.frombuffer(alpha, dtype=np.uint8), bitorder='little')[:mask.size].reshape(mask.shape)
            assert np.array_equal(decoded_mask, mask)
    else:
        rgb = rgb15(a[:, :, :3])
        payload = rgb15_bytes(rgb, mask)
        palette = b''
        words = np.frombuffer(payload, dtype='<u2').reshape(mask.shape)
        padded = np.pad(words, ((0, (-words.shape[0]) % 8), (0, (-words.shape[1]) % 8)))
        tiles = [padded[y:y + 8, x:x + 8].astype('<u2').tobytes()
                 for y in range(0, padded.shape[0], 8) for x in range(0, padded.shape[1], 8)]
        assert np.array_equal((words >> 15) != 0, mask)
        values = np.stack([words & 31, (words >> 5) & 31, (words >> 10) & 31], axis=2)
        restored = ((values.astype(np.uint32) * 255 + 15) // 31).astype(np.uint8)
        assert np.array_equal(restored[mask], rgb[mask])
    rgb[~mask] = 0
    result = Image.fromarray(np.dstack([rgb, mask.astype(np.uint8) * 255]))
    return payload, result, tiles, palette


def png_bytes(image):
    buffer = BytesIO()
    image.save(buffer, format='PNG', optimize=True)
    return buffer.getvalue()


def measure():
    OUT.mkdir(parents=True, exist_ok=True)
    paths = sorted(p for folder in ['images', 'gui'] for p in (DEFAULT_GAME / folder).rglob('*')
                   if p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'))
    rows, families = [], defaultdict(list)
    native_total, native_source_pixels = 0, 0
    full_body_raw_extra = full_body_zlib_extra = 0
    totals = defaultdict(lambda: defaultdict(int))
    for i, path in enumerate(paths):
        rel = path.relative_to(DEFAULT_GAME)
        category = classify(rel)
        with Image.open(path) as opened:
            source = opened.convert('RGBA')
        native_total += source.width * source.height * 2
        native_source_pixels += source.width * source.height
        transformed = transform(source, category)
        # Baseline: RGB15 for everything. Mixed: ordinary backgrounds 16 colors.
        rgb_payload, rgb_image, rgb_tiles, _ = encode(transformed)
        rgb_zlib = zlib.compress(rgb_payload, 9)
        if category == 'backgrounds':
            mixed_payload, converted, tiles, palette = encode(transformed, indexed=True)
        else:
            mixed_payload, converted, tiles, palette = rgb_payload, rgb_image, rgb_tiles, b''
        compressed = zlib.compress(mixed_payload, 9)
        assert zlib.decompress(compressed) == mixed_payload
        png = png_bytes(converted)
        folder = OUT / 'converted' / rel.parent
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (rel.stem + '.png')).write_bytes(png)
        (folder / (rel.stem + '.pixels.zlib')).write_bytes(compressed)
        row = dict(asset=rel.as_posix(), category=category, source_size=list(source.size),
                   target_size=list(converted.size), source_bytes=path.stat().st_size,
                   mixed_format='4bpp + RGB15 palette + optional 1-bit mask' if category == 'backgrounds' else 'A1B5G5R5',
                   raw_rgb15_bytes=len(rgb_payload), rgb15_zlib_bytes=len(rgb_zlib),
                   mixed_raw_bytes=len(mixed_payload), mixed_zlib_bytes=len(compressed), converted_png_bytes=len(png),
                   decoded_sha256=hashlib.sha256(mixed_payload).hexdigest())
        rows.append(row)
        key = (category, rel.parent.as_posix(), transformed.size, row['mixed_format'])
        families[key].append((tiles, palette, len(compressed)))
        for metric in ['source_bytes', 'raw_rgb15_bytes', 'rgb15_zlib_bytes', 'mixed_raw_bytes', 'mixed_zlib_bytes', 'converted_png_bytes']:
            totals[category][metric] += row[metric]
        totals[category]['count'] += 1
        if category == 'actors' and source.height >= 700:
            full = transform(source, category, full_body=True)
            full_payload, _, _, _ = encode(full)
            full_body_raw_extra += len(full_payload) - len(rgb_payload)
            full_body_zlib_extra += len(zlib.compress(full_payload, 9)) - len(rgb_zlib)
        if (i + 1) % 50 == 0:
            print(f'{i + 1}/{len(paths)} converted and verified', flush=True)
    # Shared dictionary candidates are grouped only by folder/size/format. This
    # is lossless on the converted pixels, no guessed palette-only relationship.
    dictionary_total = 0
    family_reports = []
    for key, frames in families.items():
        dictionary = list(dict.fromkeys(tile for frame, _, _ in frames for tile in frame))
        lookup = {tile: i for i, tile in enumerate(dictionary)}
        index_format = '<H' if len(dictionary) <= 65536 else '<I'
        maps = b''.join(struct.pack(index_format, lookup[tile]) for frame, _, _ in frames for tile in frame)
        palettes = b''.join(p for _, p, _ in frames)
        candidate = zlib.compress(b''.join(dictionary) + maps + palettes, 9)
        independent = sum(size for _, _, size in frames)
        chosen = min(len(candidate), independent)
        dictionary_total += chosen
        totals[key[0]]['dictionary_choice_bytes'] += chosen
        # Validate dictionary reconstruction using the exact serialized tile IDs.
        id_size = struct.calcsize(index_format)
        decoded = zlib.decompress(candidate)
        tile_size = len(dictionary[0])
        dictionary_end = len(dictionary) * tile_size
        assert decoded[:dictionary_end] == b''.join(dictionary)
        offset = dictionary_end
        for frame, _, _ in frames:
            restored = []
            for _ in frame:
                tile_id = struct.unpack_from(index_format, decoded, offset)[0]
                offset += id_size
                restored.append(decoded[tile_id * tile_size:(tile_id + 1) * tile_size])
            assert restored == frame
        assert decoded[offset:] == palettes
        family_reports.append(dict(category=key[0], folder=key[1], size=list(key[2]), frames=len(frames),
                                   unique_tiles=len(dictionary), dictionary_zlib_bytes=len(candidate),
                                   independent_zlib_bytes=independent, selected_bytes=chosen))
    summary = {metric: sum(group[metric] for group in totals.values()) for metric in next(iter(totals.values()))}
    assert summary['count'] == len(paths)
    assert summary['dictionary_choice_bytes'] == dictionary_total
    excluded = []
    for folder in ['images', 'gui', 'audio']:
        for path in (DEFAULT_GAME / folder).rglob('*'):
            if path.is_file() and path not in paths:
                excluded.append(dict(asset=path.relative_to(DEFAULT_GAME).as_posix(), bytes=path.stat().st_size))
    report = dict(scope='All raster images under game/images and game/gui; originals read-only',
                  rules=dict(actor='2x2; tall actors y<572 -> padded 288; smaller portraits full half-size',
                             background='Box 1/5; independent 16-color palettes, optional binary alpha mask',
                             other='Box 1/5; RGB15 with alpha threshold 128; no actor edge processing',
                             native_rgb15='source dimensions preserved, 2 bytes/pixel; arithmetic baseline only'),
                  caveats=['UI/portrait layout and other actor conversion quality not approved',
                           'zlib level 9 host measurement; not a completed NDS runtime/codec',
                           'dictionary grouping is conservative folder/size; no palette-only changes assumed',
                           'excludes headers, manifests, filesystem alignment, video, audio, text and fonts'],
                  native_resolution_rgb15_bytes=native_total, native_source_pixels=native_source_pixels,
                  summary=summary, categories=dict(totals),
                  full_body_alternative=dict(additional_raw_bytes=full_body_raw_extra,
                                             additional_independent_zlib_bytes=full_body_zlib_extra,
                                             mixed_raw_total=summary['mixed_raw_bytes'] + full_body_raw_extra,
                                             mixed_zlib_total=summary['mixed_zlib_bytes'] + full_body_zlib_extra),
                  families=family_reports, assets=rows, excluded=excluded)
    (OUT / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    with (OUT / 'assets.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(dict(summary=summary, categories=dict(totals), full_body=report['full_body_alternative'],
                          native_rgb15_bytes=native_total), ensure_ascii=False, indent=2), flush=True)
    print(f'PASS {len(paths)} images converted, all packing/zlib/dictionary roundtrips verified: {OUT}', flush=True)


if __name__ == '__main__':
    measure()
