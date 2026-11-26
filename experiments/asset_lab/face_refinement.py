"""Format/alignment adapter for one ImageGen facial candidate, not a face generator.

All artistic synthesis is from the built-in image_gen output. This adapter clips
that candidate to a small audited facial region; the original body is retained.
"""
from pathlib import Path
import hashlib
import json
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from asset_model import DEFAULT_GAME, rgb15, nearest

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'out/imagegen'
BASE = ROOT / 'out/multi_pass/mcneutral/crop576_144/07_new_outline.png'
POLYGON = [(43, 30), (55, 26), (64, 27), (69, 31), (70, 39), (67, 46), (61, 50), (52, 49), (44, 44), (41, 37)]

def digest(im):
    return hashlib.sha256(np.array(im.convert('RGBA')).tobytes()).hexdigest()

def skin(rgb):
    rgb = rgb.astype(np.int16)
    return (rgb[:, :, 0] >= 145) & (rgb[:, :, 0] > rgb[:, :, 1] + 8) & (rgb[:, :, 1] >= rgb[:, :, 2])

def build():
    OUT.mkdir(parents=True, exist_ok=True)
    base = Image.open(BASE).convert('RGBA')
    candidate = Image.open(OUT / 'mcneutral_face_candidate_v1.png').convert('RGBA')
    alpha = np.array(candidate)[:, :, 3]
    bbox = Image.fromarray((alpha >= 128).astype(np.uint8) * 255).getbbox()
    base_bbox = base.getchannel('A').getbbox()
    aligned = Image.new('RGBA', base.size)
    tile = candidate.crop(bbox).resize((base_bbox[2] - base_bbox[0], base_bbox[3] - base_bbox[1]), Image.Resampling.NEAREST)
    aligned.alpha_composite(tile, (base_bbox[0], base_bbox[1]))
    a, g = np.array(base), np.array(aligned)
    # Choose a tiny translation using facial skin silhouettes, not body matching.
    region = np.zeros(a.shape[:2], dtype=bool)
    region[24:54, 33:75] = True
    expected = skin(a[:, :, :3]) & region
    choices = []
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            placed = Image.new('RGBA', base.size)
            placed.alpha_composite(aligned, (dx, dy))
            arr = np.array(placed)
            actual = skin(arr[:, :, :3]) & region
            score = 2 * np.count_nonzero(expected & actual) / max(1, expected.sum() + actual.sum())
            choices.append((score - .001 * (abs(dx) + abs(dy)), dx, dy, arr))
    _, dx, dy, g = max(choices, key=lambda item: item[0])
    shape = Image.new('L', base.size)
    ImageDraw.Draw(shape).polygon(POLYGON, fill=255)
    mask = np.array(shape) > 0
    # Keep the existing olive hair, exterior alpha, and contour colors.
    hair = (a[:, :, 1].astype(int) > a[:, :, 0].astype(int) + 3) & (a[:, :, 0] < 150)
    mask &= ~hair & (a[:, :, 3] == 255) & (g[:, :, 3] >= 128)
    # Use original character RGB15 colors to avoid mismatched skin-tone seams.
    original = np.array(Image.open(DEFAULT_GAME / 'images/sprites/marina/coat/mcneutral.png').convert('RGBA'))
    palette = np.unique(rgb15(original[original[:, :, 3] > 0, :3]), axis=0)
    mapped = palette[nearest(g[:, :, :3], palette)]
    result = a.copy()
    result[mask, :3] = mapped[mask]
    changed = np.any(result != a, axis=2)
    assert not changed[~mask].any()
    assert np.array_equal(result[:, :, 3], a[:, :, 3])
    target = Image.fromarray(result)
    target.save(OUT / 'mcneutral_face_v1.png')
    Image.fromarray(mask.astype(np.uint8) * 255).save(OUT / 'mcneutral_face_mask_v1.png')
    metadata = dict(target_size=list(base.size), source_expression='mcneutral', framing='crop576_144',
                    base_rgba_sha256=digest(base), candidate='mcneutral_face_candidate_v1.png',
                    candidate_alpha_bbox=list(bbox), base_alpha_bbox=list(base_bbox),
                    facial_translation=[dx, dy], editable_polygon=POLYGON,
                    editable_pixels=int(mask.sum()), changed_pixels=int(changed.sum()),
                    unchanged_outside_face=True, alpha_unchanged=True,
                    source='built-in image_gen; deterministic alignment/formatting and facial clip only')
    (OUT / 'mcneutral_face_v1.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    source_small = Image.open(ROOT / 'out/multi_pass/mcneutral/crop576_144/02_box_reference.png').convert('RGBA')
    entries = [('Box / source', source_small), ('MultiPass + fade', base), ('ImageGen face / v1', target)]
    sheet = Image.new('RGB', (3 * 360, 635), '#10151c')
    d = ImageDraw.Draw(sheet)
    font = ImageFont.truetype('C:/Windows/Fonts/consola.ttf', 17)
    for j, (name, image) in enumerate(entries):
        x = j * 360 + 10
        d.text((x, 9), name, font=font, fill='white')
        view = Image.new('RGBA', image.size, '#080b0f')
        view.alpha_composite(image)
        sheet.paste(view.convert('RGB').resize((339, 432), Image.Resampling.NEAREST), (x, 42))
        close = view.crop((34, 25, 74, 55)).resize((240, 180), Image.Resampling.NEAREST).convert('RGB')
        sheet.paste(close, (x, 480))
    sheet.save(OUT / 'mcneutral_face_comparison.png')
    print(json.dumps(metadata, indent=2))
    return target, metadata

def apply(base, name, framing):
    if name != 'mcneutral' or framing != 'crop576_144':
        return base, dict(applied=False, reason='No ImageGen sample for this expression/framing')
    path = OUT / 'mcneutral_face_v1.png'
    meta_path = OUT / 'mcneutral_face_v1.json'
    if not path.exists() or not meta_path.exists():
        return base, dict(applied=False, reason='Sample not built')
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    if digest(base) != meta['base_rgba_sha256']:
        return base, dict(applied=False, reason='Baseline changed; regenerate the face integration')
    with Image.open(path) as image:
        image = image.convert('RGBA')
    mask = np.array(Image.open(OUT / 'mcneutral_face_mask_v1.png')) > 0
    assert np.array_equal(np.array(image)[~mask], np.array(base)[~mask])
    return image, dict(applied=True, variant='mcneutral_face_v1', changed_pixels=meta['changed_pixels'])

if __name__ == '__main__':
    build()
