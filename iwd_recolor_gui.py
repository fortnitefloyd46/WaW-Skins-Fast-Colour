"""
IWD team-color tool: extract localized_english_iw07.iwd, recolor faction IWI
textures (allied vs axis), remove normal-map IWIs (*_n*) from the pack, repack the IWD.

Place next to this script:
  - localized_english_iw07.iwd
  - dds2iwi-cod4.exe          (same as your existing converter)
  - iwi2dds_cod4.exe          (IWI -> DDS; companion to dds2iwi — get from COD modding wiki)

Requires: Python 3.10+, Pillow, numpy, pygame-ce (MP3 in __pycache__), and Microsoft texconv on PATH or default WinGet path.

Optional MP3 fallback if pygame-ce is unavailable: install FFmpeg and add ``ffplay`` to PATH (volume still works).

Windows setup: run ``install_requirements.bat`` — it creates a ``.venv`` in this folder, installs
Python via winget if none is found, then pip-installs Pillow, numpy, pygame-ce, etc. into that venv.
Always start the app with ``run_iwd_recolor_gui.bat`` so it uses ``.venv`` (avoids missing numpy / wrong Python).
"""

from __future__ import annotations

import colorsys
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageTk

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except ImportError:
    print("tkinter is required", file=sys.stderr)
    sys.exit(1)

IWD_NAME = "localized_english_iw07.iwd"

DDS2IWI_NAMES = ("dds2iwi-cod4.exe", "dds2iwi_cod4.exe")
IWI2DDS_NAMES = ("iwi2dds_cod4.exe", "iwi2dds-cod4.exe", "IWI2DDS_COD4.exe")

_ffplay_proc: subprocess.Popen | None = None


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def mp3_assets_dir() -> Path:
    return app_dir() / "__pycache__"


def resolve_mp3(stem: str) -> Path | None:
    """Resolve ``stem`` or ``stem.mp3`` under ``__pycache__`` next to the script."""
    d = mp3_assets_dir()
    for cand in (d / f"{stem}.mp3", d / stem):
        if cand.is_file():
            return cand
    return None


def _stop_ffplay_process() -> None:
    global _ffplay_proc
    if _ffplay_proc is None:
        return
    try:
        if _ffplay_proc.poll() is None:
            _ffplay_proc.terminate()
            try:
                _ffplay_proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                _ffplay_proc.kill()
    except Exception:
        pass
    finally:
        _ffplay_proc = None


def _try_play_pygame(path: Path, volume: float) -> bool:
    try:
        import pygame
    except ImportError:
        return False
    try:
        _stop_ffplay_process()
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        pygame.mixer.music.stop()
        pygame.mixer.music.set_volume(max(0.0, min(1.0, volume)))
        pygame.mixer.music.load(str(path.resolve()))
        pygame.mixer.music.play()
        return True
    except Exception:
        return False


