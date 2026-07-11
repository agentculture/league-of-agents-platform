# league-of-agents.ai now moves like a sibling of agentculture.org — one shared design language, buttery-smooth experience — and its demo board has grown into an awe-inspiring living strategy game: units with roles, resources on the field, and posts that matter

> league-of-agents.ai now moves like a sibling of agentculture.org — one shared design language, buttery-smooth experience — and its demo board has grown into an awe-inspiring living strategy game: units with roles, resources on the field, and posts that matter.
> instruction: Verify by loading league-of-agents.ai and agentculture.org side by side (both themes) and by watching one full hero loop: family resemblance + nameable game concepts.

## Audience

- Visitors to league-of-agents.ai — humans and agents sizing up the arena — plus the AgentCulture community who will recognize the shared brand.

## Before → After

- Before: Today the sites are unrelated: league-of-agents.ai is a scoreboard/terminal identity (system fonts, flare-orange accent, step-and-settle motion) built as Python-string CSS/JS under hard budgets (CSS<=24KB, JS<=8KB, zero external requests); agentculture.org is a dawn-mesh identity (Fraunces + Albert Sans variable webfonts, aurora teal, 'breathing' motion, no theme toggle). The demo board (hero.py) is a hardcoded decorative 12s SVG/CSS loop: 4 abstract pieces, one clash, one score tick — no roles, resources, or posts.
- Before: Toggle-bug root cause (verified 2026-07-11): the origin Lambda serves the current stylesheet (10,604 B, with data-theme blocks — byte-identical to repo), but Cloudflare's edge cache still serves the pre-dazzle /theme.css (5,863 B, no data-theme rules) under cache-control max-age=14400. New HTML + new JS + stale CSS: the toggle stamps data-theme but no rule reacts. The JS and markup are NOT buggy.
- After: The site's design, style and experience are aligned with agentculture.org (../org, repo agentculture/org): same design language, same smoothness of experience.
  - instruction: Implement in league_site/web/theme.py (tokens, easing, radius, rhythm) and scripts.py (reveal tuning); update the budget/motion contract tests first; verify with the side-by-side review and Lighthouse >=90/>=95 in both schemes.
- After: The demo board reads as an awe-inspiring strategy game: units have roles, and the field has resources and posts.
  - instruction: Implement in league_site/web/hero.py (SVG scene: role glyphs scout/harvester/defender, resource nodes, control posts, score + ticker slots) plus a first-party sim module in league_site/web/scripts.py that stages orders and animates the scene turn-by-turn; JS-off/reduced-motion renders the authored poster frame. Update tests/test_web_hero.py + test_web_scripts.py contracts first.

## Why it matters

- The demo board is the site's first impression of the game's depth; today it moves but doesn't convey strategy. Brand alignment makes league-of-agents.ai legible as part of the AgentCulture family.

## Requirements

- The upgraded demo board showcases the REAL grid-lane game's concepts, not invented ones: units with visible roles (scout / harvester / defender rendered as distinct glyph shapes), resource nodes that get gathered and delivered, and capturable posts whose control flips during the loop — with the score readout reflecting the game's actual outcome formula (missions + control + resources).
  - honesty: Every mechanic the board depicts is checked against docs/game-integration.md and tests/fixtures/grid_match_score.json — the board never shows a move the real engine cannot make.
- Experience alignment adopts agentculture.org's motion QUALITY: its two easing tokens (cubic-bezier(0.22,1,0.36,1) settle, cubic-bezier(0.45,0,0.25,1) breathe), longer staggered reveals (~0.9s), hover lift with shadow-lift, CSS view transitions, generous radius and section rhythm — tuned so nothing snaps.
  - honesty: All new motion stays inside the single prefers-reduced-motion guard and the html[data-js] gate; with motion inert the board still composes a legible poster frame showing roles, resources, and posts.
- The three-state theme toggle actually works on the live site — the user tried it and it did nothing. Diagnose and fix as part of this pass.
  - instruction: Two legs: (ops, immediate) purge Cloudflare cache for /theme.css and /site.js — operator action with the zone token, or wait out the 4h TTL; (code, durable) versioned asset URLs per the cache-busting requirement, shipped with this pass.
  - honesty: Verified on production, not locally: after the fix ships, a fresh fetch of / through Cloudflare yields HTML whose referenced stylesheet contains the data-theme blocks, and cycling the toggle visibly reskins the page in both directions.
- Asset URLs become versioned (cache-busted) — e.g. `/theme.css?v=<version>` or a content-hash path — so shelled HTML always references the exact CSS/JS build it shipped with. A deploy can then never strand a stale stylesheet against new markup, for CDN and browser caches alike; long-lived cache headers become safe by design.
  - honesty: After the next deploy, curl of prod / shows versioned asset references, and fetching the referenced URL returns the current build's bytes on the FIRST request (no purge step in the deploy runbook).

## Honesty conditions

