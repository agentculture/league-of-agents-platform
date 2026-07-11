"""The League of Agents design system — tokens and stylesheet.

``agentfront`` (see :mod:`agentfront.http_surface`) serves every registered
doc as raw markdown; it has no HTML/CSS rendering of its own. This module is
the platform's *only* stylesheet: :mod:`league_site.web.shell` serves its
:data:`STYLESHEET` at ``/theme.css`` and every shelled page links to it.

Design identity — a sibling of agentculture.org: "first light over the mesh"
-----------------------------------------------------------------------------
League of Agents is a turn-based arena for humans *and* agents, and it is a
member of the agentculture.org family. This pass (spec h1) adopts that
site's visual identity wholesale — the *dawn palette* (one scene at two
hours: light is ten minutes after sunrise, dark is the hour before it), the
Fraunces/Albert Sans type voice, and motion that moves at breathing pace.
Someone who knows agentculture.org, shown this site cold, should name the
kinship unprompted — in both color schemes. The league keeps exactly one
signature of its own on top of the family baseline: the turn-tick (the
wordmark glyph's discrete blink, the board's step-and-settle) — motion that
encodes turn-based play rather than continuous drift.

Token names are league's, values are the family's
    Consumers (the hero, the match viewer, profile pages) reference tokens
    by NAME (``--bg``, ``--surface``, ``--accent``, …), so the palette swap
    happens here once and every surface re-skins automatically. The dawn
    values map onto the existing names; the decorative sky-wash and mesh
    tokens are additive.

Type: the family voice, served first-party
    * Display (headings, the wordmark) → ``--font-display``: **Fraunces
      Variable**, the full variable file (it carries the SOFT/WONK axes),
      rendered with ``font-variation-settings: "SOFT" 75, "WONK" 0`` at
      gentle weights (h1 400, other headings ~440–500, wordmark 520).
    * Body → ``--font-body``: **Albert Sans Variable** at 1.0625rem /
      line-height 1.7, ``text-wrap: pretty`` on paragraphs and ``balance``
      on headings.
    * Code and the score/toggle glyphs → ``--font-mono`` (the OS monospace
      stack), unchanged.
    Both variable fonts are vendored in :mod:`league_site.web.fonts` (t3)
    and served at ``/fonts/*.woff2``; the ``@font-face`` rules below are
    the only place those URLs are consumed. ``font-display: swap`` keeps
    text readable on the system fallbacks while the files arrive.

Wordmark: pure CSS + one Unicode glyph
    A crossed-swords glyph (``⚔``, U+2694 CROSSED SWORDS — plain text, not
    a color emoji) in the accent color, followed by the site name set in
    Fraunces at weight 520. The old mono/uppercase/wide-tracking scoreboard
    styling is gone; the markup's own text remains uppercase until the
    shell's wordmark spans are re-authored (a shell.py change outside this
    module's ownership).

Palette: light AND dark via ``prefers-color-scheme``
    Every token below is defined once per scheme as a CSS custom property
    on ``:root`` (light, the default) and again under
    ``@media (prefers-color-scheme: dark)``. WCAG AA requires >= 4.5:1 for
    normal text and >= 3:1 for large text / non-text UI components
    (borders, focus rings). Ratios below were computed with the standard
    WCAG relative-luminance formula (``docs/`` has no build step that
    re-checks this — it is verified by hand and re-stated here so a future
    edit can be checked against the same numbers):

    Light scheme — dawn, ten minutes after sunrise
        --text (#232a4d)        on --bg        (#f4f5fb) -> 12.78:1
        --text (#232a4d)        on --surface   (#ffffff) -> 13.91:1
        --text (#232a4d)        on --surface-2 (#e9ebf5) -> 11.70:1
        --text-muted (#4d546f)  on --bg        (#f4f5fb) ->  6.86:1
        --text-muted (#4d546f)  on --surface   (#ffffff) ->  7.46:1
        --text-muted (#4d546f)  on --surface-2 (#e9ebf5) ->  6.28:1
        --accent (#0b655c)      on --bg        (#f4f5fb) ->  6.37:1
        --accent (#0b655c)      on --surface   (#ffffff) ->  6.93:1
        --accent-ink (#ffffff)  on --accent-strong (#0c5a53) -> 8.07:1  (buttons/badges)
        --accent-ink (#ffffff)  on --accent    (#0b655c) ->  6.93:1
        --border-strong (#767e9d) on --bg      (#f4f5fb) ->  3.68:1  (non-text UI, WCAG 1.4.11)

    Dark scheme — the hour before dawn
        --text (#e9ecf8)        on --bg        (#0b0f20) -> 16.15:1
        --text (#e9ecf8)        on --surface   (#161b36) -> 14.32:1
        --text (#e9ecf8)        on --surface-2 (#1b2140) -> 13.32:1
        --text-muted (#a9b0cf)  on --bg        (#0b0f20) ->  8.88:1
        --text-muted (#a9b0cf)  on --surface   (#161b36) ->  7.88:1
        --text-muted (#a9b0cf)  on --surface-2 (#1b2140) ->  7.32:1
        --accent (#7fdcc9)      on --bg        (#0b0f20) -> 11.77:1
        --accent (#7fdcc9)      on --surface   (#161b36) -> 10.44:1
        --accent-ink (#0b0f20)  on --accent    (#7fdcc9) -> 11.77:1  (buttons/badges)
        --border-strong (#5d6689) on --bg      (#0b0f20) ->  3.38:1  (non-text UI)

    ``--border`` / ``--border-soft`` (indigo/starlight hairlines at .14 /
    .08–.07 alpha, agentculture's ``--line`` / ``--line-soft``) are
    decorative dividers only (never carrying text or meaning on their own),
    so they are not held to the 3:1 non-text-contrast bar;
    ``--border-strong`` is the token used wherever a border *is* the only
    cue (focus rings, table rules) and it clears 3:1 in both schemes as
    shown above. ``--surface-2`` (#e9ebf5 light / #1b2140 dark) is derived
    from the dawn values — a slightly-lifted periwinkle for code blocks and
    table headers. The sky-wash tokens (``--sky-upper``, ``--sky-horizon``,
    ``--sky-mist``, ``--sky-glow``) and mesh tokens (``--mesh-node`` =
    the accent, ``--mesh-thread``, ``--mesh-halo``, ``--mesh-halo-alt``)
    are decorative only and never carry text alone. ``body::before``
    paints the page-wide dawn wash from the sky tokens — the single
    strongest family signal — behind all content (``z-index: -1``,
    ``pointer-events: none``; the page background lives on ``html`` so the
    negative z-index sits above it).

    Manual theme toggle: an explicit choice beats the OS
        The header toggle sets ``data-theme="dark"`` or
        ``data-theme="light"`` on ``<html>``; no attribute at all means "no
        explicit choice yet", which keeps first-visit behavior exactly as
        before (the OS decides via ``prefers-color-scheme``).
        ``:root[data-theme="dark"]`` carries the dark tokens
        unconditionally, so it wins even when the OS is light. The
        ``@media (prefers-color-scheme: dark)`` block is scoped to
        ``:root:not([data-theme="light"])`` so an explicit light choice
        keeps the OS's dark preference from clobbering it; that scoped
        selector still matches ``data-theme="dark"`` and the no-attribute
        case, which is exactly the desired fallthrough. ``color-scheme`` is
        set to match on every path (``dark`` under both the media-query
        block and ``[data-theme="dark"]``; ``light`` under
        ``[data-theme="light"]``) so native form controls and scrollbars
        follow the same decision as the rest of the palette. These
        mechanics predate the dawn palette (t2) and survive it unchanged.

Spacing, type scale, and rhythm
    An 8px-based spacing scale (``--space-1`` .. ``--space-8``) and a type
    scale from 0.875rem to 2.75rem keep rhythm consistent without a CSS
    framework; headings themselves size fluidly via ``clamp()``. Section
    breathing room comes from ``--section-pad: clamp(4.5rem, 10vh, 8rem)``
    (applied to ``main``), and corners round at the family radius
    ``--radius: 1.25rem`` (cards, pre blocks, the hero board) with a
    derived ``--radius-sm`` for inline code and pill (999px) forms for
    buttons, the toggle, and the skip link.

Desktop layout — a wide shell, one left rail (the desktop-arena pass)
    ``--max-width`` is the page *shell* (78rem), shared by header, main,
    and footer via ``.wrap`` with a fluid ``--gutter`` (1rem floor — so
    narrow viewports keep their exact previous padding — up to 3rem).
    Inside the shell, content zones do the desktop work instead of
    stretched text: every direct child of ``main.wrap`` anchors to the
    shell's left rail and prose keeps its readable ``--measure`` (46rem,
    the previous column, unchanged as a *reading* width), while the
    artifacts that earn width opt out — the hero scene and ``.table-wrap``
    tables (the leaderboard, profile histories) may run the whole shell,
    and ``pre`` blocks grow past the measure only as far as their content
    asks (``width: fit-content``). In the header, from 48rem up, an auto
    margin on the primary nav anchors the wordmark hard left and nav +
    theme toggle hard right; below that the row wraps exactly as before.
    The hero's self-centering ``--hero-w`` (sized for the old 46rem
    column) is overridden to span the shell so its copy column shares the
    rail and its board reaches toward the header's right anchor.

Performance budget (documented here per the design brief; renegotiated,
not abandoned, twice now — first for the dazzle pass (spec c11), again
for the sibling-of-agentculture.org pass (spec h1) — see the note below)
    * CSS: this stylesheet is still the *only* site-wide CSS — no
      framework, no per-page overrides — served under 32KB
      (:data:`CSS_BUDGET_BYTES`; measured by
      ``tests/test_web_theme_budget.py``; keep new rules within that band).
    * First-party JS: up to 16KB total (:data:`JS_BUDGET_BYTES`) across any
      inline pre-paint snippet the shell emits plus
      :mod:`league_site.web.scripts`'s ``SITE_JS`` served at ``/site.js``
      (also measured by ``tests/test_web_theme_budget.py``). Every byte of
      that allowance still has to earn its place.
    * Self-hosted fonts: up to 320KB total (:data:`FONT_BUDGET_BYTES`) for
      the two variable woff2 files — Fraunces Variable (display) and
      Albert Sans Variable (body) — per the sibling-of-agentculture.org
      spec's USER DECISION to adopt agentculture.org's type voice
      wholesale. t3 vendored them (~153KB combined, well inside the
      ceiling); this module's ``@font-face`` rules are what finally spend
      the allowance.
    * Total first-party asset weight (CSS + JS + FONTS) stays under 368KB
      (:data:`TOTAL_ASSET_BUDGET_BYTES`) — the sum of the three ceilings
      above, so a change to any one constant keeps this one honest too.
    * No external requests, before or after either renegotiation: no
      third-party webfont CDN, no other CDN, no third-party scripts, no
      images — CSS, JS, and fonts alike stay first-party, served by this
      platform. The wordmark glyph is a Unicode character, not an asset
      fetch, and both ``@font-face`` ``src`` URLs are same-origin
      ``/fonts/*`` paths.
    These are exactly the levers Lighthouse performance scores reward
    (payload size, request count, main-thread JS), which is why the budget
    is stated here next to the styles/scripts that have to stay inside it.

    Renegotiation note (dazzle pass, spec c11): the pre-dazzle-pass budget
    was CSS <= 10KB and *zero* JS — :mod:`league_site.web.shell` emitted no
    ``<script>`` tag at all, a baseline re-verified against this repo (not
    recalled from memory) before renegotiating it. The dazzle pass needed
    room for motion and a manual theme toggle, so CSS rose to 24KB and a
    JS allowance of 8KB was introduced, ahead of any dazzle code landing,
    with ``tests/test_web_theme_budget.py`` written/updated first to
    enforce the new numbers.

    Renegotiation note (sibling-of-agentculture.org pass, spec h1): the
    dazzle-pass budget (CSS <= 24KB, JS <= 8KB, no FONT allowance) is not
    enough room for the dawn-palette rework this pass brings (h1) plus the
    two self-hosted variable fonts USER-DECIDED for family alignment with
    agentculture.org — so CSS rose to 32KB, JS rose to 16KB, and a new 320KB
    FONT allowance was introduced, again ahead of the font files or the
    dawn-palette CSS landing, with this file's tests updated first (t2) to
    enforce the new numbers before t3 vendored the fonts and this task (t5)
    spent them. The budget evolved under negotiation both times — it was
    not quietly dropped either time.

Motion system: breathing pace, one turn-tick signature, quiet reveals
    The family feel (agentculture.org's global.css): nothing snaps —
    everything settles. Two easing tokens carry it: ``--ease-out``
    (``cubic-bezier(0.22, 1, 0.36, 1)`` — long, settling; reveals) and
    ``--ease-gentle`` (``cubic-bezier(0.45, 0, 0.25, 1)`` — breathing;
    hovers). On top of that baseline the league keeps its one signature:
    the wordmark glyph's discrete blink (turn-tick), and the hero board's
    step-and-settle (owned by :mod:`league_site.web.hero`).

    * ``--accent-glow``: the accent at low alpha — now a teal glow (light
      ``rgba(11, 101, 92, .18)``, dark ``rgba(127, 220, 201, .22)``) —
      defined in all three token blocks (``:root``,
      ``:root[data-theme="dark"]``, and the ``@media
      (prefers-color-scheme: dark)`` block), kept in sync the same way the
      rest of that block's tokens are. Used for hover glows only —
      decorative, never the sole cue.
    * ``.reveal`` / ``.revealed``: the scroll-reveal primitive. The hidden
      initial state (``opacity: 0``, ``transform: translateY(1.4rem)``)
      only applies under ``html[data-js]`` — set by the shell's pre-paint
      inline snippet before first paint — so an element is never hidden
      with JS off. ``.revealed`` (toggled by ``/site.js`` via
      IntersectionObserver — a class toggle only, no scroll-linked layout
      reads) settles it in over 0.9s on ``var(--ease-out)``. Staggerable
      per-element via ``transition-delay: var(--reveal-delay, 0s)`` —
      the 60ms-per-element stagger in :mod:`league_site.web.scripts` is
      unchanged.
    * Hover micro-interactions are gentle lifts on ``var(--ease-gentle)``
      over 0.4s: ``.card`` rises ``translateY(-4px)`` under
      ``var(--shadow-lift)``; ``.button`` rises ``translateY(-2px)`` with
      the lifted shadow plus an ``--accent-glow`` ring; the card's
      border-color change stays a static, unguarded rule (nav links and
      the wordmark were already color-only).
    * ``@view-transition { navigation: auto; }`` plus a ~180ms
      ``::view-transition-old(root)``/``::view-transition-new(root)``
      crossfade, nested inside the guard below so the browser never starts
      the view-transition machinery at all when motion is reduced —
      navigation becomes an instant swap, not a suppressed animation.
    * ``.wordmark-glyph`` keeps the "lit signal" pulse — a ``@keyframes``
      rule animating only ``opacity`` (1 <-> 0.75) with a long hold and a
      short dip, so it reads as a discrete blink (the turn-based rhythm)
      rather than a smooth fade cycle.

    The reduced-motion guarantee: every rule above — every ``transition:``,
    ``animation:``, ``@keyframes``, and the view-transition rules — lives
    inside one ``@media (prefers-reduced-motion: no-preference) { ... }``
    block near the end of :data:`STYLESHEET`. With reduced motion requested,
    none of it applies; static color/border-color hover cues still work.
    Nothing in this system animates a layout property (width, height,
    margin, padding, top/left/right/bottom) — only ``transform``/``opacity``
    continuously, plus ``box-shadow`` on hover-triggered transitions.
"""

