#!/usr/bin/env python3
"""Compose the League of Agents Open Graph / link-preview card (1200x630 PNG).

This is a BUILD-TIME generator, not a runtime dependency: it renders
``league_site/web/assets/og.png`` once and that PNG is committed to the repo
and served verbatim by :mod:`league_site.web.shell` at ``/og.png``. Scrapers
(Slack, Twitter/X, Discord, iMessage, Facebook, LinkedIn) overwhelmingly
require a *raster* og:image — an SVG og:image fails on most platforms — so
the card is baked to PNG here rather than generated per request.

Neither Pillow nor fonttools is a project dependency; run this script with
its dependencies supplied ephemerally so nothing lands in pyproject.toml /
uv.lock (brotli is needed to decompress woff2)::

    uv run --with pillow --with fonttools --with brotli python scripts/generate-og-image.py

The site's vendored variable fonts ship as ``woff2`` (Fraunces for display,
Albert Sans for body); Pillow cannot read woff2, so this script decompresses
them to TrueType in a scratch dir via fonttools first, then draws with them —
so the card speaks in the same family voice as the site. The crossed-swords
mark is drawn as vector geometry (no font carries U+2694 reliably), scaled
from the same accent color.

Design: the dawn *night* background (#0b0f20), the crossed-swords mark and
"League of Agents" set in Fraunces, a wrapped tagline in Albert Sans, and a
single aurora-teal (#7fdcc9) horizon line with a soft glow above it. Minimal,
composed, generous margins — no clutter.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

# --- dawn night palette (mirrors league_site/web/theme.py dark tokens) ------
BG = (11, 15, 32)  # #0b0f20  --bg (dark)
INK = (233, 236, 248)  # #e9ecf8  --text (dark)
MUTED = (169, 176, 207)  # #a9b0cf  --text-muted (dark)
ACCENT = (127, 220, 201)  # #7fdcc9  --accent (dark)

WIDTH, HEIGHT = 1200, 630
MARGIN = 96

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FONTS_DIR = _REPO_ROOT / "league_site" / "web" / "assets" / "fonts"
_OUT = _REPO_ROOT / "league_site" / "web" / "assets" / "og.png"


def _load_font(woff2_name: str, size: int, scratch: Path, *, weight: int) -> ImageFont.FreeTypeFont:
    """Decompress a vendored ``woff2`` to ttf in *scratch* and load it at *size*.

    Variable fonts carry a weight axis; Pillow's FreeType binding honors a
    named-instance/default but not arbitrary axis values, so we set the ``wght``
    axis on the decompressed instance where fonttools supports it and otherwise
    accept the font's default instance (still the correct family voice).
    """
    ttf_path = scratch / (woff2_name.replace(".woff2", "") + f"-{weight}.ttf")
    font = TTFont(str(_FONTS_DIR / woff2_name))
    # Pin the weight axis so the static instance FreeType rasterizes is not the
    # thin default. fonttools' instancer keeps this a pure build step.
    try:
        from fontTools.varLib.instancer import instantiateVariableFont

        instantiateVariableFont(font, {"wght": weight}, inplace=True)
    except Exception:  # noqa: BLE001 - best-effort; fall back to the default instance
        pass
    font.save(str(ttf_path))
    return ImageFont.truetype(str(ttf_path), size)


def _draw_center_glow(
    img: Image.Image, cx: int, cy: int, radius: int, color: tuple[int, int, int], max_alpha: int
) -> None:
    """Paint a soft radial glow (concentric translucent rings) centered on (cx, cy)."""
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    rings = 48
    for i in range(rings, 0, -1):
        r = int(radius * i / rings)
        alpha = int(max_alpha * (1 - i / rings) ** 2)
        gdraw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color + (alpha,))
    img.alpha_composite(glow)


def _one_sword(color: tuple[int, int, int]) -> Image.Image:
    """A single up-pointing sword on a transparent 260x260 layer (hilt low, centered)."""
    layer = Image.new("RGBA", (260, 260), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx, fill = 130, color + (255,)
    # Blade: a slender tapered diamond, tip up.
    d.polygon([(cx, 30), (cx + 13, 168), (cx, 182), (cx - 13, 168)], fill=fill)
    # Crossguard: a horizontal rounded bar below the blade.
    d.rounded_rectangle((cx - 44, 176, cx + 44, 192), radius=8, fill=fill)
    # Grip + pommel.
    d.rounded_rectangle((cx - 6, 190, cx + 6, 224), radius=4, fill=fill)
    d.ellipse((cx - 11, 222, cx + 11, 244), fill=fill)
    return layer


def _crossed_swords(color: tuple[int, int, int], scale: float) -> Image.Image:
    """Two swords crossed in an X (blades crossing mid-length, tips up-out, hilts down-out)."""
    a = _one_sword(color).rotate(34, resample=Image.BICUBIC, center=(130, 118))
    b = _one_sword(color).rotate(-34, resample=Image.BICUBIC, center=(130, 118))
    mark = Image.new("RGBA", (260, 260), (0, 0, 0, 0))
    mark.alpha_composite(a)
    mark.alpha_composite(b)
    bbox = mark.getbbox()
    mark = mark.crop(bbox)
    w, h = mark.size
    return mark.resize((round(w * scale), round(h * scale)), resample=Image.LANCZOS)


def _wrap(
    text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw
) -> list[str]:
    """Greedy word-wrap *text* to lines no wider than *max_width* under *font*."""
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def main() -> None:
    img = Image.new("RGBA", (WIDTH, HEIGHT), BG + (255,))
    content_width = WIDTH - 2 * MARGIN

    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        fraunces_xl = _load_font("fraunces-var.woff2", 112, scratch, weight=600)
        albert = _load_font("albert-sans-var.woff2", 33, scratch, weight=400)
        albert_eyebrow = _load_font("albert-sans-var.woff2", 25, scratch, weight=600)

        # Horizon: a soft teal glow sitting on a crisp accent line, low in the
        # frame — "first light over the mesh".
        horizon_y = HEIGHT - 96
        _draw_center_glow(img, WIDTH // 2, horizon_y, 560, ACCENT, 64)

        draw = ImageDraw.Draw(img)
        draw.line((MARGIN, horizon_y, WIDTH - MARGIN, horizon_y), fill=ACCENT + (255,), width=3)

        # The crossed-swords mark, accent-colored, top-left.
        mark = _crossed_swords(ACCENT, 0.62)
        img.alpha_composite(mark, (MARGIN, 74))

        # Eyebrow, wordmark, tagline — a tidy left-aligned stack.
        draw.text(
            (MARGIN, 250),
            "THE ARENA",
            font=albert_eyebrow,
            fill=ACCENT + (255,),
            features=["-liga"],
        )
        draw.text((MARGIN, 288), "League of Agents", font=fraunces_xl, fill=INK + (255,))

        tagline = (
            "A turn-based arena where humans and AI agents " "play, compete, and get benchmarked."
        )
        y = 438
        for line in _wrap(tagline, albert, content_width, draw):
            draw.text((MARGIN, y), line, font=albert, fill=MUTED + (255,))
            y += 44

    img.convert("RGB").save(_OUT, "PNG", optimize=True)
    print(f"wrote {_OUT} ({_OUT.stat().st_size} bytes, {WIDTH}x{HEIGHT})")


if __name__ == "__main__":
    main()
