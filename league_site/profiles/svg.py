"""Pure-string SVG builders: rating sparklines, og-image share cards, rank badges.

No SVG/image library dependency — every function here builds a well-formed
SVG document by string formatting alone (stdlib only, per this package's
constraint), and every builder is a pure function of its input: the same
:class:`~league_site.profiles.data.Profile` (plus, where relevant, the same
``rank``) always produces the exact same string, byte for byte — there is no
timestamp, random id, or other nondeterministic content embedded anywhere
below.

All user-controlled text (display name, model, provider) is passed through
:func:`_escape`/:func:`_escape_attr` before being interpolated — see
``tests/test_profiles_svg.py`` for the hostile-display-name regression case
(``<script>&"'``).

Palette
-------
The colors below are a literal mirror of the **dark**-scheme tokens
documented in ``league_site/web/theme.py`` (see that module's docstring for
the WCAG contrast rationale) — not an import, because SVG served to
third-party surfaces (Slack/Twitter/Discord link-preview fetchers, GitHub's
camo image proxy, a raw ``<img>`` in an HF model card) can't load our
stylesheet or resolve CSS custom properties, so every value has to be a
literal that travels with the document. This also makes the card/badge
"light/dark-agnostic": they render the same dark card everywhere rather than
guessing the consuming page's color scheme. Keep these in sync with
``league_site/web/theme.py`` by hand if that module's dark tokens change.
"""

from __future__ import annotations

import html
from collections.abc import Sequence

from league_site.profiles.data import Profile
from league_site.ratings.ledger import RatingEntry

# --- palette (mirrors league_site/web/theme.py's dark-scheme tokens) -------
_BG = "#12151a"
_SURFACE = "#1b1f27"
_TEXT = "#e6e9ef"
_TEXT_MUTED = "#a3adc2"
_BORDER = "#2b313d"
_ACCENT = "#ff8a3d"
_ACCENT_INK = "#14171c"

# --- fonts (mirror the *stacks*, not the exact CSS var, from theme.py's
# _FONT_MONO / _FONT_SANS — SVG text needs a font-family attribute, not a
# custom property) --------------------------------------------------------
_FONT_MONO_SVG = "ui-monospace, Menlo, Consolas, monospace"
_FONT_SANS_SVG = "-apple-system, Helvetica, Arial, sans-serif"

_GLYPH = "⚔"  # ⚔ CROSSED SWORDS — the wordmark glyph, see theme.py


def _escape(text: str) -> str:
    """Escape *text* for use as SVG element content (``&``, ``<``, ``>``).

    ``html.escape`` escapes exactly the characters XML element content
    requires (plus quotes, which are harmless — still valid, still
    round-trips — inside element content); using it here rather than
    :mod:`xml.sax.saxutils` avoids a stdlib module whose *parsing* side is a
    known XXE footgun, even though only its escaping helper would be used.
    """
    return html.escape(text, quote=False)


def _escape_attr(text: str) -> str:
    """Escape *text* for use inside a double-quoted SVG/XML attribute value."""
    return html.escape(text, quote=True)


# --- (a) rating sparkline ---------------------------------------------------


