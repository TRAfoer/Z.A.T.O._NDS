"""Create fault-injected containers using the exact production ARM9 program."""
from pathlib import Path
import hashlib
import json
import struct

ROOT = Path(__file__).resolve().parents[1]


def crc16(data):
    crc = 0xffff
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0xa001 if crc & 1 else 0)
    return crc


def main():
    original = (ROOT / 'zato_asset_demo_adaptive.nds').read_bytes()
    package = (ROOT / 'nitrofs/assets.zat').read_bytes()
    fat_offset, fat_bytes = struct.unpack_from('<2I', original, 0x48)
    assert fat_bytes == 8, 'Expected exactly one NitroFS file'
    file_start, file_end = struct.unpack_from('<2I', original, fat_offset)
    assert original[file_start:file_end] == package
    arm9_start, _, _, arm9_size = struct.unpack_from('<4I', original, 0x20)
    arm9_hash = hashlib.sha256(original[arm9_start:arm9_start + arm9_size]).hexdigest()
    folder = ROOT / 'artifacts' / 'adaptive_faults'
    folder.mkdir(exist_ok=True)
    entries = []

    def save(name, data, mount_ok, reason, runtime=False):
        # Header mutation requires only the NDS header CRC to be refreshed.
        struct.pack_into('<H', data, 0x15e, crc16(data[:0x15e]))
        assert hashlib.sha256(data[arm9_start:arm9_start + arm9_size]).hexdigest() == arm9_hash
        path = folder / f'zato_asset_demo_adaptive_{name}.nds'
        path.write_bytes(data)
        entries.append(dict(path=str(path), mount_ok=mount_ok, fallback_reason=reason, runtime=runtime,
                            arm9_sha256=arm9_hash))

    no_mount = bytearray(original)
    struct.pack_into('<4I', no_mount, 0x40, 0, 0, 0, 0)
    save('mount_failure', no_mount, 0, 1)

    bad_file = bytearray(original)
    bad_file[file_start] ^= 0x80
    save('bad_resource_header', bad_file, 1, 2)

    manifest = json.loads((ROOT / 'artifacts/assets_manifest.json').read_text(encoding='utf-8'))
    next_frame = manifest['stages'][(manifest['default_stage'] + 1) % len(manifest['stages'])]
    record = 40 + 16 * manifest['bank_count'] + manifest['frame_record_bytes'] * next_frame
    map_offset = struct.unpack_from('<I', package, record + 4)[0]
    bad_read = bytearray(original)
    bad_read[file_start + map_offset] ^= 0x80
    save('runtime_read_failure', bad_read, 1, 3, runtime=True)
    (folder / 'cases.json').write_text(json.dumps(entries, indent=2), encoding='utf-8')
    print('PASS three fault-injected ROMs, identical production ARM9; only filesystem/header bytes changed')


if __name__ == '__main__':
    main()
