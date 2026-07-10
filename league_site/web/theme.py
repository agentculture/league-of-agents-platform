"""The League of Agents design system — tokens and stylesheet.

``agentfront`` (see :mod:`agentfront.http_surface`) serves every registered
doc as raw markdown; it has no HTML/CSS rendering of its own. This module is
the platform's *only* stylesheet: :mod:`league_site.web.shell` serves its
:data:`STYLESHEET` at ``/theme.css`` and every shelled page links to it.

Design identity — "arena / strategy", not framework defaults
--------------------------------------------------------------
League of Agents is a turn-based arena for humans *and* agents: matches are
benchmarks as much as they are games. The visual language leans on that —
a scoreboard/terminal feel for structure (headings, wordmark, code) paired
with a calm, readable sans-serif for prose, plus one committed accent color
(a "flare" orange, evoking a marked square on a game board / a lit signal)
rather than a generic blue-link template look.

Type: a deliberate monospace-accented pairing
    * Body text, links, list/table content → ``--font-sans`` (the OS UI
      stack) for comfortable long-form reading.
    * Headings, the wordmark, and code → ``--font-mono`` (the OS monospace
      stack) for a "scoreboard/benchmark" identity — no font is downloaded;
      both stacks resolve entirely to fonts already on the device.

Wordmark: pure CSS + one Unicode glyph
    A crossed-swords glyph (``⚔``, U+2694 CROSSED SWORDS — plain
    text, not a color emoji) in the accent color, followed by
    "LEAGUE OF AGENTS" set in the monospace stack, uppercase, wide
    letter-spacing. No image, no icon font, no SVG asset.

Palette: light AND dark via ``prefers-color-scheme``
    Every token below is defined once per scheme as a CSS custom property
    on ``:root`` (light, the default) and again under
    ``@media (prefers-color-scheme: dark)``. WCAG AA requires >= 4.5:1 for
    normal text and >= 3:1 for large text / non-text UI components
    (borders, focus rings). Ratios below were computed with the standard
    WCAG relative-luminance formula (``docs/`` has no build step that
    re-checks this — it is verified by hand and re-stated here so a future
    edit can be checked against the same numbers):

    Light scheme
        --text (#14171c)        on --bg      (#f6f7f9)  -> 16.76:1
        --text (#14171c)        on --surface (#ffffff)  -> 17.96:1
        --text-muted (#475569)  on --bg      (#f6f7f9)  ->  7.07:1
        --text-muted (#475569)  on --surface (#ffffff)  ->  7.58:1
        --accent (#c2410c)      on --surface (#ffffff)  ->  5.18:1
        --accent (#c2410c)      on --bg      (#f6f7f9)  ->  4.83:1
        --accent-ink (#ffffff)  on --accent  (#c2410c)  ->  5.18:1  (buttons/badges)
        --border-strong (#828a9a) on --bg    (#f6f7f9)  ->  3.24:1  (non-text UI, WCAG 1.4.11)

    Dark scheme
        --text (#e6e9ef)        on --bg      (#12151a)  -> 15.04:1
        --text (#e6e9ef)        on --surface (#1b1f27)  -> 13.58:1
        --text-muted (#a3adc2)  on --bg      (#12151a)  ->  8.11:1
        --text-muted (#a3adc2)  on --surface (#1b1f27)  ->  7.32:1
        --accent (#ff8a3d)      on --bg      (#12151a)  ->  7.80:1
        --accent (#ff8a3d)      on --surface (#1b1f27)  ->  7.04:1
        --accent-ink (#14171c)  on --accent  (#ff8a3d)  ->  7.66:1  (buttons/badges)
        --border-strong (#5b6478) on --bg    (#12151a)  ->  3.08:1  (non-text UI)

    ``--border`` (light ``#d8dce3``, dark ``#2b313d``) is a decorative
    hairline divider only (never carries text or meaning on its own), so it
    is not held to the 3:1 non-text-contrast bar; ``--border-strong`` is the
    token used wherever a border *is* the only cue (focus rings, table
    rules) and it clears 3:1 in both schemes as shown above.

Spacing and type scale
    An 8px-based spacing scale (``--space-1`` .. ``--space-8``) and a type
    scale from 0.875rem to 2.75rem keep rhythm consistent without a CSS
    framework.

Performance budget (documented here per the design brief; renegotiated,
not abandoned, for the dazzle pass — spec c11 — see the note below)
    * CSS: this stylesheet is still the *only* CSS on the site — no
      framework, no per-page overrides — served under 24KB
      (:data:`CSS_BUDGET_BYTES`; measured by
      ``tests/test_web_theme_budget.py``; keep new rules within that band).
    * First-party JS: up to 8KB total (:data:`JS_BUDGET_BYTES`) across any
      inline pre-paint snippet the shell emits plus
      :mod:`league_site.web.scripts`'s ``SITE_JS`` served at ``/site.js``
      (also measured by ``tests/test_web_theme_budget.py``, which
      auto-activates that check once :mod:`league_site.web.scripts`
      exists). Every byte of that allowance still has to earn its place.
    * Total first-party asset weight (CSS + JS) stays under 32KB
      (:data:`TOTAL_ASSET_BUDGET_BYTES`) — the sum of the two ceilings
      above, so a change to either constant keeps this one honest too.
    * No external requests, before or after the renegotiation: no
      webfonts, no CDN, no third-party scripts, no images — CSS and JS
      alike stay first-party, served by this platform. The wordmark glyph
      is a Unicode character, not an asset fetch.
    * Fonts are 100% system stacks (:data:`_FONT_SANS`, :data:`_FONT_MONO`)
      so there is no font-download cost and no flash-of-unstyled-text.
    These are exactly the levers Lighthouse performance scores reward
    (payload size, request count, main-thread JS), which is why the budget
    is stated here next to the styles/scripts that have to stay inside it.

    Renegotiation note: the pre-dazzle-pass budget was CSS <= 10KB and
    *zero* JS — :mod:`league_site.web.shell` emitted no ``<script>`` tag
    at all, a baseline re-verified against this repo (not recalled from
    memory) before renegotiating it. The dazzle pass (this module's spec
    calls it out as requirement c11) needs room for motion and a manual
    theme toggle, so the ceilings above were deliberately raised ahead of
    any dazzle code landing, with ``tests/test_web_theme_budget.py``
    written/updated first to enforce the new numbers — the budget evolved
    under negotiation, it was not quietly dropped.
"""