def rating_sparkline(
    history: Sequence[RatingEntry],
    *,
    width: int = 200,
    height: int = 48,
    stroke: str = _ACCENT,
) -> str:
    """A compact polyline sparkline of ``resulting_rating`` over *history*.

    Returns a single ``<g class="rating-sparkline">...</g>`` fragment — valid,
    well-formed, self-contained XML on its own (one root element), meant to be
    embedded inside a larger SVG (see :func:`share_card`) via a positioning
    ``<g transform="translate(...)">`` wrapper, or parsed/rendered standalone.

    An identity with no rating history yet renders a flat, dashed baseline at
    mid-height rather than an empty ``<g>``, so the shape is still meaningful
    ("no data yet") rather than a blank rectangle.
    """
    ratings = [entry.resulting_rating for entry in history]
    if not ratings:
        midpoint_y = height / 2
        return (
            '<g class="rating-sparkline">'
            f'<line x1="0" y1="{midpoint_y:.2f}" x2="{width:.2f}" y2="{midpoint_y:.2f}" '
            f'stroke="{stroke}" stroke-width="2" stroke-dasharray="4 4" stroke-linecap="round"/>'
            "</g>"
        )

    low = min(ratings)
    high = max(ratings)
    span = high - low or 1  # avoid divide-by-zero when every rating in history is equal
    count = len(ratings)
    step = width / (count - 1) if count > 1 else 0.0

    points = []
    for index, rating in enumerate(ratings):
        x = (index * step) if count > 1 else (width / 2)
        # A higher rating draws higher on the card, i.e. a *smaller* y.
        y = height - ((rating - low) / span) * height
        points.append(f"{x:.2f},{y:.2f}")
    polyline_points = " ".join(points)

    return (
        '<g class="rating-sparkline">'
        f'<polyline fill="none" stroke="{stroke}" stroke-width="2.5" '
        f'stroke-linejoin="round" stroke-linecap="round" points="{polyline_points}"/>'
        "</g>"
    )


# --- (b) og-image share card -------------------------------------------------

_CARD_WIDTH = 1200
_CARD_HEIGHT = 630
_CARD_MARGIN = 80
_CARD_SPARKLINE_HEIGHT = 140


def share_card(profile: Profile, *, rank: int | None = None) -> str:
    """A 1200x630 og-image share card SVG for *profile*.

    Fixed dimensions (the standard Open Graph / Twitter Card image size).
    Carries the wordmark, display name, rating, a model/provider line for
    agents, an optional rank, and a rating-history sparkline
    (:func:`rating_sparkline`) — every piece of *profile*-controlled text is
    escaped via :func:`_escape`.
    """
    name = _escape(profile.display_name)
    name_attr = _escape_attr(profile.display_name)
    rating_text = str(profile.rating)

    subtitle_parts = [profile.model, profile.provider] if profile.is_agent else []
    subtitle = " · ".join(_escape(part) for part in subtitle_parts if part)

    caption = "RATING"
    if rank is not None:
        caption = f"RATING · RANK #{rank}"

    sparkline_width = _CARD_WIDTH - 2 * _CARD_MARGIN
    sparkline = rating_sparkline(
        profile.history, width=sparkline_width, height=_CARD_SPARKLINE_HEIGHT
    )
    sparkline_y = _CARD_HEIGHT - _CARD_SPARKLINE_HEIGHT - 70
    panel_pad = 24

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_CARD_WIDTH}" height="{_CARD_HEIGHT}" '
        f'viewBox="0 0 {_CARD_WIDTH} {_CARD_HEIGHT}" role="img" '
        f'aria-label="{name_attr} — League of Agents rating card">',
        f'<rect width="{_CARD_WIDTH}" height="{_CARD_HEIGHT}" fill="{_BG}"/>',
        f'<rect x="0.5" y="0.5" width="{_CARD_WIDTH - 1}" height="{_CARD_HEIGHT - 1}" '
        f'fill="none" stroke="{_BORDER}" stroke-width="1"/>',
        f'<rect width="{_CARD_WIDTH}" height="6" fill="{_ACCENT}"/>',
        f'<rect x="{_CARD_MARGIN - panel_pad}" y="{sparkline_y - panel_pad}" '
        f'width="{sparkline_width + 2 * panel_pad}" '
        f'height="{_CARD_SPARKLINE_HEIGHT + 2 * panel_pad}" rx="12" fill="{_SURFACE}"/>',
        f'<text x="{_CARD_MARGIN}" y="108" font-family="{_FONT_MONO_SVG}" font-size="30" '
        f'font-weight="700" letter-spacing="4" fill="{_TEXT}">'
        f'<tspan fill="{_ACCENT}">{_GLYPH}</tspan> LEAGUE OF AGENTS</text>',
        f'<text x="{_CARD_MARGIN}" y="230" font-family="{_FONT_MONO_SVG}" font-size="72" '
        f'font-weight="700" fill="{_TEXT}">{name}</text>',
    ]
    if subtitle:
        parts.append(
            f'<text x="{_CARD_MARGIN}" y="276" font-family="{_FONT_SANS_SVG}" font-size="26" '
            f'fill="{_TEXT_MUTED}">{subtitle}</text>'
        )
    parts.append(
        f'<text x="{_CARD_MARGIN}" y="400" font-family="{_FONT_MONO_SVG}" font-size="104" '
        f'font-weight="700" fill="{_ACCENT}">{rating_text}</text>'
    )
    parts.append(
        f'<text x="{_CARD_MARGIN}" y="432" font-family="{_FONT_SANS_SVG}" font-size="20" '
        f'letter-spacing="2" fill="{_TEXT_MUTED}">{_escape(caption)}</text>'
    )
    parts.append(f'<g transform="translate({_CARD_MARGIN}, {sparkline_y})">{sparkline}</g>')
    parts.append("</svg>")
    return "".join(parts)


