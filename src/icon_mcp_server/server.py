"""MCP server entry point (stdio transport).

Exposes the following tools:
    * ``list_icon_styles``    – introspect available styles / shapes / defaults
    * ``generate_icon``       – render a single PNG/ICO/ICNS icon to disk
    * ``generate_icon_set``   – render a multi-size PNG set + Windows .ico
    * ``generate_icon_base64``– render and return the PNG bytes as base64 (no disk write)

Run directly:
    python -m icon_mcp_server
    icon-mcp-server           # console script installed by pip
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

# Support both mcp v1 (FastMCP) and mcp v2+ (MCPServer) without pinning.
try:  # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as _ServerCls  # type: ignore
except ImportError:  # pragma: no cover - fallback for mcp < 2
    from mcp.server.fastmcp import FastMCP as _ServerCls  # type: ignore

from . import __version__
from .generator import (
    IconRequest,
    generate_icon as _generate_icon,
    generate_icon_set as _generate_icon_set,
    list_styles as _list_styles,
)

mcp = _ServerCls(
    name="icon-mcp-server",
    instructions=(
        "Generate software / app icons locally with Pillow. "
        "Use list_icon_styles to discover parameters, then generate_icon or "
        "generate_icon_set to render PNG / ICO / ICNS files, or "
        "generate_icon_base64 to receive bytes inline."
    ),
    version=__version__,
)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_icon_styles() -> dict:
    """Return the available styles, shapes, and default palette values."""
    return _list_styles()


@mcp.tool()
def generate_icon(
    out_path: str,
    text: str = "",
    style: str = "gradient",
    size: int = 512,
    shape: str = "rounded",
    fg_color: str = "#FFFFFF",
    bg_color: str = "#4F8DFD",
    bg_color_2: Optional[str] = "#8E5BFF",
    gradient_angle: float = 135.0,
    font_scale: float = 0.55,
    corner_radius: float = 0.22,
    border_width: float = 0.0,
    border_color: str = "#FFFFFF",
    shadow: bool = False,
    fmt: Optional[str] = None,
) -> dict:
    """Render a single icon and save it to ``out_path``.

    Parameters
    ----------
    out_path : str
        Destination file path. Extension determines the format unless ``fmt`` is given.
        Supported extensions: ``.png`` (default), ``.ico``, ``.icns``.
    text : str
        Short label drawn in the center (initials, single glyph, or emoji).
    style : str
        One of ``gradient`` / ``solid`` / ``outlined`` / ``shape`` / ``mono``.
    size : int
        Edge length in pixels, 16..2048.
    shape : str
        One of ``circle`` / ``square`` / ``rounded`` / ``triangle`` / ``hexagon`` / ``diamond``.
    fg_color, bg_color, bg_color_2, border_color : str
        CSS-like color strings (``#RRGGBB`` or named colors).
    gradient_angle : float
        Gradient direction in degrees (0 = left→right, 90 = top→bottom).
    font_scale : float
        Text height as fraction of the icon size (0.05..1.2).
    corner_radius : float
        Corner radius fraction for ``shape='rounded'`` (0..0.5).
    border_width : float
        Stroke width as a fraction of the icon size (0..0.15).
    shadow : bool
        Add a soft drop shadow behind the icon silhouette.
    fmt : str, optional
        Force output format (``png`` / ``ico`` / ``icns``).
    """
    req = IconRequest(
        text=text,
        style=style,  # type: ignore[arg-type]
        size=size,
        shape=shape,  # type: ignore[arg-type]
        fg_color=fg_color,
        bg_color=bg_color,
        bg_color_2=bg_color_2,
        gradient_angle=gradient_angle,
        font_scale=font_scale,
        corner_radius=corner_radius,
        border_width=border_width,
        border_color=border_color,
        shadow=shadow,
    )
    result = _generate_icon(req, out_path=out_path, fmt=fmt)  # type: ignore[arg-type]
    return {
        "path": result.path,
        "size": req.normalized().size,
        "style": req.style,
        "shape": req.shape,
        **result.extra,
    }


@mcp.tool()
def generate_icon_set(
    out_dir: str,
    base_name: str = "icon",
    text: str = "",
    style: str = "gradient",
    shape: str = "rounded",
    fg_color: str = "#FFFFFF",
    bg_color: str = "#4F8DFD",
    bg_color_2: Optional[str] = "#8E5BFF",
    gradient_angle: float = 135.0,
    font_scale: float = 0.55,
    corner_radius: float = 0.22,
    border_width: float = 0.0,
    border_color: str = "#FFFFFF",
    shadow: bool = False,
    include_ico: bool = True,
    png_sizes: Optional[list[int]] = None,
) -> dict:
    """Render a multi-resolution icon set: several PNGs + an aggregated ``.ico``.

    The largest PNG is drawn once at high resolution and downscaled with LANCZOS
    for every other size, so all outputs share the same design.
    """
    req = IconRequest(
        text=text,
        style=style,  # type: ignore[arg-type]
        size=max(png_sizes or [512]),
        shape=shape,  # type: ignore[arg-type]
        fg_color=fg_color,
        bg_color=bg_color,
        bg_color_2=bg_color_2,
        gradient_angle=gradient_angle,
        font_scale=font_scale,
        corner_radius=corner_radius,
        border_width=border_width,
        border_color=border_color,
        shadow=shadow,
    )
    result = _generate_icon_set(
        req,
        out_dir=out_dir,
        base_name=base_name,
        include_ico=include_ico,
        png_sizes=tuple(png_sizes) if png_sizes else (16, 32, 48, 64, 128, 256, 512),
    )
    return {
        "out_dir": str(Path(out_dir).resolve()),
        "files": result.extra.get("files", []),
        "count": result.extra.get("count", 0),
        "ico": result.path,
    }


@mcp.tool()
def generate_icon_base64(
    text: str = "",
    style: str = "gradient",
    size: int = 256,
    shape: str = "rounded",
    fg_color: str = "#FFFFFF",
    bg_color: str = "#4F8DFD",
    bg_color_2: Optional[str] = "#8E5BFF",
    gradient_angle: float = 135.0,
    font_scale: float = 0.55,
    corner_radius: float = 0.22,
    border_width: float = 0.0,
    border_color: str = "#FFFFFF",
    shadow: bool = False,
) -> dict:
    """Render an icon and return it as a base64-encoded PNG string (no disk write).

    Useful for previews inside MCP clients that cannot read local files.
    """
    req = IconRequest(
        text=text,
        style=style,  # type: ignore[arg-type]
        size=size,
        shape=shape,  # type: ignore[arg-type]
        fg_color=fg_color,
        bg_color=bg_color,
        bg_color_2=bg_color_2,
        gradient_angle=gradient_angle,
        font_scale=font_scale,
        corner_radius=corner_radius,
        border_width=border_width,
        border_color=border_color,
        shadow=shadow,
    ).normalized()
    result = _generate_icon(req)
    buf = io.BytesIO()
    result.image.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    return {
        "format": "png",
        "size": req.size,
        "bytes": len(data),
        "base64": base64.b64encode(data).decode("ascii"),
        "data_url": f"data:image/png;base64,{base64.b64encode(data).decode('ascii')}",
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    """Console-script entry point.

    Flags:
        ``--version``   print the package version and exit
        ``--self-test`` render a few sample icons into ``./_icon_self_test`` and exit
        ``--help``      show this message
    """
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--version" in argv or "-V" in argv:
        print(f"icon-mcp-server {__version__}")
        return 0

    if "--help" in argv or "-h" in argv:
        print(
            "icon-mcp-server – MCP stdio server that generates software icons.\n\n"
            "Usage:\n"
            "  icon-mcp-server               Run the MCP server over stdio (default)\n"
            "  icon-mcp-server --self-test   Render sample icons and exit\n"
            "  icon-mcp-server --version     Print version and exit\n"
        )
        return 0

    if "--self-test" in argv:
        out = Path(os.environ.get("ICON_MCP_SELF_TEST_DIR", "_icon_self_test"))
        samples = [
            dict(text="A", style="gradient", shape="rounded", bg_color="#4F8DFD", bg_color_2="#8E5BFF"),
            dict(text="Q", style="solid", shape="circle", bg_color="#FF7043", fg_color="#FFFFFF"),
            dict(text="M", style="outlined", shape="square", fg_color="#222222"),
            dict(text="", style="shape", shape="hexagon", bg_color="#2ECC71"),
            dict(text="", style="mono", shape="diamond", fg_color="#111111"),
        ]
        for i, kw in enumerate(samples, 1):
            _generate_icon(IconRequest(**kw, size=256), out_path=out / f"sample_{i}.png")  # type: ignore[arg-type]
        _generate_icon_set(IconRequest(text="S", size=512), out_dir=out, base_name="set_demo")
        print(json.dumps({"self_test_dir": str(out.resolve()), "samples": len(samples)}, indent=2))
        return 0

    # Default: run MCP stdio server
    mcp.run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
