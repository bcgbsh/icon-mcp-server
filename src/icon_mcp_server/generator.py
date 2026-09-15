"""Core icon generation logic built on Pillow.

Supports several visual styles:
  * ``gradient``  – linear/radial gradient background + foreground text/emoji
  * ``solid``     – flat color background + foreground text/emoji
  * ``outlined``  – transparent background + stroked text/emoji
  * ``shape``     – simple geometric shape (circle / square / triangle / hexagon)
  * ``mono``      – single-color silhouette shape, ideal for menu-bar / tray icons

The renderer always works on a large canvas (``SUPERSAMPLE``) and downscales
with LANCZOS for crisp edges at small sizes (16/24/32/48 px).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Literal, Sequence

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPERSAMPLE = 4  # render at 4x size then downscale
DEFAULT_SIZE = 512
ICO_SIZES: Sequence[tuple[int, int]] = (
    (16, 16),
    (24, 24),
    (32, 32),
    (48, 48),
    (64, 64),
    (128, 128),
    (256, 256),
)

StyleName = Literal["gradient", "solid", "outlined", "shape", "mono"]
ShapeName = Literal["circle", "square", "rounded", "triangle", "hexagon", "diamond"]

STYLE_NAMES: tuple[StyleName, ...] = ("gradient", "solid", "outlined", "shape", "mono")
SHAPE_NAMES: tuple[ShapeName, ...] = (
    "circle",
    "square",
    "rounded",
    "triangle",
    "hexagon",
    "diamond",
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class IconRequest:
    """Parameters describing a single icon render."""

    text: str = ""
    style: StyleName = "gradient"
    size: int = DEFAULT_SIZE
    shape: ShapeName = "rounded"
    fg_color: str = "#FFFFFF"
    bg_color: str = "#4F8DFD"
    bg_color_2: str | None = None  # second stop for gradient
    gradient_angle: float = 135.0  # degrees, 0 = left→right
    font_path: str | None = None
    font_scale: float = 0.55  # text height as a fraction of icon size
    padding: float = 0.0  # inner padding as fraction of size (0..0.4)
    corner_radius: float = 0.22  # only for shape='rounded'
    border_width: float = 0.0  # fraction of size (0..0.15)
    border_color: str = "#FFFFFF"
    shadow: bool = False

    def normalized(self) -> "IconRequest":
        """Return a copy with clamped / validated values."""
        data = asdict(self)
        data["size"] = max(16, min(2048, int(data["size"])))
        data["font_scale"] = max(0.05, min(1.2, float(data["font_scale"])))
        data["padding"] = max(0.0, min(0.4, float(data["padding"])))
        data["corner_radius"] = max(0.0, min(0.5, float(data["corner_radius"])))
        data["border_width"] = max(0.0, min(0.15, float(data["border_width"])))
        if data["style"] not in STYLE_NAMES:
            data["style"] = "gradient"
        if data["shape"] not in SHAPE_NAMES:
            data["shape"] = "rounded"
        return IconRequest(**data)


@dataclass
class IconResult:
    """Result of a render call.

    ``image`` is a Pillow ``RGBA`` image.  ``path`` is populated only when the
    caller asked for the icon to be saved to disk.
    """

    image: Image.Image
    path: str | None = None
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------


def _candidate_fonts() -> list[str]:
    """Return a list of font files that likely exist on the host OS."""
    candidates: list[str] = []
    system = os.name
    if system == "nt":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        base = Path(windir) / "Fonts"
        candidates += [
            "segoeui.ttf",
            "segoeuib.ttf",
            "arialbd.ttf",
            "arial.ttf",
            "calibrib.ttf",
            "DejaVuSans-Bold.ttf",
        ]
        candidates = [str(base / c) for c in candidates]
    elif system == "posix":
        bases = [
            "/usr/share/fonts/truetype/dejavu",
            "/usr/share/fonts/dejavu",
            "/System/Library/Fonts",
            "/Library/Fonts",
        ]
        names = ["DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "Helvetica.ttc", "Arial Bold.ttf"]
        for b in bases:
            for n in names:
                candidates.append(str(Path(b) / n))
    return candidates


_DEFAULT_FONT_PATH: str | None = None


def default_font_path() -> str | None:
    global _DEFAULT_FONT_PATH
    if _DEFAULT_FONT_PATH is not None:
        return _DEFAULT_FONT_PATH or None
    for p in _candidate_fonts():
        if os.path.exists(p):
            _DEFAULT_FONT_PATH = p
            return p
    _DEFAULT_FONT_PATH = ""
    return None


def _load_font(size_px: int, font_path: str | None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = font_path or default_font_path()
    if path and os.path.exists(path):
        try:
            return ImageFont.truetype(path, size=max(8, int(size_px)))
        except Exception:
            pass
    # Fallback: Pillow's bundled bitmap font (does not scale, but always works)
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Color / gradient helpers
# ---------------------------------------------------------------------------


def _parse_color(value: str | tuple | list | None, default: str = "#000000") -> tuple[int, int, int, int]:
    if value is None:
        value = default
    if isinstance(value, (tuple, list)):
        r, g, b = (int(value[0]), int(value[1]), int(value[2]))
        a = int(value[3]) if len(value) > 3 else 255
        return (r, g, b, a)
    try:
        # Let Pillow handle #RGB / #RRGGBB / names
        rgb = ImageColor.getrgb(str(value))
        return (*rgb, 255)
    except ValueError:
        rgb = ImageColor.getrgb(default)
        return (*rgb, 255)


def _linear_gradient(size: int, c1: tuple[int, int, int, int], c2: tuple[int, int, int, int], angle_deg: float) -> Image.Image:
    """Return an RGBA image filled with a two-stop linear gradient."""
    angle = math.radians(angle_deg % 360.0)
    # Gradient axis unit vector
    dx, dy = math.cos(angle), math.sin(angle)
    # Project each pixel onto the axis; normalized 0..1 across the canvas
    img = Image.new("RGBA", (size, size))
    px = img.load()
    # Precompute row-independent parts for speed
    # t(x,y) = ((x - cx)*dx + (y - cy)*dy) / (half_diag) + 0.5
    cx = cy = (size - 1) / 2.0
    half = math.sqrt(2.0) * size / 2.0
    for y in range(size):
        yy = (y - cy) * dy
        for x in range(size):
            t = ((x - cx) * dx + yy) / (2 * half) + 0.5
            t = 0.0 if t < 0 else 1.0 if t > 1 else t
            r = int(c1[0] + (c2[0] - c1[0]) * t)
            g = int(c1[1] + (c2[1] - c1[1]) * t)
            b = int(c1[2] + (c2[2] - c1[2]) * t)
            a = int(c1[3] + (c2[3] - c1[3]) * t)
            px[x, y] = (r, g, b, a)
    return img


def _fast_linear_gradient(size: int, c1: tuple, c2: tuple, angle_deg: float) -> Image.Image:
    """Vectorised-ish linear gradient using Pillow's built-in ramp + rotate.

    Faster than per-pixel loops for large canvases.
    """
    # Build a horizontal 1D ramp of length = size
    ramp = Image.new("L", (size, 1))
    ramp.putdata([int(255 * i / max(1, size - 1)) for i in range(size)])
    ramp = ramp.resize((size, size))
    # Colorize ramp: blend c1 → c2
    r_ch = ramp.point(lambda v: int(c1[0] + (c2[0] - c1[0]) * v / 255))
    g_ch = ramp.point(lambda v: int(c1[1] + (c2[1] - c1[1]) * v / 255))
    b_ch = ramp.point(lambda v: int(c1[2] + (c2[2] - c1[2]) * v / 255))
    a_ch = ramp.point(lambda v: int(c1[3] + (c2[3] - c1[3]) * v / 255))
    img = Image.merge("RGBA", (r_ch, g_ch, b_ch, a_ch))
    # Rotate around center to obtain desired angle (0° = left→right)
    if angle_deg % 360 != 0:
        img = img.rotate(-angle_deg, resample=Image.BICUBIC, expand=False, center=(size / 2, size / 2))
    return img


# ---------------------------------------------------------------------------
# Shape masks
# ---------------------------------------------------------------------------


def _shape_mask(size: int, shape: ShapeName, corner_radius: float) -> Image.Image:
    """Return an ``L`` mode mask (white = keep) for the requested shape."""
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    if shape == "circle":
        d.ellipse((0, 0, size - 1, size - 1), fill=255)
    elif shape == "square":
        d.rectangle((0, 0, size - 1, size - 1), fill=255)
    elif shape == "rounded":
        r = int(size * max(0.0, min(0.5, corner_radius)))
        d.rounded_rectangle((0, 0, size - 1, size - 1), radius=r, fill=255)
    elif shape == "triangle":
        d.polygon([(size // 2, 0), (size - 1, size - 1), (0, size - 1)], fill=255)
    elif shape == "diamond":
        d.polygon([(size // 2, 0), (size - 1, size // 2), (size // 2, size - 1), (0, size // 2)], fill=255)
    elif shape == "hexagon":
        pts = []
        cx = cy = size / 2
        r = size / 2
        for i in range(6):
            ang = math.radians(60 * i - 30)
            pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
        d.polygon(pts, fill=255)
    else:
        d.rectangle((0, 0, size - 1, size - 1), fill=255)
    return mask


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------


def _render(req: IconRequest) -> Image.Image:
    """Render at supersampled resolution and return the RGBA image (still large)."""
    S = max(64, req.size) * SUPERSAMPLE
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    fg = _parse_color(req.fg_color, "#FFFFFF")
    bg = _parse_color(req.bg_color, "#4F8DFD")
    bg2 = _parse_color(req.bg_color_2, "#8E5BFF") if req.bg_color_2 else None

    # --- 1. Background layer (only for gradient / solid styles) ---
    if req.style in ("gradient", "solid"):
        if req.style == "gradient":
            bg_layer = _fast_linear_gradient(S, bg, bg2 or bg, req.gradient_angle)
        else:
            bg_layer = Image.new("RGBA", (S, S), bg)
        mask = _shape_mask(S, req.shape, req.corner_radius)
        canvas.paste(bg_layer, (0, 0), mask)

    # --- 2. Shape silhouette (for shape / mono styles) ---
    if req.style in ("shape", "mono"):
        color = fg if req.style == "mono" else bg
        mask = _shape_mask(S, req.shape, req.corner_radius)
        layer = Image.new("RGBA", (S, S), color)
        canvas.paste(layer, (0, 0), mask)

    # --- 3. Optional drop shadow behind content (very subtle) ---
    if req.shadow and req.style in ("gradient", "solid"):
        shadow_layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow_layer)
        sm = _shape_mask(S, req.shape, req.corner_radius)
        shadow_layer.putalpha(sm.point(lambda v: int(v * 0.35)))
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(S // 90))
        base = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        base.paste(shadow_layer, (S // 60, S // 45), shadow_layer)
        canvas = Image.alpha_composite(base, canvas)

    # --- 4. Border stroke ---
    if req.border_width > 0 and req.style in ("gradient", "solid", "shape"):
        bw = int(S * req.border_width)
        if bw > 0:
            border_mask = _shape_mask(S, req.shape, req.corner_radius)
            inner = _shape_mask(max(1, S - 2 * bw), req.shape, req.corner_radius).resize((S, S))
            ring = Image.eval(border_mask, lambda v: v)
            # ring = border_mask - inner
            ring = ImageChops.subtract(border_mask, inner)
            stroke = Image.new("RGBA", (S, S), _parse_color(req.border_color, "#FFFFFF"))
            canvas.paste(stroke, (0, 0), ring)

    # --- 5. Foreground text / emoji ---
    text = (req.text or "").strip()
    if text and req.style in ("gradient", "solid", "outlined"):
        pad = int(S * req.padding)
        avail = S - 2 * pad
        font_size = int(avail * req.font_scale)
        font = _load_font(font_size, req.font_path)

        # Auto-shrink font so the text fits within `avail` width
        draw_probe = ImageDraw.Draw(canvas)
        for _ in range(12):
            bbox = draw_probe.textbbox((0, 0), text, font=font)
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            if w <= avail and h <= avail:
                break
            font_size = int(font_size * 0.9)
            if font_size < 8:
                break
            font = _load_font(font_size, req.font_path)

        bbox = draw_probe.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = (S - tw) // 2 - bbox[0]
        ty = (S - th) // 2 - bbox[1]

        if req.style == "outlined":
            stroke_w = max(2, int(S * 0.012))
            draw_probe.text(
                (tx, ty),
                text,
                font=font,
                fill=fg,
                stroke_width=stroke_w,
                stroke_fill=fg,
            )
        else:
            draw_probe.text((tx, ty), text, font=font, fill=fg)

    # Downscale to requested size
    return canvas.resize((req.size, req.size), Image.LANCZOS)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_styles() -> dict:
    """Return available styles/shapes and default palette hints (for MCP listing)."""
    return {
        "styles": list(STYLE_NAMES),
        "shapes": list(SHAPE_NAMES),
        "defaults": {
            "size": DEFAULT_SIZE,
            "fg_color": "#FFFFFF",
            "bg_color": "#4F8DFD",
            "bg_color_2": "#8E5BFF",
            "gradient_angle": 135.0,
            "shape": "rounded",
            "corner_radius": 0.22,
        },
        "ico_sizes": [f"{w}x{h}" for w, h in ICO_SIZES],
    }


def generate_icon(
    req: IconRequest | dict,
    out_path: str | os.PathLike | None = None,
    fmt: Literal["png", "ico", "icns"] | None = None,
) -> IconResult:
    """Render one icon and optionally save it to disk."""
    if isinstance(req, dict):
        req = IconRequest(**req)
    req = req.normalized()
    img = _render(req)

    saved_to: str | None = None
    extra: dict = {}

    if out_path is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        chosen_fmt = (fmt or _infer_format(out_path.suffix.lower())).lower()
        if chosen_fmt == "ico":
            img.save(out_path, format="ICO", sizes=[(req.size, req.size)])
        elif chosen_fmt == "icns":
            # Pillow needs specific sizes for ICNS; use the closest standard set
            img.save(out_path, format="ICNS")
        else:
            img.save(out_path, format="PNG", optimize=True)
        saved_to = str(out_path.resolve())
        extra["format"] = chosen_fmt
        extra["bytes"] = out_path.stat().st_size

    return IconResult(image=img, path=saved_to, extra=extra)


def generate_icon_set(
    req: IconRequest | dict,
    out_dir: str | os.PathLike,
    base_name: str = "icon",
    include_ico: bool = True,
    png_sizes: Iterable[int] = (16, 32, 48, 64, 128, 256, 512),
) -> IconResult:
    """Render a full icon set: multiple PNG sizes + a multi-resolution ``.ico``."""
    if isinstance(req, dict):
        req = IconRequest(**req)
    req = req.normalized()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    master = _render(IconRequest(**{**asdict(req), "size": max(png_sizes)}))
    for s in sorted(png_sizes):
        p = out_dir / f"{base_name}_{s}.png"
        master.resize((s, s), Image.LANCZOS).save(p, format="PNG", optimize=True)
        written.append(str(p.resolve()))

    ico_path: str | None = None
    if include_ico:
        ico_path = str((out_dir / f"{base_name}.ico").resolve())
        master.save(ico_path, format="ICO", sizes=[(w, h) for (w, h) in ICO_SIZES if w <= master.size[0]])
        written.append(ico_path)

    return IconResult(
        image=master,
        path=ico_path or written[0],
        extra={"files": written, "count": len(written)},
    )


def _infer_format(suffix: str) -> str:
    mapping = {".png": "png", ".ico": "ico", ".icns": "icns"}
    return mapping.get(suffix, "png")
