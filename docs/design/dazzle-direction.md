# Dawn-arena design direction

The recorded design-direction pass for the full-adoption dawn-arena identity
(frame `league-of-agents-ai-now-moves-like-a-sibling-of-ag`, decisions c6 /
c21 / c22, honesty h11 / h16). Authored via the frontend-design process:
subject first, then tokens / type / motion / signature, then a critique
against the generic-AI-look calibration. Tasks t5 (palette/type/motion), t6
(the board scene), t7 (the sim engine), and t11 (favicon/og:image) carry this
document; deviations need a reason recorded here. This supersedes the
dazzle-pass-only version of this file (see "What this replaces" below) — the
dazzle pass's own signature, the turn-tick, survives underneath the new
palette, re-skinned rather than discarded.

## Subject, audience, job

**Subject:** League of Agents — a turn-based arena where humans and AI
agents play, compete, and get benchmarked side by side, and a first-class
member of the AgentCulture family (sibling of agentculture.org). Matches are
games *and* benchmarks; the first shipped game is a grid-lane duel.

**Audience:** developers and agent-builders first, curious players second —
plus the AgentCulture community, who will recognize the shared brand on
sight.

**The landing page's single job:** make a first-time visitor feel two
things at once — this is *family* (AgentCulture, before they read a word)
and this arena is *alive* (a real match in motion) — then hand them a move
to make (play, watch, or read).

## The dawn palette — one scene at two hours

`league_site/web/theme.py` carries the palette as the single source of
truth (token values plus the WCAG contrast ratios computed by hand and
re-stated in its docstring); this section records the rationale, not a
second copy of the numbers.

The palette is one scene, told at two hours, matching agentculture.org's own
family palette:

- **Light — dawn, ten minutes after sunrise:** `--bg #f4f5fb`, `--text
  #232a4d`, `--accent #0b655c` (aurora teal). Warm sky-wash gradients
  (`--sky-upper`, `--sky-horizon`, `--sky-mist`, `--sky-glow`) paint the
  page-wide `body::before` wash — the single strongest family signal,
  present behind every page, not just the hero.
- **Dark — the hour before dawn:** `--bg #0b0f20`, `--text #e9ecf8`,
  `--accent #7fdcc9`. Same structure, same relationships, inverted hours —
  the family's dark scheme is not "light scheme, negated," it is the other
  side of the same day.
- **The mesh:** `--mesh-node` (= `--accent`, so it always re-skins with the
  scheme), `--mesh-thread`, `--mesh-halo`, `--mesh-halo-alt` — the
  nodes-and-threads motif agentculture.org uses for its own decorative
  texture, adopted here as the same family vocabulary rather than a
  league-specific invention.

Every pairing meets WCAG AA (>= 4.5:1 body text, >= 3:1 non-text UI) in both
schemes; `--border` / `--border-soft` stay decorative-only hairlines (never
the sole cue) so they are not held to that bar, while `--border-strong` (the
token used wherever a border *is* the only cue — focus rings, table rules)
clears 3:1 in both. Token **names** stay the league's own (`--bg`,
`--surface`, `--accent`, …); only the **values** moved to the family's — so
every consumer (hero, viewer, profiles) re-skinned by editing one file.

## The family bar — membership obvious by design and feel alone

The acceptance bar for this entire pass is claim c21, checked cold under
honesty condition h16: someone who knows agentculture.org, shown
league-of-agents.ai with no context, names the kinship *unprompted* — in
both color schemes, before reading a word of copy. If the resemblance needs
explaining, the pass has not met the bar. This is a stronger requirement
than "on brand" — it is a first-time, no-caption, sensory read.

What actually carries that signal, in order of how much of the "family"
read each one does:

1. **The dawn wash** — `body::before`'s sky gradient, present on every page,
   not only the landing hero.
2. **The type voice** — Fraunces headings at gentle weights next to Albert
   Sans body copy is agentculture.org's silhouette at a glance, before any
   word is legible.
3. **The motion feel** — nothing snaps; everything settles or breathes (see
   below). A visitor who only scrolls past once still feels this.
4. **The mesh motif** — the sky-wash + mesh tokens, decorative and never
   load-bearing for text, but present enough to read as "the same design
   system" rather than "a similar palette."
5. **The pill/rounded vocabulary** — `--radius: 1.25rem` on cards and the
   hero board, 999px pills on buttons/toggle/skip-link — soft geometry
   throughout, no hard corners.

**One deliberate divergence, kept on purpose:** the three-state light /
dark / system theme toggle. agentculture.org has no toggle (OS-only);
league-of-agents.ai keeps its explicit-choice mechanics unchanged through
this pass (c12). A family member is allowed exactly one honest difference —
alignment means shared feel, not feature regression, and the toggle predates
the dawn palette and survives it structurally unchanged
(`:root[data-theme]` blocks, `PRE_PAINT_JS`, the `localStorage["theme"]`
key).

## Family type voice

No system-font baseline anymore — this is the one place this pass spends
new bytes rather than just re-skinning existing ones, and it is spent on
the family's own voice, first-party:

