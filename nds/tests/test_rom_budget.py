from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from rom_budget import check_size, check_rom, MAX_ROM_BYTES


class RomBudgetTests(unittest.TestCase):
    def test_accepts_below_decimal64mb_boundary(self):
        self.assertEqual(MAX_ROM_BYTES,64000000)
        check_size(49618016);check_size(MAX_ROM_BYTES-1)

    def test_rejects_empty_or_at_or_above_boundary(self):
        for size in [0,-1,MAX_ROM_BYTES,MAX_ROM_BYTES+1,64*1024*1024]:
            with self.assertRaises(ValueError):check_size(size)

    def test_measures_actual_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.nds';path.write_bytes(b'\0'*1024)
            self.assertEqual(check_rom(path)['rom_bytes'],1024)


if __name__=='__main__':unittest.main()
