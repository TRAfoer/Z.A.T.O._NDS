"""Validate alias coverage, exclude unused/stale audio, and inspect actual ROM FAT."""
from collections import Counter
from pathlib import Path
import hashlib
import json
import struct
from rom_budget import check_rom

ROOT = Path(__file__).resolve().parents[1]


def main():
    manifest = json.loads((ROOT / 'artifacts/audio_manifest.json').read_text(encoding='utf-8'))
    audit = json.loads((ROOT / 'artifacts/audio_reference_audit.json').read_text(encoding='utf-8'))
    originally_referenced = {r['source'] for r in audit['files'] if r['static_reference_found']}
    aliases = manifest['source_aliases']
    assert set(aliases) == originally_referenced
    assert set(manifest['excluded_unreferenced']) == set(audit['unreferenced'])
    assert len(set(aliases.values())) == manifest['unique_sources']
    rows = manifest['assets']
    assert len(rows) == manifest['music'] + manifest['sfx']
    assert {n for r in rows for n in r['aliases']} == originally_referenced
    assert not set(manifest['excluded_unreferenced']) & {r['source'] for r in rows}
    for group in ('music', 'sfx'):
        selected = [r for r in rows if r['group'] == group]
        assert len({r['source'] for r in selected}) == len(selected)
    for r in rows:
        assert all(aliases[n] == r['source'] for n in r['aliases'])
        assert (ROOT / 'nitrofs' / r['file']).stat().st_size == r['compressed_bytes']
    expected = {'assets.zat', 'audio/index.bin'} | {r['file'] for r in rows}
    actual = {p.relative_to(ROOT / 'nitrofs').as_posix() for p in (ROOT / 'nitrofs').rglob('*') if p.is_file()}
    assert actual == expected, (actual - expected, expected - actual)
    # Check the catalog's paths against the real files, independently of the manifest.
    catalog = (ROOT / 'nitrofs/audio/index.bin').read_bytes()
    magic, version, music, sfx, size = struct.unpack_from('<8s4I', catalog)
    assert (magic, version, music, sfx, size) == (b'ZATAUD02', 2, manifest['music'], manifest['sfx'], 144)
    paths = [catalog[24+i*size+64:24+i*size+112].split(b'\0')[0].decode() for i in range(music+sfx)]
    assert paths == [r['file'] for r in rows]
    for i,row in enumerate(rows):
        start,end=struct.unpack_from('<2I',catalog,24+i*size+136)
        assert (start,end)==(row['loop_start_frame'],row['loop_end_frame'])
        assert 0<=start<end<=row['frames']
    budget = check_rom(ROOT / 'zato_asset_demo_nitrofs.nds')
    assert all(r['channels']==(2 if r['group']=='music' else 1) for r in rows)
    rom = (ROOT / 'zato_asset_demo_nitrofs.nds').read_bytes()
    fat_offset, fat_size = struct.unpack_from('<2I', rom, 0x48)
    hashes = Counter(hashlib.sha256((ROOT / 'nitrofs' / name).read_bytes()).hexdigest() for name in expected)
    packed = Counter()
    for at in range(fat_offset, fat_offset+fat_size, 8):
        start, end = struct.unpack_from('<2I', rom, at)
        assert start < end <= len(rom)
        packed[hashlib.sha256(rom[start:end]).hexdigest()] += 1
    assert packed == hashes, 'ROM contains missing, additional or stale NitroFS files'
    result = dict(result='PASS', referenced_sources=len(originally_referenced),
        unique_sources=manifest['unique_sources'], music=music, sfx=sfx,
        catalog_variants=len(rows), nitrofs_files=sum(packed.values()),
        excluded=manifest['excluded_unreferenced'],
        rom_bytes=len(rom), rom_sha256=hashlib.sha256(rom).hexdigest(),max_rom_bytes=budget['max_rom_bytes'],
        validation='Every original static reference maps to a canonical source; every catalog file occurs exactly once in the actual ROM; no unreferenced or stale audio')
    (ROOT / 'artifacts/audio_package_verification.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f'PASS {len(originally_referenced)} referenced names -> {manifest["unique_sources"]} unique sources, {len(rows)} channel variants; exact ROM FAT payload coverage, no stale files')


if __name__ == '__main__':
    main()