# --- (c) shields.io-style flat rank badge -----------------------------------

_BADGE_HEIGHT = 20
_BADGE_CHAR_WIDTH = 6.5
_BADGE_PADDING = 12
_BADGE_LABEL_BG = _ACCENT_INK
_BADGE_LABEL_TEXT = _TEXT
_BADGE_VALUE_BG = _ACCENT
_BADGE_VALUE_TEXT = _ACCENT_INK
_BADGE_DEFAULT_LABEL = "league of agents"


def _badge_segment_width(text: str) -> int:
    return round(max(len(text), 1) * _BADGE_CHAR_WIDTH + _BADGE_PADDING)


def rank_badge(profile: Profile, rank: int, *, label: str = _BADGE_DEFAULT_LABEL) -> str:
    """A shields.io-flavored flat badge SVG: ``"<label> | #<rank> · <rating>"``.

    Fixed 20px-tall two-segment badge (dark label segment, accent-colored
    value segment) sized to its own text so it drops cleanly into a GitHub
    README (``![](.../badge.svg)``) or an HF model card. *rank* is always
    present in the value segment's text.
    """
    value_text = f"#{rank} · {profile.rating}"
    label_text = label

    label_w = _badge_segment_width(label_text)
    value_w = _badge_segment_width(value_text)
    total_w = label_w + value_w

    label_esc = _escape(label_text)
    value_esc = _escape(value_text)
    aria_label = _escape_attr(f"{label_text}: {value_text}")

    return "".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="{_BADGE_HEIGHT}" '
            f'role="img" aria-label="{aria_label}">',
            f"<title>{label_esc}: {value_esc}</title>",
            '<g shape-rendering="crispEdges">',
            f'<rect width="{label_w}" height="{_BADGE_HEIGHT}" fill="{_BADGE_LABEL_BG}"/>',
            f'<rect x="{label_w}" width="{value_w}" height="{_BADGE_HEIGHT}" '
            f'fill="{_BADGE_VALUE_BG}"/>',
            "</g>",
            f'<g font-family="{_FONT_SANS_SVG}" font-size="11" text-anchor="middle">',
            f'<text x="{label_w / 2:.1f}" y="{_BADGE_HEIGHT / 2 + 4:.1f}" '
            f'fill="{_BADGE_LABEL_TEXT}">{label_esc}</text>',
            f'<text x="{label_w + value_w / 2:.1f}" y="{_BADGE_HEIGHT / 2 + 4:.1f}" '
            f'font-weight="700" fill="{_BADGE_VALUE_TEXT}">{value_esc}</text>',
            "</g>",
            "</svg>",
        ]
    )
