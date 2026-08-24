"""Extract demo showcase assets from the existing moni-web-showcase.html
so build_showcase_html.py can re-inject them on every rebuild.

Source: docs/moni-web-showcase.html L13382 inline <script type="application/json" id="deferred-artifacts">
Run: /usr/local/bin/python3 scripts/extract_showcase_assets.py
"""
import base64
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "moni-web-showcase.html"
OUT_DIR = ROOT / "scripts" / "showcase_assets"

with SRC.open() as f:
    html = f.read()
m = re.search(r'<script type="application/json" id="deferred-artifacts">(.+?)</script>', html, re.S)
data = json.loads(m.group(1))
OUT_DIR.mkdir(parents=True, exist_ok=True)

# info_graphic_png_data is "data:image/png;base64,XXX" — strip prefix then decode
img_b64 = data["info_graphic_png_data"]
prefix_match = re.match(r"^data:image/(png|webp|jpeg);base64,", img_b64)
if not prefix_match:
    raise SystemExit("unexpected info_graphic_png_data format")
img_bytes = base64.b64decode(img_b64[len(prefix_match.group(0)):])
img_path = OUT_DIR / "info_graphic.webp"
img_path.write_bytes(img_bytes)
print(f"wrote {img_path} ({len(img_bytes)} bytes)")

# html_landing is a raw HTML string
html_path = OUT_DIR / "html_landing.html"
html_path.write_text(data["html_landing"], encoding="utf-8")
print(f"wrote {html_path} ({len(data['html_landing'])} chars)")