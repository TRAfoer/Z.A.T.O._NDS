"""Temporary local Tk preview. Run with python preview.py; no asset installs."""
from __future__ import annotations

import argparse
from pathlib import Path
import tkinter as tk
from tkinter import ttk

import numpy as np
from PIL import Image, ImageTk, ImageDraw

from asset_model import DEFAULT_GAME, GROUPS, FRAMINGS, Family, stage

OUT = Path(__file__).resolve().parent / "out"


class Preview:
    def __init__(self, root, game, framing="source1x"):
        self.root, self.game = root, game
        self.families = {}
        self.photos = []
        self.auto_job = None
        self.frame_index = 0
        self.family_key = "marina_crossed"
        root.title("Z.A.T.O. Asset Lab - Python prototype")
        root.geometry("1190x1010")
        root.minsize(1120, 960)
        root.configure(bg="#10151c")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#10151c")
        style.configure("TLabel", background="#10151c", foreground="#d9e3ed", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"), foreground="#f2f7fa")
        style.configure("Muted.TLabel", foreground="#8da1b3")
        style.configure("TButton", padding=(10, 7), background="#253440", foreground="#e3edf4")
        style.map("TButton", background=[("active", "#36546b")])
        style.configure("TCheckbutton", background="#10151c", foreground="#d9e3ed")
        style.map("TCheckbutton", background=[("active", "#10151c")])
        style.configure("TCombobox", padding=5)
        main = ttk.Frame(root, padding=20)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="Z.A.T.O.  /  ASSET LAB", style="Title.TLabel").pack(anchor="w")
        ttk.Label(main, text="Python prototype · two Marina poses · shared indices / palette swaps / tile patches", style="Muted.TLabel").pack(anchor="w", pady=(2, 14))
        bar = ttk.Frame(main)
        bar.pack(fill="x")
        self.family_var = tk.StringVar(value=GROUPS[self.family_key][0])
        self.family_box = ttk.Combobox(bar, textvariable=self.family_var, state="readonly", width=28,
                                     values=[v[0] for v in GROUPS.values()])
        self.family_box.pack(side="left", padx=(0, 12))
        self.family_box.bind("<<ComboboxSelected>>", self.select_family)
        self.name_var = tk.StringVar()
        self.name_box = ttk.Combobox(bar, textvariable=self.name_var, state="readonly", width=20)
        self.name_box.pack(side="left", padx=(0, 12))
        self.name_box.bind("<<ComboboxSelected>>", self.select_frame)
        ttk.Button(bar, text="< Previous", command=lambda: self.step(-1)).pack(side="left", padx=3)
        ttk.Button(bar, text="Next >", command=lambda: self.step(1)).pack(side="left", padx=3)
        self.play_button = ttk.Button(bar, text="Play", command=self.toggle_play)
        self.play_button.pack(side="left", padx=3)
        ttk.Button(bar, text="Export comparisons", command=self.export).pack(side="right")
        settings = ttk.Frame(main)
        settings.pack(fill="x", pady=12)
        ttk.Label(settings, text="Processing").pack(side="left", padx=(0, 8))
        self.method = tk.StringVar(value="Exact RGB15 indexed" if framing in ("source1x", "crop576_1x") else "Shared 16")
        self.method_box = ttk.Combobox(settings, textvariable=self.method, state="readonly", width=25)
        self.method_box.pack(side="left", padx=(0, 18))
        self.method_box.bind("<<ComboboxSelected>>", lambda e: self.render())
        ttk.Label(settings, text="Framing").pack(side="left", padx=(0, 8))
        self.framing = tk.StringVar(value=FRAMINGS[framing])
        framing_box = ttk.Combobox(settings, textvariable=self.framing, state="readonly", width=26,
                                   values=list(FRAMINGS.values()))
        framing_box.pack(side="left", padx=(0, 12))
        framing_box.bind("<<ComboboxSelected>>", lambda e: self.load_family())
        ttk.Label(settings, text="Resize").pack(side="left", padx=(0, 8))
        self.sampling = tk.StringVar(value="Nearest")
        sampling_box = ttk.Combobox(settings, textvariable=self.sampling, state="readonly", width=10, values=["Nearest", "Box"])
        sampling_box.pack(side="left", padx=(0, 18))
        sampling_box.bind("<<ComboboxSelected>>", lambda e: self.load_family())
        self.diff = tk.BooleanVar()
        ttk.Checkbutton(settings, text="Error", variable=self.diff, command=self.render).pack(side="left", padx=4)
        self.tiles = tk.BooleanVar()
        ttk.Checkbutton(settings, text="Tiles", variable=self.tiles, command=self.render).pack(side="left", padx=4)
        display = ttk.Frame(main)
        display.pack(fill="x")
        self.headings, self.canvases, self.zooms = [], [], []
        for i, title in enumerate(["SOURCE / resized reference", "OUTPUT / processed"]):
            pane = ttk.Frame(display)
            pane.grid(row=0, column=i, padx=(0, 24) if i == 0 else 0, sticky="nw")
            heading = ttk.Label(pane, text=title)
            heading.pack(anchor="w", pady=(0, 6))
            self.headings.append(heading)
            canvas = tk.Canvas(pane, width=512, height=384, highlightthickness=1, highlightbackground="#334554", bg="#070a0e")
            canvas.pack()
            canvas.bind("<Motion>", self.pointer)
            canvas.bind("<ButtonPress-1>", self.pan_start)
            canvas.bind("<B1-Motion>", self.pan_drag)
            self.canvases.append(canvas)
            zoom = tk.Canvas(pane, width=512, height=64, highlightthickness=1, highlightbackground="#334554", bg="#070a0e")
            zoom.pack(pady=(8, 0))
            self.zooms.append(zoom)
        camera_bar = ttk.Frame(main)
        camera_bar.pack(fill="x", pady=(7, 10))
        ttk.Label(camera_bar, text="256x192 viewport / drag image or pan").pack(side="left", padx=(0, 12))
        self.camera_x = tk.DoubleVar(value=97)
        self.camera_y = tk.DoubleVar(value=32)
        ttk.Label(camera_bar, text="X").pack(side="left")
        self.x_slider = ttk.Scale(camera_bar, from_=0, to=194, variable=self.camera_x,
                                  command=lambda v: self.render(), length=180)
        self.x_slider.pack(side="left", padx=(4, 16))
        ttk.Label(camera_bar, text="Y").pack(side="left")
        self.y_slider = ttk.Scale(camera_bar, from_=0, to=528, variable=self.camera_y,
                                  command=lambda v: self.render(), length=180)
        self.y_slider.pack(side="left", padx=(4, 12))
        ttk.Button(camera_bar, text="Face / center", command=self.center_camera).pack(side="left")
        details = ttk.Frame(main)
        details.pack(fill="x")
        self.metrics = ttk.Label(details, text="", font=("Consolas", 10), justify="left")
        self.metrics.pack(side="left", anchor="n")
        self.palette_canvas = tk.Canvas(details, width=390, height=80, bg="#10151c", highlightthickness=0)
        self.palette_canvas.pack(side="right", anchor="n")
        self.status = tk.StringVar(value="Ready")
        ttk.Label(main, textvariable=self.status, style="Muted.TLabel", wraplength=1090).pack(anchor="w", pady=(10, 0))
        ttk.Label(main, text="Left / Right: frame     Space: play / pause     1–8: processing     D: error     T: tiles     Drag: 1:1 camera", style="Muted.TLabel").pack(anchor="w", pady=(6, 0))
        root.bind("<Left>", lambda e: self.step(-1))
        root.bind("<Right>", lambda e: self.step(1))
        root.bind("<space>", lambda e: self.toggle_play())
        root.bind("d", lambda e: self.toggle(self.diff))
        root.bind("t", lambda e: self.toggle(self.tiles))
        for i in range(8):
            root.bind(str(i + 1), lambda e, j=i: self.set_method(j))
        self.load_family()

    def toggle(self, variable):
        variable.set(not variable.get())
        self.render()

    def family(self):
        framing = next(k for k, v in FRAMINGS.items() if v == self.framing.get())
        key = (self.family_key, self.sampling.get(), framing)
        if key not in self.families:
            self.families[key] = Family(self.family_key, self.game, self.sampling.get(), framing)
        return self.families[key]

    def select_family(self, event=None):
        self.family_key = list(GROUPS)[self.family_box.current()]
        self.frame_index = 0
        self.load_family()

    def load_family(self):
        family = self.family()
        self.name_box.configure(values=family.names)
        self.frame_index %= len(family.names)
        self.name_var.set(family.names[self.frame_index])
        self.method_box.configure(values=family.methods)
        if self.method.get() not in family.methods:
            self.method.set("Joint + palette" if family.kind == "background" else "Exact RGB15 indexed" if family.framing in ("source1x", "crop576_1x") else "Shared 16")
        self.x_slider.configure(to=max(0, family.size[0] - 256))
        self.y_slider.configure(to=max(0, family.size[1] - 192))
        self.center_camera()

    def center_camera(self):
        family = self.family()
        self.camera_x.set(max(0, (family.size[0] - 256) // 2))
        self.camera_y.set(min(32, max(0, family.size[1] - 192)) if family.kind == "sprites"
                          else max(0, (family.size[1] - 192) // 2))
        self.render()

    def select_frame(self, event=None):
        self.frame_index = self.family().names.index(self.name_var.get())
        self.render()

    def step(self, direction):
        self.frame_index = (self.frame_index + direction) % len(self.family().names)
        self.name_var.set(self.family().names[self.frame_index])
        self.render()

    def set_method(self, index):
        if index >= len(self.family().methods):
            return
        self.method.set(self.family().methods[index])
        self.render()

    def toggle_play(self):
        if self.auto_job is not None:
            self.root.after_cancel(self.auto_job)
            self.auto_job = None
            self.play_button.configure(text="Play")
        else:
            self.play_button.configure(text="Pause")
            self.tick()

    def tick(self):
        self.step(1)
        self.auto_job = self.root.after(700, self.tick)

    def put(self, canvas, image):
        photo = ImageTk.PhotoImage(image)
        canvas.delete("all")
        canvas.create_image(0, 0, image=photo, anchor="nw")
        canvas.image = photo

    def render(self):
        family = self.family()
        frame = family.frames(self.method.get())[self.frame_index]
        original = family.reference(self.frame_index)
        camera = (int(self.camera_x.get()), int(self.camera_y.get()))
        self.left = stage(original, family.kind, camera)
        self.right = stage(frame.image(), family.kind, camera)
        shown = self.right.copy()
        if self.diff.get():
            error = np.abs(np.array(self.left).astype(np.int16) - np.array(self.right).astype(np.int16)).max(2)
            heat = np.zeros((*error.shape, 3), dtype=np.uint8)
            heat[:, :, 0] = np.minimum(error * 4, 255)
            heat[:, :, 1] = np.minimum(error, 255)
            shown = Image.fromarray(heat)
        if self.tiles.get() and frame.indices is not None:
            draw = ImageDraw.Draw(shown)
            xoff = (256 - frame.indices.shape[1]) // 2 if frame.indices.shape[1] < 256 else -camera[0]
            yoff = (192 - frame.indices.shape[0]) // 2 if frame.indices.shape[0] < 192 else -camera[1]
            for y in range(0, frame.changed.shape[0], 8):
                for x in range(0, frame.changed.shape[1], 8):
                    if frame.changed[y:y + 8, x:x + 8].any():
                        draw.rectangle((x + xoff, y + yoff, x + xoff + 7, y + yoff + 7), outline="#6bf0b8")
        self.put(self.canvases[0], self.left.resize((512, 384), Image.Resampling.NEAREST))
        self.put(self.canvases[1], shown.resize((512, 384), Image.Resampling.NEAREST))
        self.headings[0].configure(text="SOURCE / original pixels, 1:1 viewport" if family.framing in ("source1x", "crop576_1x") else "SOURCE / resized reference")
        self.headings[1].configure(text=f"{self.method.get()} / {frame.name}")
        self.update_zoom(128, 55)
        stats = frame.stats
        def size(n): return f"{n:,} B ({n / 1024:.2f} KiB)"
        box = family.crop_boxes[self.frame_index]
        lines = [f"Screen 256x192 | asset {family.size[0]}x{family.size[1]} | camera {camera}",
                 f"Visible PSNR {stats['visible_psnr_db']:.2f} dB | alpha changes {stats['alpha_changed_pixels']}",
                 f"Original PNG    {size(stats['source_png_bytes'])}",
                 f"Working frame   {size(stats['raw_frame_bytes'])}  | palette {stats['palette_bytes']} B"]
        if "changed_tiles" in stats:
            lines += [f"Host zlib       {size(stats['zlib_frame_bytes'])}  (frame only)",
                      f"Changed tiles   {stats['changed_tiles']}/{stats['total_tiles']} | patch {stats['delta_bytes']:,} B / zlib {stats['delta_zlib_bytes']:,} B",
                      f"Decode checks   {stats['roundtrip']}"]
        if "unique_family_tiles" in stats:
            lines += [f"Family reuse    {stats['unique_family_tiles']} unique tiles | dictionary + maps {stats['family_dictionary_bytes'] + stats['family_map_bytes']:,} B"]
        if stats.get("exact_rgb15"):
            lines[-1] += f" | EXACT / {stats['visible_colors']} colors"
        self.metrics.configure(text="\n".join(lines))
        pc = self.palette_canvas
        pc.delete("all")
        if frame.palette is not None:
            pc.create_text(0, 0, text=f"{len(frame.palette)} palette entries / RGB15", anchor="nw", fill="#a9bccc", font=("Consolas", 10))
            columns = 32 if len(frame.palette) > 64 else 16
            dx = 11 if columns == 32 else 22
            dy = 6 if len(frame.palette) > 64 else 12
            for j, color in enumerate(frame.palette):
                x, y = (j % columns) * dx, 24 + (j // columns) * dy
                pc.create_rectangle(x, y, x + dx - 1, y + dy - 1, fill="#%02x%02x%02x" % tuple(color), outline="")
        if family.kind == "background":
            note = "Pure palette modes reuse ONE index plane + base transparency. Joint mode trains all states together. Patches restore nearest indices using the SAME joint palette; Independent 16 is a separate quality reference."
        else:
            note = "Shared palettes are per pose family. Slot 0 is transparent. 32-256 entries use 8-bit indices. Exact RGB15 indexed retains every DS-color pixel. All tile/delta decoding is checked."
        framing_note = (" 1:1 keeps source pixels; drag/slide the 256x192 camera. Other modes resize for comparison."
                        if family.kind == "sprites" else " 1:1 keeps source pixels; 192px mode center-crops to 4:3; legacy retains 16:9.")
        self.status.set(note + framing_note + "  zlib is a host-side estimate, not NDS validation.")
        self.root.update_idletasks()

    def update_zoom(self, x, y):
        x, y = min(max(x, 32), 224), min(max(y, 4), 188)
        for canvas, image in zip(self.zooms, [self.left, self.right]):
            detail = image.crop((x - 32, y - 4, x + 32, y + 4)).resize((512, 64), Image.Resampling.NEAREST)
            self.put(canvas, detail)

    def pointer(self, event):
        self.update_zoom(event.x // 2, event.y // 2)

    def pan_start(self, event):
        self.pan_anchor = (event.x, event.y, self.camera_x.get(), self.camera_y.get())

    def pan_drag(self, event):
        if not hasattr(self, "pan_anchor"):
            return
        x, y, cx, cy = self.pan_anchor
        family = self.family()
        self.camera_x.set(min(max(0, cx + (x - event.x) / 2), max(0, family.size[0] - 256)))
        self.camera_y.set(min(max(0, cy + (y - event.y) / 2), max(0, family.size[1] - 192)))
        self.render()

    def export(self):
        self.status.set("Exporting all methods for this family...")
        self.root.update_idletasks()
        output = OUT / self.family().framing
        self.family().export(output)
        self.status.set(f"Exported comparison sheet, processed PNGs, palettes and JSON: {output}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", type=Path, default=DEFAULT_GAME)
    parser.add_argument("--export-all", action="store_true")
    parser.add_argument("--framing", choices=list(FRAMINGS), default="source1x")
    args = parser.parse_args()
    if args.export_all:
        for sampling in ["Nearest", "Box"]:
            for key in GROUPS:
                family = Family(key, args.game, sampling, args.framing)
                family.export(OUT / args.framing)
                print(f"PASS {key} / {sampling} / {args.framing} {family.size}: all decode checks, {len(family.names)} frames x {len(family.methods)} methods", flush=True)
        return
    root = tk.Tk()
    Preview(root, args.game, args.framing)
    root.mainloop()


if __name__ == "__main__":
    main()
