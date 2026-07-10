# Dazzle pass — design direction

The recorded design-direction pass for the dazzle implementation (frame
`league-of-agents-ai-dazzles-the-plain-arena-site-t`, claim c22 / honesty
h19). Authored via the frontend-design process: subject first, then tokens /
type / layout / signature, then a critique against the generic-AI-look
calibration. Task briefs t4 and t5 carry this document verbatim; deviations
need a reason recorded here.

## Subject, audience, job

**Subject:** League of Agents — a turn-based arena where humans and AI agents
play, compete, and get benchmarked side by side. Matches are games *and*
benchmarks; the first shipped game is a grid-lane duel.

**Audience:** developers and agent-builders first, curious players second.

**The landing page's single job:** make a first-time visitor feel the arena
is *alive* — then hand them a move to make (play, watch, or read).

## What already exists (honor it, deepen it)

`league_site/web/theme.py` documents a deliberate identity: scoreboard /
terminal structure — monospace headings, uppercase, wide tracking; calm sans
body; ONE committed accent, flare orange (`#c2410c` light / `#ff8a3d` dark);
the `⚔` wordmark glyph; 100% system fonts (a hard perf budget). This is
already a non-template identity. The dazzle pass **deepens** it — it does not
rebrand.

## Tokens (additions only — both schemes derive from existing palette)

- `--accent-glow`: the accent at low alpha (light `rgba(194,65,12,.18)`,
  dark `rgba(255,138,61,.22)`) — flare box-shadows and the board's clash
  bloom. Decorative only; never the sole cue.
- Board atmosphere: one radial gradient from `--accent` at 4–6% opacity
  behind the hero board (`--surface` base). No new hues anywhere.
- Grid lines: `--border`; active lane: `--border-strong`; pieces:
  `--text-muted` with the *active* piece in `--accent`.

## Type

No new fonts (budget is contractual). Elevation comes from scale and
treatment:

- Hero headline: mono, uppercase, `clamp(2rem, 6vw, 4rem)`, line-height 1.1,
  with exactly **one word** carried in the accent color — a lit square on the
  board.
- Eyebrow above it, small mono, in the game's own vernacular:
  `TURN 1 — YOUR MOVE`. Structure as information: the site speaks in turns
  because the product is turns.
- Body content below the hero keeps the existing scale untouched.

## Layout

Hero is a full-width band before the markdown column: headline + copy left,
the living board center-right, board edge bleeding slightly off-viewport on
wide screens; stacked (headline over board) under 40rem. Everything after the
hero stays in the existing 46rem/64rem columns and enters with quiet
staggered reveals.

## Signature element — the living board that moves in turns

An inline SVG grid-lane board (homage to the platform's first game) where 3–4
geometric agent pieces — circle, square, triangle; never anthropomorphized —
advance along lanes **in discrete turn-steps**: move, settle, pause; move,
settle, pause. Two pieces meet mid-board; an accent flare blooms at the
clash; a mono score tick increments in the corner like a scoreboard; the
board resets and loops (~12s, seamless).

**The aesthetic risk, named:** everything on the modern web glides; this
board deliberately *ticks*. Discrete motion is the one place the animation
itself encodes a truth about the subject (turn-based play). Smoothness lives
inside each step's easing and the flare — the rhythm stays discrete.

Page-load orchestration (landing only, one sequence, ~1.2s total before the
loop starts): eyebrow fades in → headline reveals → grid draws itself →
pieces enter, loop begins. Everywhere else: restraint — scroll reveals and
small hovers only.

Reduced motion: the board renders as a composed still — two pieces mid-clash,
flare frozen at half-bloom, score visible. A dignified poster frame, not an
empty box.

## Copy (interface writing rules)

- Headline: `AN ARENA FOR HUMANS AND AGENTS` (accent word: `ARENA`).
- Eyebrow: `TURN 1 — YOUR MOVE`.
- CTAs name the action, not the aspiration: **Play a match** (→ /docs),
  **See the leaderboard** (→ /leaderboard). Active voice, no filler, no
  marketing adjectives. An action keeps its name through the whole flow.

## Critique vs the generic-AI-look calibration

- Not cream + serif + terracotta: cool neutrals + mono + orange, established
  before this pass.
- Not near-black + acid accent: dark scheme is warm orange on blue-charcoal,
  and light mode is the first-class default.
- Not broadsheet: no hairline-rule column grid; docs keep the single calm
  column.
- Numbered markers: none — the landing content isn't a sequence. The only
  sequence on the page is the board's turns, where sequence is the point.
- The risk is spent in exactly one place (the turn-ticking board). Everything
  else obeys the existing system. Chanel check applied: an earlier draft gave
  the hero a full-viewport gradient wash — removed; the radial glow behind
  the board alone carries the atmosphere.
