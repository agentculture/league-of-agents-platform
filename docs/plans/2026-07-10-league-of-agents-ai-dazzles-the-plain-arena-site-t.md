# Build Plan — league-of-agents.ai dazzles: the plain arena site transforms — smooth animated visuals, a League of Agents hero video in matching light and dark variants, and an awe-inspiring experience that shows what Claude Code can turn a site into

slug: `league-of-agents-ai-dazzles-the-plain-arena-site-t` · status: `exported` · from frame: `league-of-agents-ai-dazzles-the-plain-arena-site-t`

> league-of-agents.ai dazzles: the plain arena site transforms — smooth animated visuals, a League of Agents hero video in matching light and dark variants, and an awe-inspiring experience that shows what Claude Code can turn a site into

## Tasks

### t1 — Renegotiate the performance budget contract first: new explicit ceilings (CSS bytes, first-party JS bytes, asset weight) written into league_site/web/theme.py's docstring contract and tests/test_web_theme_budget.py

- instruction: Read theme.py's docstring budget section + tests/test_web_theme_budget.py first; verify the live baseline (CSS bytes served at /theme.css, zero `<script>` in shell.py). Then write the new contract: CSS ceiling ~24KB, first-party JS ceiling ~8KB, external requests = 0, and update the test to enforce all three. Keep the docstring's WCAG table intact.
- covers: c8, c11, h6, h14
- acceptance:
  - theme.py's budget contract states explicit ceilings for CSS bytes, first-party JS bytes, and total asset weight
  - tests/test_web_theme_budget.py enforces the CSS and JS ceilings and fails when exceeded
  - the pre-dazzle baseline (zero-script shell, ~10KB CSS) is re-verified against the repo in this task, not recalled from memory

### t2 — Theme token plumbing for the manual toggle: tokens keyed off :root[data-theme=dark] with prefers-color-scheme fallback for system, in league_site/web/theme.py

- instruction: Extend theme.py's token blocks: keep :root (light) + the prefers-color-scheme dark block for system default; add :root[data-theme=dark] and :root[data-theme=light] override blocks so an explicit visitor choice beats the OS. Keep html color-scheme in sync with the active theme so form controls/scrollbars match. Update the docstring's WCAG table only if token values change.
- depends on: t1
- covers: c6, c12
- acceptance:
  - with `<html data-theme="dark">`, dark tokens apply even when the OS is light (and vice versa for data-theme=light)
  - with no data-theme attribute, prefers-color-scheme decides — first-visit behavior unchanged
  - WCAG contrast documentation in theme.py stays in sync with any token change

### t3 — Header theme toggle + first-party /site.js + pre-paint snippet: three-state toggle (light/dark/system) in the shell header, localStorage persistence, tiny inline head script applying data-theme before first paint; /site.js served like /theme.css (new module, e.g. league_site/web/scripts.py)

