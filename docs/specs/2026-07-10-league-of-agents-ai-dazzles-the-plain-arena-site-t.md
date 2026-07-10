# league-of-agents.ai dazzles: the plain arena site transforms — smooth animated visuals, a League of Agents hero video in matching light and dark variants, and an awe-inspiring experience that shows what Claude Code can turn a site into

> league-of-agents.ai dazzles: the plain arena site transforms — smooth animated visuals, a League of Agents hero video in matching light and dark variants, and an awe-inspiring experience that shows what Claude Code can turn a site into
> instruction: One coherent dazzle PR on the web shell: evolved theme.css + minimal first-party JS + hero scene; acceptance = load league-of-agents.ai in light and dark and watch the transformation land in the first viewport

## Audience

- Humans visiting league-of-agents.ai — players, spectators, benchmark readers — plus anyone evaluating what Claude Code can do to a site; agents keep consuming the raw markdown surface untouched
  - instruction: Scope dazzle to with_shell-rendered pages only; prove the agent surface via the existing raw-passthrough byte-identity tests

## Before → After

- Before: Today the site is deliberately austere: server-rendered markdown in one shared shell, a single ~10KB stylesheet at /theme.css, zero `<script>` tags, no images/webfonts/external requests, light/dark only via prefers-color-scheme — a documented performance budget enforced by tests/test_web_theme_budget.py
  - instruction: Anchors: league_site/web/theme.py (tokens + budget contract), league_site/web/shell.py (zero-script shell), tests/test_web_theme_budget.py (enforced budget)
- After: Landing on league-of-agents.ai feels like an event: smooth animated visuals, a League of Agents video that matches the active theme, and polish that makes visitors say 'wow' — in both light and dark mode
  - instruction: PR checklist item: walkthrough of /, /docs, /leaderboard, /about in both schemes with screenshots attached

## Why it matters

- First impressions drive adoption: the arena is live but plain, and a dazzling front door is itself a showcase of what Claude Code can turn a site into — the fairy-godmother transformation is the demo
  - instruction: The landing hero itself tells the arena story (agents clashing on a grid) — the showcase needs no caption to impress

## Requirements

- A League of Agents video plays on the site with light and dark variants, each matching the site theme that is active
  - instruction: Implement as the theme-native animated hero scene (per c13) embedded above the fold on the landing page; the encoded-video pair is the parked follow-up v4; verify it plays in light AND dark
  - honesty: A concrete production path for the video content exists — it can be generated/rendered programmatically in-repo (or the user supplies a produced asset); 'video' does not silently degrade to a static image
- The site supports light and dark modes end to end — every new visual (animations, video, imagery) is authored for both schemes
  - instruction: Author every new visual against the CSS custom properties in theme.py; never hardcode a scheme-specific color outside the token blocks
  - honesty: Every new visual is authored against the design tokens in league_site/web/theme.py, and a both-schemes visual check is part of the ship gate, not an afterthought
- Motion is smooth and pervasive-but-tasteful: page/section entrance animations, hover/focus micro-interactions, and animated hero moments
  - instruction: Entrance reveals via IntersectionObserver toggling a class; keyframes limited to transform/opacity; verify smoothness with a DevTools performance trace
  - honesty: Motion animates compositor-friendly properties only (transform/opacity) and stays smooth on mid-range hardware — no layout-thrashing scroll handlers
- All motion respects prefers-reduced-motion: with it set, animations and autoplaying video are disabled or reduced to opacity-only, and the site remains fully usable
  - instruction: Wrap all motion in @media (prefers-reduced-motion: no-preference); add a stylesheet test asserting the guard exists; manual OS-setting check in both schemes
  - honesty: With prefers-reduced-motion set, nothing autoplays or translates — verified by an automated check or an explicit manual test step in both schemes
- The agent-facing raw surface stays byte-identical: *.md passthrough, /llms.txt, /front, and /theme.css remain uncontaminated by the dazzle layer — the transformation is for shelled human pages only
  - instruction: Do not touch _is_raw_passthrough or the unshelled path set; run the existing byte-identity tests as the proof
  - honesty: The existing raw-passthrough tests (byte-identity of *.md, /llms.txt, /front) pass unchanged after the dazzle layer lands
- The performance budget is renegotiated, not abandoned: the docstring contract in league_site/web/theme.py and the budget test are updated to a new explicit budget (CSS size, JS allowance, asset weight), and Lighthouse performance stays >= 90 on the landing page
  - instruction: First commit of the PR updates theme.py's budget contract + test numbers (CSS/JS/asset bytes); post-deploy, run Lighthouse against <https://league-of-agents.ai> and record scores
  - honesty: The new budget numbers (CSS bytes, JS bytes, total asset weight) are written into theme.py's contract and the budget test BEFORE implementation, and Lighthouse >= 90 is measured on the real deployed site, not just localhost
- A manual theme toggle (light / dark / system) is added to the header, persisted per visitor, complementing prefers-color-scheme
  - instruction: Three-state header toggle (light/dark/system) persisted in localStorage; tiny inline pre-paint script in `<head>` sets data-theme on `<html>`; tokens keyed off :root[data-theme=dark] and prefers-color-scheme fallback for system
  - honesty: The toggle causes no flash-of-wrong-theme: theme class is applied before first paint by a tiny inline script, and system remains the default for first-time visitors
