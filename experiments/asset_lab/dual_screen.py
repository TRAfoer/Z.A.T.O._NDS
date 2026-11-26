"""Fixed-composition, two-screen Python preview; not NDS GPU emulation."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from asset_model import DEFAULT_GAME, GROUPS, Family, train_palette, nearest, rgb15, SAMPLING_MODES, STRIPPED_BOX

SCENES = {"Marina · 抱臂": "marina_crossed", "Marina · 抬手": "marina_raised",
          "Library · 色板切换": "library", "Vents · 色板切换": "vents", "插画 / CG": "cg"}
COLOR_MODES = ["原图 / 不减色", "RGB15 / 不额外减色", "256 色", "64 色", "32 色", "16 色"]
POLICIES = ["逐张量化", "基准索引 + 换色板", "联合索引 + 换色板", "联合色板 + 图块补丁"]
FRAMING_LABELS = {"2×2 · y<572→286+2空行": "crop572_288", "4×4 测试 · y<576→144": "crop576_144", "完整构图 · 16:9": "legacy144", "半身适配 · 截至 y=576": "crop576"}
BACKGROUND_NAMES = ["library", "library2", "library3", "library4", "library5", "vents", "vents_red"]
PAGES = ["这里是一段用于检查阅读效果的示例文字。\n请观察字号、换行、标点和上下屏的留白。",
         "普通对白显示在下屏。\n画面保持固定构图，切换素材时不会移动镜头。",
         "这段文字仅用于排版预览。\n点击正文可以继续，也可以使用下方的操作按钮。"]
BUTTONS = [(8, 164, 64, 186, "上一项"), (68, 164, 124, 186, "下一项"),
           (128, 164, 184, 186, "自动"), (188, 164, 248, 186, "对照")]
OUT = Path(__file__).resolve().parent / "out" / "dual_screen"

def font(size, chinese=True):
    return ImageFont.truetype("C:/Windows/Fonts/msyh.ttc" if chinese else "C:/Windows/Fonts/consola.ttf", size)

def wrap(text, face, width):
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for char in paragraph:
            if line and face.getlength(line + char) > width:
                lines.append(line)
                line = ""
            line += char
        lines.append(line)
    return lines

def lower_screen(page=0, playing=False):
    im = Image.new("RGB", (256, 192), (8, 8, 8))
    d = ImageDraw.Draw(im)
    d.text((12, 10), "排版样张", font=font(14), fill=(230, 230, 230))
    d.text((218, 13), f"{page % len(PAGES) + 1}/3", font=font(11, False), fill=(156, 156, 156))
    d.line((12, 34, 244, 34), fill=(75, 75, 75))
    face = font(16)
    for j, line in enumerate(wrap(PAGES[page % len(PAGES)], face, 232)):
        d.text((12, 47 + j * 22), line, font=face, fill=(239, 239, 239))
    for x0, y0, x1, y1, label in BUTTONS:
        if label == "自动" and playing:
            label = "暂停"
        d.rectangle((x0, y0, x1, y1), outline=(100, 100, 100), fill=(24, 24, 24))
        face = font(12)
        d.text(((x0 + x1 - face.getlength(label)) / 2, y0 + 3), label, font=face, fill="white")
    return Image.fromarray(rgb15(np.array(im)))

@dataclass
class Rendered:
    reference: Image.Image
    output: Image.Image
    lower: Image.Image
    info: dict

class Renderer:
    def __init__(self, game=DEFAULT_GAME):
        self.game = Path(game)
        self.families = {}
        self.images = {}
        self.cg_paths = sorted((self.game / "images/cgs").glob("*.png"))

    def names(self, key):
        return [p.stem for p in self.cg_paths] if key == "cg" else GROUPS[key][2]

    def family(self, key, sampling, framing):
        cache_key = key, sampling, framing
        if cache_key not in self.families:
            self.families[cache_key] = Family(key, self.game, sampling, framing)
        return self.families[cache_key]

    def image(self, path, sampling):
        sampling = "Nearest" if sampling == "Nearest" else "Box"
        key = str(path), sampling
        if key not in self.images:
            with Image.open(path) as im:
                im = im.convert("RGBA").resize((256, 144), Image.Resampling.BOX if sampling == "Box" else Image.Resampling.NEAREST)
            self.images[key] = im
        return self.images[key]

    @staticmethod
    def convert(im, color_mode):
        if color_mode == COLOR_MODES[0]:
            return im.copy()
        a = np.array(im)
        mask = a[:, :, 3] >= 128
        if color_mode == COLOR_MODES[1]:
            rgb = rgb15(a[:, :, :3])
        else:
            pal = train_palette([a], int(color_mode.split()[0]))
            rgb = pal[nearest(a[:, :, :3], pal)]
        return Image.fromarray(np.dstack([rgb, mask.astype(np.uint8) * 255]))

    def render(self, key, index, color_mode, sampling="Box", framing="legacy144",
               background="library", policy=POLICIES[0], page=0, playing=False):
        assert color_mode in COLOR_MODES
        if sampling not in SAMPLING_MODES:
            raise ValueError(f"Unsupported sampling mode: {sampling}")
        reference = Image.new("RGBA", (256, 192), (0, 0, 0, 255))
        output = reference.copy()
        info = {"scene": key, "color_mode": color_mode, "sampling": sampling,
                "screen_size": [256, 192], "fixed_composition": True,
                "actor_framing": framing, "camera_panning": False}
        if key == "cg":
            if not self.cg_paths:
                raise FileNotFoundError("No top-level CG PNGs found")
            path = self.cg_paths[index % len(self.cg_paths)]
            original = self.image(path, sampling)
            reference.alpha_composite(original, (0, 24))
            output.alpha_composite(self.convert(original, color_mode), (0, 24))
            with Image.open(path) as source:
                source_size = list(source.size)
            info.update(asset=path.name, image_size=list(original.size), source_size=source_size,
                        image_position=[0, 24], whole_image=True, image_sampling="Nearest" if sampling == 'Nearest' else 'Box',
                        palette_policy="single CG")
        elif GROUPS[key][1] == "sprites":
            # Fixed full background canvas even with optional half-body actors.
            bg = self.image(self.game / "images/bgs" / (background + ".png"), sampling)
            reference.alpha_composite(bg, (0, 24))
            output.alpha_composite(self.convert(bg, color_mode), (0, 24))
            family = self.family(key, sampling, framing)
            index %= len(family.names)
            reference_family = self.family(key, "Box", framing) if sampling.startswith("MultiPass") or sampling == STRIPPED_BOX else family
            original = reference_family.reference(index)
            if color_mode == COLOR_MODES[0]:
                converted = original
            else:
                method = "RGB15" if color_mode == COLOR_MODES[1] else "Shared " + color_mode.split()[0]
                frame = family.frames(method)[index]
                converted = frame.image()
                info.update(asset_statistics=frame.stats)
            xy = ((256 - original.width) // 2, max(0, (192 - original.height) // 2))
            reference.alpha_composite(original, xy)
            output.alpha_composite(converted, xy)
            info.update(asset=family.names[index], background=background, image_size=list(original.size),
                        actor_position=list(xy), source_crop=list(family.crop_boxes[index]),
                        clipped_bottom_pixels=max(0, original.height - 192),
                        palette_policy="shared per actor pose")
            if sampling.startswith("MultiPass") and color_mode != COLOR_MODES[0]:
                info.update(multi_pass=family.pass_infos[index], reference_sampling="Box")
            elif sampling == STRIPPED_BOX and color_mode != COLOR_MODES[0]:
                info.update(actor_processing="remove source white edge, Box body, add 1px white outline; preserve detached white effects separately", reference_sampling="Box")
        else:
            family = self.family(key, "Nearest" if sampling == "Nearest" else "Box", "legacy144")
            index %= len(family.names)
            original = family.reference(index)
            if color_mode == COLOR_MODES[0]:
                converted = original
            elif color_mode == "16 色":
                method = dict(zip(POLICIES, ["Independent 16", "Base + palette", "Joint + palette", "Joint + tile patches"]))[policy]
                frame = family.frames(method)[index]
                converted = frame.image()
                info.update(asset_statistics=frame.stats)
            else:
                converted = self.convert(original, color_mode)
            reference.alpha_composite(original, (0, 24))
            output.alpha_composite(converted, (0, 24))
            info.update(asset=family.names[index], image_size=list(original.size), palette_policy=policy if color_mode == "16 色" else "independent")
        reference, output = reference.convert("RGB"), output.convert("RGB")
        error = np.array(output).astype(np.int16) - np.array(reference).astype(np.int16)
        mse = float(np.mean(error.astype(np.float32) ** 2))
        info["top_screen_psnr_db"] = None if mse == 0 else round(float(10 * np.log10(255 ** 2 / mse)), 2)
        info["top_screen_max_channel_difference"] = int(np.max(np.abs(error)))
        return Rendered(reference, output, lower_screen(page, playing), info)

def stack(top, bottom):
    assert top.size == bottom.size == (256, 192)
    result = Image.new("RGB", (256, 400), (27, 32, 39))
    result.paste(top, (0, 0))
    result.paste(bottom, (0, 208))
    return result

def export(rendered, out=OUT):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rendered.reference.save(out / "source_top_256x192.png")
    rendered.output.save(out / "converted_top_256x192.png")
    rendered.lower.save(out / "lower_256x192.png")
    device = stack(rendered.output, rendered.lower)
    device.save(out / "dual_screen_1x.png")
    device.resize((512, 800), Image.Resampling.NEAREST).save(out / "dual_screen_2x.png")
    comparison = Image.new("RGB", (528, 400), (27, 32, 39))
    comparison.paste(stack(rendered.reference, rendered.lower), (0, 0))
    comparison.paste(device, (272, 0))
    comparison.save(out / "dual_screen_comparison.png")
    (out / "preview_state.json").write_text(json.dumps(rendered.info, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
