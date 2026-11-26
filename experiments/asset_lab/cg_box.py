"""Export full-composition CG Box / RGB15 trials without modifying originals."""
from pathlib import Path
import json
import zlib
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from asset_model import rgb15_bytes
from dual_screen import Renderer, COLOR_MODES


def main():
    renderer = Renderer()
    out = Path(__file__).resolve().parent / 'out' / 'cg_box'
    out.mkdir(parents=True, exist_ok=True)
    reports = []
    sheets = [Image.new('RGB', (4 * 272, 5 * 174 + 30), '#10151c') for _ in range((len(renderer.cg_paths) + 19) // 20)]
    face = ImageFont.truetype('C:/Windows/Fonts/consola.ttf', 12)
    for j, sheet in enumerate(sheets):
        ImageDraw.Draw(sheet).text((8, 7), f'Full CG / Box -> RGB15 / 256x144 / page {j + 1}', font=face, fill='white')
    for i, path in enumerate(renderer.cg_paths):
        original = renderer.image(path, 'Box')
        rgb15 = renderer.convert(original, COLOR_MODES[1])
        # Compare directly with the source resize so no actor treatment or crop
        # can silently enter this export path.
        with Image.open(path) as source:
            expected = source.convert('RGBA').resize((256, 144), Image.Resampling.BOX)
            source_size = list(source.size)
        assert np.array_equal(np.array(original), np.array(expected)), 'CG differs from full-source Box'
        folder = out / path.stem
        folder.mkdir(exist_ok=True)
        original.save(folder / 'box_original_colors_256x144.png')
        rgb15.save(folder / 'box_rgb15_256x144.png')
        rendered = renderer.render('cg', i, COLOR_MODES[1], sampling='Box')
        rendered.output.save(folder / 'top_256x192.png')
        assert not np.array(rendered.output)[:24].any() and not np.array(rendered.output)[168:].any()
        assert np.array_equal(np.array(rendered.output)[24:168], np.array(rgb15.convert('RGB')))
        a = np.array(rgb15)
        raw = rgb15_bytes(a[:, :, :3], a[:, :, 3] >= 128)
        reports.append(dict(asset=path.stem, source_size=source_size, size=[256, 144],
                            raw_rgb15_bytes=len(raw), zlib_bytes=len(zlib.compress(raw, 9))))
        sheet = sheets[i // 20]
        x, y = (i % 4) * 272 + 8, ((i % 20) // 4) * 174 + 30
        ImageDraw.Draw(sheet).text((x, y), path.stem, font=face, fill='white')
        sheet.paste(rgb15.convert('RGB'), (x, y + 18))
    for i, sheet in enumerate(sheets):
        sheet.save(out / f'contact_sheet_{i + 1}.png')
    (out / 'summary.json').write_text(json.dumps(dict(sampling='Box', framing='whole image, original aspect ratio',
        screen=[256, 192], position=[0, 24], assets=reports), ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'PASS {len(reports)} full CG Box matches, RGB15 outputs, exact centered screen composition / {out}')


if __name__ == '__main__':
    main()