- The announcement holds only if BOTH legs ship in one pass: the family alignment is visible site-wide AND the hero board demonstrably shows roles, resources, and posts — shipping one without the other fails the frame.
- Alignment is verified side-by-side in both color schemes, and any asset-budget growth is renegotiated test-first: the budget constants and their tests change with the spec, never silently.
- A first-time visitor watching the hero loop alone (no docs, no caption-reading) can say 'units have roles, they collect resources, they take posts' — validated by actually watching it, not by intent.
- Both named audiences stay first-class after the redesign: the human path (shelled pages) gets the new experience while the agent path (`/<slug>.md`, `/llms.txt`, /front, start-agent docs) is untouched and byte-identical.
- Holds only if the board actually carries the first impression: it remains the first rendered element on / and the redesign never demotes it below the fold.
- The before-state is verifiable in git at frame time (theme.py flare-orange tokens, hero.py 12s hardcoded loop, budget constants CSS<=24KB/JS<=8KB) — the baseline is the repo, not memory.
- Proven mechanically: tests/test_web_raw_surface.py and the byte-identity assertions pass unchanged after the redesign; no new framework or build step appears in pyproject/infra.
- The hero makes zero network requests for match data (static markup + first-party sim only), and the real-match viewer board stays parked as v2 in the exported spec rather than silently absorbed into this pass.
- After the redesign the header toggle still cycles light -> dark -> system on every shelled page, persists across navigation, and visibly reskins the dawn palette both ways — verified on production.
- Each signal is checked as stated, not assumed: side-by-side screenshots in both themes, a naive-viewer read of one hero loop, Lighthouse against production, and the renegotiated contract tests green in CI.
- The root cause is reproducible from the recorded 2026-07-11 probes (origin 10,604 B with data-theme blocks vs edge 5,863 B without; cache-control max-age=14400, cf-cache-status HIT); if prod drifts before implementation, re-verify before building.
- Checked cold, not assumed: someone who knows agentculture.org is shown the new league-of-agents.ai landing page with no context and names the family resemblance unprompted, in both color schemes.

## Success signals

- Side-by-side, the two sites read as siblings (type voice, motion feel, component vocabulary); a first-time visitor watching one hero loop can name roles, resources, and posts without reading docs; Lighthouse stays >=90 perf / >=95 a11y in both schemes; all budget/motion/hero contract tests updated first and passing.
- The site feels like part of the AgentCulture.org family, and that membership is OBVIOUS by design and feel alone — a first-time visitor who knows agentculture.org recognizes the kinship at a glance (palette, type voice, motion, mesh motif) before reading any copy.
  - instruction: This is the acceptance bar for the whole alignment leg: run the side-by-side family check (both themes) as part of the final verification sweep; if the kinship needs explaining, the pass has not met the bar.

## Scope / boundaries

- Agent-facing raw surfaces (`/<slug>.md`, `/llms.txt`, /front) stay byte-identical; no framework rewrite — the site remains a Python WSGI app with string-constant assets; dazzle scope stays on with_shell pages.
- The hero board remains decorative (aria-hidden), self-contained, and NOT driven by live match data — a visual board for real matches in the viewer is a parked follow-up, not this frame.
- The three-state theme toggle stays (deliberate divergence: agentculture.org has no toggle, OS-only). Alignment means shared family feel, not feature regression.
  - instruction: Keep #theme-toggle + PRE_PAINT_JS + localStorage 'theme' mechanics; port the :root[data-theme] token blocks to the new dawn palette; verify on prod post-deploy (ties to c14/c16).

## Decisions

- USER DECISION — 'posts' means BOTH: capturable control posts on the field (the game's can_capture / control scoring) AND a feed-style message ticker where units post terse commentary as they play (faithful to real matches: agents send per-turn messages, rendered today in the transcript viewer; 'communication' is a scored cooperation signal).
- USER DECISION — FULL ADOPTION of agentculture.org's visual identity: dawn palette (bg #f4f5fb/#0b0f20, ink #232a4d/#e9ecf8, accent aurora teal #0b655c/#7fdcc9 with the sky-wash/mesh decorative tokens), Fraunces Variable serif display voice, mesh nodes-and-threads motif. The flare-orange scoreboard/terminal identity is retired. The three-state theme toggle stays (c12).
- USER DECISION — adopt BOTH self-hosted variable fonts like agentculture.org: Fraunces Variable (display, SOFT 75/WONK 0 axis) + Albert Sans Variable (body), woff2, preloaded, served first-party (zero external requests preserved). Asset budget contract renegotiated test-first; Lambda/API Gateway must serve binary font assets correctly.
- USER DECISION — the demo board is a JS-DRIVEN SIMULATION: a small first-party engine (in the site's one JS file, renegotiated budget) stages per-team orders and animates the inline SVG turn by turn, so loops vary run to run. With JS off or reduced motion, a hand-authored composed poster frame renders (roles + resources + posts legible, ticker frozen on one line).
- USER DECISION — favicon + og:image are pulled INTO this pass (supersedes parked follow-up v3): a dawn-identity favicon with a prefers-color-scheme variant (like agentculture.org's) and a refreshed og:image, folded into the design-docs/release wave. Browser tab and link previews are part of 'obvious at a glance'.

## Open / follow-up

- A visual grid board for REAL matches in the match viewer (`/matches/<id>/watch`) — the dazzle spec's own open follow-up; this frame only upgrades the decorative hero
- Favicon + og:image refresh to match whatever palette direction is chosen
