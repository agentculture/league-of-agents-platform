# Build Plan — league-of-agents.ai now moves like a sibling of agentculture.org — one shared design language, buttery-smooth experience — and its demo board has grown into an awe-inspiring living strategy game: units with roles, resources on the field, and posts that matter

slug: `league-of-agents-ai-now-moves-like-a-sibling-of-ag` · status: `exported` · from frame: `league-of-agents-ai-now-moves-like-a-sibling-of-ag`

> league-of-agents.ai now moves like a sibling of agentculture.org — one shared design language, buttery-smooth experience — and its demo board has grown into an awe-inspiring living strategy game: units with roles, resources on the field, and posts that matter.

## Tasks

### t2 — Renegotiate the asset-budget contract, test-first (theme.py constants + tests/test_web_theme_budget.py)

- covers: h1
- acceptance:
  - New ceilings pinned: CSS<=32KB, JS<=16KB, FONTS<=320KB (two variable woff2), TOTAL<=368KB; constants and tests updated together; the zero-external-request assertion is unchanged and passing
  - Budget tests fail red before the new constants land and pass green after — contract-first discipline preserved

### t3 — Vendor and serve Fraunces Variable + Albert Sans Variable first-party (font files, fonts module, shell.py routes, Lambda binary responses, preload links)

- depends on: t2
- covers: c2
- acceptance:
  - GET /fonts/fraunces-var.woff2 and /fonts/albert-sans-var.woff2 return 200 font/woff2, byte-identical to the vendored files, via site_app AND through the Lambda handler's isBase64Encoded binary path — new tests/test_web_fonts.py proves all of it
  - Shell `<head>` preloads both woff2 (crossorigin) before the stylesheet link; no external font requests anywhere — budget test still green

### t4 — Versioned asset URLs — shelled HTML always references the exact build it shipped with (shell.py)

- depends on: t3
- covers: c16, h7, c14
- acceptance:
  - Every stylesheet/script/font URL the shell emits carries the build version (`?v=<version>` or hashed path); bumping the version changes every reference; tests assert the referenced URL returns current-build bytes on the FIRST fetch
  - Asset responses carry long-lived immutable cache headers, and the deploy docs drop any asset-purge step

### t5 — Full-adoption design system: dawn palette, family type voice, breathing motion (theme.py)