from __future__ import annotations

_FONT_SANS = (
    '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, '
    'sans-serif, "Apple Color Emoji", "Segoe UI Emoji"'
)
_FONT_MONO = (
    'ui-monospace, "SF Mono", "Cascadia Code", "Roboto Mono", Menlo, Consolas, '
    '"Liberation Mono", monospace'
)

# Kept in sync with the numbers documented in this module's docstring above —
# change a ceiling there and here together.
#
# Renegotiated for the dazzle pass (spec c11): CSS 10KB -> 24KB, and a new
# first-party JS allowance (JS_BUDGET_BYTES) where there was previously
# none at all. See the "Renegotiation note" at the end of the docstring's
# Performance budget section for why — the old zero-JS budget was
# deliberately evolved, not abandoned.
CSS_BUDGET_BYTES = 24 * 1024

#: First-party JS ceiling in bytes — covers any inline pre-paint snippet
#: emitted by :mod:`league_site.web.shell` plus
#: :mod:`league_site.web.scripts`'s ``SITE_JS`` served at ``/site.js``,
#: combined. Zero before this renegotiation; see :data:`CSS_BUDGET_BYTES`'s
#: comment above.
JS_BUDGET_BYTES = 8 * 1024

#: Combined first-party asset weight ceiling (CSS + JS). Derived from the
#: two ceilings above rather than restated, so it can never drift out of
#: sync with them.
TOTAL_ASSET_BUDGET_BYTES = CSS_BUDGET_BYTES + JS_BUDGET_BYTES