- instruction: New module league_site/web/scripts.py exporting SITE_JS (plain string, mirroring theme.STYLESHEET); shell.py serves it at /site.js and links it with `<script defer>`. Pre-paint inline snippet in `<head>`: read localStorage 'theme', set document.documentElement.dataset.theme when light/dark, and set dataset.js='1' (the hook t4's reveal styles gate on). Header `<button>` cycles light->dark->system (on system: remove the attribute + clear storage), aria-label reflecting current state. IntersectionObserver reveal logic (adds .revealed to .reveal elements) also lives in site.js.
- depends on: t2
- covers: c12, h7
- acceptance:
  - toggle cycles light/dark/system, persists in localStorage, and applies before first paint — no flash of wrong theme
  - /site.js is served first-party with the correct content type; no external requests are introduced
  - with JS disabled every page remains fully readable and theming falls back to the system preference

### t4 — Motion system in theme.css: entrance reveals, hover/focus micro-interactions, view-transition smoothness — transform/opacity keyframes only, every motion rule inside @media (prefers-reduced-motion: no-preference)

- instruction: All in theme.py's stylesheet: .reveal entrance (opacity 0 + translateY(8px) -> visible via .revealed, staggerable via a custom-property delay) — initial hidden state gated on html[data-js] AND the motion media query so content is never hidden with JS off or reduced motion on; hover/focus micro-interactions on .button/.card/nav (small transform + accent glow); @view-transition {navigation: auto} for cross-page fades where supported; subtle wordmark-glyph pulse. Keyframes/transitions animate transform/opacity only; every motion rule inside @media (prefers-reduced-motion: no-preference).
- depends on: t2
- covers: c7, h3, c9
- acceptance:
  - all keyframes and transitions animate transform/opacity only — a grep of the stylesheet finds no animated layout properties
  - every motion rule sits inside the prefers-reduced-motion: no-preference guard
  - entrance reveals need no scroll-linked JS layout reads (IntersectionObserver class toggle only)

### t5 — The hero: a theme-native animated League of Agents arena scene (inline SVG/CSS driven by var(--accent)/var(--bg)/var(--text)) embedded above the fold on the landing page only — agents advance and clash on a grid, score pulses, accent flares

- instruction: New module league_site/web/hero.py exporting the hero fragment (inline SVG + its own scoped `<style>` so the file stays disjoint from t4's theme.py work); shell.py embeds it only for `_LANDING_PATHS`, as the first element of `<main>` before the rendered markdown. Scene: stylized arena grid (var(--border)/var(--surface-2)), 3-5 geometric agent pieces advancing along lanes (transform keyframes), a clash with an accent flare (var(--accent) opacity/scale pulse), a score tick; ~12s seamless loop; zero hardcoded colors — every fill/stroke via var(). Under prefers-reduced-motion the scene is a dignified still mid-clash. Overlay: headline + CTA using the existing .button. The landing's .md passthrough stays byte-identical (hero is shell-injected, never content).
- depends on: t3, t4
- covers: c1, c3, c4, c5, c13, h1, h8, h13
- acceptance:
  - the scene renders above the fold on / and /index only, every color derived from the live design tokens
  - flipping the theme toggle re-skins the scene live without a reload
  - the scene animates smoothly and degrades to a dignified still composition under prefers-reduced-motion
  - the landing page's raw .md passthrough remains byte-identical

### t6 — Raw-surface byte-identity + no-external-origin proof: tests asserting *.md, /llms.txt, /front are untouched by the dazzle layer, and emitted HTML/CSS/JS reference no third-party origins

- instruction: Tests only — extend the existing shell/raw-passthrough test module; do not modify shell.py. Assert byte-identity of /index.md, /llms.txt, /front against the unwrapped app with the dazzle shell active. Add a no-external-resources test: scan rendered / HTML head+script+link tags, /theme.css url()s, and /site.js for externally-FETCHED resources and assert none (anchor hrefs in markdown content are allowed — the rule is about fetched resources, not links).
- depends on: t5
- covers: c2, c10, c15, h5, h11
- acceptance:
  - the existing byte-identity tests for *.md, /llms.txt, /front pass unchanged
  - a test asserts no third-party origin URL appears in emitted HTML, /theme.css, or /site.js

### t7 — Accessibility hardening: test asserting the stylesheet guards all motion behind prefers-reduced-motion; toggle keyboard-operable with a visible focus ring and an accessible name/state

- instruction: Tests only: parse served /theme.css and assert every @keyframes/animation/transition declaration sits inside the prefers-reduced-motion: no-preference block; assert the theme toggle button exposes an accessible name reflecting its current state. Any markup fix this surfaces belongs in t3's files — flag it, don't fork shell.py in parallel.
- depends on: t3, t4
- covers: c9, h4
- acceptance:
  - a test asserts every animation/transition rule in the served stylesheet is inside the prefers-reduced-motion guard
  - the toggle is fully keyboard-operable, exposes an accessible name and current state, and keeps a visible focus indicator

### t8 — Ship gate (ops, main session): deploy, then Lighthouse >=90 perf / >=95 a11y on the live landing in both schemes (desktop+mobile), zero-third-party network-panel check, walkthrough screenshots of /, /docs, /leaderboard, /about in both schemes attached to the PR; diff review confirms no framework/build step and the match viewer untouched (c16), honoring the open-rewrite decision (c18)

- instruction: Main-session ops after the code waves merge: deploy via the repo's standing path (infra/ + Makefile), run Lighthouse desktop+mobile against <https://league-of-agents.ai> in both schemes (via the toggle and via OS preference), record scores in the PR; DevTools network panel zero-third-party check on /, /docs, /leaderboard; both-schemes screenshots of /, /docs, /leaderboard, /about; final diff review: no framework deps in pyproject, no build step, match viewer untouched. Any gate failure iterates before 'shipped'; if the shell provably cannot deliver, stop and return the rewrite decision to the user.
- depends on: t5, t6, t7
- covers: c1, c3, c17, c19, h2, h9, h10, h12, h15, h16
- acceptance:
  - Lighthouse reports >=90 performance and >=95 accessibility on the deployed landing in both schemes, desktop and mobile — report attached to the PR
  - DevTools network panel shows zero third-party origins across /, /docs, /leaderboard
  - both-schemes walkthrough screenshots of /, /docs, /leaderboard, /about are attached to the PR
  - diff review confirms: no frontend framework or build step, match viewer untouched beyond inherited theme.css; if the shell provably cannot deliver a required experience, work stops and the rewrite decision returns to the user

## Risks

- [unknown_nonblocking] Lighthouse scores on the deployed Lambda vary with cold starts — measure warm, note methodology in the PR
- [unknown_nonblocking] Exact new budget ceilings (CSS/JS bytes) are picked in t1 and may need one adjustment round once the hero lands
- [unknown_nonblocking] View Transitions API support varies by browser — cross-page smoothness is progressive enhancement, never required for usability
- [follow_up] Encoded WebM/MP4 hero pair for social embeds / og:video is a parked follow-up (frame v4), not in this plan
