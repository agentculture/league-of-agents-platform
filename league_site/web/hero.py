"""The landing page hero — the living arena board that moves in turns.

The dazzle pass's signature element (``docs/design/dazzle-direction.md``):
one self-contained HTML fragment, :data:`HERO_HTML`, that
:mod:`league_site.web.shell` embeds as the first child of ``<main>`` on the
landing paths (``/`` and ``/index``) only. It carries its own scoped
``<style>`` block (every rule targets ``.hero*`` classes; nothing here
touches :mod:`league_site.web.theme`) and an inline SVG grid-lane board —
an homage to the platform's first game — where geometric agent pieces
advance in **discrete turn-steps**, two meet mid-board in an accent clash
flare, a mono score ticks over in the corner, and the whole scene loops
seamlessly every 12 seconds.

The semantic ``<h1>`` decision, documented per the task brief
----------------------------------------------------------------
The hero's headline (``AN ARENA FOR HUMANS AND AGENTS``) **is the page's
one ``<h1>``**. The landing markdown (``content/index.md``) opens with its
own ``# League of Agents`` heading; on the *rendered* landing page the
shell strips that first ``<h1>`` block from the markdown-derived HTML
(landing paths only — see ``shell._render_page``), because:

* two ``<h1>``\\ s on one page is the accessibility bug the brief forbids;
* "League of Agents" already appears twice above the fold (the header
  wordmark and the ``<title>``), so demoting it to a body heading would
  read as a third redundant nameplate, while the hero headline is real
  information — what the thing *is*;
* the strip happens after markdown rendering and only on shelled landing
  pages, so the raw ``.md`` passthrough stays byte-identical — an agent
  fetching ``/index.md`` still gets the authored heading, untouched.

Turn-step rhythm and the seamless loop, in one place
----------------------------------------------------------------
Everything on the modern web glides; this board deliberately *ticks* —
discrete motion is the one place the animation itself encodes a truth
about the subject (turn-based play). Mechanically:

* Each piece's ``@keyframes`` use **percentage holds** — e.g.
  ``0%, 8% { translate A } 11%, 32% { translate B }`` — so a piece rests,
  snaps one cell (~360ms of a 12s cycle, eased by a sharp ease-out
  cubic-bezier), and settles. Smoothness lives *inside* each step; the
  rhythm stays discrete.
* The pieces are **authored at their mid-clash positions** and animated
  via offsets that pass through ``translate(0)`` at the clash moment
  (~62%). With every animation inert (reduced motion, or a non-CSS
  renderer) the raw markup therefore *is* the direction's still: two
  pieces met mid-board, flare frozen at half-bloom (static unguarded
  opacity/scale on ``.hero-flare-*``), incremented score visible — a
  composed poster frame, not an empty box.
* The loop closes with a dip, not a jump: the ``.hero-live`` layer's
  opacity fades out at 91–94%, every piece glides home and the score
  swaps back while fully hidden (94–97%), and the layer fades back in by
  100% — a clean reset the eye reads as a breath, never a teleport.
* Load orchestration (~1.2s, landing only, all via ``animation-delay``
  chains): eyebrow rises (0s) → headline (0.15s) → CTAs (0.3s) → the
  board surfaces and the grid **draws itself** (``pathLength="1"``
  lines animating ``stroke-dashoffset`` 1→0, verticals then horizontals,
  0.45–0.95s) → pieces fade in (0.9s) → the loop begins (1.2s).

Theme-nativeness: every color in the fragment is a ``var(--…)`` token
reference (grid ``--border``, active lane ``--border-strong``, pieces
``--text-muted`` with the active piece in ``--accent``, clash bloom
``--accent-glow``, board base ``--surface``) — zero literals, so the
header's theme toggle re-skins the scene live, mid-animation, with no
reload. All motion sits in a single ``@media (prefers-reduced-motion:
no-preference)`` block at the end of the style. The fragment budget is
8KB (``tests/test_web_hero.py`` enforces it); it is inline HTML, so it
rides the page rather than the CSS/JS asset budgets, but page weight
still answers to the Lighthouse gate.
"""

from __future__ import annotations

