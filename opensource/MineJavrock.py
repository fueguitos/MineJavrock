#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Java ➜ Bedrock Texture Pack Converter (.zip ➜ .mcpack)
Advanced GUI with Tkinter + ttk

Main features:
- Load a Java resource pack (.zip)
- Reorganize textures into Bedrock structure (textures/blocks, textures/items, textures/ui, etc.)
- Generate valid manifest.json with UUIDs
- Automatically generate terrain_texture.json and item_texture.json
- Option to keep original names or normalize them
- Basic detection of animations (.mcmeta) with warning (exports base texture)
- Export as a single .mcpack ready to import in Bedrock
- Detailed logging in UI, progress bar, and validations

Note: This prototype focuses on converting block and item textures. Very specific elements
(shaders, models, advanced colormaps, CTM, OptiFine, emissive, etc.) are ignored with a warning.

Requirements: Python 3.8+
(No external libraries required; optionally uses Pillow if installed to convert non-PNG formats)
"""

import io
import os
import re
import sys
import json
import uuid
import time
import shutil
import zipfile
import tempfile
from pathlib import Path

try:
    from PIL import Image
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_NAME = "Java→Bedrock Pack Converter"
MIN_ENGINE_VERSION = [1, 13, 0]
DEFAULT_VERSION = [1, 0, 0]

JAVA_TEXTURE_ROOT = Path("assets/minecraft/textures")

# Basic mapping Java ➜ Bedrock
DEFAULT_DIR_MAP = {
    "block": "blocks",
    "blocks": "blocks",
    "item": "items",
    "items": "items",
    "entity": "entity",
    "gui": "ui",
    "font": "font",
    "environment": "environment",
    "painting": "painting",
    "particle": "particles",
    "particles": "particles",
    "colormap": "colormap",
}

IGNORED_DIRS = {
    "mcpatcher", "optifine", "models", "blockstates", "shaders", "atlases", "texts",
}

VALID_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tga", ".bmp"}

# Utilities -------------------------------------------------------------------

def log(ui, msg):
    ui.log_box.configure(state="normal")
    ui.log_box.insert("end", f"{time.strftime('%H:%M:%S')} • {msg}\n")
    ui.log_box.see("end")
    ui.log_box.configure(state="disabled")
    ui.root.update_idletasks()

def slugify(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9_\- ]+", "", name)
    name = name.replace(" ", "_")
    return name or "pack"

def ensure_png_bytes(data: bytes, src_suffix: str) -> bytes:
    if src_suffix.lower() == ".png":
        return data
    if not PIL_AVAILABLE:
        raise ValueError("Cannot convert to PNG (Pillow not installed)")
    with Image.open(io.BytesIO(data)) as im:
        out = io.BytesIO()
        im.save(out, format="PNG")
        return out.getvalue()

def make_uuid() -> str:
    return str(uuid.uuid4())

def write_json(path: Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def zip_dir_to_file(src_dir: Path, out_file: Path):
    with zipfile.ZipFile(out_file, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(src_dir):
            for fn in files:
                p = Path(root) / fn
                z.write(p, arcname=str(p.relative_to(src_dir)))

# Conversion core ------------------------------------------------------------

class Converter:
    def __init__(self, ui):
        self.ui = ui
        self.warnings = []
        self.stats = {"copied": 0, "skipped": 0, "converted": 0}
        self.terrain_textures = {}
        self.item_textures = {}

    def convert(self, zip_path: Path, out_dir: Path, pack_name: str, pack_desc: str,
                version: list, normalize_names: bool, include_misc: bool):
        self.warnings.clear()
        self.stats = {"copied": 0, "skipped": 0, "converted": 0}
        self.terrain_textures.clear()
        self.item_textures.clear()

        build = Path(tempfile.mkdtemp(prefix="mcpack_build_"))
        bedrock_root = build
        textures_root = bedrock_root / "textures"
        (textures_root / "blocks").mkdir(parents=True, exist_ok=True)
        (textures_root / "items").mkdir(parents=True, exist_ok=True)

        log(self.ui, f"Extracting and reading: {zip_path}")
        with zipfile.ZipFile(zip_path, "r") as z:
            names = z.namelist()

            # pack.png or pack_icon.png for Bedrock
            icon_written = False
            for candidate in ("pack.png", "pack_icon.png", "pack.jpg", "pack.jpeg"):
                if candidate in names:
                    data = z.read(candidate)
                    try:
                        png = ensure_png_bytes(data, Path(candidate).suffix)
                        (bedrock_root / "pack_icon.png").write_bytes(png)
                        icon_written = True
                        log(self.ui, f"Pack icon converted/copied from {candidate}")
                    except Exception as e:
                        self.warnings.append(f"Failed to convert icon {candidate}: {e}")
                    break
            if not icon_written:
                self.warnings.append("No 'pack.png'/'pack_icon.png' found. Can be added later.")

            base_prefix = str(JAVA_TEXTURE_ROOT).replace("\\", "/") + "/"
            tex_entries = [n for n in names if n.startswith(base_prefix)]
            if not tex_entries:
                raise RuntimeError("The .zip does not contain 'assets/minecraft/textures'. Is this a Java pack?")

            total = len(tex_entries)
            processed = 0

            for entry in tex_entries:
                processed += 1
                self.ui.progress["value"] = processed * 100 / max(1, total)
                if entry.endswith("/"):
                    continue

                rel = entry[len(base_prefix):]
                parts = rel.split("/")
                if not parts:
                    continue
                if parts[0] in IGNORED_DIRS:
                    self.stats["skipped"] += 1
                    continue

                p = Path(rel)
                suffix = p.suffix.lower()
                if suffix == ".mcmeta":
                    base_png = str(p.with_suffix(".png"))
                    if base_prefix + base_png in names:
                        self.warnings.append(f"Animation detected ({p.name}). Exporting base image; Bedrock requires manual flipbook_textures.json.")
                    else:
                        self.warnings.append(f".mcmeta file without base image: {p}")
                    continue

                if suffix not in VALID_IMAGE_EXTS:
                    self.stats["skipped"] += 1
                    continue

                top = parts[0]
                bd_dir = DEFAULT_DIR_MAP.get(top)
                if bd_dir is None:
                    if include_misc:
                        bd_dir = top
                    else:
                        self.stats["skipped"] += 1
                        continue

                stem = p.stem
                if normalize_names:
                    stem = slugify(stem)

                target_subdir = f"{bd_dir}"
                target_name = f"{stem}.png"
                target_rel = Path("textures") / target_subdir / target_name
                target_abs = bedrock_root / target_rel

                try:
                    raw = z.read(entry)
                    png = ensure_png_bytes(raw, suffix)
                    target_abs.parent.mkdir(parents=True, exist_ok=True)
                    target_abs.write_bytes(png)
                    if bd_dir == "blocks":
                        self.terrain_textures[stem] = f"textures/{bd_dir}/{stem}"
                    elif bd_dir == "items":
                        self.item_textures[stem] = f"textures/{bd_dir}/{stem}"
                    self.stats["converted" if suffix != ".png" else "copied"] += 1
                except Exception as e:
                    self.stats["skipped"] += 1
                    self.warnings.append(f"Failed to process {entry}: {e}")

        terrain_json = {
            "resource_pack_name": pack_name,
            "texture_name": "atlas.terrain",
            "padding": 8,
            "num_mip_levels": 4,
            "textures": self.terrain_textures,
        }
        item_json = {
            "resource_pack_name": pack_name,
            "texture_name": "atlas.items",
            "padding": 8,
            "num_mip_levels": 4,
            "texture_data": {k: {"textures": v} for k, v in self.item_textures.items()},
        }

        write_json(bedrock_root / "textures" / "terrain_texture.json", terrain_json)
        write_json(bedrock_root / "textures" / "item_texture.json", item_json)

        header_uuid = make_uuid()
        module_uuid = make_uuid()
        manifest = {
            "format_version": 2,
            "header": {
                "name": pack_name,
                "description": pack_desc,
                "uuid": header_uuid,
                "version": version,
                "min_engine_version": MIN_ENGINE_VERSION,
            },
            "modules": [
                {"type": "resources", "uuid": module_uuid, "version": version}
            ],
        }
        write_json(bedrock_root / "manifest.json", manifest)

        out_file = out_dir / f"{slugify(pack_name)}.mcpack"
        zip_dir_to_file(bedrock_root, out_file)
        shutil.rmtree(build)
        log(self.ui, f"Conversion complete: {out_file}")
        if self.warnings:
            log(self.ui, "Warnings:")
            for w in self.warnings:
                log(self.ui, f"- {w}")

# UI -------------------------------------------------------------------------

class PackConverterUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("720x500")
        self.root.resizable(False, False)

        self.log_box = None
        self.progress = None

        self.zip_path_var = tk.StringVar()
        self.out_dir_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.desc_var = tk.StringVar()
        self.version_var = tk.StringVar(value="1.0.0")
        self.normalize_var = tk.BooleanVar(value=True)
        self.misc_var = tk.BooleanVar(value=False)

        self.converter = Converter(self)
        self.create_widgets()

    def create_widgets(self):
        cont = ttk.Frame(self.root, padding=10)
        cont.pack(fill="both", expand=True)

        ttk.Label(cont, text=APP_NAME, font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(cont, text="Convert Java resource packs (.zip) to Bedrock (.mcpack)").pack(anchor="w")

        # Options frame
        opts = ttk.LabelFrame(cont, text="Conversion Options", padding=10)
        opts.pack(fill="x", pady=10)

        ttk.Label(opts, text="Java Pack (.zip)").grid(row=0, column=0, sticky="w")
        ttk.Entry(opts, textvariable=self.zip_path_var, width=50).grid(row=0, column=1, sticky="w")
        ttk.Button(opts, text="Browse…", command=self.browse_zip).grid(row=0, column=2, sticky="w")

        ttk.Label(opts, text="Output Folder").grid(row=1, column=0, sticky="w")
        ttk.Entry(opts, textvariable=self.out_dir_var, width=50).grid(row=1, column=1, sticky="w")
        ttk.Button(opts, text="Browse…", command=self.browse_out).grid(row=1, column=2, sticky="w")

        ttk.Label(opts, text="Pack Name").grid(row=2, column=0, sticky="w")
        ttk.Entry(opts, textvariable=self.name_var, width=50).grid(row=2, column=1, columnspan=2, sticky="w")

        ttk.Label(opts, text="Description").grid(row=3, column=0, sticky="w")
        ttk.Entry(opts, textvariable=self.desc_var, width=50).grid(row=3, column=1, columnspan=2, sticky="w")

        ttk.Label(opts, text="Version (x.y.z)").grid(row=4, column=0, sticky="w")
        ttk.Entry(opts, textvariable=self.version_var, width=20).grid(row=4, column=1, sticky="w")

        ttk.Checkbutton(opts, text="Normalize names (lowercase, underscores)", variable=self.normalize_var).grid(row=5, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(opts, text="Include miscellaneous folders (entity, ui, etc.)", variable=self.misc_var).grid(row=6, column=0, columnspan=3, sticky="w")

        ttk.Button(opts, text="Convert to .mcpack", command=self.start_conversion).grid(row=7, column=0, columnspan=3, pady=10)

        # Log frame
        log_frame = ttk.LabelFrame(cont, text="Log & Progress", padding=5)
        log_frame.pack(fill="both", expand=True)
        self.log_box = tk.Text(log_frame, height=15, state="disabled", wrap="word")
        self.log_box.pack(fill="both", expand=True)
        self.progress = ttk.Progressbar(cont, orient="horizontal", length=700, mode="determinate")
        self.progress.pack(pady=5)

    def browse_zip(self):
        path = filedialog.askopenfilename(filetypes=[("ZIP files", "*.zip")])
        if path:
            self.zip_path_var.set(path)

    def browse_out(self):
        path = filedialog.askdirectory()
        if path:
            self.out_dir_var.set(path)

    def start_conversion(self):
        try:
            zip_path = Path(self.zip_path_var.get())
            out_dir = Path(self.out_dir_var.get())
            pack_name = self.name_var.get() or zip_path.stem
            pack_desc = self.desc_var.get() or "Converted Java pack"
            version_str = self.version_var.get()
            normalize = self.normalize_var.get()
            misc = self.misc_var.get()

            version = [int(x) for x in version_str.strip().split(".")]
            if len(version) != 3:
                messagebox.showerror("Error", "Version must be in x.y.z format, e.g., 1.0.0")
                return
            self.converter.convert(zip_path, out_dir, pack_name, pack_desc, version, normalize, misc)
            messagebox.showinfo("Done", "Conversion finished successfully!")
        except Exception as e:
            messagebox.showerror("Error", str(e))
            log(self, f"Error: {e}")

    def run(self):
        self.root.mainloop()

# Run UI ---------------------------------------------------------------------

if __name__ == "__main__":
    PackConverterUI().run()
