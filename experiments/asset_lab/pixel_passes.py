"""Experimental contour-aware reduction: strip white, vote, reconnect, outline.

Only NumPy/Pillow are needed. Pixel voting chooses an existing RGB15 color;
it never creates an averaged fill color. Line repair requires source-ink evidence.
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from pathlib import Path
import json
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def rgb15(a):
    a = np.asarray(a, dtype=np.uint16)
    return ((((a * 31 + 127) // 255) * 255 + 15) // 31).astype(np.uint8)


def shift(mask, dy, dx):
    out = np.zeros_like(mask)
    h, w = mask.shape
    sy, ey = max(0, -dy), min(h, h - dy)
    sx, ex = max(0, -dx), min(w, w - dx)
    out[sy + dy:ey + dy, sx + dx:ex + dx] = mask[sy:ey, sx:ex]
    return out


def dilate(mask):
    return np.logical_or.reduce([shift(mask, y, x) for y in (-1, 0, 1) for x in (-1, 0, 1)])


def white_effect_mask(source, threshold=225):
    """Protect detached white artwork; body outlines touch non-white artwork."""
    a = np.array(source.convert('RGBA'))
    rgb = a[:, :, :3].astype(np.int16)
    visible = a[:, :, 3] > 0
    white = visible & (rgb.min(2) >= threshold) & ((rgb.max(2) - rgb.min(2)) <= 28)
    attached = white & dilate(visible & ~white)
    queue = deque(zip(*np.where(attached)))
    h, w = white.shape
    while queue:
        y, x = queue.popleft()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                yy, xx = y + dy, x + dx
                if 0 <= yy < h and 0 <= xx < w and white[yy, xx] and not attached[yy, xx]:
                    attached[yy, xx] = True
                    queue.append((yy, xx))
    return white & ~attached


def remove_white_edge(source, threshold=225):
    a = np.array(source.convert("RGBA"))
    visible = a[:, :, 3] > 0
    rgb = a[:, :, :3].astype(np.int16)
    candidate = visible & (rgb.min(2) >= threshold) & ((rgb.max(2) - rgb.min(2)) <= 28)
    candidate &= ~white_effect_mask(source, threshold)
    outside = ~visible
    # Count the exterior of the canvas as transparent too.
    seeds = candidate & dilate(outside)
    seeds[0] |= candidate[0]
    seeds[-1] |= candidate[-1]
    seeds[:, 0] |= candidate[:, 0]
    seeds[:, -1] |= candidate[:, -1]
    removed = seeds.copy()
    queue = deque(zip(*np.where(seeds)))
    h, w = visible.shape
    while queue:
        y, x = queue.popleft()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                yy, xx = y + dy, x + dx
                if 0 <= yy < h and 0 <= xx < w and candidate[yy, xx] and not removed[yy, xx]:
                    removed[yy, xx] = True
                    queue.append((yy, xx))
    a[removed] = 0
    a[a[:, :, 3] == 0] = 0
    return Image.fromarray(a), removed


def blocks(a, fx, fy):
    h, w = a.shape[:2]
    if a.ndim == 2:
        return a.reshape(h // fy, fy, w // fx, fx).transpose(0, 2, 1, 3).reshape(-1, fy * fx)
    return a.reshape(h // fy, fy, w // fx, fx, a.shape[2]).transpose(0, 2, 1, 3, 4).reshape(-1, fy * fx, a.shape[2])


def step_reduce(source, size):
    width, height = size
    fx, fy = round(source.width / width), round(source.height / height)
    if fx < 1 or fy < 1 or abs(source.width / width - fx) > .02 or abs(source.height / height - fy) > .02:
        raise ValueError("MultiPass expects an integer block reduction; use a matching framing")
    # Source padding is transparent, never stretched to force divisibility.
    padded = Image.new("RGBA", (width * fx, height * fy))
    padded.paste(source, (0, 0))
    a = np.array(padded)
    rgb = rgb15(a[:, :, :3])
    valid = a[:, :, 3] >= 128
    q = ((rgb.astype(np.uint16) * 31 + 127) // 255)
    code = q[:, :, 0] | (q[:, :, 1] << 5) | (q[:, :, 2] << 10)
    cb = blocks(code, fx, fy)
    vb = blocks(valid, fx, fy)
    samples = fx * fy
    counts = ((cb[:, :, None] == cb[:, None, :]) & vb[:, None, :]).sum(2)
    counts[~vb] = -1
    colors = blocks(rgb, fx, fy)
    luminance = (colors[:, :, 0].astype(np.int32) * 54 + colors[:, :, 1].astype(np.int32) * 183 + colors[:, :, 2].astype(np.int32) * 19) // 256
    # Majority first; tie-break toward darker source colors, retaining ink.
    scores = counts * 256 - luminance
    choice = scores.argmax(1)
    result = colors[np.arange(len(choice)), choice].reshape(height, width, 3)
    mask = (vb.sum(1) >= max(1, int(np.ceil(samples * .45)))).reshape(height, width)
    rgba = np.dstack([result, mask.astype(np.uint8) * 255])
    rgba[~mask] = 0
    ink = valid & (rgb.max(2) <= 72) & ((rgb.max(2).astype(np.int16) - rgb.min(2)) <= 45)
    ink_blocks = blocks(ink, fx, fy)
    ink_grid = ink_blocks.reshape(height, width, fy, fx)
    hline = (ink_grid.any(2).sum(2) >= max(2, int(np.ceil(fx * .75))))
    vline = (ink_grid.any(3).sum(2) >= max(2, int(np.ceil(fy * .75))))
    ink_counts = ink_blocks.sum(1).reshape(height, width)
    # The repaired color must itself be a voted source ink color.
    ink_votes = counts.copy()
    ink_votes[~ink_blocks] = -1
    pick = (ink_votes * 256 - luminance).argmax(1)
    ink_colors = colors[np.arange(len(pick)), pick].reshape(height, width, 3)
    evidence = dict(ink_counts=ink_counts, hline=hline, vline=vline,
                    ink_colors=ink_colors, factor=[fx, fy], padded_size=list(padded.size))
    return Image.fromarray(rgba), evidence


def reconnect_lines(step, evidence):
    a = np.array(step)
    mask = a[:, :, 3] > 0
    rgb = a[:, :, :3]
    ink = mask & (rgb.max(2) <= 72) & ((rgb.max(2).astype(np.int16) - rgb.min(2)) <= 45)
    hsrc, vsrc = evidence['hline'] & mask, evidence['vline'] & mask
    support = evidence['ink_counts'] > 0
    changed = np.zeros_like(mask)
    for _ in range(2):
        left, right, up, down = shift(ink, 0, 1), shift(ink, 0, -1), shift(ink, 1, 0), shift(ink, -1, 0)
        degree = sum(shift(ink, dy, dx).astype(np.uint8) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx)
        thin = ink & (degree <= 3)
        horizontal = left & right & shift(thin, 0, 1) & shift(thin, 0, -1) & hsrc
        vertical = up & down & shift(thin, 1, 0) & shift(thin, -1, 0) & vsrc
        diagonal = ((shift(thin, 1, 1) & shift(thin, -1, -1)) |
                    (shift(thin, 1, -1) & shift(thin, -1, 1))) & (hsrc | vsrc)
        # Broad dark areas and closed light holes (eyes/collars) are not broken
        # lines. Repair sparse one-pixel gaps between thin endpoints only.
        sparse = evidence['ink_counts'] <= max(1, int(np.prod(evidence['factor']) * .4))
        promote = mask & ~ink & support & sparse & (horizontal | vertical | diagonal)
        a[promote, :3] = evidence['ink_colors'][promote]
        changed |= promote
        ink |= promote
    assert not np.any(changed & ~support), "Invented a line without source ink"
    return Image.fromarray(a), changed


def add_white_edge(source):
    a = np.array(source)
    mask = a[:, :, 3] > 0
    edge = dilate(mask) & ~mask
    a[edge] = (255, 255, 255, 255)
    return Image.fromarray(a), edge


def box_with_white_edge(source, size, content_height=None):
    """Box the body, add one binary-alpha pixel of white, overlay thin effects."""
    a = np.array(source.convert('RGBA'))
    effects = white_effect_mask(source)
    effect_source = np.zeros_like(a)
    effect_source[effects] = a[effects]
    a[effects] = 0
    body = np.array(Image.fromarray(a).resize(size, Image.Resampling.BOX))
    # Use the same binary-alpha threshold as the NDS frame formats, so faint
    # Box fringe pixels do not inflate the new silhouette.
    visible = body[:, :, 3] >= 128
    body[:, :, 3] = visible.astype(np.uint8) * 255
    body[~visible] = 0
    body = Image.fromarray(body)
    final, edge = add_white_edge(body)
    fx = np.array(Image.fromarray(effect_source).resize(size, Image.Resampling.BOX))
    # Independent single-pixel strokes need coverage > 0, not majority alpha.
    fx_visible = fx[:, :, 3] > 0
    fx[:, :, 3] = fx_visible.astype(np.uint8) * 255
    fx[~fx_visible] = 0
    if content_height is not None and content_height < size[1]:
        output = np.array(final)
        output[content_height:] = 0
        final = Image.fromarray(output)
        edge[content_height:] = False
        fx[content_height:] = 0
    effects_image = Image.fromarray(fx)
    bare = body.copy()
    bare.alpha_composite(effects_image)
    final.alpha_composite(effects_image)
    edge[fx[:, :, 3] > 0] = False
    return bare, final, edge, effects_image


def reference_fade(lines, reference, evidence, strength=.75):
    """Lighten over-dark blocks against the cleaned source area reference.

    High-coverage original ink is protected. Only a local excess of darkness is
    corrected; alpha and silhouette are unchanged. This is intentionally a
    separate pass because its limited blending differs from majority sampling.
    """
    a = np.array(lines)
    rgb = a[:, :, :3].astype(np.float32)
    ref = np.array(reference)[:, :, :3].astype(np.float32)
    weights = np.array([.2126, .7152, .0722], dtype=np.float32)
    lum, ref_lum = rgb @ weights, ref @ weights
    coverage = evidence['ink_counts'] / np.prod(evidence['factor'])
    excess = np.clip((ref_lum - lum - 16) / 80, 0, 1)
    ambiguity = np.clip((.60 - coverage) / .45, 0, 1)
    alpha = strength * excess * ambiguity
    alpha[(a[:, :, 3] == 0) | (lum > 90) | (coverage >= .60)] = 0
    target = np.maximum(ref, rgb)
    corrected = np.rint(rgb + (target - rgb) * alpha[:, :, None]).astype(np.uint8)
    changed = np.any(corrected != a[:, :, :3], axis=2)
    a[:, :, :3] = corrected
    assert np.all(corrected >= rgb), "Reference fade darkened a channel"
    assert not np.any(changed & (coverage >= .60)), "Reference fade changed strong original ink"
    return Image.fromarray(a), changed, alpha


@dataclass
class Passes:
    original: Image.Image
    stripped: Image.Image
    box: Image.Image
    stripped_box: Image.Image
    box_final: Image.Image
    box_outline: np.ndarray
    effects: Image.Image
    nearest: Image.Image
    step: Image.Image
    lines: Image.Image
    faded: Image.Image
    final: Image.Image
    info: dict
    repaired: np.ndarray
    outline: np.ndarray
    fade_map: np.ndarray


def process(source, size, crop=None, fade=True, fade_strength=.75):
    original = source.convert("RGBA")
    stripped, removed = remove_white_edge(original)
    crop = crop or (0, 0, original.width, original.height)
    source_crop, clean_crop = original.crop(crop), stripped.crop(crop)
    # Align block grids with transparent padding only, never image stretching.
    fx, fy = round(clean_crop.width / size[0]), round(clean_crop.height / size[1])
    if fx < 1 or fy < 1 or not (0 <= fy * size[1] - clean_crop.height <= 2 * fy) or not (0 <= fx * size[0] - clean_crop.width < fx):
        raise ValueError("MultiPass requires an integer grid with at most two transparent padding rows")
    aligned_size = (size[0] * max(1, fx), size[1] * max(1, fy))
    def align(im):
        aligned = Image.new("RGBA", aligned_size)
        aligned.paste(im, (0, 0))
        return aligned
    raw, clean = align(source_crop), align(clean_crop)
    valid_rows = (source_crop.height + fy - 1) // fy
    stripped_box, box_final, box_outline, effects = box_with_white_edge(clean, size, valid_rows)
    # Effects are overlaid once at the end, never thickened by body outlining.
    body_array = np.array(clean)
    body_array[white_effect_mask(clean)] = 0
    clean_body = Image.fromarray(body_array)
    step, evidence = step_reduce(clean_body, size)
    lines, repaired = reconnect_lines(step, evidence)
    clean_reference = clean_body.resize(size, Image.Resampling.BOX)
    if fade:
        faded, fade_changed, fade_map = reference_fade(lines, clean_reference, evidence, fade_strength)
    else:
        faded = lines.copy()
        fade_changed = np.zeros((size[1], size[0]), dtype=bool)
        fade_map = np.zeros_like(fade_changed, dtype=np.float32)
    final, edge = add_white_edge(faded)
    # A crop through the body is not a new object boundary. Keep bottom padding
    # transparent instead of drawing a white horizontal cut edge into that padding.
    if valid_rows < size[1]:
        final_array = np.array(final)
        final_array[valid_rows:] = 0
        edge[valid_rows:] = False
        final = Image.fromarray(final_array)
    final.alpha_composite(effects)
    edge[np.array(effects)[:, :, 3] > 0] = False
    info = dict(source_size=list(original.size), source_crop=list(crop), target_size=list(size),
                block_factor=evidence['factor'], padded_source_size=evidence['padded_size'],
                transparent_padding_source=[aligned_size[0] - source_crop.width, aligned_size[1] - source_crop.height],
                content_height=(source_crop.height + fy - 1) // fy,
                removed_white_pixels=int(removed.sum()), repaired_line_pixels=int(repaired.sum()),
                new_outline_pixels=int(edge.sum()), white_threshold=225, outline_width=1,
                preserved_effect_source_pixels=int(white_effect_mask(original).sum()),
                box_outline_pixels=int(box_outline.sum()),
                reference_fade=fade, fade_strength=fade_strength if fade else 0,
                faded_dark_pixels=int(fade_changed.sum()),
                notes="Experimental heuristic, source-supported line repair; not hand-retouched pixel art.")
    return Passes(original, stripped, raw.resize(size, Image.Resampling.BOX),
                  stripped_box, box_final, box_outline, effects,
                  raw.resize(size, Image.Resampling.NEAREST), step, lines, faded, final, info, repaired, edge, fade_map)


def export_passes(result, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for name, image in [('00_original', result.original), ('01_stripped', result.stripped),
                        ('02_box_reference', result.box), ('03_nearest_reference', result.nearest),
                        ('02b_stripped_box', result.stripped_box),
                        ('02c_box_new_outline', result.box_final), ('02d_white_effects', result.effects),
                        ('04_step_majority', result.step), ('05_lines_repaired', result.lines),
                        ('06_reference_fade', result.faded), ('07_new_outline', result.final)]:
        image.save(out / (name + '.png'))
    entries = [('Box', result.box), ('No edge + Box', result.stripped_box), ('Box + white edge', result.box_final), ('Majority step', result.step),
               ('Line continuity', result.lines), ('Reference fade', result.faded), ('Step + white edge', result.final)]
    w, h = result.final.size
    scale = 1 if w > 160 else 2
    cellw = w * scale + 16
    detail_height = 144 if w > 160 else 128
    sheet = Image.new('RGB', (cellw * len(entries), 60 + h * scale + detail_height), '#10151c')
    d = ImageDraw.Draw(sheet)
    face = ImageFont.truetype('C:/Windows/Fonts/consola.ttf', 14)
    d.text((8, 7), f"Source crop {result.info['source_crop']} -> {w}x{h}; block {result.info['block_factor']}", font=face, fill='white')
    for col, (label, image) in enumerate(entries):
        x = col * cellw + 8
        d.text((x, 31), label, font=face, fill='#c8d8e5')
        view = Image.new('RGBA', image.size, '#080b0f')
        view.alpha_composite(image)
        sheet.paste(view.convert('RGB').resize((w * scale, h * scale), Image.Resampling.NEAREST), (x, 56))
        cx = w // 2
        top = int(h * (.18 if w > 160 else .12))
        halfwidth, detailrows = (32, 48) if w > 160 else (24, 32)
        crop = view.crop((cx - halfwidth, top, cx + halfwidth, top + detailrows)).convert('RGB').resize((192, detail_height), Image.Resampling.NEAREST)
        sheet.paste(crop, (x, 60 + h * scale))
    sheet.save(out / 'pass_comparison.png')
    (out / 'passes.json').write_text(json.dumps(result.info, ensure_ascii=False, indent=2), encoding='utf-8')
    return out