- **Display** (`--font-display`): Fraunces Variable, the full variable
  file (it carries the SOFT/WONK axes), rendered with
  `font-variation-settings: "SOFT" 75, "WONK" 0` at gentle weights — h1 at
  weight 400, other headings ~440–500, the wordmark at 520. `text-wrap:
  balance` on headings.
- **Body** (`--font-body`): Albert Sans Variable at 1.0625rem / line-height
  1.7, `text-wrap: pretty` on paragraphs.
- **Mono** (`--font-mono`): unchanged — the OS monospace stack, retained
  for code blocks, the score readout, the ticker, and the theme-toggle
  glyph. Mono is not retired; it is demoted from "the whole site's voice"
  to "the register for structured/machine-adjacent content," which is
  exactly what it is for.

Both variable woff2 files are vendored (t3) and served first-party at
`/fonts/*.woff2`, `font-display: swap` so text stays readable on system
fallbacks while they arrive. No external font CDN, before or after this
pass.

## Breathing motion system

The family feel, ported wholesale from agentculture.org's own motion
language: nothing snaps, everything settles or breathes. Two easing tokens
carry the whole system:

- `--ease-out` — `cubic-bezier(0.22, 1, 0.36, 1)`, long and settling, used
  for reveals.
- `--ease-gentle` — `cubic-bezier(0.45, 0, 0.25, 1)`, the breathing curve,
  used for hovers and ambient pulses.

On that baseline:

- **Reveals:** `.reveal` / `.revealed` settle in over **0.9s** on
  `--ease-out`, staggered 60ms per element (`html[data-js]`-gated, so
  nothing is ever hidden with JS off).
- **Hover lifts:** `.card` rises `translateY(-4px)`, `.button` rises
  `translateY(-2px)` with an `--accent-glow` ring — both gentle, 0.4s,
  `--ease-gentle`.
- **View transitions:** a ~180ms crossfade on navigation, nested inside the
  motion guard so reduced motion means an instant swap, not a suppressed
  animation.

Atop that family baseline the league keeps exactly one signature of its
own: **the turn-tick.** The wordmark glyph's discrete blink (a long hold, a
short dip, `wordmark-pulse`, 4s) and the hero board's step-and-settle both
encode the same truth — this product moves in turns, not continuously —
inside a system that otherwise glides. The risk from the original dazzle
pass is preserved, just re-skinned onto the dawn palette rather than
retired with it.

Everything above lives inside one `@media (prefers-reduced-motion:
no-preference)` guard; nothing animates a layout property (width, height,
margin, padding, position offsets) — only `transform`/`opacity`, plus
`box-shadow` on hover.

## The board-as-game direction — roles, resources, posts, score, ticker

The hero (`league_site/web/hero.py`) is no longer a decorative loop of
abstract pieces — it depicts a real mid-game moment of the actual grid-lane
game, and every mechanic shown is checked against
`docs/game-integration.md` and `tests/fixtures/grid_match_score.json`:

- **Six role-distinct units, three per team** — scout (triangle), harvester
  (circle), defender (square); never anthropomorphized. Team identity rides
  fill/stroke (`hp-blue` solid accent, `hp-red` ink-outline), not new hues.
- **Resource nodes and gather beams** — dashed lines tie a harvester to the
  node it is working; the economy is visible, not implied.
- **Three capturable control posts** with team-ownership rings — held posts
  carry a breathing `--accent-glow` (or `--border`) halo on `--ease-gentle`;
  a neutral post's ring is dashed.
- **A real score readout** — `missions + control + resources = total`, the
  actual outcome formula, spelled out per team, not a decorative tick.
- **A message ticker** in the game's real commentary voice (`blue-u1 ·
  scout — "mid post is open"`).

**The sim engine (t7):** a first-party engine in `SITE_JS`
(`initSim`) stages per-team orders and resolves them against the scene one
turn at a time — moves to adjacent cells only, gather→deliver, post
captures flipping `data-owner`, the score readout updating for real.
Roughly one resolved turn every 2.75s, a 12-turn loop, then a quiet reset;
a small xorshift PRNG seeded from `Date.now()` means consecutive loops
differ. The sim activates only when `html[data-js]` is set **and**
`prefers-reduced-motion` is not requested; either gate absent, the poster
frame stands untouched. Zero network requests — it is a simulation of the
game's mechanics, not a live match feed (the real-match viewer board stays
a parked follow-up).

**The poster-frame contract (unchanged discipline from the dazzle pass,
now the sim's contract too):** with JS off or motion reduced, the
*unmodified authored markup* composes a legible mid-game still — role
variety, a gather in progress, a held post, the real score, one ticker
line, all visible without a single animated rule applying. Every attribute
the sim mutates is snapshotted at init and restored exactly on reset, so
the poster is never left corrupted mid-loop. A dignified still, not an
empty box, on every path that reaches it.

## What this replaces

Before this pass, `league_site/web/theme.py` carried a different, equally
deliberate identity: a **scoreboard/terminal** system — 100% system fonts
(a hard perf budget with zero webfont allowance), monospace uppercase
headings with wide tracking, calm sans body, and ONE committed accent, flare
orange (`#c2410c` light / `#ff8a3d` dark). The hero board was a hardcoded,
non-simulated 12s SVG/CSS loop of four abstract pieces — one clash, one
score tick, no roles, no resources, no posts. That identity is retired, not
erased: it is claim c6's recorded before-state (honesty h11 — verifiable in
git, not memory), and it stays inspectable at the `0.7.0` tag (the dazzle
pass) and in this file's own git history for anyone who wants to compare
the before/after directly. The scoreboard/terminal pass was itself a
considered, non-template identity in its time (see the CHANGELOG's 0.7.0
entry) — full adoption of the dawn palette is a deliberate USER DECISION to
trade a league-only identity for a legible, obvious-at-a-glance membership
in the AgentCulture family, not a judgment that the prior identity failed.

