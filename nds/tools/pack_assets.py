"""Pack converted masters into shared 8x8 dictionaries + LZ10 maps/banks."""
from collections import defaultdict, deque
from pathlib import Path
import hashlib
import json
import struct
import sys
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT.parent / 'experiments' / 'asset_lab'
sys.path.insert(0, str(LAB))
from full_asset_audit import encode
from asset_model import DEFAULT_GAME, train_palette, nearest

HEADER = struct.Struct('<8s8I')
BANK = struct.Struct('<4I')
FRAME = struct.Struct('<4I4H2h2HI32s128s')
CATEGORIES = dict(backgrounds=0, illustrations=1, actors=2, effects=3, ui=4, titles_credits=5, particles=6)
MAX_BANK_RAW = 256 * 1024
BG_EDGE_COLOR = (66, 66, 66)  # Exact displayed RGB15(8, 8, 8).


def gray_background_edge(image):
    """One inward pixel at the exterior boundary; reserve one of 16 colors."""
    a = np.array(image.convert('RGBA'))
    visible = a[:, :, 3] >= 128
    # Flood only canvas-connected transparency; enclosed holes/highlights are
    # not exterior edges and must retain their original artwork.
    transparent = np.pad(~visible, 1, constant_values=True)
    exterior = np.zeros_like(transparent)
    exterior[0, 0] = True
    queue = deque([(0, 0)])
    height, width = transparent.shape
    while queue:
        y, x = queue.popleft()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                yy, xx = y + dy, x + dx
                if 0 <= yy < height and 0 <= xx < width and transparent[yy, xx] and not exterior[yy, xx]:
                    exterior[yy, xx] = True
                    queue.append((yy, xx))
    neighbors = np.zeros_like(visible)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            neighbors |= exterior[1 + dy:1 + dy + visible.shape[0], 1 + dx:1 + dx + visible.shape[1]]
    edge = visible & neighbors
    interior = a.copy()
    interior[edge, 3] = 0
    palette = np.vstack([train_palette([interior], 15), np.array([BG_EDGE_COLOR], dtype=np.uint8)])
    ids = nearest(a[:, :, :3], palette)
    ids[edge] = 15
    result = np.dstack([palette[ids], visible.astype(np.uint8) * 255])
    result[~visible] = 0
    return Image.fromarray(result), edge


def fnv(data):
    value = 2166136261
    for b in data:
        value = ((value ^ b) * 16777619) & 0xffffffff
    return value


def lz10(data):
    result = bytearray(b'\x10' + len(data).to_bytes(3, 'little'))
    positions = defaultdict(deque)
    pos = 0
    while pos < len(data):
        flag_pos = len(result)
        result.append(0)
        for bit in range(7, -1, -1):
            if pos >= len(data):
                break
            length, distance = 0, 0
            if pos + 2 < len(data):
                key = data[pos:pos + 3]
                candidates = positions[key]
                while candidates and candidates[0] < pos - 4096:
                    candidates.popleft()
                for candidate in list(candidates)[-64:][::-1]:
                    n = 3
                    limit = min(18, len(data) - pos)
                    while n < limit and data[candidate + n] == data[pos + n]:
                        n += 1
                    if n > length:
                        length, distance = n, pos - candidate
                    if n == limit:
                        break
            if length >= 3:
                result[flag_pos] |= 1 << bit
                token = ((length - 3) << 12) | (distance - 1)
                result.extend(token.to_bytes(2, 'big'))
                step = length
            else:
                result.append(data[pos])
                step = 1
            for cursor in range(pos, pos + step):
                if cursor + 2 < len(data):
                    bucket = positions[data[cursor:cursor + 3]]
                    bucket.append(cursor)
                    while bucket and bucket[0] < cursor - 4096:
                        bucket.popleft()
            pos += step
    return bytes(result)


def unlz10(data):
    assert data[0] == 0x10
    size = int.from_bytes(data[1:4], 'little')
    output = bytearray()
    pos = 4
    while len(output) < size:
        flags = data[pos]
        pos += 1
        for bit in range(7, -1, -1):
            if len(output) == size:
                break
            if flags & (1 << bit):
                token = int.from_bytes(data[pos:pos + 2], 'big')
                pos += 2
                count, distance = (token >> 12) + 3, (token & 4095) + 1
                assert distance <= len(output) and len(output) + count <= size
                for _ in range(count):
                    output.append(output[-distance])
            else:
                output.append(data[pos])
                pos += 1
    return bytes(output)