from __future__ import annotations

_FONT_DISPLAY = '"Fraunces Variable", "Iowan Old Style", Georgia, serif'
_FONT_BODY = '"Albert Sans Variable", -apple-system, "Segoe UI", system-ui, sans-serif'
_FONT_MONO = (
    'ui-monospace, "SF Mono", "Cascadia Code", "Roboto Mono", Menlo, Consolas, '
    '"Liberation Mono", monospace'
)

# Kept in sync with the numbers documented in this module's docstring above —
# change a ceiling there and here together.
#
# Renegotiated twice now. Dazzle pass (spec c11): CSS 10KB -> 24KB, and a
# new first-party JS allowance (JS_BUDGET_BYTES) where there was previously
# none at all. Sibling-of-agentculture.org pass (spec h1, task t2):
# CSS 24KB -> 32KB, JS 8KB -> 16KB, and a new FONT_BUDGET_BYTES allowance
# (320KB, for the two self-hosted variable woff2 fonts t3 vendored and the
# @font-face rules below now consume). See the "Renegotiation note" entries
# at the end of the docstring's Performance budget section for why each
# change happened — every ceiling here has been deliberately evolved, never
# quietly dropped.
CSS_BUDGET_BYTES = 32 * 1024

#: First-party JS ceiling in bytes — covers any inline pre-paint snippet
#: emitted by :mod:`league_site.web.shell` plus
#: :mod:`league_site.web.scripts`'s ``SITE_JS`` served at ``/site.js``,
#: combined. Zero before the dazzle-pass renegotiation; see
#: :data:`CSS_BUDGET_BYTES`'s comment above.
JS_BUDGET_BYTES = 16 * 1024

