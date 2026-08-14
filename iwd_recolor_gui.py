"""
IWD team-color tool: recolour faction IWI textures (allied vs axis) into a
late-loading ``zz_team_colours.iwd`` so stock duplicates (iw_01/iw_02) cannot
hide Red Army skins.

Optional checkboxes on COLOR:
  - ``no smoke`` → replace smoke particle IWIs with ``smoke_particle.png`` → ``zz_no_smoke.iwd``
    (keeps clouds drawing so the custom particles are visible)
  - ``no foliage`` → grass dvars off + blank foliage/grass → ``zz_no_foliage.iwd``
  - ``better scope`` → COD4 scope pixels in stock WaW IWI shells → ``zz_better_scope.iwd``
    (source: ``scope_source.zip`` / ``scope.iwd``)

Place next to this script:
  - iw_*.iwd / localized_english_*.iwd (game main folder)
  - dds2iwi-cod4.exe
  - iwi2dds_cod4.exe

Requires: Python 3.10+, Pillow, numpy, pygame-ce (MP3 in __pycache__), and Microsoft texconv.

Windows setup: run ``install_requirements.bat``, then ``run_iwd_recolor_gui.bat``.
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


def _reexec_in_local_venv_if_needed() -> None:
    """If started outside ``.venv``, re-launch with ``.venv\\Scripts\\python.exe``."""
    if getattr(sys, "frozen", False):
        return
    here = Path(__file__).resolve().parent
    venv_py = here / ".venv" / "Scripts" / "python.exe"
    if not venv_py.is_file():
        return
    try:
        if Path(sys.executable).resolve() == venv_py.resolve():
            return
    except OSError:
        return
    os.execv(str(venv_py), [str(venv_py), str(Path(__file__).resolve()), *sys.argv[1:]])


_reexec_in_local_venv_if_needed()

try:
    import numpy as np
    from PIL import Image, ImageTk
except ImportError:
    print(
        "Missing packages (numpy / Pillow).\n"
        "Run install_requirements.bat in this folder, then start with run_iwd_recolor_gui.bat.",
        file=sys.stderr,
    )
    sys.exit(1)

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except ImportError:
    print("tkinter is required", file=sys.stderr)
    sys.exit(1)

IWD_NAME = "localized_english_iw07.iwd"
TEAM_OVERRIDE_IWD = "zz_team_colours.iwd"
SMOKE_OVERRIDE_IWD = "zz_no_smoke.iwd"
# Localized late pack — some installs prioritize localized_* over zz_*.
SMOKE_OVERRIDE_IWD_LOCALIZED = "localized_english_zzzz_no_smoke.iwd"
SMOKE_OVERRIDE_LEGACY = "zz_smoke_opacity.iwd"
SMOKE_AUTOEXEC_MARKER_BEGIN = "// --- WaW-Skins-Fast-Colour no smoke begin ---"
SMOKE_AUTOEXEC_MARKER_END = "// --- WaW-Skins-Fast-Colour no smoke end ---"
SMOKE_AUTOEXEC_CFG = "autoexec.cfg"
# Custom particle image used when ``no smoke`` is checked (near-black → transparent).
SMOKE_PARTICLE_PNG = "smoke_particle.png"

FOLIAGE_OVERRIDE_IWD = "zz_no_foliage.iwd"
FOLIAGE_OVERRIDE_IWD_LOCALIZED = "localized_english_zzzz_no_foliage.iwd"
FOLIAGE_AUTOEXEC_MARKER_BEGIN = "// --- WaW-Skins-Fast-Colour no foliage begin ---"
FOLIAGE_AUTOEXEC_MARKER_END = "// --- WaW-Skins-Fast-Colour no foliage end ---"

# COD4 scope pack (ZIP). Keep as ``.zip`` so the game does not load it as an IWD.
SCOPE_SOURCE_IWD = "scope_source.zip"
SCOPE_SOURCE_FALLBACKS = ("scope.iwd", "scope.zip")
BETTER_SCOPE_OVERRIDE_IWD = "zz_better_scope.iwd"
BETTER_SCOPE_OVERRIDE_IWD_LOCALIZED = "localized_english_zzzz_better_scope.iwd"
WAW_SCOPE_OVERLAY_NAMES = (
    "scope_overlay.iwi",
    "scope_overlay_american.iwi",
    "scope_overlay_britain.iwi",
    "scope_overlay_german.iwi",
    "scope_overlay_japanese.iwi",
    "scope_overlay_mp.iwi",
    "scope_overlay_russian.iwi",
)
# Fraction unused; stars use fixed pixel size so they never become a big ring.
SMOKE_PARTICLE_SCALE = 0.04
# Each stamped star is this many pixels wide (fixed — not relative to texture).
SMOKE_STAR_PX = 10
# Rough stars per 256×256 texel area.
SMOKE_STARS_PER_256 = 48

FXT_SMK_PREFIX = "fxt_smk"
# Extra particle/sprite names often used alongside grenade smoke FX.
SMOKE_EXTRA_IMAGE_NAMES = (
    "smoke.iwi",
    "smoke_test_c.iwi",
    "fx_smoke_shadow_c.iwi",
    "fxt_debris_plume_smoke.iwi",
)
SMOKE_WEAPON_NAMES = (
    "m8_white_smoke_mp",
    "m8_white_smoke",
    "m8_white_smoke_light",
)

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
    if re.match(r"^~?viewmodel_ger_", base):
        return "axis"
    if re.match(r"^~?char_ger_", base):
        return "axis"
    if re.match(r"^~?viewmodel_jap_", base):
        return "axis"
    if re.match(r"^~?char_jap_", base):
        return "axis"
    if re.match(r"^~?viewmodel_rus_", base):
        return "allied"
    if re.match(r"^~?char_rus_", base):
        return "allied"
    if re.match(r"^~?viewmodel_usa_", base):
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
    """Tangent-space normal maps (*_n.iwi, *_n_*)."""
    stem = Path(filename).stem.lower()
    if stem.endswith("_n"):
        return True
    return "_n_" in stem


def is_fxt_smk_iwi(filename: str) -> bool:
    return Path(filename).name.lower().startswith(FXT_SMK_PREFIX)


def is_smoke_particle_iwi(filename: str) -> bool:
    """Textures that can contribute to visible smoke particles / shadows."""
    name = Path(filename).name.lower()
    if name.startswith(FXT_SMK_PREFIX):
        return True
    return name in SMOKE_EXTRA_IMAGE_NAMES


def is_foliage_iwi(filename: str) -> bool:
    """
    Map foliage / grass card textures to blank (not character ghillie/camo skins).
    """
    name = Path(filename).name.lower()
    if not name.endswith(".iwi"):
        return False
    # Never touch player / viewmodel skins that happen to say \"foliage\".
    if name.startswith("char_") or name.startswith("~char_"):
        return False
    if name.startswith("viewmodel_") or name.startswith("~viewmodel_"):
        return False
    if name.startswith("usmc_") or "bombplanted" in name:
        return False

    # Explicit grass/leaf FX + procedural grass.
    if name in {
        "procedural_grass.iwi",
        "grass_lowline_col.iwi",
        "kwai_grass_c.iwi",
        "fxt_env_leaves.iwi",
        "fxt_env_leaves_cherry.iwi",
        "fxt_env_leaves_elm.iwi",
        "fxt_env_leaves_gib.iwi",
        "fxt_debris_gib_grass.iwi",
    }:
        return True
    if name.startswith("pacific_grass") or name.startswith("~pacific_grass"):
        return True

    keys = (
        "foliage",
        "bush",
        "fern",
        "weed",
        "ivy",
        "shrub",
        "hedge",
        "sapling",
        "plant_",
        "_plant",
        "leaves",
        "leaf_",
        "_leaf",
        "grassclump",
        "grass_clump",
        "longgrass",
        "tallgrass",
    )
    if any(k in name for k in keys):
        return True
    # Ground-cover grass cards (avoid random \"terrain_grass_*\" dirt blends when possible:
    # still include obvious grass sprite names).
    if re.search(r"(^|_)grass($|_|\.)", name) and "terrain_" not in name:
        return True
    if "terrain_grass" in name or "mud_trampled_grass" in name:
        return True
    return False


def update_autoexec_block(
    base: Path,
    *,
    begin: str,
    end: str,
    body_lines: list[str],
) -> str:
    """Replace a marked block inside ``autoexec.cfg`` (create file if needed)."""
    cfg = base / SMOKE_AUTOEXEC_CFG
    existing = cfg.read_text(encoding="utf-8", errors="ignore") if cfg.is_file() else ""
    while begin in existing and end in existing:
        a = existing.find(begin)
        b = existing.find(end)
        if a < 0 or b < 0 or b < a:
            break
        b += len(end)
        if b < len(existing) and existing[b] == "\n":
            b += 1
        existing = existing[:a] + existing[b:]
    existing = existing.rstrip() + ("\n\n" if existing.strip() else "")
    block = begin + "\n" + "\n".join(body_lines) + "\n" + end + "\n"
    existing += block
    cfg.write_text(existing, encoding="utf-8")
    return str(cfg)


def apply_no_smoke_autoexec(base: Path, enabled: bool) -> str:
    """
    Legacy hide-clouds toggle. Custom particle replacement keeps clouds enabled
    (``fx_drawClouds 1``) so the remapped sprites can draw.
    """
    # ``enabled`` used to mean "hide smoke"; custom PNG mode always leaves clouds on.
    val = "1"
    return update_autoexec_block(
        base,
        begin=SMOKE_AUTOEXEC_MARKER_BEGIN,
        end=SMOKE_AUTOEXEC_MARKER_END,
        body_lines=[f'seta fx_drawClouds "{val}"'],
    )


def find_smoke_particle_png(base: Path) -> Path | None:
    """Locate the custom smoke particle PNG (bundled name or Pictures fallback)."""
    candidates = [
        base / SMOKE_PARTICLE_PNG,
        app_dir() / SMOKE_PARTICLE_PNG,
        Path.home() / "Pictures" / "Israeli_blue_Star_of_David.png",
        Path.home() / "OneDrive" / "Pictures" / "Israeli_blue_Star_of_David.png",
        app_dir() / "Israeli_blue_Star_of_David.png",
        base / "Israeli_blue_Star_of_David.png",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def load_smoke_particle_rgba(src: Path) -> Image.Image:
    """Load particle art; force near-black background to transparent."""
    with Image.open(src) as im:
        rgba = im.convert("RGBA")
    arr = np.array(rgba)
    # Treat near-black as transparent so only the star shows as a particle.
    lum = arr[:, :, :3].max(axis=2)
    arr[lum < 24, 3] = 0
    return Image.fromarray(arr, "RGBA")


def fit_particle_to_size(particle: Image.Image, w: int, h: int) -> Image.Image:
    """
    Fill the texture with many fixed-size tiny stars.

    A single large Star-of-David outline reads as a blue hexagonal/square ring in
    smoke billboards; a starfield of ~10px stamps looks like particle stars.
    """
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    star_px = max(6, min(SMOKE_STAR_PX, min(w, h) // 2))
    art = particle.copy()
    art.thumbnail((star_px, star_px), Image.Resampling.LANCZOS)
    if art.width < 2 or art.height < 2:
        return canvas

    area_scale = (w * h) / float(256 * 256)
    n = max(8, int(SMOKE_STARS_PER_256 * area_scale))
    n = min(n, 400)
    rng = np.random.default_rng(abs(hash((w, h))) % (2**32))
    max_x = max(1, w - art.width)
    max_y = max(1, h - art.height)
    for _ in range(n):
        x = int(rng.integers(0, max_x))
        y = int(rng.integers(0, max_y))
        canvas.alpha_composite(art, (x, y))
    return canvas


def apply_no_foliage_autoexec(base: Path, enabled: bool) -> str:
    """Toggle grass system; foliage cards are blanked via override IWD."""
    val = "0" if enabled else "1"
    return update_autoexec_block(
        base,
        begin=FOLIAGE_AUTOEXEC_MARKER_BEGIN,
        end=FOLIAGE_AUTOEXEC_MARKER_END,
        body_lines=[
            f'seta r_grassEnable "{val}"',
            f'seta r_gfxopt_dynamic_foliage "{val}"',
        ],
    )


def remove_smoke_override_iwds(base: Path) -> list[str]:
    removed: list[str] = []
    for name in (SMOKE_OVERRIDE_IWD, SMOKE_OVERRIDE_IWD_LOCALIZED, SMOKE_OVERRIDE_LEGACY):
        p = base / name
        if p.is_file():
            p.unlink()
            removed.append(name)
    return removed


def remove_foliage_override_iwds(base: Path) -> list[str]:
    removed: list[str] = []
    for name in (FOLIAGE_OVERRIDE_IWD, FOLIAGE_OVERRIDE_IWD_LOCALIZED):
        p = base / name
        if p.is_file():
            p.unlink()
            removed.append(name)
    return removed


def remove_better_scope_override_iwds(base: Path) -> list[str]:
    removed: list[str] = []
    for name in (BETTER_SCOPE_OVERRIDE_IWD, BETTER_SCOPE_OVERRIDE_IWD_LOCALIZED):
        p = base / name
        if p.is_file():
            p.unlink()
            removed.append(name)
    return removed


def find_better_scope_source(base: Path) -> Path | None:
    """Locate COD4 scope pack (prefer ``scope_source.zip``; accept legacy ``scope.iwd``)."""
    names = (SCOPE_SOURCE_IWD, *SCOPE_SOURCE_FALLBACKS)
    dirs = (
        base,
        app_dir(),
        Path.home() / "Desktop",
        Path.home() / "OneDrive" / "Desktop",
    )
    for d in dirs:
        for name in names:
            p = d / name
            if p.is_file():
                return p
    return None


def extract_first_iwi_from_iwd(iwd_path: Path, dest_iwi: Path) -> Path:
    """Extract the first ``.iwi`` entry from an IWD/ZIP into ``dest_iwi``."""
    with zipfile.ZipFile(iwd_path, "r") as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            if name.lower().endswith(".iwi"):
                dest_iwi.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(dest_iwi, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                return dest_iwi
    raise FileNotFoundError(f"No .iwi found in {iwd_path.name}")


def extract_stock_scope_templates(base: Path) -> dict[str, bytes]:
    """Read stock WaW ``scope_overlay*.iwi`` bytes (for valid IWI headers/flags)."""
    want = {n.lower(): n for n in WAW_SCOPE_OVERLAY_NAMES}
    found: dict[str, bytes] = {}
    for iwd in _iter_source_iwds(base):
        try:
            with zipfile.ZipFile(iwd, "r") as z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    name = Path(info.filename).name
                    key = name.lower()
                    if key not in want or key in found:
                        continue
                    found[key] = z.read(info)
                    if len(found) == len(want):
                        return {want[k]: v for k, v in found.items()}
        except zipfile.BadZipFile:
            continue
    return {want[k]: v for k, v in found.items()}


def inject_dds_into_stock_iwi(stock_iwi: bytes, dds_bytes: bytes) -> bytes:
    """
    Keep the stock WaW IWI header/flags (byte 0xC3 etc.) and replace DXT payload
    with pixels from a DDS. Stock IWDs use a 28-byte header + raw DXT5 for 1024 overlays.
    """
    if len(dds_bytes) < 128 or dds_bytes[:4] != b"DDS ":
        raise ValueError("not a DDS file")
    payload = dds_bytes[128:]
    hdr_len = len(stock_iwi) - len(payload)
    if hdr_len < 16 or hdr_len > 256:
        hdr_len = 28
    if len(stock_iwi) < hdr_len:
        raise ValueError("stock IWI too small")
    return stock_iwi[:hdr_len] + payload


def convert_cod4_scope_to_dds(
    src_iwi: Path,
    *,
    iwi2dds: Path,
) -> tuple[bool, bytes | None, str]:
    """Decode COD4 scope IWI → DDS bytes."""
    with tempfile.TemporaryDirectory(prefix="iwd_scope_") as td:
        work = Path(td)
        local = work / "scope_in.iwi"
        shutil.copy2(src_iwi, local)
        code, out, err = run_cmd([iwi2dds, local], cwd=work)
        raw_dds = _iwi2dds_output_dds(work, local, out, err)
        if raw_dds is None:
            return False, None, (
                f"iwi2dds failed for scope overlay (exit {code}): {out}\n{err}"
            )
        return True, raw_dds.read_bytes(), f"decoded scope DDS ({raw_dds.stat().st_size} bytes)"


def patch_scope_weapon_bytes(data: bytes) -> tuple[bytes, list[str]]:
    """
    Point sniper ADS overlays at stock ``empty_overlay`` so the heavy black
    vignette cannot draw. Image overrides alone are unreliable for HUD overlays
    (often still served from the original IWD / FastFile path).
    """
    text = data.decode("latin1")
    changes: list[str] = []

    def repl_field(src: str, key: str, new_val: str) -> str:
        pat = re.compile("(" + re.escape(key) + r"\\)([^\\]*)")
        m = pat.search(src)
        if not m:
            return src
        old = m.group(2)
        if old == new_val:
            return src
        changes.append(f"{key}: {old!r} -> {new_val!r}")
        return pat.sub(r"\g<1>" + new_val, src, count=1)

    text2 = text
    text2 = repl_field(text2, "adsOverlayShader", "empty_overlay")
    text2 = repl_field(text2, "adsOverlayShaderLowRes", "empty_overlay")
    return text2.encode("latin1"), changes


def collect_and_patch_scope_weapons(
    base: Path, dest_dir: Path
) -> list[tuple[Path, list[str]]]:
    """Copy scoped weapon files that use scope_overlay* and retarget ADS overlay."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    found: dict[str, tuple[Path, list[str]]] = {}
    for iwd in _iter_source_iwds(base):
        try:
            with zipfile.ZipFile(iwd, "r") as z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    low = info.filename.replace("\\", "/").lower()
                    if not low.startswith("weapons/"):
                        continue
                    raw = z.read(info)
                    # Fast reject: only files that reference a scope overlay shader.
                    if b"scope_overlay" not in raw and b"adsOverlayShader" not in raw:
                        continue
                    # Must actually be a scoped overlay user.
                    try:
                        text = raw.decode("latin1")
                    except Exception:
                        continue
                    if "scope_overlay" not in text and "adsOverlayShader" not in text:
                        continue
                    if "adsOverlayShader" not in text:
                        continue
                    patched, changes = patch_scope_weapon_bytes(raw)
                    if not changes:
                        continue
                    key = low
                    if key in found:
                        continue
                    out = dest_dir / Path(info.filename)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(patched)
                    found[key] = (out, changes)
        except zipfile.BadZipFile:
            continue
    return [found[k] for k in sorted(found)]


