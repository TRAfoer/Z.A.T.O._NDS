"""Credits content, original attribution and read-only source contract."""
from pathlib import Path
import ast
import hashlib
import importlib.util
import json
import re
import struct
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('menu_credit_packer', ROOT/'nds/tools/pack_story_menu.py')
packer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packer)


class StoryMenuCreditContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.game = packer.source.DEFAULT_GAME
        cls.original_path = cls.game/'tl/chinese/screens.rpy'
        cls.source_hash = hashlib.sha256(cls.original_path.read_bytes()).hexdigest()
        lines = cls.original_path.read_text(encoding='utf-8-sig').splitlines()
        values = [ast.literal_eval(x.strip()[4:]) for x in lines if x.strip().startswith('new ')]
        original = next(x for x in values if 'Ferry (Nopanamaman)' in x)
        cls.original = re.sub(r'\{[^{}]*\}', '', original).strip()
        cls.value = packer.credits(cls.game)

    def test_original_credits_and_exact_user_signatures_are_retained_once(self):
        self.assertEqual(self.value.count(self.original), 1)
        for signature in [
            '中文汉化(ParaTranz)：https://paratranz.cn/projects/17445',
            '翻译校对：bilibili@100ol  、bilibili@枫封峰',
            '移植制作：bilibili@神币の商家   GPT-6.1 Sol',
        ]:
            self.assertEqual(self.value.count(signature), 1)
        self.assertEqual(hashlib.sha256(self.original_path.read_bytes()).hexdigest(), self.source_hash)

    def test_sections_have_one_heading_and_preserve_original_then_localization_order(self):
        headings = ['原作制作', '中文本地化', 'NDS移植', '字体与工具']
        positions = []
        for heading in headings:
            self.assertEqual(self.value.splitlines().count(heading), 1, heading)
            positions.append(self.value.index(heading))
        self.assertEqual(positions, sorted(positions))
        self.assertLess(self.value.index(self.original), positions[1])
        self.assertLess(positions[1], self.value.index('bilibili@100ol'))
        self.assertLess(positions[2], self.value.index('bilibili@神币の商家'))

    def test_official_font_and_tool_references_and_confirmed_license_names(self):
        references = {x['name']: x for x in packer.TOOL_CREDITS}
        expected = {
            'Fusion Pixel Font': ('https://github.com/TakWolf/fusion-pixel-font', 'SIL Open Font License 1.1'),
            'ISAS FangSong': ('https://fontpixel.com/zh/fonts/isas-fangsong/', 'ISAS (1988)'),
            'BlocksDS': ('https://blocksds.skylyrac.net/docs/', '各组件许可证见官方说明'),
            'rpatool': ('https://github.com/Shizmob/rpatool', 'WTFPL Version 2'),
            'Python': ('https://www.python.org/', 'PSF License Version 2'),
        }
        self.assertEqual(set(references), set(expected))
        without_breaks = self.value.replace('\n', '')
        for name, (url, license_name) in expected.items():
            item = references[name]
            self.assertEqual(item['url'], url)
            self.assertEqual(item['license'], license_name)
            self.assertIn(url, without_breaks)
            self.assertIn(license_name, self.value)
            self.assertIn(item['license_url'], without_breaks)
        self.assertIn('TakWolf', self.value)
        self.assertIn('中科院仿宋点阵 / ISAS FangSong', self.value)
        self.assertIn('中国科学院软件研究所', self.value)
        self.assertIn('The Institute of Software, Academia Sinica', without_breaks)
        self.assertIn('Copyright (C) 1988', self.value)
        self.assertIn('开场视频16px文字', self.value)
        self.assertNotIn('Lyusung', self.value)
        self.assertNotIn('CC BY-SA 4.0', self.value)
        font = references['ISAS FangSong']
        self.assertEqual(font['license_source'], 'https://xorg.freedesktop.org/archive/individual/font/font-isas-misc-1.0.4.tar.xz')
        self.assertEqual(font['local_font_source'], 'experiments/video_lab/fonts/isas-fangsong-16/gb16fs.bdf')
        self.assertEqual(font['local_license'], 'experiments/video_lab/fonts/isas-fangsong-16/COPYING')
        self.assertIn('Python Software Foundation', self.value)

    def test_tool_lines_fit_native_232px_body_and_url_breaks_lose_no_characters(self):
        analysis = json.loads((ROOT/'experiments/font_lab/out/analysis.json').read_text(encoding='utf8'))
        sys.path.insert(0, str(ROOT/'experiments/font_lab'))
        from font_core import parse_bdf
        with zipfile.ZipFile(analysis['font_archive']) as archive:
            font = parse_bdf(archive.read('fusion-pixel-12px-monospaced-zh_hans.bdf'))
        for item in packer.TOOL_CREDITS:
            for line in item['lines']:
                width = sum(font.glyphs[ord(c)]['advance'] for c in line)
                self.assertLessEqual(width, 232, (item['name'], line, width))
            joined = ''.join(item['lines'])
            self.assertIn(item['url'], joined)
            self.assertIn(item['license_url'], joined)

    def test_generated_package_embeds_content_and_keeps_story_save_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)/'menu.bin'
            manifest = Path(folder)/'manifest.json'
            report = packer.build(self.game, output, manifest)
            data = output.read_bytes()
            header = struct.unpack_from('<8s24I', data)
            self.assertEqual(header[0], b'ZSTMENU1')
            self.assertEqual(header[1], 1)
            at, size = struct.unpack_from('<II', data, header[9] + header[21]*8)
            self.assertEqual(data[header[10]+at:header[10]+at+size].decode('utf8'), self.value)
            self.assertEqual(report['credit_tools'], list(packer.TOOL_CREDITS))
            self.assertEqual(report['original_credits_sha256'], hashlib.sha256(self.original.encode('utf8')).hexdigest())
            self.assertEqual(report['credits_layout'], {'font': 12, 'line_pitch': 14, 'body_width': 232, 'scroll': 'continuous rows'})
            self.assertEqual(report['story_identity_sha256'], hashlib.sha256((ROOT/'nds/nitrofs/story/script.bin').read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