#: Self-hosted font ceiling in bytes — spent on the two variable woff2
#: files (Fraunces Variable + Albert Sans Variable) t3 vendored and serves
#: first-party at ``/fonts/*`` (~153KB combined), consumed by the
#: ``@font-face`` rules in :data:`STYLESHEET`. Zero before the spec-h1
#: renegotiation (fonts were 100% system stacks); see
#: :data:`CSS_BUDGET_BYTES`'s comment above.
FONT_BUDGET_BYTES = 320 * 1024

#: Combined first-party asset weight ceiling (CSS + JS + FONTS). Derived
#: from the three ceilings above rather than restated, so it can never
#: drift out of sync with them.
TOTAL_ASSET_BUDGET_BYTES = CSS_BUDGET_BYTES + JS_BUDGET_BYTES + FONT_BUDGET_BYTES

#: The dark palette — the hour before dawn — written ONCE and interpolated
#: into both selectors that need it (:root[data-theme="dark"] and the
#: prefers-color-scheme media block). Plain CSS cannot reuse a
#: custom-property block across selectors, but this file is Python —
#: generating both blocks from one constant makes drift between
#: explicit-choice dark and OS-default dark structurally impossible instead
#: of comment-policed. ``--mesh-node`` is absent on purpose: it is defined
#: once on :root as ``var(--accent)`` and re-skins with the accent.
_DARK_TOKENS = """\
  color-scheme: dark;

  --bg: #0b0f20;
  --surface: #161b36;
  --surface-2: #1b2140;
  --text: #e9ecf8;
  --text-muted: #a9b0cf;
  --border: rgba(233, 236, 248, .14);
  --border-soft: rgba(233, 236, 248, .07);
  --border-strong: #5d6689;
  --accent: #7fdcc9;
  --accent-strong: #7fdcc9;
  --accent-ink: #0b0f20;
  --accent-glow: rgba(127, 220, 201, .22);

  --sky-upper: rgba(64, 92, 181, .25);
  --sky-horizon: rgba(255, 159, 102, .1);
  --sky-mist: rgba(88, 214, 181, .14);
  --sky-glow: rgba(96, 226, 189, .14);

  --mesh-thread: rgba(233, 236, 248, .28);
  --mesh-halo: rgba(96, 226, 189, .4);
  --mesh-halo-alt: rgba(255, 170, 120, .28);

  --shadow: 0 4px 24px rgba(2, 4, 12, .5), 0 1px 4px rgba(2, 4, 12, .4);
  --shadow-lift: 0 14px 40px rgba(2, 4, 12, .6), 0 2px 8px rgba(2, 4, 12, .4);"""