def encode_clear_scope_dds(
    *,
    texconv: Path,
    size: int = 1024,
    accent: Image.Image | None = None,
) -> bytes:
    """
    Build a WaW-friendly clear overlay DDS: fully transparent, optional tiny
    center accent from the COD4 pack (red cross etc.).
    """
    with tempfile.TemporaryDirectory(prefix="iwd_scope_png_") as td:
        work = Path(td)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        if accent is not None:
            a = accent.convert("RGBA")
            # Keep only bright non-black accent pixels (e.g. red cross).
            arr = np.array(a)
            lum = arr[:, :, :3].max(axis=2)
            arr[lum < 40, 3] = 0
            a = Image.fromarray(arr, "RGBA")
            if a.size != (size, size):
                a = a.resize((size, size), Image.Resampling.NEAREST)
            canvas.alpha_composite(a)
        png = work / "scope_overlay.png"
        canvas.save(png)
        dds = work / "scope_overlay.dds"
        code, o, e = run_cmd(
            [texconv, "-f", "BC3_UNORM", "-m", "1", "-o", str(work), str(png), "-y"],
            cwd=work,
        )
        if code != 0 or not dds.is_file():
            raise RuntimeError(f"texconv clear scope failed: {o}\n{e}")
        return dds.read_bytes()