def pack():
    audit = LAB / 'out' / 'full_asset_audit'
    if not (audit / 'report.json').exists():
        raise RuntimeError('Run experiments/asset_lab/full_asset_audit.py first')
    source = json.loads((audit / 'report.json').read_text(encoding='utf-8'))
    frames, banks = [], []
    by_group = defaultdict(list)
    for row in source['assets']:
        path = Path(row['asset'])
        master = audit / 'converted' / path.with_suffix('.png')
        indexed = row['category'] == 'backgrounds'
        crop = None
        stage_master = None
        outline_pixels = 0
        if indexed:
            # Source BG canvases include deliberate transparent margins. The
            # new framing fits the actual visible artwork to screen width.
            with Image.open(DEFAULT_GAME / path) as opened:
                original = opened.convert('RGBA')
            mask = original.getchannel('A').point(lambda value: 255 if value >= 128 else 0)
            crop = mask.getbbox() or (0, 0, original.width, original.height)
            if crop != (0, 0, original.width, original.height):
                # Some variants have 1-3px edge overshoot. A shared centered
                # 980px scene window keeps those variants at exactly 245px,
                # without drifting the horizontal camera between differences.
                if 976 <= crop[2] - crop[0] <= 984:
                    left = (original.width - 980) // 2
                    crop = (left, crop[1], left + 980, crop[3])
                content = original.crop(crop)
                size = ((content.width + 3) // 4, (content.height + 3) // 4)
                # Integer 4x4 Box blocks; no stretching to compensate for a
                # partial final block. Ordinary 980px artwork becomes 245px.
                aligned = Image.new('RGBA', (size[0] * 4, size[1] * 4))
                aligned.paste(content, (0, 0))
                image = aligned.resize(size, Image.Resampling.BOX)
                image, edge = gray_background_edge(image)
                outline_pixels = int(edge.sum())
            else:
                # Full-canvas effects/solid fills retain their previous framing.
                with Image.open(master) as opened:
                    image = opened.convert('RGBA')
            stage_master = Path('artifacts') / 'stage_masters' / path.with_suffix('.png')
            master = ROOT / stage_master
            master.parent.mkdir(parents=True, exist_ok=True)
            image.save(master)
        else:
            with Image.open(master) as opened:
                image = opened.convert('RGBA')
        _, converted, tiles, palette = encode(image, indexed=indexed)
        assert np.array_equal(np.array(converted), np.array(image)), f'Packing changed master: {master}'
        a = np.array(image)
        words = ((a[:, :, :3].astype(np.uint16) * 31 + 127) // 255)
        words = words[:, :, 0] | (words[:, :, 1] << 5) | (words[:, :, 2] << 10)
        words = np.where(a[:, :, 3] >= 128, words | 0x8000, 0).astype('<u2')
        category = CATEGORIES[row['category']]
        w, h = image.size
        # Top alignment for large actors; full CG/background centered on screen.
        x = (256 - w) // 2
        y = max(0, (192 - h) // 2)
        entry = dict(name=row['asset'], width=w, height=h, cols=(w + 7) // 8, rows=(h + 7) // 8,
                     x=x, y=y, category=category, format=int(indexed), palette=palette.ljust(32, b'\0'),
                     hash=fnv(words.tobytes()), tiles=tiles,
                     master=stage_master.as_posix() if stage_master else None,
                     source_crop=list(crop) if crop else None,
                     gray_outline_pixels=outline_pixels)
        frames.append(entry)
        by_group[(str(path.parent), image.size, indexed)].append(len(frames) - 1)
    for group, members in by_group.items():
        current = None
        for frame_id in members:
            frame = frames[frame_id]
            missing = set(frame['tiles']) - (set(current['lookup']) if current else set())
            if current is None or (len(current['lookup']) + len(missing)) * len(frame['tiles'][0]) > MAX_BANK_RAW:
                current = dict(lookup={}, tiles=[], members=[], group=str(group))
                banks.append(current)
            for tile in frame['tiles']:
                if tile not in current['lookup']:
                    current['lookup'][tile] = len(current['tiles'])
                    current['tiles'].append(tile)
            current['members'].append(frame_id)
            frame['bank'] = len(banks) - 1
            ids = [current['lookup'][tile] for tile in frame['tiles']]
            raw_map = struct.pack('<' + 'H' * len(ids), *ids)
            frame['map_raw'] = len(raw_map)
            frame['map_blob'] = lz10(raw_map)
            assert unlz10(frame['map_blob']) == raw_map
            assert [current['tiles'][i] for i in ids] == frame['tiles']
    stages = [i for i, f in enumerate(frames) if f['category'] in (0, 1)]
    actors = [i for i, f in enumerate(frames) if f['category'] == 2]
    default_stage = next(i for i, frame_id in enumerate(stages) if frames[frame_id]['name'] == 'images/bgs/library.png')
    default_actor = next(i for i, frame_id in enumerate(actors) if frames[frame_id]['name'] == 'images/sprites/marina/coat/mcneutral.png')
    header = HEADER.pack(b'ZATONDP1', 1, len(frames), len(banks), len(stages), len(actors), default_stage, default_actor, FRAME.size)
    data_offset = len(header) + BANK.size * len(banks) + FRAME.size * len(frames) + 2 * (len(stages) + len(actors))
    payload = bytearray()
    def append(blob):
        while (data_offset + len(payload)) % 4:
            payload.append(0)
        offset = data_offset + len(payload)
        payload.extend(blob)
        return offset
    bank_records = []
    for bank in banks:
        raw = b''.join(bank['tiles'])
        blob = lz10(raw)
        assert unlz10(blob) == raw
        bank['raw_bytes'], bank['lz10_bytes'] = len(raw), len(blob)
        bank_records.append(BANK.pack(append(blob), len(blob), len(raw), len(bank['tiles'][0])))
    frame_records = []
    manifest = []
    for i, frame in enumerate(frames):
        blob = frame['map_blob']
        name = frame['name'].encode('utf-8')
        assert len(name) < 128
        frame_records.append(FRAME.pack(frame['bank'], append(blob), len(blob), frame['map_raw'],
            frame['width'], frame['height'], frame['cols'], frame['rows'], frame['x'], frame['y'],
            frame['category'], frame['format'], frame['hash'], frame['palette'], name.ljust(128, b'\0')))
        manifest.append(dict(id=i, **{k: frame[k] for k in ['name', 'width', 'height', 'x', 'y', 'category', 'format', 'hash', 'bank', 'master', 'source_crop', 'gray_outline_pixels']}))
    tables = b''.join(bank_records) + b''.join(frame_records) + struct.pack('<' + 'H' * len(stages), *stages) + struct.pack('<' + 'H' * len(actors), *actors)
    pack_data = header + tables + payload
    (ROOT / 'nitrofs').mkdir(exist_ok=True)
    (ROOT / 'artifacts').mkdir(exist_ok=True)
    (ROOT / 'nitrofs' / 'assets.zat').write_bytes(pack_data)
    report = dict(version=1, compression='LZ10', tile_size=[8, 8], package_bytes=len(pack_data),
                  background_rule='Ordinary BG: centered 980px scene window, trim vertical transparent margins, exact 4x4 Box blocks -> 245px, centered; full-canvas effects/CG keep previous framing; no runtime scaling/panning',
                  background_outline=dict(width=1, placement='inward exterior boundary', rgb=list(BG_EDGE_COLOR), palette_slots=1, colors_total=16),
                  frame_record_bytes=FRAME.size, source_audit=source['summary'],
                  bank_count=len(banks), max_bank_raw_bytes=max(b['raw_bytes'] for b in banks),
                  total_unique_tiles=sum(len(b['tiles']) for b in banks),
                  total_frame_tiles=sum(len(f['tiles']) for f in frames),
                  banks=[{k: b[k] for k in ['group', 'raw_bytes', 'lz10_bytes']} for b in banks],
                  stages=stages, actors=actors, default_stage=default_stage, default_actor=default_actor,
                  frames=manifest, package_sha256=hashlib.sha256(pack_data).hexdigest())
    (ROOT / 'artifacts' / 'assets_manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'PASS {len(frames)} current masters exact, dictionaries/maps LZ10 roundtrips; {len(stages)} stage images, {len(actors)} actors', flush=True)
    print(f'{len(banks)} banks; max raw bank {report["max_bank_raw_bytes"]}; package {len(pack_data):,} B', flush=True)


if __name__ == '__main__':
    pack()