STYLESHEET = f"""\
/* League of Agents — design tokens + stylesheet. See league_site/web/theme.py
   for the palette rationale and the WCAG contrast ratios these tokens hold. */

:root {{
  color-scheme: light dark;

  --bg: #f6f7f9;
  --surface: #ffffff;
  --surface-2: #eef0f4;
  --text: #14171c;
  --text-muted: #475569;
  --border: #d8dce3;
  --border-strong: #828a9a;
  --accent: #c2410c;
  --accent-ink: #ffffff;
  --link: var(--accent);

  --font-sans: {_FONT_SANS};
  --font-mono: {_FONT_MONO};

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

  --radius: 0.375rem;
  --max-width: 46rem;
}}

@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #12151a;
    --surface: #1b1f27;
    --surface-2: #232833;
    --text: #e6e9ef;
    --text-muted: #a3adc2;
    --border: #2b313d;
    --border-strong: #5b6478;
    --accent: #ff8a3d;
    --accent-ink: #14171c;
  }}
}}

* {{ box-sizing: border-box; }}

html {{ color-scheme: light dark; }}

body {{
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-sans);
  font-size: var(--text-base);
  line-height: 1.6;
  -webkit-text-size-adjust: 100%;
  text-rendering: optimizeLegibility;
}}

.skip-link {{
  position: absolute;
  left: -999px;
  top: auto;
  background: var(--accent);
  color: var(--accent-ink);
  padding: var(--space-2) var(--space-4);
  z-index: 10;
}}
.skip-link:focus {{
  left: var(--space-4);
  top: var(--space-4);
}}

a {{ color: var(--link); text-decoration-thickness: 0.08em; text-underline-offset: 0.15em; }}
a:hover, a:focus-visible {{ text-decoration-thickness: 0.16em; }}
:focus-visible {{ outline: 2px solid var(--border-strong); outline-offset: 2px; }}

.wrap {{
  max-width: var(--max-width);
  margin: 0 auto;
  padding: 0 var(--space-4);
}}

.site-header {{
  border-bottom: 1px solid var(--border);
  padding: var(--space-4) 0;
}}
.site-header .wrap {{
  max-width: 64rem;
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
  font-family: var(--font-mono);
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
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
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}}
nav[aria-label="Primary"] a {{ color: var(--text-muted); text-decoration: none; }}
nav[aria-label="Primary"] a:hover,
nav[aria-label="Primary"] a:focus-visible {{ color: var(--accent); }}

main {{
  max-width: 64rem;
  padding-top: var(--space-7);
  padding-bottom: var(--space-8);
}}
main > .wrap {{ padding: 0; }}

h1, h2, h3, h4, h5, h6 {{
  font-family: var(--font-mono);
  font-weight: 700;
  line-height: 1.25;
  margin-top: var(--space-7);
  margin-bottom: var(--space-3);
}}
h1 {{
  font-size: var(--text-2xl);
  text-transform: uppercase;
  letter-spacing: 0.02em;
  margin-top: 0;
}}
h2 {{
  font-size: var(--text-xl);
  border-bottom: 1px solid var(--border);
  padding-bottom: var(--space-2);
}}
h3 {{ font-size: var(--text-lg); }}
h4, h5, h6 {{ font-size: var(--text-md); }}

p, ul, ol, table, blockquote, pre {{ margin: 0 0 var(--space-4) 0; }}

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
  font-size: 0.9em;
  background: var(--surface-2);
  border-radius: var(--radius);
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
th {{ font-family: var(--font-mono); background: var(--surface-2); }}

.card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--space-5);
  margin-bottom: var(--space-4);
}}

.button {{
  display: inline-block;
  background: var(--accent);
  color: var(--accent-ink);
  font-family: var(--font-mono);
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  text-decoration: none;
  border-radius: var(--radius);
  padding: var(--space-3) var(--space-5);
}}
.button:hover, .button:focus-visible {{ text-decoration: none; filter: brightness(1.08); }}

.site-footer {{
  border-top: 1px solid var(--border);
  padding: var(--space-5) 0;
  color: var(--text-muted);
  font-size: var(--text-sm);
}}
.site-footer:empty, .site-footer .wrap:empty {{ display: none; }}

@media (max-width: 40rem) {{
  main {{ padding-top: var(--space-5); }}
  h1 {{ font-size: var(--text-xl); }}
}}
"""