def build_better_scope_override(
    base: Path,
    *,
    iwi2dds: Path,
    dds2iwi: Path,
    texconv: Path | None = None,
) -> tuple[bool, str]:
    """
    Build ``zz_better_scope.iwd`` (+ localized twin):

    1. Patch scoped weapons so ``adsOverlayShader`` → ``empty_overlay`` (removes
       the stock black ring reliably).
    2. Replace ``scope_overlay*.iwi`` with a clear WaW-header IWI (optional COD4
       accent), packed ZIP_STORED like stock.
    """
    del dds2iwi
    src_iwd = find_better_scope_source(base)
    templates = extract_stock_scope_templates(base)
    missing = [n for n in WAW_SCOPE_OVERLAY_NAMES if n not in templates]
    if missing:
        return False, "Missing stock scope templates: " + ", ".join(missing)

    with tempfile.TemporaryDirectory(prefix="iwd_better_scope_") as td:
        uroot = Path(td)

        weapons = collect_and_patch_scope_weapons(base, uroot)
        for wp, ch in weapons:
            # logged by caller if needed; keep message summary
            _ = (wp, ch)

        accent = None
        if src_iwd is not None and texconv is not None:
            raw = uroot / "cod4_scope.iwi"
            try:
                extract_first_iwi_from_iwd(src_iwd, raw)
                ok, dds_bytes, _msg = convert_cod4_scope_to_dds(raw, iwi2dds=iwi2dds)
                raw.unlink(missing_ok=True)
                if ok and dds_bytes is not None:
                    # DDS → PNG for accent extract
                    with tempfile.TemporaryDirectory(prefix="scope_acc_") as td2:
                        w2 = Path(td2)
                        dds_path = w2 / "c.dds"
                        dds_path.write_bytes(dds_bytes)
                        run_cmd(
                            [texconv, "-ft", "png", "-o", str(w2), str(dds_path), "-y"],
                            cwd=w2,
                        )
                        png = w2 / "c.png"
                        if png.is_file():
                            accent = Image.open(png).convert("RGBA").copy()
            except (FileNotFoundError, zipfile.BadZipFile, OSError):
                accent = None

        if texconv is None:
            return False, "texconv is required to build clear scope overlays"

        try:
            # Match stock overlay resolution from template size.
            stock_len = len(templates[WAW_SCOPE_OVERLAY_NAMES[0]])
            # 28-byte header + DXT5 1024² ⇒ 1048604; derive size from payload.
            payload = max(0, stock_len - 28)
            # DXT5 bytes = w*h; for square: side = sqrt(payload)
            side = int(round(payload ** 0.5))
            if side < 64:
                side = 1024
            clear_dds = encode_clear_scope_dds(
                texconv=texconv, size=side, accent=accent
            )
        except RuntimeError as e:
            return False, str(e)

        images = uroot / "images"
        images.mkdir(parents=True, exist_ok=True)
        for name in WAW_SCOPE_OVERLAY_NAMES:
            try:
                injected = inject_dds_into_stock_iwi(templates[name], clear_dds)
            except ValueError as e:
                return False, f"{name}: {e}"
            (images / name).write_bytes(injected)

        tmp_out = uroot.parent / "_out_better_scope.zip"
        if tmp_out.is_file():
            tmp_out.unlink()
        repack_iwd(uroot, tmp_out, compress=False)
        written: list[str] = []
        for out_name in (BETTER_SCOPE_OVERRIDE_IWD, BETTER_SCOPE_OVERRIDE_IWD_LOCALIZED):
            dest = base / out_name
            if dest.is_file():
                dest.unlink()
            shutil.copy2(tmp_out, dest)
            written.append(out_name)
        tmp_out.unlink(missing_ok=True)

        try:
            if src_iwd is not None:
                stable = base / SCOPE_SOURCE_IWD
                if src_iwd.resolve() != stable.resolve():
                    shutil.copy2(src_iwd, stable)
                legacy = base / "scope.iwd"
                if legacy.is_file():
                    legacy.unlink()
        except OSError:
            pass

        return True, (
            f"Wrote {', '.join(written)}: {len(weapons)} scoped weapon(s) -> "
            f"empty_overlay, {len(WAW_SCOPE_OVERLAY_NAMES)} clear overlays."
        )


