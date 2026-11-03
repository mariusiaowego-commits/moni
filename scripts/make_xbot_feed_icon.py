"""Generate the xbot_feed source icon (256×256 PNG).

Style follows the existing ``moni/web/static/icons/sources/*.png`` tiles:
a black rounded-square background with a glossy, gradient-filled glyph.

The glyph is a blue "X" (X/Twitter) whose lower-right arm ends in a gold
chevron — "X content, imported as a feed".

Usage::

    python scripts/make_xbot_feed_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SIZE = 256
SS = 4  # supersampling factor for smooth edges

OUT = (
    Path(__file__).resolve().parents[1]
    / "moni" / "web" / "static" / "icons" / "sources" / "xbot_feed.png"
)

# X cobalt blue (same family as xwatch.png's blue X) with a gold rim.
BLUE_HI = (86, 156, 232)
BLUE_LO = (20, 58, 116)
GOLD_HI = (247, 208, 96)
GOLD_LO = (176, 124, 26)


def _stroke_x(draw: ImageDraw.ImageDraw, cx: float, cy: float, span: float,
               width: float, color) -> None:
    """Draw an X as two thick diagonals with round caps."""
    half = span / 2
    a = span * 0.5
    for p0, p1 in (
        ((cx - half, cy - half), (cx + half, cy + half)),
        ((cx - half, cy + half), (cx + half, cy - half)),
    ):
        draw.line([p0, p1], fill=color, width=int(width))
        r = width / 2
        for px, py in (p0, p1):
            draw.ellipse((px - r, py - r, px + r, py + r), fill=color)
    del a


def _vertical_gradient(size: int, hi, lo, top: float, bottom: float) -> Image.Image:
    """Vertical gradient as an RGBA image of the given size."""
    strip = Image.new("RGB", (1, 64))
    px = strip.load()
    span = max(1e-6, bottom - top)
    for y in range(64):
        t = max(0.0, min(1.0, (y / 63 * size - top) / span))
        px[0, y] = tuple(int(hi[i] + (lo[i] - hi[i]) * t) for i in range(3))
    return strip.resize((size, size), Image.BILINEAR).convert("RGBA")


def build() -> Image.Image:
    big = SIZE * SS
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    # black rounded-square background
    draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=52 * SS,
                           fill=(0, 0, 0, 255))

    cx = cy = big / 2
    span = big * 0.56          # tip-to-tip extent of the X
    stroke = big * 0.175       # blue stroke width

    # gold rim: a wider X underneath the blue one
    _stroke_x(draw, cx, cy, span * 1.07, stroke + 9 * SS, GOLD_LO + (255,))

    # blue body, masked to the X strokes (so the gradient shows through)
    layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    layer_draw = ImageDraw.Draw(layer)
    _stroke_x(layer_draw, cx, cy, span, stroke, (255, 255, 255, 255))
    mask = layer.getchannel("A")

    body = _vertical_gradient(big, BLUE_HI, BLUE_LO, big * 0.18, big * 0.82)
    body.putalpha(mask)
    canvas.paste(body, (0, 0), body)

    # gold "feed" badge in the top-right corner: a disc with an inward arrow
    # ("X content, imported as a feed").
    draw = ImageDraw.Draw(canvas)
    bx, by = big * 0.775, big * 0.225
    br = big * 0.135
    draw.ellipse((bx - br, by - br, bx + br, by + br), fill=GOLD_LO + (255,))
    draw.ellipse(
        (bx - br + 3 * SS, by - br + 3 * SS, bx + br - 3 * SS, by + br - 3 * SS),
        fill=GOLD_HI + (255,),
    )
    # inward arrow (down into the feed)
    arrow_w = 5 * SS
    draw.line(
        [(bx, by - br * 0.55), (bx, by + br * 0.30)],
        fill=(24, 34, 56, 255), width=arrow_w,
    )
    draw.line(
        [(bx - br * 0.38, by + br * 0.02), (bx, by + br * 0.42),
         (bx + br * 0.38, by + br * 0.02)],
        fill=(24, 34, 56, 255), width=arrow_w, joint="curve",
    )

    return canvas.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    img = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, format="PNG")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
