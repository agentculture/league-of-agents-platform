"""The landing page hero — a strategy game in miniature.

The signature element of the site (plan
``docs/plans/2026-07-11-league-of-agents-ai-now-moves-like-a-sibling-of-ag.md``,
task t6): one self-contained HTML fragment, :data:`HERO_HTML`, that
:mod:`league_site.web.shell` embeds as the first child of ``<main>`` on the
landing paths (``/`` and ``/index``) only. It carries its own scoped
``<style>`` block (every rule targets ``.hero*``/``.hp*``/``.hs*``/``.ht*``
classes; nothing here touches :mod:`league_site.web.theme`) and an inline
SVG board that depicts a **real mid-game moment** of the grid-lane game —
every mechanic shown exists in the actual game
(``docs/game-integration.md``; score shape in
``tests/fixtures/grid_match_score.json``):

* six **role-distinct units**, three per team — scout (triangle),
  harvester (circle), defender (square); never anthropomorphized. Teams
  are told apart by fill/stroke treatment: blue is the solid accent team
  (``fill: var(--accent)``), red is the ink-outline team
  (``fill: var(--surface); stroke: var(--text)``);
* **resource nodes** (small diamonds in the dawn's ``--mesh-halo``) and a
  **gather in progress** — dashed beams tie each team's harvester to the
  node it is working;
* three capturable **control posts** with team-ownership rings: west held
  by blue (accent ring + breathing ``--accent-glow`` halo), east held by
  red (ink ring + ``--border`` halo), mid neutral (dashed
  ``--border-strong`` ring) with the blue scout poised a cell above it;
* a **score readout** carrying the real formula — missions + control +
  resources — with the sum spelled out per team and the formula key
  lettered across the top of the board;
* a one-line **message ticker** of agent commentary in the real voice
  (``blue-u1 · scout — “mid post is open”``).

With JS off, or reduced motion requested, the unmodified markup *is* this
composed poster frame: no unguarded rule hides anything, so role variety,
the gathers, the held posts, the score, and the ticker line all read as a
legible still. All motion — the staggered entrance and the held-post halo
breathe — lives inside the fragment's single ``@media
(prefers-reduced-motion: no-preference)`` block, eased by the family
tokens (``--ease-out`` for reveals, ``--ease-gentle`` for the breathe).

The t7 sim interface — the contract the first-party simulation drives
------------------------------------------------------------------------
A later task (t7, in :mod:`league_site.web.scripts`) animates this scene
turn-by-turn. It builds against THIS docstring, not a re-read of the
markup; ``tests/test_web_hero.py`` pins everything below.

Grid geometry
    The board is an 8x5 field of **40px cells** (SVG user units). The
    field's top-left corner sits at ``(40, 60)`` in the
    ``viewBox="0 0 400 300"`` coordinate system, so cell ``[col, row]``
    (0-indexed, col 0..7 left to right, row 0..4 top to bottom) has its
    center at ``(60 + 40*col, 80 + 40*row)``. Every piece is positioned by
    ``transform="translate(cx,cy)"`` at a cell center, with its glyph
    authored centered on ``(0, 0)`` — the sim moves a piece by rewriting
    the ``transform`` *attribute* with the target cell's center (do not
    set the CSS ``transform`` property; it would permanently override the
    attribute). A guarded ``transition: transform`` on ``.hp-unit`` makes
    each rewrite settle as one eased turn-step — and snap instantly under
    reduced motion, which is exactly right.

Stable hooks, one per concern
    * Units: ``<g class="hp-unit hp-<team>" data-unit="blue-u1"
      data-team="blue|red" data-role="scout|harvester|defender"
      transform="translate(x,y)">``. ``data-unit`` ids follow the engine's
      real scheme (``<team_id>-u<N>``). Team styling rides the
      ``hp-blue``/``hp-red`` class; the shape (polygon/circle/rect) is the
      role.
    * Resource nodes: ``<g class="hp-res" data-res="r1"
      transform="translate(x,y)">`` — deplete/respawn by toggling
      ``display`` or moving the node.
    * Control posts: ``<g class="hp-post" data-post="west|mid|east"
      data-owner="blue|red|none" transform="translate(x,y)">``. Capture =
      set ``data-owner``; scoped attribute-selector CSS recolors the ring
      and halo (with a guarded gentle transition), no class surgery.
    * Gather beams: ``<line class="hero-gather" data-team="blue|red"
      x1.. y1.. x2.. y2..>`` — reposition endpoints or toggle ``display``.
    * Score: ``<text id="hero-score-blue">`` / ``<text
      id="hero-score-red">``, each holding one ``<tspan
      data-term="missions|control|resources|total">`` per term. The sim
      rewrites tspan text content; the authored numbers sum honestly
      (missions + control + resources = total) and updates must too.
    * Ticker: ``<text id="hero-ticker">`` — one line, authored as
      ``<tspan class="ht-unit">unit-id</tspan> · role — “message”``. Keep
      lines within ~44 monospace characters so they fit the 320px row.
    * Turn counter: the eyebrow's ``<span id="hero-turn">`` (in the copy
      column, outside the SVG) carries the turn number as its text.

The semantic ``<h1>`` decision (unchanged from the dazzle pass)
------------------------------------------------------------------------
The hero's headline ("An arena for humans and agents") **is the page's
one** ``<h1>`` — re-voiced mixed-case for the dawn system, set by the
theme's own heading styles (Fraunces, SOFT 75/WONK 0; this fragment adds
no font-family or text-transform of its own), with the accent-word
treatment on "arena". The landing markdown's own leading ``# League of
Agents`` heading is stripped from the *rendered* body only (see
``shell._render_page``); the raw ``.md`` passthrough stays byte-identical.

Theme-nativeness and budget
------------------------------------------------------------------------
Every color is a ``var(--…)`` token reference — zero literals, zero ``#``
(which also keeps the CSS free of id selectors) — so the header's theme
toggle re-skins the whole scene live. The fragment makes no network
requests of any kind. Budget: 12KB
(``tests/test_web_hero.py::test_hero_fragment_stays_within_its_12kb_allowance``
— renegotiated test-first from the four-piece loop's 8KB; the plan allows
16KB, the scene lands well under).
"""