STYLESHEET = f"""\
/* League of Agents — design tokens + stylesheet. See league_site/web/theme.py
   for the palette rationale and the WCAG contrast ratios these tokens hold.
   The identity is agentculture.org's dawn — "first light over the mesh" —
   worn by the arena: same sky, same type voice, plus the league's own
   turn-tick. */

@font-face {{
  font-family: "Fraunces Variable";
  font-style: normal;
  font-weight: 100 900;
  font-display: swap;
  src: url(/fonts/fraunces-var.woff2) format("woff2-variations");
}}

@font-face {{
  font-family: "Albert Sans Variable";
  font-style: normal;
  font-weight: 100 900;
  font-display: swap;
  src: url(/fonts/albert-sans-var.woff2) format("woff2-variations");
}}

:root {{
  color-scheme: light dark;

  /* dawn, ten minutes after sunrise */
  --bg: #f4f5fb;
  --surface: #ffffff;
  --surface-2: #e9ebf5;
  --text: #232a4d;
  --text-muted: #4d546f;
  --border: rgba(35, 42, 77, .14);
  --border-soft: rgba(35, 42, 77, .08);
  --border-strong: #767e9d;
  --accent: #0b655c;
  --accent-strong: #0c5a53;
  --accent-ink: #ffffff;
  --accent-glow: rgba(11, 101, 92, .18);
  --link: var(--accent);

  /* sky washes (decorative only — never carry text alone) */
  --sky-upper: rgba(198, 210, 248, .55);
  --sky-horizon: rgba(255, 205, 166, .5);
  --sky-mist: rgba(167, 216, 205, .35);
  --sky-glow: rgba(255, 178, 125, .5);

  /* the mesh */
  --mesh-node: var(--accent);
  --mesh-thread: rgba(35, 42, 77, .32);
  --mesh-halo: rgba(255, 170, 110, .55);
  --mesh-halo-alt: rgba(122, 158, 245, .5);

  --shadow: 0 4px 24px rgba(35, 42, 77, .07), 0 1px 4px rgba(35, 42, 77, .05);
  --shadow-lift: 0 14px 40px rgba(35, 42, 77, .12), 0 2px 8px rgba(35, 42, 77, .06);

  --font-display: {_FONT_DISPLAY};
  --font-body: {_FONT_BODY};
  --font-mono: {_FONT_MONO};

  /* motion */
  --ease-out: cubic-bezier(0.22, 1, 0.36, 1);
  --ease-gentle: cubic-bezier(0.45, 0, 0.25, 1);

  --space-1: 0.25rem;
  --space-2: 0.5rem;
  --space-3: 0.75rem;
  --space-4: 1rem;
  --space-5: 1.5rem;
  --space-6: 2rem;
  --space-7: 3rem;
  --space-8: 4rem;

  --text-sm: 0.875rem;
  --text-base: 1rem;
  --text-md: 1.125rem;
  --text-lg: 1.5rem;
  --text-xl: 2rem;
  --text-2xl: 2.75rem;

  /* rhythm — a wide desktop shell (--max-width) with a readable prose
     measure (--measure) anchored to its left rail; see the content-zone
     rules under main.wrap below. --gutter floors at --space-4 so narrow
     viewports keep their exact pre-desktop-pass padding. */
  --section-pad: clamp(4.5rem, 10vh, 8rem);
  --radius: 1.25rem;
  --radius-sm: 0.5rem;
  --max-width: 78rem;
  --measure: 46rem;
  --gutter: clamp(var(--space-4), 4vw, var(--space-7));
}}

/* Explicit visitor choice: data-theme="dark"/"light" on <html>, set by the
   header toggle (league_site/web/shell.py + scripts.py). An explicit choice
   always beats the OS. No attribute at all (first visit) falls through to
   the @media (prefers-color-scheme: dark) block below, which is the only
   place the OS decides. */
:root[data-theme="dark"] {{
{_DARK_TOKENS}
}}

:root[data-theme="light"] {{
  color-scheme: light;
}}

/* OS default: only applies when the visitor has not explicitly picked
   light (:not([data-theme="light"]) also matches data-theme="dark" and no
   attribute at all — harmless in the first case since the values are the
   SAME interpolated _DARK_TOKENS constant as :root[data-theme="dark"]
   above, and exactly the desired first-visit behavior in the second). */
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
{_DARK_TOKENS}
  }}
}}

* {{ box-sizing: border-box; }}

html {{
  color-scheme: light dark;
  background: var(--bg);
}}

body {{
  margin: 0;
  position: relative;
  background: transparent;
  color: var(--text);
  font-family: var(--font-body);
  font-size: 1.0625rem;
  line-height: 1.7;
  -webkit-text-size-adjust: 100%;
  text-rendering: optimizeLegibility;
  -webkit-font-smoothing: antialiased;
}}

/* The high sky belongs to the page: every page opens under the same dawn
   and scrolls up out of it into plain air. Decorative only — behind all
   content (the page background lives on html so the negative z-index sits
   above it), inert to pointers, invisible to assistive tech. */
body::before {{
  content: "";
  position: absolute;
  inset: 0 0 auto 0;
  height: min(100%, 115vh, 60rem);
  z-index: -1;
  pointer-events: none;
  background:
    radial-gradient(95% 70% at 12% -12%, var(--sky-upper), transparent 58%),
    radial-gradient(85% 62% at 88% -6%, var(--sky-mist), transparent 60%);
}}

.skip-link {{
  position: absolute;
  left: -999px;
  top: auto;
  background: var(--accent-strong);
  color: var(--accent-ink);
  padding: var(--space-2) var(--space-4);
  border-radius: 999px;
  z-index: 10;
}}
.skip-link:focus {{
  left: var(--space-4);
  top: var(--space-4);
}}

a {{
  color: var(--link);
  text-decoration-color: color-mix(in srgb, var(--accent) 40%, transparent);
  text-decoration-thickness: 1px;
  text-underline-offset: 0.2em;
}}
a:hover, a:focus-visible {{ text-decoration-color: var(--accent); }}
:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 3px; }}
::selection {{ background: color-mix(in srgb, var(--accent) 22%, var(--bg)); }}

.wrap {{
  max-width: var(--max-width);
  margin: 0 auto;
  padding: 0 var(--gutter);
}}

.site-header {{
  border-bottom: 1px solid var(--border);
  padding: var(--space-4) 0;
}}
.site-header .wrap {{
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}}

.wordmark {{
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-family: var(--font-display);
  font-variation-settings: "SOFT" 75, "WONK" 0;
  font-weight: 520;
  letter-spacing: 0.02em;
  color: var(--text);
  text-decoration: none;
  font-size: var(--text-md);
}}
.wordmark:hover, .wordmark:focus-visible {{ color: var(--accent); }}
.wordmark-glyph {{ color: var(--accent); font-size: 1.2em; }}
.wordmark-accent {{ color: var(--accent); margin-left: 0.35em; }}

nav[aria-label="Primary"] {{
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-4);
  font-size: 0.95rem;
  font-weight: 500;
}}
nav[aria-label="Primary"] a {{ color: var(--text-muted); text-decoration: none; }}
nav[aria-label="Primary"] a:hover,
nav[aria-label="Primary"] a:focus-visible {{ color: var(--accent); }}

/* Sign-in / signed-in account entry — sits between the nav and the theme
   toggle, so at desktop widths it rides the same auto-margin group to the
   header's right edge. Only shared design tokens, so both palettes hold. */
.header-account {{
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  font-size: 0.95rem;
  font-weight: 500;
}}
.account-name {{
  color: var(--text);
  max-width: 16ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}}
.header-auth {{
  color: var(--text-muted);
  text-decoration: none;
  font-size: 0.95rem;
  font-weight: 500;
}}
.header-auth:hover, .header-auth:focus-visible {{ color: var(--accent); }}

/* One row up from mobile the header stops floating mid-strip: the
   wordmark anchors hard left, and the auto margin sends nav + theme
   toggle hard right, to the shell's edges. Below this the row wraps and
   space-between lays the wrapped rows out exactly as before. */
@media (min-width: 48rem) {{
  nav[aria-label="Primary"] {{ margin-left: auto; gap: var(--space-5); }}
  .site-header .wrap {{ gap: var(--space-4); }}
}}

/* Every page shell renders <main id="main" class="wrap">; the compound
   selector outranks .wrap's padding shorthand (a bare `main` rule would
   lose that cascade and the rhythm would silently never apply). */
main.wrap {{
  padding-block: var(--section-pad);
}}
main > .wrap {{ padding: 0; }}

/* Content zones inside the wide shell. Everything shares one hard left
   rail — the wordmark's own edge — so the page reads as architecture,
   not a centered strip. Prose keeps its readable measure; the artifacts
   that earn width take it: the hero scene and tables (the leaderboard, a
   profile's match history) may run the whole shell, and code blocks grow
   past the measure only when their content does. */
main.wrap > * {{ max-width: var(--measure); }}
main.wrap > .hero, main.wrap > .table-wrap {{ max-width: none; }}
main.wrap > pre {{
  width: fit-content;
  min-width: min(var(--measure), 100%);
  max-width: 100%; /* fit-content alone would trace an unwrappable long
    line past the shell (a pre's min-content is its longest line); the cap
    hands anything longer back to overflow-x. */
}}

/* The hero spans the shell (its own --hero-w self-centering was sized
   for the old narrow column) so its copy column starts on the shared
   left rail and the board's right edge lands exactly on the header's
   right anchor — the board's own >=78rem pop-out margin compensated for
   the narrow column and is retired with it. The wider column gap only
   applies at desktop widths — below that the hero's own gap rules
   stand. */
main.wrap > .hero {{ --hero-w: 100%; }}
main.wrap > .hero .hero-board {{ margin-right: 0; }}
@media (min-width: 64rem) {{
  main.wrap > .hero {{ column-gap: clamp(var(--space-7), 6vw, 5rem); }}
}}

h1, h2, h3, h4, h5, h6 {{
  font-family: var(--font-display);
  font-optical-sizing: auto;
  font-variation-settings: "SOFT" 75, "WONK" 0;
  font-weight: 440;
  line-height: 1.14;
  letter-spacing: -0.012em;
  text-wrap: balance;
  margin-top: var(--space-7);
  margin-bottom: var(--space-3);
}}
h1 {{
  font-size: clamp(2.4rem, 5.5vw, 3.6rem);
  font-weight: 400;
  margin-top: 0;
}}
h2 {{ font-size: clamp(1.6rem, 3vw, 2.1rem); }}
h3 {{ font-size: 1.25rem; font-weight: 500; }}
h4, h5, h6 {{ font-size: var(--text-md); font-weight: 500; }}

p, ul, ol, table, blockquote, pre {{ margin: 0 0 var(--space-4) 0; }}
p {{ text-wrap: pretty; }}

ul, ol {{ padding-left: var(--space-6); }}
li {{ margin-bottom: var(--space-2); }}
li > ul, li > ol {{ margin-top: var(--space-2); }}

blockquote {{
  border-left: 3px solid var(--accent);
  margin-left: 0;
  padding: var(--space-1) var(--space-4);
  color: var(--text-muted);
}}

code {{
  font-family: var(--font-mono);
  font-size: 0.85em;
  letter-spacing: 0.01em;
  background: var(--surface-2);
  border-radius: var(--radius-sm);
  padding: 0.1em 0.35em;
}}
pre {{
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--space-4);
  overflow-x: auto;
}}
pre code {{ background: none; padding: 0; }}

hr {{
  border: none;
  border-top: 1px solid var(--border);
  margin: var(--space-6) 0;
}}

.table-wrap {{ overflow-x: auto; margin: 0 0 var(--space-4) 0; }}
table {{ border-collapse: collapse; width: 100%; margin: 0; font-size: var(--text-sm); }}
th, td {{
  border: 1px solid var(--border);
  padding: var(--space-2) var(--space-3);
  text-align: left;
  vertical-align: top;
}}
th {{ font-weight: 600; background: var(--surface-2); }}

.card {{
  background: var(--surface);
  border: 1px solid var(--border-soft);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: var(--space-5);
  margin-bottom: var(--space-4);
}}
.card:hover {{ border-color: var(--border-strong); }}

.button {{
  display: inline-block;
  background: var(--accent-strong);
  color: var(--accent-ink);
  font-weight: 600;
  letter-spacing: 0.01em;
  text-decoration: none;
  border-radius: 999px;
  padding: var(--space-3) var(--space-5);
}}
.button:hover, .button:focus-visible {{ text-decoration: none; filter: brightness(1.08); }}

/* --- Play surface (t9): the start-a-match and your-move forms ---
   /play's pages reach these rules the same two ways the viewer's do: the
   standalone page shell (league_site.viewer.wsgi.page_shell) inlines this
   whole stylesheet, and shelled pages load it as /theme.css. `.button` was
   authored for <a>; a real <button class="button"> (the forms' submit)
   needs the element defaults neutralized to render identically. The select
   keeps the pill vocabulary (999px) the buttons/toggle already speak; the
   global :focus-visible rule above covers its focus ring. */
button.button {{
  border: 0;
  cursor: pointer;
  font: inherit;
  font-weight: 600;
}}
.play-form {{
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  margin: 0;
}}
.play-form label {{
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  color: var(--text-muted);
}}
.play-form select {{
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  color: var(--text);
  background: var(--surface-2);
  border: 1px solid var(--border-strong);
  border-radius: 999px;
  padding: var(--space-2) var(--space-3);
  max-width: 100%;
}}
.play-match-list {{ list-style: none; padding-left: 0; margin: 0 0 var(--space-4) 0; }}
.play-match-list li {{ margin: 0 0 var(--space-2) 0; }}

.theme-toggle {{
  background: none;
  border: 1px solid var(--border-strong);
  border-radius: 999px;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: var(--text-base);
  line-height: 1;
  padding: var(--space-1) var(--space-2);
  cursor: pointer;
}}
.theme-toggle:hover, .theme-toggle:focus-visible {{
  color: var(--accent);
  border-color: var(--accent);
}}

.site-footer {{
  border-top: 1px solid var(--border);
  padding: var(--space-6) 0;
  color: var(--text-muted);
  font-size: var(--text-sm);
}}
.site-footer:empty, .site-footer .wrap:empty {{ display: none; }}

@media (max-width: 40rem) {{
  main.wrap {{ padding-block: var(--space-7); }}
}}

/* ==========================================================================
   Motion system — reveals, micro-interactions, view transitions, at the
   family's breathing pace (t4 structure, t5 voice). Every animated rule in
   this file lives inside this single guard: with reduced motion requested,
   none of it applies, and nothing above needed a fallback because none of
   it ever hides content on its own — see this module's docstring ("Motion
   system" section) for the full contract.
   ========================================================================== */
@media (prefers-reduced-motion: no-preference) {{

  /* --- Reveal primitives: quiet scroll reveals, gated on JS + motion ---
     The shell's pre-paint inline snippet sets html[data-js] before first
     paint; /site.js adds the .revealed class to .reveal elements via
     IntersectionObserver (a class toggle only — no scroll-linked layout
     reads here). Without html[data-js] (JS disabled/blocked) or with
     reduced motion, no rule below applies, so .reveal elements simply keep
     the browser default (fully visible) — content is never hidden behind
     JS or motion preference. 0.9s on the long settling curve; the 60ms
     stagger lives in scripts.py, unchanged. */
  html[data-js] .reveal {{
    opacity: 0;
    transform: translateY(1.4rem);
    transition: opacity 0.9s var(--ease-out),
      transform 0.9s var(--ease-out);
    transition-delay: var(--reveal-delay, 0s);
  }}
  html[data-js] .reveal.revealed {{
    opacity: 1;
    transform: translateY(0);
  }}

  /* --- Micro-interactions: gentle lifts, nothing snaps ---
     Color-only hover cues (nav links, the wordmark, .card's border-color
     above) already work without JS or motion and stay outside this guard;
     only the transform/box-shadow motion lives here. */
  .button {{
    transition: transform 0.4s var(--ease-gentle), box-shadow 0.4s var(--ease-gentle);
  }}
  .button:hover, .button:focus-visible {{
    transform: translateY(-2px);
    box-shadow: var(--shadow-lift), 0 0 0 5px var(--accent-glow);
  }}
  .card {{
    transition: transform 0.4s var(--ease-gentle), box-shadow 0.4s var(--ease-gentle);
  }}
  .card:hover {{
    transform: translateY(-4px);
    box-shadow: var(--shadow-lift);
  }}
  .theme-toggle {{
    transition: box-shadow 0.4s var(--ease-gentle);
  }}
  .theme-toggle:hover, .theme-toggle:focus-visible {{
    box-shadow: 0 0 0 4px var(--accent-glow);
  }}

  /* --- View transitions: a gentle crossfade, nested so reduced motion
     means instant navigation, not a suppressed animation. Nesting the
     @view-transition rule itself inside this guard means the browser never
     starts the view-transition machinery at all when motion is reduced —
     navigation is a plain, instant page swap. */
  @view-transition {{
    navigation: auto;
  }}
  ::view-transition-old(root),
  ::view-transition-new(root) {{
    animation-duration: 180ms;
  }}

  /* --- Wordmark glyph: the league's turn-tick atop the family baseline —
     a long hold followed by a short dip reads as a discrete blink (the
     turn-based rhythm), not a continuous glow cycle. */
  @keyframes wordmark-pulse {{
    0%, 88% {{ opacity: 1; }}
    94% {{ opacity: 0.75; }}
    100% {{ opacity: 1; }}
  }}
  .wordmark-glyph {{
    animation: wordmark-pulse 4s ease-in-out infinite;
  }}
}}
"""
