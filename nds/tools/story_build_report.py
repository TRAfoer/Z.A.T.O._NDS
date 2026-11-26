"""Report a completed development package; enforce size only when requested."""
from pathlib import Path
import argparse
import json
import sys

MAX_ROM_BYTES=64_000_000


def report_package(path,*,strict_budget=False):
    path=Path(path).resolve();size=path.stat().st_size
    within=0<size<MAX_ROM_BYTES
    report=dict(rom=str(path),rom_bytes=size,max_rom_bytes=MAX_ROM_BYTES,
                remaining_bytes=MAX_ROM_BYTES-size,within_strict_budget=within,
                strict_budget=bool(strict_budget))
    if strict_budget and not within:
        raise ValueError(f'Explicit strict budget failed: {size:,} bytes; required 0 < bytes < {MAX_ROM_BYTES:,}')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('rom',type=Path)
    parser.add_argument('--strict-budget',action='store_true');args=parser.parse_args()
    try:report=report_package(args.rom,strict_budget=args.strict_budget)
    except ValueError as error:print(str(error),file=sys.stderr);return 1
    print(json.dumps(report));return 0


if __name__=='__main__':raise SystemExit(main())