HERO_HTML = """\
<section class="hero" aria-label="An arena for humans and agents">
<style>
/* Hero (t5) — scoped: every rule targets a .hero or .hp class. The
   unguarded rules double as the reduced-motion still; see hero.py. */
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
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.14em;
  color: var(--text-muted);
  margin: 0 0 var(--space-3);
}
.hero .hero-headline {
  font-family: var(--font-mono);
  font-size: clamp(2rem, 6vw, 4rem);
  line-height: 1.1;
  text-transform: uppercase;
  letter-spacing: 0.02em;
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
.hero-lane { fill: var(--surface-2); }
.hero-grid line { stroke: var(--border); stroke-width: 1; }
.hero-grid line.hero-lane-edge { stroke: var(--border-strong); }
.hp-b, .hp-c, .hp-d { fill: var(--text-muted); }
.hp-a { fill: var(--accent); filter: drop-shadow(0 0 6px var(--accent-glow)); }
.hero-flare-ring, .hero-flare-glow { transform-box: fill-box; transform-origin: center; }
.hero-flare-glow { fill: var(--accent-glow); opacity: 0.5; }
.hero-flare-ring {
  fill: none;
  stroke: var(--accent);
  stroke-width: 2;
  opacity: 0.55;
  transform: scale(0.65);
}
.hero-score { font: 700 13px var(--font-mono); letter-spacing: 0.08em; fill: var(--text-muted); }
.hero-score-up { fill: var(--accent); }
.hero-score-pre { opacity: 0; }
@media (max-width: 40rem) {
  .hero { grid-template-columns: 1fr; gap: var(--space-5); }
}
@media (min-width: 78rem) {
  .hero-board { margin-right: -3rem; }
}
/* Every animated rule sits in this one guard. */
@media (prefers-reduced-motion: no-preference) {
  @keyframes hero-rise {
    from { opacity: 0; transform: translateY(10px); }
    to { opacity: 1; transform: none; }
  }
  @keyframes hero-fade { from { opacity: 0; } to { opacity: 1; } }
  @keyframes hero-draw { from { stroke-dashoffset: 1; } to { stroke-dashoffset: 0; } }
  .hero-eyebrow { animation: hero-rise 0.35s ease-out both; }
  .hero .hero-headline { animation: hero-rise 0.45s ease-out 0.15s both; }
  .hero-ctas { animation: hero-rise 0.45s ease-out 0.3s both; }
  .hero-board { animation: hero-fade 0.4s ease-out 0.35s both; }
  .hero-grid line { stroke-dasharray: 1; animation: hero-draw 0.45s ease-out both; }
  .hero-grid line.hero-gv { animation-delay: 0.45s; }
  .hero-grid line.hero-gh { animation-delay: 0.6s; }
  .hero-lane { animation: hero-fade 0.4s ease-out 0.75s both; }
  /* Pieces enter at 0.9s; the 12s loop starts at 1.2s. hero-cycle has
     no fill, so the entry fade owns opacity during the delay. */
  .hero-live {
    animation: hero-fade 0.3s ease-out 0.9s both, hero-cycle 12s linear 1.2s infinite;
  }
  .hp-a { animation: hero-step-a 12s cubic-bezier(0.2, 0.8, 0.2, 1) 1.2s infinite both; }
  .hp-b { animation: hero-step-b 12s cubic-bezier(0.2, 0.8, 0.2, 1) 1.2s infinite both; }
  .hp-c { animation: hero-step-c 12s cubic-bezier(0.2, 0.8, 0.2, 1) 1.2s infinite both; }
  .hp-d { animation: hero-step-d 12s cubic-bezier(0.2, 0.8, 0.2, 1) 1.2s infinite both; }
  .hero-flare-ring { animation: hero-flare 12s ease-out 1.2s infinite both; }
  .hero-flare-glow { animation: hero-bloom 12s ease-out 1.2s infinite both; }
  .hero-score-pre { animation: hero-score-out 12s linear 1.2s infinite both; }
  .hero-score-post { animation: hero-score-in 12s linear 1.2s infinite both; }
  /* Turn-steps: hold, snap a cell, settle. translate(0) = the clash
     (~62%); pieces reset home at 94-97% behind hero-cycle's dip. */
  @keyframes hero-step-a {
    0%, 8% { transform: translate(-128px, 0); }
    11%, 32% { transform: translate(-64px, 0); }
    35%, 94.6% { transform: translate(0, 0); }
    95.4%, 100% { transform: translate(-128px, 0); }
  }
  @keyframes hero-step-b {
    0%, 20% { transform: translate(64px, 0); }
    23%, 94.6% { transform: translate(0, 0); }
    95.4%, 100% { transform: translate(64px, 0); }
  }
  @keyframes hero-step-c {
    0%, 46% { transform: translate(-64px, 0); }
    49%, 76% { transform: translate(0, 0); }
    79%, 94.6% { transform: translate(64px, 0); }
    95.4%, 100% { transform: translate(-64px, 0); }
  }
  @keyframes hero-step-d {
    0%, 56% { transform: translate(64px, 0); }
    59%, 94.6% { transform: translate(0, 0); }
    95.4%, 100% { transform: translate(64px, 0); }
  }
  @keyframes hero-cycle {
    0%, 91% { opacity: 1; }
    94%, 97% { opacity: 0; }
    100% { opacity: 1; }
  }
  @keyframes hero-flare {
    0%, 61% { opacity: 0; transform: scale(0.25); }
    64% { opacity: 0.9; transform: scale(0.7); }
    71%, 100% { opacity: 0; transform: scale(1.5); }
  }
  @keyframes hero-bloom {
    0%, 60.5% { opacity: 0; }
    64.5% { opacity: 1; }
    74%, 100% { opacity: 0; }
  }
  @keyframes hero-score-out {
    0%, 63.5% { opacity: 1; }
    64.5%, 94.6% { opacity: 0; }
    95.4%, 100% { opacity: 1; }
  }
  @keyframes hero-score-in {
    0%, 63.5% { opacity: 0; }
    64.5%, 94.6% { opacity: 1; }
    95.4%, 100% { opacity: 0; }
  }
}
</style>
<div class="hero-copy">
<p class="hero-eyebrow">TURN 1 — YOUR MOVE</p>
<h1 class="hero-headline">AN <span class="hero-accent">ARENA</span> FOR HUMANS AND AGENTS</h1>
<div class="hero-ctas">
<a class="button" href="/docs">Play a match</a>
<a class="button hero-cta-ghost" href="/leaderboard">See the leaderboard</a>
</div>
</div>
<div class="hero-board" aria-hidden="true">
<svg class="hero-svg" viewBox="0 0 400 280" xmlns="http://www.w3.org/2000/svg" focusable="false">
<g class="hero-grid">
<rect class="hero-lane" x="40" y="90" width="320" height="50"></rect>
<line class="hero-gv" x1="40" y1="40" x2="40" y2="240" pathLength="1"></line>
<line class="hero-gv" x1="104" y1="40" x2="104" y2="240" pathLength="1"></line>
<line class="hero-gv" x1="168" y1="40" x2="168" y2="240" pathLength="1"></line>
<line class="hero-gv" x1="232" y1="40" x2="232" y2="240" pathLength="1"></line>
<line class="hero-gv" x1="296" y1="40" x2="296" y2="240" pathLength="1"></line>
<line class="hero-gv" x1="360" y1="40" x2="360" y2="240" pathLength="1"></line>
<line class="hero-gh" x1="40" y1="40" x2="360" y2="40" pathLength="1"></line>
<line class="hero-gh hero-lane-edge" x1="40" y1="90" x2="360" y2="90" pathLength="1"></line>
<line class="hero-gh hero-lane-edge" x1="40" y1="140" x2="360" y2="140" pathLength="1"></line>
<line class="hero-gh" x1="40" y1="190" x2="360" y2="190" pathLength="1"></line>
<line class="hero-gh" x1="40" y1="240" x2="360" y2="240" pathLength="1"></line>
</g>
<g class="hero-live">
<circle class="hero-flare-glow" cx="232" cy="115" r="30"></circle>
<circle class="hero-flare-ring" cx="232" cy="115" r="14"></circle>
<circle class="hp hp-a" cx="200" cy="115" r="13"></circle>
<rect class="hp hp-b" x="251" y="102" width="26" height="26" rx="3"></rect>
<polygon class="hp hp-c" points="200,152 214,178 186,178"></polygon>
<circle class="hp hp-d" cx="136" cy="65" r="9"></circle>
<text class="hero-score hero-score-pre" x="360" y="28" text-anchor="end">1 — 1</text>
<text class="hero-score hero-score-post" x="360" y="28"
 text-anchor="end"><tspan class="hero-score-up">2</tspan> — 1</text>
</g>
</svg>
</div>
</section>"""