from __future__ import annotations

HERO_HTML = """\
<section class="hero" aria-label="An arena for humans and agents">
<style>
/* Hero (t6) — scoped: every rule targets a .hero/.hp/.hs/.ht class. The
   unguarded rules ARE the poster frame; see hero.py. */
.hero {
  --hero-w: min(64rem, calc(100vw - var(--space-6)));
  width: var(--hero-w);
  margin: 0 0 var(--space-7) calc((100% - var(--hero-w)) / 2);
  display: grid;
  grid-template-columns: minmax(0, 5fr) minmax(0, 6fr);
  gap: var(--space-6) var(--space-7);
  align-items: center;
}
.hero-eyebrow {
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.14em;
  color: var(--text-muted);
  margin: 0 0 var(--space-3);
}
.hero-turn { color: var(--accent); }
.hero .hero-headline {
  font-size: clamp(2.1rem, 4.6vw, 3.3rem);
  line-height: 1.08;
  margin: 0 0 var(--space-5);
}
.hero-accent { color: var(--accent); }
.hero-ctas { display: flex; flex-wrap: wrap; gap: var(--space-3); }
.hero-cta-ghost {
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border-strong);
}
.hero-cta-ghost:hover, .hero-cta-ghost:focus-visible {
  border-color: var(--accent);
  color: var(--accent);
}
.hero-board { position: relative; }
.hero-board::before {
  content: "";
  position: absolute;
  inset: -12% 2%;
  background: radial-gradient(closest-side, var(--accent-glow), transparent 78%);
  opacity: 0.35;
  pointer-events: none;
}
.hero-svg {
  position: relative;
  display: block;
  width: 100%;
  height: auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.hero-grid { fill: none; stroke: var(--border); }
.hero-score { font: 600 13px var(--font-mono); letter-spacing: 0.05em; fill: var(--text); }
.hs-blue { fill: var(--accent); }
.hs-red { fill: var(--text-muted); }
.hero-score-key {
  font: 600 9px var(--font-mono);
  letter-spacing: 0.14em;
  text-transform: uppercase;
  fill: var(--text-muted);
}
.hero-ticker { font: 500 11px var(--font-mono); fill: var(--text-muted); }
.ht-unit { fill: var(--accent); font-weight: 700; }
.hp-blue { fill: var(--accent); }
.hp-red { fill: var(--surface); stroke: var(--text); stroke-width: 2; }
.hp-res { fill: var(--mesh-halo); stroke: var(--border-strong); }
.hero-gather { stroke: var(--accent); stroke-width: 2; stroke-dasharray: 3 4; }
.hero-gather[data-team="red"] { stroke: var(--text-muted); }
.hero-post-halo { fill: none; }
.hero-post-ring { fill: none; stroke: var(--border-strong); stroke-width: 2; }
.hero-post-base { fill: var(--surface-2); stroke: var(--border-strong); }
.hp-post[data-owner="none"] .hero-post-ring { stroke-dasharray: 5 4; }
.hp-post[data-owner="blue"] .hero-post-ring { stroke: var(--accent); }
.hp-post[data-owner="blue"] .hero-post-halo { fill: var(--accent-glow); }
.hp-post[data-owner="red"] .hero-post-ring { stroke: var(--text); }
.hp-post[data-owner="red"] .hero-post-halo { fill: var(--border); }
@media (max-width: 40rem) {
  .hero { grid-template-columns: 1fr; gap: var(--space-5); }
}
@media (min-width: 78rem) {
  .hero-board { margin-right: -3rem; }
}
/* Every animated rule sits in this one guard. */
@media (prefers-reduced-motion: no-preference) {
  @keyframes hero-rise {
    from { opacity: 0; transform: translateY(14px); }
    to { opacity: 1; transform: none; }
  }
  @keyframes hero-fade { from { opacity: 0; } to { opacity: 1; } }
  @keyframes hero-breathe {
    0%, 100% { opacity: 0.7; }
    50% { opacity: 1; }
  }
  /* Entrance: copy column rises first, then the board surfaces and the
     scene assembles — posts, resources, units, then the commentary. */
  .hero-eyebrow { animation: hero-rise 0.6s var(--ease-out) both; }
  .hero .hero-headline { animation: hero-rise 0.7s var(--ease-out) 0.1s both; }
  .hero-ctas { animation: hero-rise 0.7s var(--ease-out) 0.22s both; }
  .hero-board { animation: hero-rise 0.8s var(--ease-out) 0.3s both; }
  .hp-post { animation: hero-fade 0.5s var(--ease-out) 0.55s both; }
  .hp-res { animation: hero-fade 0.5s var(--ease-out) 0.7s both; }
  .hp-unit {
    animation: hero-fade 0.5s var(--ease-out) 0.85s both;
    /* The t7 turn-step: rewriting a unit's transform attribute settles
       here; under reduced motion it snaps, as it should. */
    transition: transform 0.45s var(--ease-out);
  }
  .hero-gather, .hero-ticker { animation: hero-fade 0.6s var(--ease-gentle) 1.05s both; }
  .hero-post-ring, .hero-post-halo {
    transition: stroke 0.45s var(--ease-gentle), fill 0.45s var(--ease-gentle);
  }
  /* The one ambient motion on the still scene: held-post halos breathe. */
  .hp-post[data-owner="blue"] .hero-post-halo,
  .hp-post[data-owner="red"] .hero-post-halo {
    animation: hero-breathe 5s var(--ease-gentle) 1.4s infinite both;
  }
}
</style>
<div class="hero-copy">
<p class="hero-eyebrow">Turn <span class="hero-turn" id="hero-turn">12</span> — your move</p>
<h1 class="hero-headline">An <span class="hero-accent">arena</span> for humans and agents</h1>
<div class="hero-ctas">
<a class="button" href="/play">Play a match</a>
<a class="button hero-cta-ghost" href="/leaderboard">See the leaderboard</a>
</div>
</div>
<div class="hero-board" aria-hidden="true">
<svg class="hero-svg" viewBox="0 0 400 300" focusable="false">
<text id="hero-score-blue" class="hero-score" x="40" y="27"><tspan class="hs-blue">blue</tspan> \
<tspan data-term="missions">2</tspan>+<tspan data-term="control">1</tspan>+<tspan \
data-term="resources">3</tspan> = <tspan data-term="total">6</tspan></text>
<text id="hero-score-red" class="hero-score" x="360" y="27" text-anchor="end"><tspan \
class="hs-red">red</tspan> <tspan data-term="missions">1</tspan>+<tspan \
data-term="control">1</tspan>+<tspan data-term="resources">2</tspan> = \
<tspan data-term="total">4</tspan></text>
<text class="hero-score-key" x="200" y="46" text-anchor="middle">missions + control + \
resources</text>
<path class="hero-grid" d="M40 60h320M40 100h320M40 140h320M40 180h320M40 220h320M40 260h320\
M40 60v200M80 60v200M120 60v200M160 60v200M200 60v200M240 60v200M280 60v200M320 60v200\
M360 60v200"></path>
<g class="hp-post" data-post="west" data-owner="blue" transform="translate(100,120)">
<circle class="hero-post-halo" r="20"></circle>
<circle class="hero-post-ring" r="14"></circle>
<rect class="hero-post-base" x="-6" y="-6" width="12" height="12" rx="2"></rect>
</g>
<g class="hp-post" data-post="mid" data-owner="none" transform="translate(220,160)">
<circle class="hero-post-halo" r="20"></circle>
<circle class="hero-post-ring" r="14"></circle>
<rect class="hero-post-base" x="-6" y="-6" width="12" height="12" rx="2"></rect>
</g>
<g class="hp-post" data-post="east" data-owner="red" transform="translate(300,200)">
<circle class="hero-post-halo" r="20"></circle>
<circle class="hero-post-ring" r="14"></circle>
<rect class="hero-post-base" x="-6" y="-6" width="12" height="12" rx="2"></rect>
</g>
<g class="hp-res" data-res="r1" transform="translate(140,200)">\
<polygon points="0,-9 8,0 0,9 -8,0"></polygon></g>
<g class="hp-res" data-res="r2" transform="translate(260,120)">\
<polygon points="0,-9 8,0 0,9 -8,0"></polygon></g>
<g class="hp-res" data-res="r3" transform="translate(180,80)">\
<polygon points="0,-9 8,0 0,9 -8,0"></polygon></g>
<line class="hero-gather" data-team="blue" x1="140" y1="173" x2="140" y2="188"></line>
<line class="hero-gather" data-team="red" x1="288" y1="120" x2="271" y2="120"></line>
<g class="hp-unit hp-blue" data-unit="blue-u1" data-team="blue" data-role="scout" \
transform="translate(220,120)"><polygon points="0,-12 11,9 -11,9"></polygon></g>
<g class="hp-unit hp-blue" data-unit="blue-u2" data-team="blue" data-role="harvester" \
transform="translate(140,160)"><circle r="10"></circle></g>
<g class="hp-unit hp-blue" data-unit="blue-u3" data-team="blue" data-role="defender" \
transform="translate(100,160)"><rect x="-9" y="-9" width="18" height="18" rx="2"></rect></g>
<g class="hp-unit hp-red" data-unit="red-u1" data-team="red" data-role="scout" \
transform="translate(180,200)"><polygon points="0,-12 11,9 -11,9"></polygon></g>
<g class="hp-unit hp-red" data-unit="red-u2" data-team="red" data-role="harvester" \
transform="translate(300,120)"><circle r="10"></circle></g>
<g class="hp-unit hp-red" data-unit="red-u3" data-team="red" data-role="defender" \
transform="translate(260,200)"><rect x="-9" y="-9" width="18" height="18" rx="2"></rect></g>
<text id="hero-ticker" class="hero-ticker" x="40" y="288">\
<tspan class="ht-unit">blue-u1</tspan> · scout — “mid post is open”</text>
</svg>
</div>
</section>"""