def collect_foliage_iwis_from_iwds(base: Path, dest_dir: Path) -> list[Path]:
    """Extract foliage/grass IWIs into dest_dir (unique by name)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    found: dict[str, Path] = {}
    for iwd in _iter_source_iwds(base):
        try:
            with zipfile.ZipFile(iwd, "r") as z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    name = Path(info.filename).name
                    if not is_foliage_iwi(name):
                        continue
                    key = name.lower()
                    if key in found:
                        continue
                    out = dest_dir / "images" / name
                    out.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as src, open(out, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    found[key] = out
        except zipfile.BadZipFile:
            continue
    return sorted(found.values(), key=lambda p: p.name.lower())


def _iter_source_iwds(base: Path) -> list[Path]:
    skip = {
        TEAM_OVERRIDE_IWD.lower(),
        SMOKE_OVERRIDE_IWD.lower(),
        SMOKE_OVERRIDE_IWD_LOCALIZED.lower(),
        SMOKE_OVERRIDE_LEGACY.lower(),
        FOLIAGE_OVERRIDE_IWD.lower(),
        FOLIAGE_OVERRIDE_IWD_LOCALIZED.lower(),
        BETTER_SCOPE_OVERRIDE_IWD.lower(),
        BETTER_SCOPE_OVERRIDE_IWD_LOCALIZED.lower(),
        "scope.iwd",
        SCOPE_SOURCE_IWD.lower(),
    }
    out: list[Path] = []
    for pattern in ("iw_*.iwd", "localized_english_*.iwd"):
        for p in sorted(base.glob(pattern)):
            if p.name.lower() in skip:
                continue
            out.append(p)
    return out


def collect_faction_iwis_from_iwds(base: Path, dest_dir: Path) -> list[Path]:
    """Extract faction character IWIs into dest_dir (unique by name; normals skipped)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    found: dict[str, Path] = {}
    for iwd in _iter_source_iwds(base):
        try:
            with zipfile.ZipFile(iwd, "r") as z:
                for info in z.infolist():
                    if info.is_dir() or not info.filename.lower().endswith(".iwi"):
                        continue
                    name = Path(info.filename).name
                    if is_normal_map_iwi(name) or team_for_name(name) is None:
                        continue
                    key = name.lower()
                    if key in found:
                        continue
                    rel = Path(info.filename)
                    out = (
                        dest_dir / rel
                        if rel.parts and rel.parts[0].lower() == "images"
                        else dest_dir / "images" / name
                    )
                    out.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as src, open(out, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    found[key] = out
        except zipfile.BadZipFile:
            continue
    return sorted(found.values(), key=lambda p: p.name.lower())


def collect_smoke_particle_iwis_from_iwds(base: Path, dest_dir: Path) -> list[Path]:
    """Extract smoke particle IWIs into dest_dir (unique by name)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    found: dict[str, Path] = {}
    for iwd in _iter_source_iwds(base):
        try:
            with zipfile.ZipFile(iwd, "r") as z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    name = Path(info.filename).name
                    if not is_smoke_particle_iwi(name):
                        continue
                    key = name.lower()
                    if key in found:
                        continue
                    out = dest_dir / "images" / name
                    out.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as src, open(out, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    found[key] = out
        except zipfile.BadZipFile:
            continue
    return sorted(found.values(), key=lambda p: p.name.lower())


def patch_smoke_weapon_bytes(data: bytes) -> tuple[bytes, list[str]]:
    """
    Disable smoke grenade visual/engine cloud by clearing FX and explosion type.

    The particle FX lives in FastFiles; blanking IWI sprites alone does not stop
    ``projExplosionType\\smoke`` from spawning the vision-blocking cloud.
    """
    text = data.decode("latin1")
    changes: list[str] = []

    def repl_field(src: str, key: str, new_val: str) -> str:
        # Weapon files: KEY\\value\\KEY\\value (single backslash separators).
        pat = re.compile("(" + re.escape(key) + r"\\)([^\\]*)")
        m = pat.search(src)
        if not m:
            return src
        old = m.group(2)
        if old == new_val:
            return src
        changes.append(f"{key}: {old!r} -> {new_val!r}")
        return pat.sub(r"\g<1>" + new_val, src, count=1)

    text2 = text
    text2 = repl_field(text2, "projExplosionType", "none")
    text2 = repl_field(text2, "projExplosionEffect", "")
    text2 = repl_field(text2, "projTrailEffect", "")
    # Avoid the dedicated Smoke Grenade offhand path where possible (listen/local).
    text2 = repl_field(text2, "offhandClass", "Frag Grenade")
    return text2.encode("latin1"), changes


def collect_and_patch_smoke_weapons(base: Path, dest_dir: Path) -> list[tuple[Path, list[str]]]:
    """Copy smoke grenade weapon files into dest_dir and patch out smoke FX."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    found: dict[str, tuple[Path, list[str]]] = {}
    want = {n.lower() for n in SMOKE_WEAPON_NAMES}
    for iwd in _iter_source_iwds(base):
        try:
            with zipfile.ZipFile(iwd, "r") as z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    name = Path(info.filename).name.lower()
                    if name not in want:
                        continue
                    # Prefer weapons/mp and weapons/sp paths
                    low = info.filename.replace("\\", "/").lower()
                    if not low.startswith("weapons/"):
                        continue
                    key = low
                    if key in found:
                        continue
                    raw = z.read(info)
                    patched, changes = patch_smoke_weapon_bytes(raw)
                    out = dest_dir / Path(info.filename)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(patched)
                    found[key] = (out, changes)
        except zipfile.BadZipFile:
            continue
    return [found[k] for k in sorted(found)]


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


def repack_iwd(
    source_dir: Path,
    dest_iwd: Path,
    *,
    compress: bool = True,
) -> None:
    """
    Pack ``source_dir`` into an IWD (ZIP). Stock WaW IWDs use ZIP_STORED for images;
    ``compress=False`` matches that (needed for some overlay overrides).
    """
    dest_iwd.parent.mkdir(parents=True, exist_ok=True)
    method = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    with zipfile.ZipFile(dest_iwd, "w", method) as z:
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


def process_one_smoke_blank(
    iwi_src: Path,
    *,
    texconv: Path,
    iwi2dds: Path,
    dds2iwi: Path,
) -> tuple[bool, str]:
    """Replace a smoke/foliage IWI with a fully blank (0,0,0,0) texture of the same size."""
    return process_one_smoke_replace(
        iwi_src,
        texconv=texconv,
        iwi2dds=iwi2dds,
        dds2iwi=dds2iwi,
        particle=None,
    )


def process_one_smoke_replace(
    iwi_src: Path,
    *,
    texconv: Path,
    iwi2dds: Path,
    dds2iwi: Path,
    particle: Image.Image | None,
) -> tuple[bool, str]:
    """
    Replace an IWI with either a blank texture (``particle is None``) or a resized
    copy of ``particle`` (RGBA), keeping the original dimensions.
    """
    with tempfile.TemporaryDirectory(prefix="iwdsmoke_") as td:
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

        with Image.open(png) as src_im:
            w, h = src_im.size
        if particle is None:
            Image.fromarray(np.zeros((h, w, 4), dtype=np.uint8), "RGBA").save(png)
            label = "blanked"
        else:
            fit_particle_to_size(particle, w, h).save(png)
            label = "particle"

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
        return True, f"{label} {iwi_src.name} ({w}x{h})"


def main() -> None:
    """Tkinter UI."""
    root = tk.Tk()
    root.title("good goy garys skin recolour")
    root.geometry("700x860")

    font = ("Segoe UI", 10)
    frm = ttk.Frame(root, padding=10)
    frm.pack(fill=tk.BOTH, expand=True)

    ttk.Label(frm, text="Looks for IWDs in:", font=font).pack(anchor=tk.W)
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

    no_smoke_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(
        frm, text="custom smoke (star particles)", variable=no_smoke_var
    ).pack(anchor=tk.W, pady=(4, 0))
    no_foliage_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(frm, text="no foliage", variable=no_foliage_var).pack(anchor=tk.W, pady=(2, 0))
    better_scope_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(frm, text="better scope", variable=better_scope_var).pack(anchor=tk.W, pady=(2, 0))

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
        iwi2dds = find_converter(base, IWI2DDS_NAMES)
        dds2iwi = find_converter(base, DDS2IWI_NAMES)
        texc = find_texconv()
        no_smoke = bool(no_smoke_var.get())
        no_foliage = bool(no_foliage_var.get())
        better_scope = bool(better_scope_var.get())

        if no_smoke and find_smoke_particle_png(base) is None:
            messagebox.showerror(
                "Missing smoke PNG",
                f"\"custom smoke\" needs {SMOKE_PARTICLE_PNG}.\n\n"
                f"Place it in:\n{base}\n"
                "or next to this script / in Pictures "
                "(Israeli_blue_Star_of_David.png).",
            )
            return

        if not _iter_source_iwds(base):
            messagebox.showerror(
                "Missing IWD",
                f"No iw_*.iwd / localized_english_*.iwd found in:\n{base}",
            )
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

        out_team = base / TEAM_OVERRIDE_IWD
        if out_team.is_file():
            backup = out_team.with_name(
                out_team.stem
                + datetime.now().strftime("_%Y%m%d_%H%M%S")
                + out_team.suffix
                + ".bak"
            )
            try:
                shutil.copy2(out_team, backup)
                log(f"Backup: {backup.name}")
            except OSError as e:
                log(f"Warning: could not backup team IWD: {e}")

        btn_color.configure(state=tk.DISABLED)

        def work() -> None:
            failed = 0
            try:
                # --- team colours ---
                with tempfile.TemporaryDirectory(prefix="iwd_team_") as unpack:
                    uroot = Path(unpack)
                    log(f"Collecting faction skins → {TEAM_OVERRIDE_IWD} …")
                    iwis = collect_faction_iwis_from_iwds(base, uroot)
                    total = len(iwis)
                    if total == 0:
                        log("No faction IWIs found.")
                        root.after(
                            0,
                            lambda: messagebox.showerror(
                                "No skins",
                                "No faction character textures found to recolour.",
                            ),
                        )
                        return
                    rus = sum(
                        1
                        for p in iwis
                        if "char_rus" in p.name.lower() or "viewmodel_rus" in p.name.lower()
                    )
                    log(f"Found {total} faction IWI(s) (Red Army / rus: {rus}).")
                    done = 0
                    for p in iwis:
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
                        log(f"Team recolour finished with {failed} error(s); not writing override.")
                        root.after(
                            0,
                            lambda: messagebox.showwarning(
                                "Done with errors",
                                f"{failed} team texture(s) failed. See log.",
                            ),
                        )
                        return

                    log(f"Packing {TEAM_OVERRIDE_IWD} …")
                    tmp_out = uroot.parent / "_out_team_iwd.zip"
                    if tmp_out.is_file():
                        tmp_out.unlink()
                    repack_iwd(uroot, tmp_out)
                    if out_team.is_file():
                        out_team.unlink()
                    shutil.move(str(tmp_out), str(out_team))
                    log(f"Wrote {TEAM_OVERRIDE_IWD} ({total} textures).")

                # --- custom smoke particles ---
                if no_smoke:
                    png_path = find_smoke_particle_png(base)
                    assert png_path is not None  # validated before thread start
                    # Keep engine clouds on so remapped particle sprites can draw.
                    cfg_path = apply_no_smoke_autoexec(base, enabled=True)
                    log(
                        f"custom smoke: fx_drawClouds 1 in {Path(cfg_path).name}; "
                        f"using particle art {png_path.name}"
                    )
                    try:
                        if png_path.resolve() != (base / SMOKE_PARTICLE_PNG).resolve():
                            shutil.copy2(png_path, base / SMOKE_PARTICLE_PNG)
                    except OSError:
                        pass
                    particle = load_smoke_particle_rgba(png_path)
                    with tempfile.TemporaryDirectory(prefix="iwd_smoke_") as unpack:
                        uroot = Path(unpack)
                        log(
                            "custom smoke: replacing smoke particle textures → "
                            f"{SMOKE_OVERRIDE_IWD} …"
                        )
                        smokes = collect_smoke_particle_iwis_from_iwds(base, uroot)
                        stotal = len(smokes)
                        log(f"Found {stotal} smoke particle IWI(s) to replace.")
                        sdone = 0
                        sfailed = 0
                        for p in smokes:
                            ok, msg = process_one_smoke_replace(
                                p,
                                texconv=texc,
                                iwi2dds=iwi2dds,
                                dds2iwi=dds2iwi,
                                particle=particle,
                            )
                            log(msg)
                            if not ok:
                                sfailed += 1
                            sdone += 1
                            d, t = sdone, max(stotal, 1)
                            root.after(
                                0, lambda d=d, t=t: prog.configure(maximum=t, value=d)
                            )

                        if sfailed:
                            log(
                                f"Smoke replace had {sfailed} error(s); "
                                "not writing override."
                            )
                            failed += sfailed
                        elif stotal == 0:
                            log("No smoke particle IWIs found to replace.")
                        else:
                            legacy = base / SMOKE_OVERRIDE_LEGACY
                            if legacy.is_file():
                                try:
                                    legacy.unlink()
                                    log(f"Removed legacy {SMOKE_OVERRIDE_LEGACY}")
                                except OSError:
                                    pass
                            tmp_out = uroot.parent / "_out_smoke_iwd.zip"
                            if tmp_out.is_file():
                                tmp_out.unlink()
                            repack_iwd(uroot, tmp_out)
                            for out_name in (
                                SMOKE_OVERRIDE_IWD,
                                SMOKE_OVERRIDE_IWD_LOCALIZED,
                            ):
                                out_smoke = base / out_name
                                if out_smoke.is_file():
                                    out_smoke.unlink()
                                shutil.copy2(tmp_out, out_smoke)
                                log(
                                    f"Wrote {out_name} "
                                    f"({stotal} custom particle textures)."
                                )
                            tmp_out.unlink(missing_ok=True)
                else:
                    cfg_path = apply_no_smoke_autoexec(base, enabled=False)
                    log(f"Restored fx_drawClouds 1 in {Path(cfg_path).name}")
                    removed = remove_smoke_override_iwds(base)
                    if removed:
                        log("Smoke restored (removed): " + ", ".join(removed))
                    else:
                        log("custom smoke unchecked — stock smoke left as-is.")

                # --- no foliage / grass ---
                if no_foliage:
                    cfg_path = apply_no_foliage_autoexec(base, enabled=True)
                    log(
                        f"no foliage: set r_grassEnable / r_gfxopt_dynamic_foliage 0 "
                        f"in {Path(cfg_path).name}"
                    )
                    with tempfile.TemporaryDirectory(prefix="iwd_foliage_") as unpack:
                        uroot = Path(unpack)
                        log(
                            "no foliage: blanking foliage/grass textures → "
                            f"{FOLIAGE_OVERRIDE_IWD} …"
                        )
                        foliage = collect_foliage_iwis_from_iwds(base, uroot)
                        ftotal = len(foliage)
                        log(f"Found {ftotal} foliage/grass IWI(s) to blank.")
                        fdone = 0
                        ffailed = 0
                        for p in foliage:
                            ok, msg = process_one_smoke_blank(
                                p,
                                texconv=texc,
                                iwi2dds=iwi2dds,
                                dds2iwi=dds2iwi,
                            )
                            log(msg)
                            if not ok:
                                ffailed += 1
                            fdone += 1
                            d, t = fdone, max(ftotal, 1)
                            root.after(
                                0, lambda d=d, t=t: prog.configure(maximum=t, value=d)
                            )

                        if ffailed:
                            log(
                                f"Foliage blanking had {ffailed} error(s); "
                                "not writing override."
                            )
                            failed += ffailed
                        elif ftotal == 0:
                            log(
                                "No foliage IWIs found to blank "
                                "(grass dvars still applied)."
                            )
                        else:
                            tmp_out = uroot.parent / "_out_foliage_iwd.zip"
                            if tmp_out.is_file():
                                tmp_out.unlink()
                            repack_iwd(uroot, tmp_out)
                            for out_name in (
                                FOLIAGE_OVERRIDE_IWD,
                                FOLIAGE_OVERRIDE_IWD_LOCALIZED,
                            ):
                                out_fol = base / out_name
                                if out_fol.is_file():
                                    out_fol.unlink()
                                shutil.copy2(tmp_out, out_fol)
                                log(f"Wrote {out_name} ({ftotal} blank textures).")
                            tmp_out.unlink(missing_ok=True)
                else:
                    cfg_path = apply_no_foliage_autoexec(base, enabled=False)
                    log(
                        f"Restored grass/foliage dvars in {Path(cfg_path).name}"
                    )
                    removed = remove_foliage_override_iwds(base)
                    if removed:
                        log("Foliage restored (removed): " + ", ".join(removed))
                    else:
                        log("no foliage unchecked — stock foliage left as-is.")

                # --- better scope (COD4 overlay → WaW names) ---
                if better_scope:
                    log(
                        f"better scope: converting {SCOPE_SOURCE_IWD} → "
                        f"{BETTER_SCOPE_OVERRIDE_IWD} …"
                    )
                    ok, msg = build_better_scope_override(
                        base,
                        iwi2dds=iwi2dds,
                        dds2iwi=dds2iwi,
                        texconv=texc,
                    )
                    log(msg)
                    if not ok:
                        failed += 1
                else:
                    removed = remove_better_scope_override_iwds(base)
                    if removed:
                        log("Scope restored (removed): " + ", ".join(removed))
                    else:
                        log("better scope unchecked — stock overlays left as-is.")

                if failed:
                    root.after(
                        0,
                        lambda: messagebox.showwarning(
                            "Done with errors",
                            f"{failed} file(s) failed. See log.",
                        ),
                    )
                else:
                    def show_done_success() -> None:
                        stop_mp3_music()
                        play_mp3_music(resolve_mp3("sheckles"), 0.05)
                        smoke_msg = (
                            f"\nCustom smoke: {SMOKE_PARTICLE_PNG} → {SMOKE_OVERRIDE_IWD}."
                            if no_smoke
                            else "\nSmoke: restored (checkbox off)."
                        )
                        foliage_msg = (
                            f"\nNo foliage: blanked textures + {FOLIAGE_OVERRIDE_IWD}."
                            if no_foliage
                            else "\nFoliage: restored (checkbox off)."
                        )
                        scope_msg = (
                            f"\nBetter scope: {BETTER_SCOPE_OVERRIDE_IWD}."
                            if better_scope
                            else "\nScope: restored (checkbox off)."
                        )
                        messagebox.showinfo(
                            "Done",
                            f"Wrote {TEAM_OVERRIDE_IWD}.{smoke_msg}{foliage_msg}{scope_msg}",
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
        text="COLOR writes zz_team_colours.iwd (loads last — Red Army included). "
        "\"custom smoke\" remaps smoke particle textures to smoke_particle.png "
        f"(Star of David) in {SMOKE_OVERRIDE_IWD}, and keeps fx_drawClouds on. "
        "\"no foliage\" blanks grass/foliage textures into zz_no_foliage.iwd "
        "and sets r_grassEnable / r_gfxopt_dynamic_foliage 0. "
        "\"better scope\" remaps COD4 scope.iwd onto WaW scope_overlay*.iwi "
        f"→ {BETTER_SCOPE_OVERRIDE_IWD}. Uncheck any option to restore.",
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