- The hero 'video' is theme-native rather than two encoded files: an animated, self-contained scene (CSS/SVG/canvas driven by the site's design tokens) that automatically renders in light or dark — with an optional `<video>` element with per-scheme sources only if an encoded asset is later produced
  - instruction: Build the hero as an inline SVG/CSS (or canvas reading getComputedStyle) scene driven by var(--accent)/var(--bg)/var(--text); acceptance = flipping the toggle re-skins the scene live without reload
  - honesty: The hero scene derives every color from the live CSS custom properties, so flipping the theme re-renders it instantly without reload — proving the light/dark variants are one artifact, not two divergent ones
- Zero broken links on the site: every nav, footer, and in-content link on every shelled page resolves — internal links to 200-serving pages, external links valid or removed
  - instruction: Server-side test: render every shelled page via the WSGI app, collect internal hrefs (nav, footer, content), assert each resolves 200; post-deploy, Playwright crawls the live site and asserts the same plus no console errors
  - honesty: Link integrity is proven by automated crawl — a server-side test walking every shelled page's hrefs plus a live Playwright crawl post-deploy — never by eyeballing
- Verification is browser-real via the Playwright plugin: both-scheme screenshots, toggle interaction, live hero re-skin proof, reduced-motion emulation, and a full link crawl run against the real rendered site
  - instruction: Use the playwright plugin MCP tools (browser_navigate, browser_take_screenshot, browser_evaluate for color-scheme/reduced-motion emulation, browser_snapshot) for t7/t8 verification evidence
  - honesty: The Playwright checks run against the real rendered site (local WSGI during development, the deployed domain at the ship gate) and their evidence — screenshots, crawl results — is attached to the PR

## Honesty conditions

- The shipped site is judged dazzling by the user on the live domain in both schemes — not merely 'has animations'; the fairy-godmother before/after is visible to a first-time visitor
- The dazzle layer touches only shelled human pages; the agent surface is provably unchanged (existing byte-identity tests)
- A manual walkthrough of landing/docs/leaderboard/about in both schemes finds no page left visually 'plain' — the shell upgrade carries every page, not just the landing
- The transformation is real to a first-time visitor without explanation — the wow lands in the first viewport, before any scrolling or reading
- The described before-state is verified against the repo at implementation time (theme.py budget contract, zero-script shell.py, budget test) so the transformation is measured from the real baseline, not a remembered one
- Every request on every shelled page resolves to the platform's own origin — checked in the DevTools network panel as part of the ship gate
- The gate numbers are measured and recorded (Lighthouse perf/a11y in both schemes) before declaring shipped — a screenshot or report is attached to the PR
- If during implementation the shell provably cannot deliver a required experience, work stops and the rewrite decision comes back to the user rather than being made unilaterally

## Success signals

- Ship gate: landing page Lighthouse performance >= 90 and accessibility >= 95 in both schemes; the hero animation/video visibly plays in light AND dark; zero regressions in the raw-markdown byte-identity tests
  - instruction: Run Lighthouse (desktop + mobile) on the deployed landing in both schemes; attach the report to the PR; CI keeps raw byte-identity tests green

## Scope / boundaries

- No external requests introduced: no CDNs, webfonts, or third-party scripts; every asset (JS, video/animation, images) is served first-party from the platform
  - instruction: Review gate: DevTools network panel shows zero third-party origins across landing/docs/leaderboard
- This pass ships as progressive enhancement inside the server-rendered WSGI shell — modern CSS (view transitions, scroll-driven reveals) plus small first-party vanilla JS deliver the SPA-smooth feel; the framework-rewrite option (c18) stays open for a future pass if ambitions outgrow the shell
  - instruction: Deliver dazzle via theme.css evolution + a small /site.js (and inline pre-paint theme snippet); no build step, no framework deps; View Transitions API for cross-page smoothness where supported

## Non-goals

- Not redesigning the match viewer or gameplay UX — this pass is the site shell, landing page, and doc pages; game surfaces only inherit the shared theme improvements
  - instruction: Match viewer and game endpoints receive only what /theme.css gives them for free; no viewer-specific work in this pass

## Decisions

- A framework rewrite is acceptable if it opens a much better experience and flexibility to do more — the no-SPA boundary is not absolute
- The visual direction is authored through the frontend-design plugin's process before implementation: a token/type/layout/signature design plan grounded in the arena subject, critiqued against the generic-AI-look calibration; the hero is the signature element and boldness concentrates there
  - instruction: Main agent authors the design direction per the frontend-design skill (two-pass: plan tokens/type/layout/signature, then critique vs the generic-default calibration) and bakes it verbatim into the t4/t5 task briefs; copy follows the skill's writing guidance (plain verbs, active voice, user-side naming)

## Hard questions

- What IS the video, concretely: (a) a code-driven, theme-native animated hero scene (CSS/SVG/canvas — one artifact, adapts to both schemes live), (b) two encoded video files (WebM/MP4 light+dark) produced offline and swapped per scheme, or (c) a user-supplied produced video? Claude Code can build (a) fully in-repo; (b) needs an offline render pipeline and binary asset serving; (c) needs an asset from the user.

## Open / follow-up

- Favicon + og:image/social-share cards matching the new look (site currently ships zero images)
- Extending the dazzle pass to the match viewer / live game surfaces beyond inherited theme improvements
- Record the finished hero scene into encoded WebM/MP4 light+dark video files for social embeds / og:video
