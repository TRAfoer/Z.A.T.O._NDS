"""Delivery budget: actual .nds file must be strictly below64,000,000bytes."""
from pathlib import Path
import argparse
import json

MAX_ROM_BYTES=64000000


def check_size(size):
    if not 0<size<MAX_ROM_BYTES:
        raise ValueError(f'ROM size {size:,} B exceeds delivery policy: 0 < size < {MAX_ROM_BYTES:,} B')


def check_rom(path):
    path=Path(path);size=path.stat().st_size;check_size(size)
    return dict(rom=str(path.resolve()),rom_bytes=size,max_rom_bytes=MAX_ROM_BYTES,remaining_bytes=MAX_ROM_BYTES-size)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('rom',type=Path);args=parser.parse_args()
    print('PASS ROM budget:',json.dumps(check_rom(args.rom)))
