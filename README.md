# icon-mcp-server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that
generates **software / app icons** locally with Pillow — no external API, no
network calls, no keys required.

Renders PNG / ICO / ICNS files with gradients, shapes, borders, drop shadows,
and centered text or emoji — perfect for quickly bootstrapping app icons,
favicons, tray icons, or placeholder assets.

---

## Features

- 5 visual styles: `gradient`, `solid`, `outlined`, `shape`, `mono`
- 6 silhouette shapes: `circle`, `square`, `rounded`, `triangle`, `hexagon`, `diamond`
- Multi-resolution output: single PNG, full PNG set, or aggregated Windows `.ico`
- Optional `.icns` for macOS bundles
- 4× supersampling + LANCZOS downscale → crisp results even at 16 px
- Base64 preview mode for MCP clients that cannot read local files
- Zero external services — everything runs offline via Pillow

---

## Install from PyPI

```bash
pip install icon-mcp-server
```

Or run without installing:

```bash
pipx run icon-mcp-server
```

## Wire it up to an MCP client

### Qoder / Claude Desktop / Cursor — `mcp.json`

```json
{
  "mcpServers": {
    "icon-generator": {
      "command": "icon-mcp-server",
      "args": []
    }
  }
}
```

If the console script is not on `PATH`, invoke via Python:

```json
{
  "mcpServers": {
    "icon-generator": {
      "command": "python",
      "args": ["-m", "icon_mcp_server"]
    }
  }
}
```

### uv (recommended for isolated installs)

```json
{
  "mcpServers": {
    "icon-generator": {
      "command": "uvx",
      "args": ["icon-mcp-server"]
    }
  }
}
```

---

## Exposed MCP tools

| Tool                     | Purpose                                                              |
| ------------------------ | -------------------------------------------------------------------- |
| `list_icon_styles`       | Discover available styles, shapes, and default palette               |
| `generate_icon`          | Render a single PNG / ICO / ICNS file to disk                        |
| `generate_icon_set`      | Render a multi-size PNG set **plus** an aggregated Windows `.ico`    |
| `generate_icon_base64`   | Render and return inline base64 PNG (no disk write, great for previews) |

### Common parameters

| Param            | Type          | Default      | Notes                                            |
| ---------------- | ------------- | ------------ | ------------------------------------------------ |
| `text`           | str           | `""`         | Centered label / initials / single emoji         |
| `style`          | enum          | `gradient`   | `gradient` \| `solid` \| `outlined` \| `shape` \| `mono` |
| `shape`          | enum          | `rounded`    | `circle` \| `square` \| `rounded` \| `triangle` \| `hexagon` \| `diamond` |
| `size`           | int (px)      | `512`        | Clamped to 16..2048                              |
| `fg_color`       | color         | `#FFFFFF`    | Text / silhouette color                          |
| `bg_color`       | color         | `#4F8DFD`    | Background or gradient start                     |
| `bg_color_2`     | color / null  | `#8E5BFF`    | Gradient end (ignored when null)                 |
| `gradient_angle` | float (deg)   | `135`        | `0` = left→right, `90` = top→bottom              |
| `font_scale`     | float         | `0.55`       | Text height as fraction of icon size             |
| `corner_radius`  | float         | `0.22`       | Only for `shape='rounded'`                       |
| `border_width`   | float         | `0.0`        | Stroke width as fraction of size                 |
| `border_color`   | color         | `#FFFFFF`    | Stroke color                                     |
| `shadow`         | bool          | `false`      | Soft drop shadow behind silhouette               |

---

## Example calls

```jsonc
// generate_icon
{
  "out_path": "./out/app.png",
  "text": "Q",
  "style": "gradient",
  "shape": "rounded",
  "bg_color": "#4F8DFD",
  "bg_color_2": "#8E5BFF",
  "gradient_angle": 135,
  "size": 512
}
```

```jsonc
// generate_icon_set  → writes app_16.png … app_512.png + app.ico
{
  "out_dir": "./dist/icons",
  "base_name": "app",
  "text": "A",
  "style": "solid",
  "shape": "circle",
  "bg_color": "#FF7043",
  "include_ico": true,
  "png_sizes": [16, 32, 48, 64, 128, 256, 512]
}
```

---

## Local development

```bash
git clone <your-fork-url> icon-mcp-server
cd icon-mcp-server
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS / Linux:
# source .venv/bin/activate

pip install -e ".[dev]"
icon-mcp-server --self-test          # renders samples into ./_icon_self_test
python -m icon_mcp_server --version
```

---

## Publishing to PyPI

One-time setup:

```bash
pip install --upgrade build twine
```

Build & upload:

```bash
# 1. Bump version in pyproject.toml (and src/icon_mcp_server/__init__.py)
# 2. Clean previous artifacts
rmdir /s /q dist 2>nul || rm -rf dist

# 3. Build sdist + wheel
python -m build

# 4. Validate
twine check dist/*

# 5. Upload to TestPyPI first (recommended)
twine upload --repository testpypi dist/*

# 6. Smoke-test install from TestPyPI
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ icon-mcp-server
icon-mcp-server --self-test

# 7. Publish to real PyPI
twine upload dist/*
```

### Auth options for `twine`

- **API token** (recommended): set `TWINE_USERNAME=__token__` and
  `TWINE_PASSWORD=<your-pypi-token>`, or configure `~/.pypirc`:

  ```ini
  [distutils]
  index-servers = pypi, testpypi

  [pypi]
  username = __token__
  password = pypi-xxxxxxxxxxxxxxxxxxxxxxxx

  [testpypi]
  repository = https://test.pypi.org/legacy/
  username = __token__
  password = pypi-xxxxxxxxxxxxxxxxxxxxxxxx
  ```

- **Trusted Publisher (OIDC)** — no token needed. Configure it in the PyPI
  project settings and publish from a GitHub Actions workflow using
  `pypa/gh-action-pypi-publish`.

---

## License

MIT — see [LICENSE](./LICENSE).
