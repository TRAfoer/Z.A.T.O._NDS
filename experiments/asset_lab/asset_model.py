"""Disposable, deterministic asset experiments. Original game assets are read-only.

No Ren'Py code is executed. zlib sizes are host estimates, not an NDS codec claim.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import struct
import zlib

import numpy as np
from PIL import Image, ImageDraw, ImageFont

DEFAULT_GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Z.A.T.O.  I Love the World and Everything In It\game")
GROUPS = {
    "marina_crossed": ("Marina / crossed arms", "sprites", [
        "mcannoyed", "mcangry", "mcchallenge", "mchmm", "mclearn", "mcserious", "mcsmug"]),
    "marina_raised": ("Marina / raised hand", "sprites", [
        "mcneutral", "mcehh", "mchuh", "mcmbub", "mcsad", "mcshock", "mcsigh", "mcsoft", "mcstern", "mcyay"]),
    "library": ("Library / 1 - 5", "background", ["library", "library2", "library3", "library4", "library5"]),
    "vents": ("Vents / normal - red", "background", ["vents", "vents_red"]),
}
BG_METHODS = ["Independent 16", "Base + palette", "Joint + palette", "Joint + tile patches", "RGB15"]
SPRITE_METHODS = ["Shared 16", "Shared 32", "Shared 64", "Independent 16", "RGB15", "Shared 128", "Shared 256"]
STRIPPED_BOX = "去白边 + Box + 新白边"
SAMPLING_MODES = ["Box", STRIPPED_BOX, "Nearest", "MultiPass", "MultiPass+Fade"]
FRAMINGS = {
    "crop572_288": "2x2 / source y<572 to 286px + 2 transparent rows",
    "crop576_144": "4x4 / source y<576 to 144px",
    "source1x": "Original pixels / 1:1",
    "crop576_1x": "Original 1:1 / source y<576",
    "crop576": "Half body / source y<576",
    "full192": "Full body / 192px high",
    "legacy144": "Legacy / 144px high",
}


def rgb15(rgb):
    """Round to DS 5-bit channel values, return expanded display RGB."""
    a = np.asarray(rgb, dtype=np.uint16)
    q = (a * 31 + 127) // 255
    return ((q * 255 + 15) // 31).astype(np.uint8)


def palette_bytes(palette):
    a = np.asarray(palette, dtype=np.uint16)
    q = (a * 31 + 127) // 255
    words = q[:, 0] | (q[:, 1] << 5) | (q[:, 2] << 10)
    return words.astype("<u2").tobytes()


def rgb15_bytes(rgb, mask):
    """Little-endian A1B5G5R5; zero transparent pixels for stable compression."""
    q = (np.asarray(rgb, dtype=np.uint16) * 31 + 127) // 255
    words = q[:, :, 0] | (q[:, :, 1] << 5) | (q[:, :, 2] << 10) | np.uint16(0x8000)
    words = np.where(mask, words, 0).astype("<u2")
    return words.tobytes()


def nearest(rgb, palette):
    # Keep signed differences; uint8 subtraction would wrap.
    original = np.asarray(rgb).reshape(-1, 3).astype(np.uint32)
    codes = (original[:, 0] << 16) | (original[:, 1] << 8) | original[:, 2]
    unique, inverse = np.unique(codes, return_inverse=True)
    flat = np.column_stack([unique >> 16, (unique >> 8) & 255, unique & 255]).astype(np.float32)
    p = np.asarray(palette).astype(np.float32)
    dist = (flat * flat).sum(1)[:, None] + (p * p).sum(1)[None, :] - 2 * flat @ p.T
    return dist.argmin(1)[inverse].reshape(rgb.shape[:2]).astype(np.uint8)


def train_palette(frames, colors, reserve_white=False):
    visible = np.concatenate([a[a[:, :, 3] >= 128, :3] for a in frames])
    requested = colors
    if reserve_white and colors > 1:
        visible = visible[np.any(visible != 255, axis=1)]
        colors -= 1
    if len(visible) == 0:
        return np.full((requested, 3), 255, dtype=np.uint8)
    # Train only on visible colors; transparent white must not consume entries.
    image = Image.fromarray(visible.reshape(-1, 1, 3))
    quantized = image.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    actual = np.array(quantized.getpalette(), dtype=np.uint8).reshape(-1, 3)[:colors]
    # Pillow returns fewer entries if the visible source has fewer colors than
    # requested. Pad slots deterministically, without inventing visible colors.
    if len(actual) < colors:
        actual = np.concatenate([actual, np.repeat(actual[-1:], colors - len(actual), axis=0)])
    palette = rgb15(actual)
    if reserve_white and requested > 1:
        palette = np.vstack([palette, np.array([[255, 255, 255]], dtype=np.uint8)])
    return palette


def common_indices(frames, colors=16):
    """Joint RGB-vector clustering: one index plane, a palette for each variant.

    This deliberately models a palette-only constraint. Local geometry changes
    can consume palette classes and are *not* assumed to disappear magically.
    """
    rgb = np.stack([a[:, :, :3] for a in frames])
    masks = np.stack([a[:, :, 3] >= 128 for a in frames])
    h, w = frames[0].shape[:2]
    features = (rgb * masks[:, :, :, None]).transpose(1, 2, 0, 3).reshape(h * w, -1).astype(np.float32)
    visible = masks.any(0).ravel()
    points, counts = np.unique(features[visible], axis=0, return_counts=True)
    # Deterministic weighted sample, bounding startup work for the temporary UI.
    rng = np.random.default_rng(314159)
    if len(points) > 7000:
        select = rng.choice(len(points), 7000, replace=False, p=counts / counts.sum())
        points, counts = points[select], counts[select]
    k = min(colors, len(points))
    centers = [points[np.argmax(counts)]]
    best = np.full(len(points), np.inf, dtype=np.float32)
    for _ in range(1, k):
        best = np.minimum(best, ((points - centers[-1]) ** 2).sum(1))
        score = best * counts
        centers.append(points[int(rng.choice(len(points), p=score / score.sum()))] if score.sum() else points[0])
    centers = np.array(centers, dtype=np.float32)
    for _ in range(18):
        dist = (points * points).sum(1)[:, None] + (centers * centers).sum(1)[None, :] - 2 * points @ centers.T
        labels = dist.argmin(1)
        for j in range(k):
            take = labels == j
            if take.any():
                centers[j] = np.average(points[take], axis=0, weights=counts[take])
    dist = (features * features).sum(1)[:, None] + (centers * centers).sum(1)[None, :] - 2 * features @ centers.T
    ids = dist.argmin(1).reshape(h, w).astype(np.uint8)
    # Refit palette using all target pixels, including original RGB at transparent
    # locations. Transparency is tracked separately, not treated as a free color.
    palettes = []
    for a in frames:
        pal = np.zeros((colors, 3), dtype=np.uint8)
        for j in range(k):
            take = (ids == j) & (a[:, :, 3] >= 128)
            if take.any():
                pal[j] = np.rint(a[:, :, :3][take].mean(0)).astype(np.uint8)
        palettes.append(rgb15(pal))
    return ids, palettes


def fit_palette(ids, frame, colors=16):
    result = np.zeros((colors, 3), dtype=np.uint8)
    for j in range(colors):
        take = (ids == j) & (frame[:, :, 3] >= 128)
        if take.any():
            result[j] = np.rint(frame[:, :, :3][take].mean(0)).astype(np.uint8)
    return rgb15(result)


def tiled(indices, bpp=4):
    h, w = indices.shape
    padded = np.pad(indices, ((0, (-h) % 8), (0, (-w) % 8)))
    tiles = []
    for y in range(0, padded.shape[0], 8):
        for x in range(0, padded.shape[1], 8):
            values = padded[y:y + 8, x:x + 8].ravel()
            tiles.append((values[::2] | (values[1::2] << 4)).tobytes() if bpp == 4 else values.tobytes())
    return tiles


def untile(tiles, shape, bpp=4):
    h, w = shape
    ph, pw = (h + 7) // 8 * 8, (w + 7) // 8 * 8
    result = np.zeros((ph, pw), dtype=np.uint8)
    for i, tile in enumerate(tiles):
        values = np.frombuffer(tile, dtype=np.uint8)
        if bpp == 4:
            expanded = np.empty(64, dtype=np.uint8)
            expanded[::2], expanded[1::2] = values & 15, values >> 4
            values = expanded
        y, x = divmod(i, pw // 8)
        result[y * 8:y * 8 + 8, x * 8:x * 8 + 8] = values.reshape(8, 8)
    return result[:h, :w]


def mask_tiles(mask):
    h, w = mask.shape
    a = np.pad(mask, ((0, (-h) % 8), (0, (-w) % 8)))
    return [np.packbits(a[y:y + 8, x:x + 8].ravel(), bitorder="little").tobytes()
            for y in range(0, a.shape[0], 8) for x in range(0, a.shape[1], 8)]


def unmask(tiles, shape):
    unpacked = [np.unpackbits(np.frombuffer(t, dtype=np.uint8), bitorder="little").tobytes() for t in tiles]
    return untile(unpacked, shape, 8).astype(bool)


@dataclass
class Frame:
    name: str
    rgb: np.ndarray
    mask: np.ndarray
    palette: np.ndarray | None
    indices: np.ndarray | None
    stats: dict
    changed: np.ndarray

    def image(self):
        return Image.fromarray(np.dstack([self.rgb, self.mask.astype(np.uint8) * 255]))


class Family:
    def __init__(self, key, game=DEFAULT_GAME, sampling="Nearest", framing="crop576"):
        if sampling not in SAMPLING_MODES:
            raise ValueError(f"Unsupported sampling mode: {sampling}")
        self.key, self.title, self.kind, names = key, *GROUPS[key]
        folder = game / "images" / ("sprites/marina/coat" if self.kind == "sprites" else "bgs")
        self.paths = [folder / (name + ".png") for name in names]
        self.names = names
        self.sampling = sampling
        if framing not in FRAMINGS:
            raise ValueError(f"Unknown framing: {framing}")
        self.framing = framing
        resampling = Image.Resampling.NEAREST if sampling == "Nearest" else Image.Resampling.BOX
        self.arrays, self.crop_boxes, self.pass_infos = [], [], []
        for path in self.paths:
            with Image.open(path) as source:
                image = source.convert("RGBA")
            width, height = image.size
            box = (0, 0, width, height)
            if framing in ("source1x", "crop576_1x"):
                if framing == "crop576_1x":
                    box = (0, 0, width, min(576, height))
                size = (box[2], box[3])
            elif self.kind == "sprites":
                if framing == "crop572_288":
                    box = (0, 0, width, min(572, height))
                    size = ((width + 1) // 2, 288)
                elif framing in ("crop576", "crop576_144"):
                    box = (0, 0, width, min(576, height))
                if framing != "crop572_288":
                    target_height = 144 if framing in ("legacy144", "crop576_144") else 192
                    size = ((width + 3) // 4 if framing == "crop576_144" else round(width * target_height / box[3]), target_height)
            elif framing == "legacy144":
                size = (256, 144)
            else:
                # Fill a 4:3 screen without stretching: a centered camera crop.
                crop_width = min(width, round(height * 256 / 192))
                left = (width - crop_width) // 2
                box = (left, 0, left + crop_width, height)
                size = (256, 192)
            self.crop_boxes.append(box)
            if sampling.startswith("MultiPass") and self.kind == "sprites":
                from pixel_passes import process
                passes = process(image, size, box, fade=sampling == "MultiPass+Fade")
                self.arrays.append(np.array(passes.final))
                self.pass_infos.append(passes.info)
            else:
                if sampling == STRIPPED_BOX and self.kind == "sprites":
                    from pixel_passes import remove_white_edge
                    image, _ = remove_white_edge(image)
                working = image.crop(box)
                if framing in ("crop576_144", "crop572_288") and self.kind == "sprites":
                    factor = 2 if framing == "crop572_288" else 4
                    aligned = Image.new("RGBA", (size[0] * factor, size[1] * factor))
                    aligned.paste(working, (0, 0))
                    working = aligned
                if sampling == STRIPPED_BOX and self.kind == "sprites":
                    from pixel_passes import box_with_white_edge
                    content_height = (box[3] - box[1] + factor - 1) // factor if framing in ("crop576_144", "crop572_288") else size[1]
                    _, final, _, _ = box_with_white_edge(working, size, content_height)
                    self.arrays.append(np.array(final))
                else:
                    self.arrays.append(np.array(working.resize(size, resampling)))
                self.pass_infos.append(None)
        self.size = (self.arrays[0].shape[1], self.arrays[0].shape[0])
        self.cache = {}
        self.reference_masks = [a[:, :, 3] >= 128 for a in self.arrays]
        self.base_ids = None
        self.joint_ids = None
        self.joint_palettes = None

    @property
    def methods(self):
        if self.kind != "sprites":
            return BG_METHODS
        return SPRITE_METHODS + (["Exact RGB15 indexed"] if self.framing in ("source1x", "crop576_1x") else [])

    def reference(self, index):
        return Image.fromarray(self.arrays[index])

    def frames(self, method):
        if method in self.cache:
            return self.cache[method]
        masks = self.reference_masks
        if method == "RGB15":
            result = [Frame(n, rgb15(a[:, :, :3]), m, None, None,
                            {"raw_frame_bytes": int(m.size * 2), "palette_bytes": 0}, np.zeros_like(m))
                      for n, a, m in zip(self.names, self.arrays, masks)]
            for frame in result:
                raw = rgb15_bytes(frame.rgb, frame.mask)
                compressed = zlib.compress(raw, 9)
                restored = np.frombuffer(zlib.decompress(compressed), dtype="<u2").reshape(frame.mask.shape)
                assert np.array_equal((restored & 0x8000) != 0, frame.mask), "RGB15 alpha mismatch"
                channels = np.stack([restored & 31, (restored >> 5) & 31, (restored >> 10) & 31], axis=2)
                decoded = ((channels.astype(np.uint32) * 255 + 15) // 31).astype(np.uint8)
                assert np.array_equal(decoded[frame.mask], frame.rgb[frame.mask]), "RGB15 color mismatch"
                frame.stats.update(bpp=16, zlib_frame_bytes=len(compressed), roundtrip="PASS",
                                   storage_format="A1B5G5R5, little endian, alpha threshold 128")
            self.cache[method] = result
            self._quality(result)
            return result
        if self.kind == "sprites":
            exact = method == "Exact RGB15 indexed"
            if exact:
                values = np.concatenate([rgb15(a[:, :, :3][mask]) for a, mask in zip(self.arrays, masks)])
                shared_pal = np.unique(values, axis=0)
                colors = len(shared_pal) + 1
                if colors > 256:
                    raise ValueError(f"Exact palette needs {colors} entries; choose RGB15 direct color")
            else:
                colors = int(method.split()[-1])
            shared = not method.startswith("Independent")
            if not exact:
                shared_pal = train_palette(self.arrays, colors - 1, reserve_white=self.sampling.startswith("MultiPass") or self.sampling == STRIPPED_BOX) if shared else None
            result = []
            for name, a, mask in zip(self.names, self.arrays, masks):
                pal = shared_pal if shared else train_palette([a], colors - 1, reserve_white=self.sampling.startswith("MultiPass") or self.sampling == STRIPPED_BOX)
                palette = np.vstack([np.zeros((1, 3), dtype=np.uint8), pal])
                ids = nearest(rgb15(a[:, :, :3]) if exact else a[:, :, :3], pal) + 1
                ids[~mask] = 0
                result.append(Frame(name, palette[ids], mask, palette, ids, {}, np.zeros_like(mask)))
                if exact:
                    assert np.array_equal(palette[ids][mask], rgb15(a[:, :, :3])[mask]), "RGB15 color loss"
            self._sprite_stats(result, colors)
            if exact:
                for f in result:
                    f.stats.update(exact_rgb15="PASS", visible_colors=colors - 1)
        else:
            if self.base_ids is None:
                base_palette = train_palette(self.arrays[:1], 16)
                self.base_ids = nearest(self.arrays[0][:, :, :3], base_palette)
                self.base_ids[~masks[0]] = 0
            if method.startswith("Joint") and self.joint_ids is None:
                self.joint_ids, self.joint_palettes = common_indices(self.arrays)
            result = []
            for i, (name, a, mask) in enumerate(zip(self.names, self.arrays, masks)):
                if method == "Independent 16":
                    palette = train_palette([a], 16)
                    ids = nearest(a[:, :, :3], palette)
                    outmask = mask
                elif method == "Base + palette":
                    ids = self.base_ids.copy()
                    palette = fit_palette(ids, a)
                    outmask = masks[0]
                else:
                    ids = self.joint_ids.copy()
                    palette = self.joint_palettes[i]
                    outmask = masks[0]
                    if method.endswith("patches"):
                        ids = nearest(a[:, :, :3], palette)
                        outmask = mask
                ids = ids.copy()
                ids[~outmask] = 0
                base = self.joint_ids if method.startswith("Joint") else self.base_ids
                changed = ((ids != base) & (outmask | masks[0])) | (outmask != masks[0])
                result.append(Frame(name, palette[ids], outmask, palette, ids, {}, changed))
            self._background_stats(result, method)
        self.cache[method] = result
        self._quality(result)
        return result

    def _quality(self, frames):
        for i, f in enumerate(frames):
            source = self.arrays[i][:, :, :3].astype(np.float32) * (self.arrays[i][:, :, 3:4] / 255)
            target = f.rgb.astype(np.float32) * f.mask[:, :, None]
            mse = float(np.mean((source - target) ** 2))
            active = self.reference_masks[i] | f.mask
            active_mse = float(np.mean((source[active] - target[active]) ** 2)) if active.any() else 0
            f.stats.update(psnr_db=round(10 * np.log10(255 ** 2 / max(mse, 1e-10)), 2),
                           visible_psnr_db=round(10 * np.log10(255 ** 2 / max(active_mse, 1e-10)), 2),
                           alpha_changed_pixels=int(np.count_nonzero(f.mask != self.reference_masks[i])),
                           source_png_bytes=self.paths[i].stat().st_size,
                           source_sha256=hashlib.sha256(self.paths[i].read_bytes()).hexdigest())
            if self.pass_infos[i] is not None:
                f.stats['multi_pass'] = self.pass_infos[i]

    def _sprite_stats(self, frames, colors):
        bpp = 4 if colors <= 16 else 8
        collections = [tiled(f.indices, bpp) for f in frames]
        unique = list(dict.fromkeys(tile for tiles in collections for tile in tiles))
        lookup = {tile: i for i, tile in enumerate(unique)}
        maps = b"".join(struct.pack("<H", lookup[t]) for tiles in collections for t in tiles)
        dictionary = b"".join(unique)
        for f, tiles in zip(frames, collections):
            decoded = untile(tiles, f.indices.shape, bpp)
            assert np.array_equal(decoded, f.indices), "Tile unpack mismatch"
            restored = [unique[lookup[t]] for t in tiles]
            assert np.array_equal(untile(restored, f.indices.shape, bpp), f.indices), "Dictionary mismatch"
            base = collections[0]
            changes = [i for i, (a, b) in enumerate(zip(base, tiles)) if a != b]
            patch = b"".join(struct.pack("<H", i) + tiles[i] for i in changes)
            restored = base.copy()
            unit = (32 if bpp == 4 else 64) + 2
            for at in range(0, len(patch), unit):
                j = struct.unpack_from("<H", patch, at)[0]
                restored[j] = patch[at + 2:at + unit]
            assert np.array_equal(untile(restored, f.indices.shape, bpp), f.indices), "Delta mismatch"
            f.changed = f.indices != frames[0].indices
            raw = b"".join(tiles)
            compressed = zlib.compress(raw, 9)
            assert zlib.decompress(compressed) == raw
            f.stats.update(bpp=bpp, raw_frame_bytes=len(raw), palette_bytes=colors * 2,
                           zlib_frame_bytes=len(compressed), changed_tiles=len(changes), total_tiles=len(tiles),
                           delta_bytes=len(patch), delta_zlib_bytes=len(zlib.compress(patch, 9)) if patch else 0,
                           unique_family_tiles=len(unique), family_dictionary_bytes=len(dictionary), family_map_bytes=len(maps),
                           family_dictionary_maps_zlib_bytes=len(zlib.compress(dictionary + maps, 9)),
                           independent_family_raw_bytes=sum(len(t) * (32 if bpp == 4 else 64) for t in collections),
                           shared_palette=all(np.array_equal(x.palette, frames[0].palette) for x in frames), roundtrip="PASS")

    def _background_stats(self, frames, method):
        base_ids = self.joint_ids if method.startswith("Joint") else self.base_ids
        base_ids = base_ids.copy()
        base_ids[~self.reference_masks[0]] = 0
        base_tiles = tiled(base_ids)
        base_masks = mask_tiles(self.reference_masks[0])
        for f in frames:
            tiles, masks = tiled(f.indices), mask_tiles(f.mask)
            changes = [j for j, (a, b, c, d) in enumerate(zip(base_tiles, tiles, base_masks, masks)) if a != b or c != d]
            patch = b"".join(struct.pack("<H", j) + tiles[j] + masks[j] for j in changes)
            restored, alpha = base_tiles.copy(), base_masks.copy()
            for at in range(0, len(patch), 42):
                j = struct.unpack_from("<H", patch, at)[0]
                restored[j], alpha[j] = patch[at + 2:at + 34], patch[at + 34:at + 42]
            assert np.array_equal(untile(restored, f.indices.shape), f.indices), "Background delta mismatch"
            assert np.array_equal(unmask(alpha, f.mask.shape), f.mask), "Mask mismatch"
            raw = b"".join(tiles) + b"".join(masks)
            z = zlib.compress(raw, 9)
            assert zlib.decompress(z) == raw
            f.stats.update(bpp=4, raw_frame_bytes=len(raw), index_bytes=sum(map(len, tiles)), mask_bytes=sum(map(len, masks)),
                           palette_bytes=32, zlib_frame_bytes=len(z), changed_tiles=len(changes), total_tiles=len(tiles),
                           delta_bytes=len(patch), delta_zlib_bytes=len(zlib.compress(patch, 9)) if patch else 0,
                           palette_only=method.endswith("palette"), roundtrip="PASS")

    def export(self, out):
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        methods = self.methods
        report = {"key": self.key, "sampling": self.sampling, "framing": self.framing,
                  "source_crop_boxes": self.crop_boxes, "size": list(self.size), "methods": {}}
        for method in methods:
            frames = self.frames(method)
            report["methods"][method] = {f.name: f.stats for f in frames}
            for f in frames:
                stem = f"{self.key}_{self.sampling.lower()}_{f.name}_{method.lower().replace(' ', '_').replace('+', 'and')}"
                f.image().save(out / (stem + ".png"))
                if f.palette is not None:
                    (out / (stem + ".pal")).write_bytes(palette_bytes(f.palette))
        # Contact sheet is deliberately deterministic, no browser or game runtime.
        display_methods = methods[:4]
        w, h = self.size
        cellw, cellh = (w + 18, h + 36)
        sheet = Image.new("RGB", (cellw * 5, cellh * len(self.names) + 42), "#131920")
        draw = ImageDraw.Draw(sheet)
        font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 12)
        draw.text((10, 10), f"{self.title} | {self.sampling} | {self.framing} | host prototype", font=font, fill="#e6edf3")
        for i, name in enumerate(self.names):
            entries = [("Source resized", self.reference(i))] + [(m, self.frames(m)[i].image()) for m in display_methods]
            for j, (label, image) in enumerate(entries):
                x, y = j * cellw + 9, i * cellh + 42
                draw.text((x, y), f"{name} / {label}", font=font, fill="#b6c8d5")
                sheet.paste(image, (x, y + 24), image)
        sheet.save(out / f"{self.key}_{self.sampling.lower()}_comparison.png")
        (out / f"{self.key}_{self.sampling.lower()}_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report


def stage(image, kind, camera=(0, 0)):
    result = Image.new("RGBA", (256, 192), (7, 10, 14, 255))
    cx = min(max(int(camera[0]), 0), max(0, image.width - 256))
    cy = min(max(int(camera[1]), 0), max(0, image.height - 192))
    visible = image.crop((cx, cy, min(image.width, cx + 256), min(image.height, cy + 192)))
    result.alpha_composite(visible, ((256 - visible.width) // 2, (192 - visible.height) // 2))
    return result.convert("RGB")
