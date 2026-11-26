"""Temporary NDS-layout preview: two 256x192 screens, fixed composition."""
from __future__ import annotations
import argparse
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
from asset_model import DEFAULT_GAME, SAMPLING_MODES, STRIPPED_BOX
from dual_screen import (Renderer, SCENES, COLOR_MODES, POLICIES, FRAMING_LABELS,
                         BACKGROUND_NAMES, BUTTONS, PAGES, export)

class Preview:
    def __init__(self, root, game=DEFAULT_GAME, scale=1, scene='marina_raised'):
        self.root = root
        self.renderer = Renderer(game)
        self.frame_index, self.page = 0, 0
        self.auto_job = None
        self.last_layout = None
        root.title("Z.A.T.O. · NDS 双屏预览")
        root.configure(bg="#10151c")
        root.minsize(760, 700)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#10151c")
        style.configure("TLabel", background="#10151c", foreground="#dce6ee", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"), foreground="#f0f5f8")
        style.configure("Muted.TLabel", foreground="#97aabc", font=("Microsoft YaHei UI", 9))
        style.configure("TButton", padding=(9, 6), background="#253440", foreground="#eef4f8")
        style.map("TButton", background=[("active", "#36546b")])
        style.configure("TCheckbutton", background="#10151c", foreground="#dce6ee")
        style.map("TCheckbutton", background=[("active", "#10151c")])
        style.configure("TCombobox", padding=5)
        main = ttk.Frame(root, padding=18)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="Z.A.T.O. / NDS PREVIEW", style="Title.TLabel").pack(anchor="w")
        ttk.Label(main, text="上屏演出 · 下屏对白与操作 · 每屏 256×192", style="Muted.TLabel").pack(anchor="w", pady=(2, 12))
        body = ttk.Frame(main)
        body.pack(fill="both", expand=True)
        controls = ttk.Frame(body, width=295)
        controls.pack(side="left", anchor="n", fill="y", padx=(0, 24))
        controls.pack_propagate(False)
        self.scene = tk.StringVar(value=next(label for label, key in SCENES.items() if key == scene))
        self.asset = tk.StringVar()
        self.color = tk.StringVar(value="RGB15 / 不额外减色")
        self.sampling = tk.StringVar(value=STRIPPED_BOX)
        self.actor_sampling = STRIPPED_BOX
        self.previous_scene = None
        self.framing = tk.StringVar(value="2×2 · y<572→286+2空行")
        self.background = tk.StringVar(value="library")
        self.policy = tk.StringVar(value=POLICIES[0])
        self.scale = tk.StringVar(value=f"{scale}×")
        self.compare = tk.BooleanVar(value=True)
        self.scene_box = self.combo(controls, "素材组", self.scene, list(SCENES), self.select_scene)
        self.asset_box = self.combo(controls, "当前素材", self.asset, [], self.select_asset)
        nav = ttk.Frame(controls)
        nav.pack(fill="x", pady=(0, 10))
        ttk.Button(nav, text="上一项", command=lambda: self.step(-1)).pack(side="left")
        ttk.Button(nav, text="下一项", command=lambda: self.step(1)).pack(side="left", padx=5)
        self.play_button = ttk.Button(nav, text="自动", command=self.toggle_play)
        self.play_button.pack(side="left")
        self.combo(controls, "颜色模式", self.color, COLOR_MODES, self.changed)
        self.combo(controls, "显示倍率", self.scale, ["1×", "2×"], self.changed)
        ttk.Button(controls, text="ep2d3 · 特殊动效与前后剧情", command=self.open_anim).pack(fill="x", pady=(0, 8))
        ttk.Checkbutton(controls, text="并排显示原图参照（完整双屏）", variable=self.compare,
                        command=self.changed).pack(anchor="w", pady=(0, 10))
        self.framing_box = self.combo(controls, "立绘构图", self.framing, list(FRAMING_LABELS), self.changed)
        self.bg_box = self.combo(controls, "立绘测试背景", self.background, BACKGROUND_NAMES, self.changed)
        self.sampling_box = self.combo(controls, "缩小采样 / 立绘处理", self.sampling, SAMPLING_MODES, self.changed)
        self.policy_box = self.combo(controls, "背景 16 色处理", self.policy, POLICIES, self.changed)
        exports = ttk.Frame(controls)
        exports.pack(fill="x", pady=(0, 10))
        ttk.Button(exports, text="导出双屏", command=self.export).pack(side="left", fill="x", expand=True)
        self.pass_button = ttk.Button(exports, text="各 Pass 对照", command=self.export_passes)
        self.pass_button.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.details = tk.StringVar()
        ttk.Label(controls, textvariable=self.details, style="Muted.TLabel", wraplength=285, justify="left").pack(anchor="w")
        self.screens = ttk.Frame(body)
        self.screens.pack(side="left", anchor="n")
        self.panels = []
        for reference in [True, False]:
            panel = ttk.Frame(self.screens)
            heading = ttk.Label(panel, text="原图参照 / 双屏" if reference else "当前转换 / 双屏")
            heading.pack(anchor="w", pady=(0, 6))
            top = tk.Canvas(panel, highlightthickness=0, bg="black")
            top.pack()
            ttk.Label(panel, text="上屏 256×192", style="Muted.TLabel").pack(anchor="w", pady=(3, 8))
            bottom = tk.Canvas(panel, highlightthickness=0, bg="black", cursor="hand2")
            bottom.pack()
            bottom.bind("<Button-1>", self.touch)
            ttk.Label(panel, text="下屏 256×192 · 正文为排版样张", style="Muted.TLabel").pack(anchor="w", pady=(3, 0))
            self.panels.append((panel, heading, top, bottom))
        self.status = tk.StringVar(value="")
        ttk.Label(main, textvariable=self.status, style="Muted.TLabel", wraplength=1100).pack(anchor="w", pady=(12, 0))
        root.bind("<Left>", lambda e: self.step(-1))
        root.bind("<Right>", lambda e: self.step(1))
        root.bind("<space>", lambda e: self.toggle_play())
        root.bind("a", lambda e: self.step(1))
        root.bind("c", lambda e: self.toggle_compare())
        root.bind("1", lambda e: self.set_scale(1))
        root.bind("2", lambda e: self.set_scale(2))
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.select_scene()

    def combo(self, parent, label, variable, values, callback):
        ttk.Label(parent, text=label).pack(anchor="w", pady=(0, 3))
        box = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly", width=29)
        box.pack(fill="x", pady=(0, 9))
        box.bind("<<ComboboxSelected>>", callback)
        return box

    def open_anim(self):
        from anim_preview import AnimPreview
        window = tk.Toplevel(self.root)
        AnimPreview(window, self.renderer.game, int(self.scale.get()[0]))

    def key(self):
        return SCENES[self.scene.get()]

    def select_scene(self, event=None):
        if self.previous_scene and self.previous_scene.startswith('marina'):
            self.actor_sampling = self.sampling.get()
        actor = self.key().startswith('marina')
        self.sampling_box.configure(values=SAMPLING_MODES if actor else ['Box', 'Nearest'])
        self.sampling.set(self.actor_sampling if actor else 'Box')
        self.previous_scene = self.key()
        self.frame_index = 0
        self.asset_box.configure(values=self.renderer.names(self.key()))
        self.asset.set(self.renderer.names(self.key())[0])
        self.changed()

    def select_asset(self, event=None):
        self.frame_index = self.renderer.names(self.key()).index(self.asset.get())
        self.changed()

    def step(self, direction):
        names = self.renderer.names(self.key())
        self.frame_index = (self.frame_index + direction) % len(names)
        self.page = (self.page + direction) % len(PAGES)
        self.asset.set(names[self.frame_index])
        self.changed()

    def toggle_play(self):
        if self.auto_job is not None:
            self.root.after_cancel(self.auto_job)
            self.auto_job = None
            self.play_button.configure(text="自动")
            self.changed()
        else:
            self.play_button.configure(text="暂停")
            self.auto_job = self.root.after(1000, self.tick)
            self.changed()

    def tick(self):
        self.auto_job = self.root.after(1000, self.tick)
        self.step(1)

    def toggle_compare(self):
        self.compare.set(not self.compare.get())
        self.changed()

    def set_scale(self, scale):
        self.scale.set(f"{scale}×")
        self.changed()

    def touch(self, event):
        scale = int(self.scale.get()[0])
        x, y = event.x / scale, event.y / scale
        for i, (x0, y0, x1, y1, _) in enumerate(BUTTONS):
            if x0 <= x <= x1 and y0 <= y <= y1:
                [lambda: self.step(-1), lambda: self.step(1), self.toggle_play, self.toggle_compare][i]()
                return
        self.step(1)

    def put(self, canvas, im, scale):
        assert im.size == (256, 192)
        photo = ImageTk.PhotoImage(im.resize((256 * scale, 192 * scale), Image.Resampling.NEAREST))
        canvas.configure(width=256 * scale, height=192 * scale)
        canvas.delete("all")
        canvas.create_image(0, 0, anchor="nw", image=photo)
        canvas.image = photo

    def changed(self, event=None):
        self.rendered = self.renderer.render(self.key(), self.frame_index, self.color.get(),
            self.sampling.get(), FRAMING_LABELS[self.framing.get()], self.background.get(),
            self.policy.get(), self.page, self.auto_job is not None)
        scale = int(self.scale.get()[0])
        compare = self.compare.get()
        reference_panel, output_panel = self.panels
        for panel, *_ in self.panels:
            panel.grid_forget()
        if compare:
            reference_panel[0].grid(row=0, column=0, sticky="n", padx=(0, 16))
        output_panel[0].grid(row=0, column=1 if compare else 0, sticky="n")
        for panel, top in [(reference_panel, self.rendered.reference), (output_panel, self.rendered.output)]:
            self.put(panel[2], top, scale)
            self.put(panel[3], self.rendered.lower, scale)
        role = self.key().startswith("marina")
        self.bg_box.configure(state="readonly" if role else "disabled")
        self.framing_box.configure(state="readonly" if role else "disabled")
        self.policy_box.configure(state="readonly" if self.key() in ("library", "vents") and self.color.get() == "16 色" else "disabled")
        self.pass_button.configure(state="normal" if role else "disabled")
        info = self.rendered.info
        size = info.get("image_size", [256, 192])
        stats = info.get("asset_statistics", {})
        text = [f"素材：{info['asset']}", "输出：上下屏各 256×192", f"电脑显示：每屏 {256 * scale}×{192 * scale}",
                f"画面素材：{size[0]}×{size[1]}", f"当前模式：{self.color.get()}",
                "固定构图；不平移镜头", "1× 为像素对应，不是物理尺寸校准。"]
        if stats:
            text += [f"素材原始数据：{stats['raw_frame_bytes']:,} B", f"色板：{stats.get('palette_bytes', 0)} B"]
            if 'zlib_frame_bytes' in stats:
                text.append(f"zlib 文件估算：{stats['zlib_frame_bytes']:,} B")
            if "delta_bytes" in stats:
                text.append(f"差分：{stats['changed_tiles']} 块 / {stats['delta_bytes']:,} B")
        if info.get('multi_pass'):
            p = info['multi_pass']
            text += [f"Pass：{p['block_factor'][0]}×{p['block_factor'][1]} 多数色采样",
                     f"去边 {p['removed_white_pixels']} / 修线 {p['repaired_line_pixels']}",
                     f"原图参考淡化：{p['faded_dark_pixels']} 像素"]
        if info.get('actor_processing'):
            text.append("处理：去白边 → Box → 1像素新白边；独立白色特效保留")
        if info.get('clipped_bottom_pixels'):
            text.append(f"顶部固定显示；底部 {info['clipped_bottom_pixels']} 像素在屏外")
        if self.key() == 'cg':
            text += [f"插画：{self.frame_index + 1}/{len(self.renderer.cg_paths)}", "完整 16:9 构图；上下各留 24 像素黑边"]
        self.details.set("\n".join(text))
        hint = ("插画完整缩为 256×144；可切换原色 / RGB15 / 索引色进行比较。" if self.key() == 'cg'
                else "2×2 立绘资源高 288，上屏固定显示顶部 192 行。完整素材请看各 Pass 对照。" if role
                else "背景完整显示；16 色模式可比较换色板与图块补丁。")
        self.status.set("方向键/A：素材　空格：自动　C：对照　1/2：倍率。" + hint + "这是 Python 预览。")
        layout = scale, compare
        if layout != self.last_layout:
            width = 375 + (2 if compare else 1) * 256 * scale + (16 if compare else 0)
            height = max(950, 192 * scale * 2 + 240)
            self.root.geometry(f"{max(800, width)}x{height}")
            self.last_layout = layout

    def export(self):
        path = export(self.rendered)
        self.status.set(f"已导出 1×/2× 双屏、并排对照及参数：{path}")

    def export_passes(self):
        from pixel_passes import process, export_passes
        from dual_screen import OUT
        sampling = self.sampling.get() if self.sampling.get().startswith('MultiPass') else 'MultiPass+Fade'
        family = self.renderer.family(self.key(), sampling, FRAMING_LABELS[self.framing.get()])
        index = self.frame_index % len(family.names)
        with Image.open(family.paths[index]) as source:
            result = process(source, family.size, family.crop_boxes[index], fade=sampling == 'MultiPass+Fade')
        path = export_passes(result, OUT.parent / 'multi_pass' / family.names[index] / family.framing)
        self.status.set(f"各 Pass、Box/Nearest 对照及参数已导出：{path}")
        self.show_comparison(path / 'pass_comparison.png', f"各 Pass 对照 · {family.names[index]}", "Box → 去白边+Box → Box+新白边 → 多数色 Step → 修线 → 淡化 → Step+新白边")

    def show_comparison(self, path, title, caption):
        if getattr(self, 'pass_window', None) is not None and self.pass_window.winfo_exists():
            self.pass_window.destroy()
        self.pass_window = tk.Toplevel(self.root)
        self.pass_window.title(title)
        self.pass_window.configure(bg='#10151c')
        ttk.Label(self.pass_window, text=caption, padding=12).pack(anchor='w')
        with Image.open(path) as image:
            picture = ImageTk.PhotoImage(image.copy())
        canvas = tk.Canvas(self.pass_window, width=picture.width(), height=picture.height(), highlightthickness=0)
        canvas.pack(padx=8, pady=(0, 8))
        canvas.create_image(0, 0, anchor='nw', image=picture)
        canvas.image = picture

    def close(self):
        if self.auto_job is not None:
            self.root.after_cancel(self.auto_job)
        self.root.destroy()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", type=Path, default=DEFAULT_GAME)
    parser.add_argument("--scale", type=int, choices=[1, 2], default=1)
    parser.add_argument("--scene", choices=list(SCENES.values()) + ['anim'], default='marina_raised')
    parser.add_argument("--headless-export", action="store_true")
    args = parser.parse_args()
    if args.scene == 'anim':
        if args.headless_export:
            from verify_anim_preview import main as verify_anim
            verify_anim()
            return
        from anim_preview import run
        run(args.game, args.scale)
        return
    if args.headless_export:
        result = Renderer(args.game).render(args.scene, 0, COLOR_MODES[1], sampling=STRIPPED_BOX if args.scene.startswith('marina') else 'Box', framing='crop572_288')
        print(export(result))
        return
    root = tk.Tk()
    Preview(root, args.game, args.scale, scene=args.scene)
    root.mainloop()

if __name__ == "__main__":
    main()