## Performance budget (renegotiated twice, not abandoned)

- **Dazzle pass (spec c11):** CSS 10KB → 24KB, JS 0 → 8KB (a pre-dazzle
  site emitted no `<script>` at all).
- **This pass (spec h1):** CSS 24KB → **32KB**, JS 8KB → **16KB**, and a new
  **320KB** font allowance introduced for the two self-hosted variable
  woff2 files (Fraunces Variable + Albert Sans Variable, ~153KB combined as
  vendored). Total first-party asset weight (CSS + JS + fonts) stays under
  **368KB**, derived from the three ceilings so it can never drift out of
  sync with them.
- **Unchanged across both renegotiations:** zero external requests. No
  third-party webfont CDN, no other CDN, no third-party scripts, no images
  — CSS, JS, and fonts are all first-party, all test-enforced
  (`tests/test_web_theme_budget.py`).

Every ceiling above was moved test-first, ahead of the code that would
spend it — the contract evolved under negotiation, never quietly.

## Favicon + og:image (t11)

Pulled into this pass by USER DECISION (c22) rather than deferred: browser
tab identity and link-preview cards are part of "obvious at a glance," so
they could not ship generation-lagged behind the palette. `/favicon.svg`
is a dawn-palette SVG carrying its own `prefers-color-scheme` dark variant
(inline, the same pattern agentculture.org uses); `/og.png` is a refreshed
1200x630 share-card in the dawn palette. Both are served first-party,
versioned via the same `asset_url()` content-hash mechanism as every other
asset (`?v=<hash>`), and the old flare-orange favicon asset is gone from
the tree, not just unreferenced.

## Versioned asset URLs (t4) — why the toggle can never strand again

Every stylesheet/script/font/favicon/og-image URL the shell emits carries
`?v=<sha256-prefix-of-the-served-bytes>`. A deploy that changes any asset's
bytes can only ever be reached at a new URL, so a CDN or browser cache
serving a stale copy against new HTML — the actual root cause of the
production theme-toggle incident this pass also fixed — is now structurally
impossible rather than something an operator has to remember to purge. The
Cloudflare purge runbook (`docs/runbooks/cloudflare-league-of-agents-ai.md`)
still documents the purge-by-URL procedure for the narrow case of an
in-place same-URL republish, but routine purges are no longer part of the
deploy path.

## Copy (interface writing rules — unchanged discipline)

- Headline: `An arena for humans and agents` (accent word: `arena`),
  re-voiced mixed-case for the dawn system's Fraunces headings — the
  scoreboard pass's uppercase mono treatment is gone along with the rest of
  that identity.
- Eyebrow: `Turn N — your move`, `N` live from the sim's turn counter.
- CTAs name the action, not the aspiration: **Play a match** (→ /docs),
  **See the leaderboard** (→ /leaderboard). Active voice, no filler, no
  marketing adjectives.

## Critique vs the generic-AI-look calibration

- Not cream + serif + terracotta: the dawn palette is cool indigo ink on a
  pale sky wash (light) or deep indigo-black with aurora teal (dark) — not
  the templated warm-neutral-plus-serif look, and dark mode is a genuinely
  different hour, not light-mode-inverted.
- Not near-black + acid accent: dark scheme reads as *dawn*, not *void* —
  the sky-wash gradients and mesh halos keep it atmospheric rather than
  flat-black-and-neon.
- Not broadsheet: no hairline-rule column grid; the single calm content
  column (`--max-width: 46rem` / `64rem` for the header) is unchanged by
  this pass.
- The family-alignment risk is spent everywhere on purpose (that is the
  point of "full adoption," not a scoped risk in one component) — the
  discipline that keeps it from reading as generic-AI dawn-palette-of-the-
  month is that every token traces to a specific sibling site's specific
  values, verified side-by-side, not to a trend.
- The board's discrete turn-tick against the family's continuous "breathing"
  motion is still the one place this site's animation itself encodes a
  truth about the subject (turn-based play) rather than following the
  family baseline verbatim — the dazzle pass's signature, kept.
