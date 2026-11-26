"""Measure original-resolution Marina conversion; emit a decodable host pack.

The ZATPLAB1 file is a disposable Python/zlib experiment, not an NDS runtime.
"""
from pathlib import Path
import json
import struct
import zlib

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from asset_model import Family, tiled, untile, rgb15, palette_bytes, stage

OUT = Path(__file__).resolve().parent / "out" / "native_pixels"


def pack_family(family, frames):
    tiles = [tiled(f.indices, 8) for f in frames]
    unique = list(dict.fromkeys(t for frame in tiles for t in frame))
    lookup = {tile: i for i, tile in enumerate(unique)}
    maps = b"".join(struct.pack("<H", lookup[tile]) for frame in tiles for tile in frame)
    meta = {"width": family.size[0], "height": family.size[1], "bpp": 8,
            "frames": family.names, "palette_entries": len(frames[0].palette),
            "unique_tiles": len(unique), "tiles_per_frame": len(tiles[0])}
    header = json.dumps(meta, separators=(",", ":")).encode()
    return b"ZATPLAB1" + struct.pack("<I", len(header)) + header + palette_bytes(frames[0].palette) + zlib.compress(b"".join(unique) + maps, 9)


def unpack_family(data):
    assert data[:8] == b"ZATPLAB1"
    length = struct.unpack_from("<I", data, 8)[0]
    meta = json.loads(data[12:12 + length])
    at = 12 + length
    words = np.frombuffer(data[at:at + meta["palette_entries"] * 2], dtype="<u2")
    channels = np.column_stack([words & 31, (words >> 5) & 31, (words >> 10) & 31])
    pal = ((channels.astype(np.uint16) * 255 + 15) // 31).astype(np.uint8)
    raw = zlib.decompress(data[at + meta["palette_entries"] * 2:])
    tile_end = meta["unique_tiles"] * 64
    tiles = [raw[i:i + 64] for i in range(0, tile_end, 64)]
    maps = np.frombuffer(raw[tile_end:], dtype="<u2").reshape(len(meta["frames"]), meta["tiles_per_frame"])
    results = []
    for indexes in maps:
        ids = untile([tiles[j] for j in indexes], (meta["height"], meta["width"]), 8)
        results.append(np.dstack([pal[ids], (ids > 0).astype(np.uint8) * 255]))
    return meta, results


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"scope": "Original pixels, no resampling; RGB888 -> RGB15, then exact shared indexed color.",
              "compression": "Host zlib with shared 8x8 tiles and maps; not an NDS codec", "families": {}}
    for framing in ["source1x", "crop576_1x"]:
        report["families"][framing] = {}
        for key in ["marina_crossed", "marina_raised"]:
            family = Family(key, framing=framing)
            exact = family.frames("Exact RGB15 indexed")
            packed = pack_family(family, exact)
            meta, decoded = unpack_family(packed)
            for i, (frame, reconstructed) in enumerate(zip(exact, decoded)):
                assert np.array_equal(reconstructed, np.array(frame.image())), "Pack RGBA mismatch"
                source = family.arrays[i]
                visible = source[:, :, 3] > 0
                assert np.array_equal(reconstructed[:, :, 3], source[:, :, 3]), "Original alpha lost"
                assert np.array_equal(reconstructed[visible, :3], rgb15(source[visible, :3])), "Native colors lost"
            path = OUT / f"{key}_{framing}.zatp"
            path.write_bytes(packed)
            metrics = {"size": list(family.size), "frames": len(exact), "rgb15_visible_colors": len(exact[0].palette) - 1,
                       "package_bytes": len(packed), "source_png_total_bytes": sum(p.stat().st_size for p in family.paths),
                       "decoded_pixel_shapes_preserved": "100%", "rgb15_color_roundtrip": "PASS", "package_roundtrip": "PASS",
                       "methods": {}}
            for method in ["Shared 16", "Shared 32", "Shared 64", "Shared 128", "Shared 256", "Exact RGB15 indexed", "RGB15"]:
                frames = family.frames(method)
                matches, errors = [], []
                for i, f in enumerate(frames):
                    mask = family.reference_masks[i]
                    reference = rgb15(family.arrays[i][:, :, :3])[mask]
                    actual = f.rgb[mask]
                    matches.append(float(np.mean(np.all(actual == reference, axis=1))) * 100)
                    errors.append(int(np.max(np.abs(actual.astype(np.int16) - reference.astype(np.int16)))))
                first = frames[0].stats
                metrics["methods"][method] = {
                    "min_exact_rgb15_pixel_pct": round(min(matches), 3),
                    "mean_exact_rgb15_pixel_pct": round(float(np.mean(matches)), 3),
                    "max_channel_error_vs_rgb15": max(errors),
                    "mean_visible_psnr_vs_source_db": round(float(np.mean([f.stats['visible_psnr_db'] for f in frames])), 2),
                    "frame_zlib_min_bytes": min(f.stats.get('zlib_frame_bytes', 0) for f in frames),
                    "frame_zlib_max_bytes": max(f.stats.get('zlib_frame_bytes', 0) for f in frames),
                    "family_dictionary_and_maps_bytes": first.get('family_dictionary_bytes', 0) + first.get('family_map_bytes', 0)}
            report["families"][framing][key] = metrics
            print(key, framing, family.size, metrics["rgb15_visible_colors"], 'colors;', len(packed), 'B package; original pixel/alpha/RGB15 + package decode PASS', flush=True)
            # All columns use precisely the same unscaled source-pixel viewport.
            canvas = Image.new("RGB", (4 * 256, 225), "#10151c")
            draw = ImageDraw.Draw(canvas)
            font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 15)
            entries = [("Source / 1:1", family.reference(0)),
                       ("Shared 16 / 1:1", family.frames('Shared 16')[0].image()),
                       ("Shared 64 / 1:1", family.frames('Shared 64')[0].image()),
                       ("Exact RGB15 / 1:1", exact[0].image())]
            for col, (label, im) in enumerate(entries):
                draw.text((col * 256 + 8, 7), label, font=font, fill="#e4edf5")
                canvas.paste(stage(im, 'sprites', (97, 32)), (col * 256, 32))
            canvas.save(OUT / f"{key}_{framing}_face_comparison.png")
            exact[0].image().save(OUT / f"{key}_{framing}_exact.png")
    (OUT / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('PASS native pixel report and four decodable .zatp experiments', flush=True)


if __name__ == '__main__':
    main()