def _try_play_ffplay(path: Path, volume: float) -> bool:
    ffplay = shutil.which("ffplay")
    if not ffplay:
        return False
    v = max(0.0, min(1.0, volume))
    try:
        flags = 0
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        global _ffplay_proc
        _stop_ffplay_process()
        try:
            import pygame

            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except Exception:
            pass
        _ffplay_proc = subprocess.Popen(
            [
                ffplay,
                "-nodisp",
                "-autoexit",
                "-loglevel",
                "quiet",
                "-af",
                f"volume={v}",
                str(path.resolve()),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
        )
        return True
    except Exception:
        _ffplay_proc = None
        return False


def _try_play_playsound(path: Path) -> None:
    try:
        from playsound import playsound
    except ImportError:
        return

    def _run() -> None:
        try:
            playsound(str(path.resolve()), block=True)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def play_mp3_music(path: Path | None, volume: float) -> None:
    if path is None or not path.is_file():
        return
    v = max(0.0, min(1.0, volume))
    if _try_play_pygame(path, v):
        return
    if _try_play_ffplay(path, v):
        return
    # playsound has no volume control; startup track plays at system volume
    _try_play_playsound(path)


def stop_mp3_music() -> None:
    try:
        import pygame

        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
    except Exception:
        pass
    _stop_ffplay_process()


def find_texconv() -> Path | None:
    w = shutil.which("texconv")
    if w:
        return Path(w).resolve()
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    for cand in (
        app_dir() / "texconv.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "texconv.exe",
        Path(r"C:\Program Files\Microsoft DirectX Texture Converter\texconv.exe"),
        Path(pf86) / "Microsoft DirectX Texture Converter" / "texconv.exe",
    ):
        if cand.is_file():
            return cand.resolve()
    pk = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if pk.is_dir():
        try:
            for folder in sorted(pk.glob("Microsoft.DirectXTex.Texconv_*"), reverse=True):
                for exe in folder.rglob("texconv.exe"):
                    return exe.resolve()
        except OSError:
            pass
    return None


def find_converter(script_dir: Path, names: tuple[str, ...]) -> Path | None:
    for n in names:
        p = script_dir / n
        if p.is_file():
            return p.resolve()
    return None


def team_for_name(filename: str) -> str | None:
    base = Path(filename).name.lower()
    if re.match(r"^~viewmodel_ger_", base):
        return "axis"
    if re.match(r"^~?char_ger_", base):
        return "axis"
    if re.match(r"^~?char_jap_", base):
        return "axis"
    if re.match(r"^~?char_rus_", base):
        return "allied"
    if re.match(r"^~?char_usa_", base):
        return "allied"
    if re.match(r"^~?char_marinewet", base):
        return "allied"
    if re.match(r"^~?char_marine", base):
        return "allied"
    if re.match(r"^~?char_navy_", base):
        return "allied"
    if re.match(r"^~?char_raider", base):
        return "allied"
    if re.match(r"^usmc_", base):
        return "allied"
    return None


def is_normal_map_iwi(filename: str) -> bool:
    """Tangent-space normal maps (*_n.iwi, *_n_*); these are dropped from the repacked IWD."""
    stem = Path(filename).stem.lower()
    if stem.endswith("_n"):
        return True
    return "_n_" in stem


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"#{r:02X}{g:02X}{b:02X}"


def parse_hex_rgb(s: str) -> tuple[int, int, int] | None:
    """Parse ``#RRGGBB`` or ``RRGGBB`` into ``(R, G, B)``. Returns None if invalid."""
    t = (s or "").strip()
    if t.startswith("#"):
        t = t[1:]
    if len(t) != 6:
        return None
    try:
        n = int(t, 16)
    except ValueError:
        return None
    r = (n >> 16) & 0xFF
    g = (n >> 8) & 0xFF
    b = n & 0xFF
    return r, g, b


def hsv_to_hex_string(h: float, s: float, v: float) -> str:
    r, g, b = colorsys.hsv_to_rgb(h / 360.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return rgb_to_hex((int(r * 255), int(g * 255), int(b * 255)))


class ColorPicker(ttk.Frame):
    """Embedded HSV + hex field similar to a compact web color picker."""

    SV_W = 220
    SV_H = 150
    HUE_W = 220
    HUE_H = 14

    def __init__(
        self,
        parent: tk.Misc,
        hex_var: tk.StringVar,
        *,
        default_rgb: tuple[int, int, int],
    ) -> None:
        super().__init__(parent)
        self.hex_var = hex_var
        self._suspend_trace = False
        t = parse_hex_rgb(hex_var.get())
        if t is None:
            t = default_rgb
            hex_var.set(rgb_to_hex(t))
        r, g, b = (x / 255.0 for x in t)
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        self._h = h * 360.0
        self._s = s
        self._v = v

        self._sv_photo: ImageTk.PhotoImage | None = None
        self._hue_photo: ImageTk.PhotoImage | None = None

        card = tk.Frame(self, bg="#ececec", highlightthickness=1, highlightbackground="#d0d0d0")
        card.pack(fill=tk.X, pady=2)
        inner = tk.Frame(card, bg="white", padx=10, pady=10)
        inner.pack(fill=tk.X)

        self.sv_canvas = tk.Canvas(
            inner,
            width=self.SV_W,
            height=self.SV_H,
            highlightthickness=0,
            borderwidth=0,
            bg="white",
        )
        self.sv_canvas.pack(anchor=tk.NW)
        self.sv_canvas.bind("<ButtonPress-1>", self._on_sv)
        self.sv_canvas.bind("<B1-Motion>", self._on_sv)

        self.hue_canvas = tk.Canvas(
            inner,
            width=self.HUE_W,
            height=self.HUE_H,
            highlightthickness=0,
            borderwidth=0,
            bg="white",
        )
        self.hue_canvas.pack(anchor=tk.NW, pady=(8, 0))
        self.hue_canvas.bind("<ButtonPress-1>", self._on_hue)
        self.hue_canvas.bind("<B1-Motion>", self._on_hue)

        row = ttk.Frame(inner)
        row.pack(fill=tk.X, pady=(10, 0))

        self.preview = tk.Canvas(
            row,
            width=30,
            height=30,
            highlightthickness=1,
            highlightbackground="#c8c8c8",
        )
        self.preview.pack(side=tk.LEFT)
        self.hex_entry = ttk.Entry(row, textvariable=hex_var, width=11, font=("Consolas", 10))
        self.hex_entry.pack(side=tk.LEFT, padx=(8, 4))
        self.hex_entry.bind("<Return>", self._on_hex_commit)
        self.hex_entry.bind("<FocusOut>", self._on_hex_commit)
        ttk.Button(row, text="Copy", width=6, command=self._copy_hex).pack(side=tk.LEFT, padx=2)
        fmt = ttk.Combobox(row, values=("Hex",), width=7, state="readonly")
        fmt.set("Hex")
        fmt.pack(side=tk.RIGHT)

        self._build_hue_strip()
        self._redraw_sv()
        self._sync_preview()

        hex_var.trace_add("write", self._on_hex_var_trace)

    def _build_hue_strip(self) -> None:
        w, h = self.HUE_W, self.HUE_H
        arr = np.zeros((h, w, 3), dtype=np.uint8)
        for x in range(w):
            hh = (x / max(w - 1, 1)) * 360.0
            rd, gd, bd = colorsys.hsv_to_rgb(hh / 360.0, 1.0, 1.0)
            arr[:, x] = (int(rd * 255), int(gd * 255), int(bd * 255))
        self._hue_photo = ImageTk.PhotoImage(Image.fromarray(arr, "RGB"))
        self.hue_canvas.create_image(0, 0, anchor=tk.NW, image=self._hue_photo)
        self._hue_indicator = self.hue_canvas.create_line(
            0, -2, 0, h + 2, width=2, fill="white", capstyle=tk.ROUND,
        )
        self.hue_canvas.tag_raise(self._hue_indicator)

    def _redraw_sv(self) -> None:
        w, h = self.SV_W, self.SV_H
        hh = self._h
        arr = np.zeros((h, w, 3), dtype=np.uint8)
        for yy in range(h):
            vv = 1.0 - yy / max(h - 1, 1)
            for xx in range(w):
                ss = xx / max(w - 1, 1)
                rd, gd, bd = colorsys.hsv_to_rgb(hh / 360.0, ss, vv)
                arr[yy, xx] = (int(rd * 255), int(gd * 255), int(bd * 255))
        self._sv_photo = ImageTk.PhotoImage(Image.fromarray(arr, "RGB"))
        self.sv_canvas.delete("all")
        self.sv_canvas.create_image(0, 0, anchor=tk.NW, image=self._sv_photo)
        rad = 6
        self._sv_marker = self.sv_canvas.create_oval(
            -1, -1, -1, -1, outline="white", width=2, fill="",
        )
        self.sv_canvas.tag_raise(self._sv_marker)
        self._update_markers()

    def _update_markers(self) -> None:
        w, h = self.SV_W, self.SV_H
        x = self._s * (w - 1)
        y = (1.0 - self._v) * (h - 1)
        rad = 6
        self.sv_canvas.coords(self._sv_marker, x - rad, y - rad, x + rad, y + rad)
        hx = (self._h / 360.0) * (self.HUE_W - 1)
        self.hue_canvas.coords(self._hue_indicator, hx, -2, hx, self.HUE_H + 2)

    def _sync_preview(self) -> None:
        t = parse_hex_rgb(self.hex_var.get())
        if t is None:
            return
        hx = rgb_to_hex(t)
        self.preview.delete("all")
        self.preview.create_rectangle(0, 0, 32, 32, fill=hx, outline="#b0b0b0", width=1)

    def _push_hex_from_hsv(self) -> None:
        new_hex = hsv_to_hex_string(self._h, self._s, self._v)
        self._suspend_trace = True
        self.hex_var.set(new_hex)
        self._suspend_trace = False
        self._sync_preview()

    def _on_sv(self, event: tk.Event) -> None:
        w, h = self.SV_W, self.SV_H
        x = max(0, min(w - 1, event.x))
        y = max(0, min(h - 1, event.y))
        self._s = x / max(w - 1, 1)
        self._v = 1.0 - y / max(h - 1, 1)
        self._push_hex_from_hsv()
        self._update_markers()

    def _on_hue(self, event: tk.Event) -> None:
        x = max(0, min(self.HUE_W - 1, event.x))
        self._h = (x / max(self.HUE_W - 1, 1)) * 360.0
        self._redraw_sv()
        self._push_hex_from_hsv()

    def _on_hex_commit(self, event: object | None = None) -> None:
        t = parse_hex_rgb(self.hex_var.get())
        if t is None:
            return
        r, g, b = (x / 255.0 for x in t)
        hh, ss, vv = colorsys.rgb_to_hsv(r, g, b)
        self._h = hh * 360.0
        self._s = ss
        self._v = vv
        self._suspend_trace = True
        self.hex_var.set(rgb_to_hex(t))
        self._suspend_trace = False
        self._redraw_sv()
        self._sync_preview()

    def _on_hex_var_trace(self, *_a: object) -> None:
        if self._suspend_trace:
            return
        raw = self.hex_var.get().strip()
        if raw.startswith("#"):
            raw = raw[1:]
        if len(raw) != 6:
            return
        t = parse_hex_rgb(self.hex_var.get())
        if t is None:
            return
        r, g, b = (x / 255.0 for x in t)
        hh, ss, vv = colorsys.rgb_to_hsv(r, g, b)
        self._h = hh * 360.0
        self._s = ss
        self._v = vv
        self._redraw_sv()
        self._sync_preview()

    def _copy_hex(self) -> None:
        top = self.winfo_toplevel()
        try:
            top.clipboard_clear()
            top.clipboard_append(self.hex_var.get())
        except tk.TclError:
            pass


def _iwi2dds_output_dds(work: Path, local_iwi: Path, stdout: str, stderr: str) -> Path | None:
    """Find DDS produced by iwi2dds_cod4 (often ``name.iwi_out.dds``); may parse tool output."""
    text = f"{stdout or ''}\n{stderr or ''}"
    for m in re.finditer(r' to "([^"]+\.dds)"', text, flags=re.IGNORECASE):
        p = Path(m.group(1))
        if p.is_file():
            return p.resolve()
        q = work / p.name
        if q.is_file():
            return q.resolve()
    for cand in (
        work / f"{local_iwi.name}_out.dds",
        work / (local_iwi.stem + ".dds"),
    ):
        if cand.is_file():
            return cand.resolve()
    return None


def run_cmd(
    args: list[str | Path],
    *,
    cwd: Path | None = None,
) -> tuple[int, str, str]:
    r = subprocess.run(
        [str(x) for x in args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )
    return r.returncode, r.stdout or "", r.stderr or ""


def extract_iwd(iwd_path: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(iwd_path, "r") as z:
        z.extractall(out_dir)


def repack_iwd(source_dir: Path, dest_iwd: Path) -> None:
    dest_iwd.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest_iwd, "w", zipfile.ZIP_DEFLATED) as z:
        for fp in sorted(source_dir.rglob("*")):
            if fp.is_file():
                arc = fp.relative_to(source_dir).as_posix()
                z.write(fp, arc)


def process_one_iwi(
    iwi_src: Path,
    *,
    texconv: Path,
    iwi2dds: Path,
    dds2iwi: Path,
    rgb_axis: tuple[int, int, int],
    rgb_allied: tuple[int, int, int],
) -> tuple[bool, str]:
    team = team_for_name(iwi_src.name)
    if team is None:
        return True, f"skip (no faction) {iwi_src.name}"

    rgb = rgb_axis if team == "axis" else rgb_allied
    arr = np.array(rgb, dtype=np.uint8)

    with tempfile.TemporaryDirectory(prefix="iwdrecolor_") as td:
        work = Path(td)
        local_iwi = work / iwi_src.name
        shutil.copy2(iwi_src, local_iwi)

        code, out, err = run_cmd([iwi2dds, local_iwi], cwd=work)
        raw_dds = _iwi2dds_output_dds(work, local_iwi, out, err)
        if raw_dds is None:
            return False, (
                f"iwi2dds produced no DDS for {iwi_src.name} (exit {code}): {out}\n{err}"
            )

        dds = work / (local_iwi.stem + ".dds")
        try:
            if raw_dds.resolve() != dds.resolve():
                if dds.is_file():
                    dds.unlink()
                shutil.move(str(raw_dds), str(dds))
        except OSError as e:
            return False, f"could not place DDS for {iwi_src.name}: {e}"
        if not dds.is_file():
            return False, f"no DDS after iwi2dds: {iwi_src.name}"

        code, o2, e2 = run_cmd([texconv, "-ft", "png", "-o", str(work), str(dds), "-y"], cwd=work)
        if code != 0:
            return False, f"texconv->png {iwi_src.name}: {o2}\n{e2}"

        png = work / (dds.stem + ".png")
        if not png.is_file():
            return False, f"no PNG: {iwi_src.name}"

        im = np.array(Image.open(png))
        if im.ndim != 3 or im.shape[2] not in (3, 4):
            return False, f"bad image shape {iwi_src.name}: {im.shape}"
        if im.shape[2] == 3:
            a = np.full((im.shape[0], im.shape[1], 1), 255, dtype=np.uint8)
            im = np.concatenate([im, a], axis=2)
        im[:, :, 0] = arr[0]
        im[:, :, 1] = arr[1]
        im[:, :, 2] = arr[2]
        Image.fromarray(im).save(png)

        code, o3, e3 = run_cmd([texconv, "-f", "BC3_UNORM", "-o", str(work), str(png), "-y"], cwd=work)
        if code != 0:
            return False, f"texconv->dds {iwi_src.name}: {o3}\n{e3}"

        if not dds.is_file():
            return False, f"DDS missing after encode: {iwi_src.name}"

        code, o4, e4 = run_cmd([dds2iwi, dds], cwd=work)
        if code != 0:
            return False, f"dds2iwi {iwi_src.name}: {o4}\n{e4}"

        out_iwi = work / (dds.stem + ".iwi")
        if not out_iwi.is_file():
            return False, f"no output IWI: {iwi_src.name}"

        shutil.copy2(out_iwi, iwi_src)
        return True, f"{team} {iwi_src.name} -> {rgb_to_hex((int(arr[0]), int(arr[1]), int(arr[2])))}"


def main() -> None:
    """Tkinter UI."""
    root = tk.Tk()
    root.title("good goy garys skin recolour")
    root.geometry("700x780")

    font = ("Segoe UI", 10)
    frm = ttk.Frame(root, padding=10)
    frm.pack(fill=tk.BOTH, expand=True)

    ttk.Label(frm, text=f"Looks for {IWD_NAME} in:", font=font).pack(anchor=tk.W)
    dir_var = tk.StringVar(value=str(app_dir()))
    ttk.Entry(frm, textvariable=dir_var, width=80).pack(fill=tk.X, pady=(0, 6))

    axis_frm = ttk.LabelFrame(frm, text="Axis (German / Japanese)", padding=6)
    axis_frm.pack(fill=tk.X, pady=4)
    allied_frm = ttk.LabelFrame(frm, text="Allies (Soviet / US Marine / Navy / Raiders)", padding=6)
    allied_frm.pack(fill=tk.X, pady=4)

    axis_hex = tk.StringVar(value=rgb_to_hex((255, 24, 240)))
    ColorPicker(axis_frm, axis_hex, default_rgb=(255, 24, 240)).pack(fill=tk.X)
    allied_hex = tk.StringVar(value=rgb_to_hex((0, 255, 0)))
    ColorPicker(allied_frm, allied_hex, default_rgb=(0, 255, 0)).pack(fill=tk.X)

    log_q: queue.Queue[str] = queue.Queue()
    log_w = tk.Text(frm, height=5, wrap=tk.WORD, font=("Consolas", 8))

    def pull_log() -> None:
        try:
            while True:
                log_w.insert(tk.END, log_q.get_nowait())
                log_w.see(tk.END)
        except queue.Empty:
            pass
        root.after(200, pull_log)

    pull_log()

    prog = ttk.Progressbar(frm, mode="determinate")
    prog.pack(fill=tk.X, pady=(6, 4))

    def log(msg: str) -> None:
        log_q.put(msg + "\n")

    btn_color = ttk.Button(frm, text="COLOR")
    btn_color.pack(pady=4)

    log_w.pack(fill=tk.X, expand=False, pady=(0, 6))

    def run_job() -> None:
        base = Path(dir_var.get().strip())
        iwd = base / IWD_NAME
        iwi2dds = find_converter(base, IWI2DDS_NAMES)
        dds2iwi = find_converter(base, DDS2IWI_NAMES)
        texc = find_texconv()

        if not iwd.is_file():
            messagebox.showerror("Missing IWD", f"Not found:\n{iwd}")
            return
        if not iwi2dds:
            messagebox.showerror(
                "Missing iwi2dds",
                f"Place one of {IWI2DDS_NAMES} in:\n{base}",
            )
            return
        if not dds2iwi:
            messagebox.showerror(
                "Missing dds2iwi",
                f"Place one of {DDS2IWI_NAMES} in:\n{base}",
            )
            return
        if not texc:
            messagebox.showerror(
                "Missing texconv",
                "DirectXTex texconv is required for DDS/PNG conversion.\n\n"
                "Run **install_requirements.bat** again (it installs texconv via winget),\n"
                "or run in a terminal: winget install Microsoft.DirectXTex.Texconv\n"
                "Or place **texconv.exe** in this program folder.",
            )
            return

        ha = axis_hex.get().strip()
        hl = allied_hex.get().strip()
        parsed_axis = parse_hex_rgb(ha)
        parsed_allied = parse_hex_rgb(hl)
        if parsed_axis is None:
            messagebox.showerror(
                "Invalid hex",
                "Axis color must be #RRGGBB or RRGGBB (6 hex digits).\n"
                f"Got: {ha!r}",
            )
            return
        if parsed_allied is None:
            messagebox.showerror(
                "Invalid hex",
                "Allied color must be #RRGGBB or RRGGBB (6 hex digits).\n"
                f"Got: {hl!r}",
            )
            return
        rgb_axis = parsed_axis
        rgb_allied = parsed_allied

        backup = iwd.with_name(
            iwd.stem + datetime.now().strftime("_%Y%m%d_%H%M%S") + iwd.suffix + ".bak"
        )
        try:
            shutil.copy2(iwd, backup)
            log(f"Backup: {backup.name}")
        except OSError as e:
            log(f"Warning: could not backup IWD: {e}")

        btn_color.configure(state=tk.DISABLED)

        def work() -> None:
            try:
                with tempfile.TemporaryDirectory(prefix="iwd_unpack_") as unpack:
                    uroot = Path(unpack)
                    log(f"Extracting {iwd.name} …")
                    extract_iwd(iwd, uroot)
                    iwis = [p for p in uroot.rglob("*.iwi") if p.is_file()]
                    total = len(iwis)
                    log(f"Found {total} IWI file(s).")
                    done = 0
                    failed = 0
                    for p in sorted(iwis):
                        if is_normal_map_iwi(p.name):
                            arc = p.relative_to(uroot).as_posix()
                            try:
                                p.unlink()
                                log(f"removed (normal map) {arc}")
                            except OSError as e:
                                log(f"could not remove normal map {arc}: {e}")
                                failed += 1
                            done += 1
                            d, t = done, total
                            root.after(
                                0, lambda d=d, t=t: prog.configure(maximum=t, value=d)
                            )
                            continue

                        ok, msg = process_one_iwi(
                            p,
                            texconv=texc,
                            iwi2dds=iwi2dds,
                            dds2iwi=dds2iwi,
                            rgb_axis=rgb_axis,
                            rgb_allied=rgb_allied,
                        )
                        log(msg)
                        if not ok:
                            failed += 1
                        done += 1
                        d, t = done, total
                        root.after(0, lambda d=d, t=t: prog.configure(maximum=t, value=d))

                    if failed:
                        log(f"Finished with {failed} error(s).")
                        root.after(
                            0,
                            lambda: messagebox.showwarning(
                                "Done with errors",
                                f"{failed} file(s) failed. See log.",
                            ),
                        )
                    else:
                        log("Repacking IWD …")
                        tmp_out = uroot.parent / "_out_iwd.zip"
                        if tmp_out.is_file():
                            tmp_out.unlink()
                        repack_iwd(uroot, tmp_out)
                        if iwd.is_file():
                            iwd.unlink()
                        shutil.move(str(tmp_out), str(iwd))
                        log(f"Wrote {IWD_NAME}.")

                        def show_done_success() -> None:
                            stop_mp3_music()
                            play_mp3_music(resolve_mp3("sheckles"), 0.05)
                            messagebox.showinfo(
                                "Done",
                                "lol thanks for letting me install a keylogger :p",
                            )

                        root.after(0, show_done_success)
            except Exception as e:
                log(f"FATAL: {e!r}")
                root.after(0, lambda: messagebox.showerror("Error", str(e)))
            finally:
                root.after(0, lambda: btn_color.configure(state=tk.NORMAL))

        threading.Thread(target=work, daemon=True).start()

    btn_color.configure(command=run_job)

    ttk.Label(
        frm,
        text="Faction detection uses file names (char_ger_/char_jap_=axis; char_rus_/char_usa_/marine/navy/raider=usmc=allied). "
        "Normal maps (*_n.iwi / *_n_*) are deleted from this IWD before repack (game may use normals from other IWDs).",
        font=("Segoe UI", 8),
        foreground="#444",
        wraplength=620,
    ).pack(anchor=tk.W, pady=(4, 0))

    def play_startup_jewsong() -> None:
        play_mp3_music(resolve_mp3("jewsong"), 0.05)

    root.after(150, play_startup_jewsong)

    root.mainloop()


if __name__ == "__main__":
    main()