- depends on: t2, t3
- covers: c2, c8, c12, h8
- acceptance:
  - All tokens swap to the agentculture.org dawn palette in BOTH schemes (bg #f4f5fb/#0b0f20, ink #232a4d/#e9ecf8, accent #0b655c/#7fdcc9, sky-wash + mesh decorative tokens); the :root[data-theme] blocks and 3-state toggle mechanics survive unchanged; test_web_theme_tokens.py updated first and green
  - @font-face declares both variable fonts; headings render Fraunces (SOFT 75 / WONK 0) and body Albert Sans; WCAG AA pairs recomputed and documented inline
  - Motion adopts --ease-out cubic-bezier(.22,1,.36,1) + --ease-gentle cubic-bezier(.45,0,.25,1), ~0.9s staggered reveals, hover lift with shadow-lift, radius 1.25rem, clamp() section rhythm; every animated rule sits inside the single prefers-reduced-motion guard (test_web_theme_motion.py updated first)

### t6 — The strategy-game board scene: roles, resources, posts, score, ticker (hero.py)

- depends on: t5
- covers: c3, c5, c7, c11, h2, h3, h10, h13, h4
- acceptance:
  - The SVG scene shows role-distinct unit glyphs (scout/harvester/defender), resource nodes, at least two capturable control posts with team-ownership rings, a missions+control+resources score readout, and a one-line message-ticker slot — every color via var(--*) tokens, zero literals
  - With JS off or reduced motion, the unmodified markup composes a legible mid-game poster frame: roles, a gather in progress, a held post, the score, and one ticker line all visible (test_web_hero.py contract rewritten first)
  - Board stays the first child of `<main>` on `/` only, aria-hidden decorative, zero network requests; every depicted mechanic exists in the real game per docs/game-integration.md + tests/fixtures/grid_match_score.json

### t7 — First-party sim engine driving the board (scripts.py)

- depends on: t2, t6
- covers: c3, h4
- acceptance:
  - A sim module in SITE_JS stages per-team orders and resolves turns against the scene: moves, gather->deliver, post captures flipping ownership, the score ticking by the real formula, the ticker posting terse role-flavored lines; consecutive loops differ (seeded variation) — test_web_scripts.py contract updated first
  - The sim activates only under html[data-js] AND prefers-reduced-motion: no-preference; with either absent the poster frame stands; theme toggle + reveal behaviors keep working unchanged; combined JS stays within the t2 ceiling

### t8 — Ops: purge the stale edge cache, verify the toggle on production, document purge in the runbook

- covers: c14, c15, h6, h15
- acceptance:
  - Cloudflare cache purged for /theme.css and /site.js (operator action with the zone token); a fresh prod fetch returns the current 10,604-byte stylesheet with data-theme blocks, and cycling the toggle visibly reskins the page both ways
  - docs/runbooks/cloudflare-league-of-agents-ai.md gains a cache-purge section: the purge-by-URL API call, required token scope, and when purging is (and is no longer) needed once versioned asset URLs ship

### t9 — Design docs + release: rewrite the design direction for the dawn-arena identity; changelog + version bump

- depends on: t5, t6, t7
- covers: c6, h11
- acceptance:
  - docs/design/dazzle-direction.md rewritten for full adoption: the dawn palette rationale, the family bar (membership obvious by design and feel), the board-as-game direction — with the retired flare-orange baseline recorded so the before-state stays verifiable against git history
  - CHANGELOG entry + minor version bump (0.8.0) via /version-bump; stale references to the old identity in README/docs updated

### t11 — Dawn-identity favicon + og:image (user-approved scope pull)

- depends on: t5
- covers: c2
- acceptance:
  - A dawn-palette favicon ships with a prefers-color-scheme dark variant (inline SVG style, like agentculture.org's) and is linked from the shell; the old orange asset is gone
  - A refreshed og:image carries the new identity; link-preview meta on shelled pages points at it; raw agent surfaces remain byte-identical

### t10 — Verification sweep + deploy: the family is obvious and the game is nameable

- depends on: t4, t7, t8, t9, t11
- covers: c1, c4, c10, c13, h2, h5, h9, h12, h14, c21, h16
- acceptance:
  - Deployed to prod; side-by-side with agentculture.org in BOTH themes, a cold viewer who knows agentculture.org names the kinship unprompted (the c21/h16 family bar); a naive viewer watching one board loop names roles, resources, and posts unaided
  - Lighthouse on prod >=90 perf / >=95 a11y in both schemes; full internal link crawl green; raw agent surfaces byte-identical (existing tests unchanged and passing); human and agent paths both first-class
  - Post-deploy curl of / shows versioned asset references whose first fetch returns current-build bytes — no purge step in the deploy path

## Risks

- [unknown_nonblocking] Vendored variable-font byte sizes vs the pinned ceilings — measure at vendoring; t2 sets ceilings with headroom (task t3)
- [unknown_nonblocking] API Gateway HTTP API binary (base64) response path for woff2 through the Lambda adapter — believed supported; prove it in t3's Lambda-path test before building on it (task t3)
- [unknown_nonblocking] The cache purge needs the Cloudflare zone token on the operator side — the agent was (correctly) denied scraping it from editor history; the user runs the purge or exports the token into the shell (task t8)
- [follow_up] A visual grid board for REAL matches in the viewer — the frame's parked v2 follow-up, deliberately out of this pass
- [follow_up] Favicon + og:image still carry the retired orange identity — frame's parked v3; tension with c21's at-a-glance family bar, may deserve pulling in-scope
