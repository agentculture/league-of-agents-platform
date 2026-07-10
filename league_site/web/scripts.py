"""The League of Agents first-party JavaScript — all of it.

:mod:`league_site.web.theme` holds the site's one stylesheet as a plain
Python string (:data:`~league_site.web.theme.STYLESHEET`); this module is
its JS twin. It holds the site's *entire* first-party JavaScript as two
plain strings, and :mod:`league_site.web.shell` is the only consumer:

:data:`SITE_JS`
    Served at ``/site.js`` (``<script defer src="/site.js">`` in the
    shell's ``<head>``). Three behaviors, all progressive enhancements:

    * **Theme toggle** — the header's ``#theme-toggle`` button cycles
      light → dark → system. An explicit choice is stored in
      ``localStorage`` under the key ``"theme"`` (``"light"`` /
      ``"dark"``) and mirrored onto ``<html data-theme="…">``, which the
      stylesheet's ``:root[data-theme=…]`` token blocks honor over the OS
      preference. Choosing *system* removes both the attribute and the
      stored key, handing the decision back to ``prefers-color-scheme``.
      The button's glyph, ``title`` and ``aria-label`` are repainted to
      reflect the **current** state on every change (and once at load, to
      correct the static markup when a stored choice exists).
    * **Reveal stagger** — the direct element children of ``main#main``
      are stamped with class ``reveal`` plus an incremental
      ``--reveal-delay`` (60ms per element, at most 12 elements), so page
      content enters with the quiet staggered cascade the design direction
      asks for. The landing hero (class ``hero``) is skipped — it
      orchestrates its own entrance (see :mod:`league_site.web.hero`) and
      must not be double-animated.
    * **Reveal-on-scroll** — elements carrying class ``reveal`` gain class
      ``revealed`` when they enter the viewport (``IntersectionObserver``,
      threshold 0.1, unobserved after revealing). Where
      ``IntersectionObserver`` is missing, everything is revealed
      immediately — motion is decoration, never a gate on content.

:data:`PRE_PAINT_JS`
    A tiny render-blocking snippet the shell inlines in ``<head>``
    **before** the stylesheet link (it must run before first paint, so it
    cannot ride the deferred ``/site.js``). It does exactly two things:

    * reads ``localStorage["theme"]`` inside try/catch (storage can be
      disabled or throw in private modes) and sets
      ``document.documentElement.dataset.theme`` when the value is
      ``"light"`` or ``"dark"`` — so a stored explicit choice applies
      before the first painted frame and there is no flash of the wrong
      theme;
    * sets ``document.documentElement.dataset.js = "1"`` — the t3/t4
      interface contract: t4's reveal styles hide pre-reveal elements
      only under ``html[data-js]``, so with JavaScript disabled nothing
      is ever hidden and every page stays fully readable.

Budget
------
:data:`~league_site.web.theme.JS_BUDGET_BYTES` (8KB) is the ceiling for
first-party JS **combined** — ``len(SITE_JS) + len(PRE_PAINT_JS)``. The
pre-paint snippet lives inline in every page rather than at ``/site.js``,
but it is still JS the visitor pays for, so it counts against the same
budget; ``tests/test_web_theme_budget.py`` and ``tests/test_web_scripts.py``
enforce both the combined ceiling and the snippet's own ~300-byte cap.
No external requests, ever: no CDN, no analytics, no third-party scripts.

Style: vanilla ES5-compatible JS, one strict-mode IIFE, zero globals,
zero console output — modern enough to be honest, old enough to run
anywhere the site renders.
"""

from __future__ import annotations

#: Inlined by the shell as ``<script>{PRE_PAINT_JS}</script>`` before the
#: stylesheet link. Keep it tiny (~300 bytes including tags) — it blocks
#: parsing on every page. See the module docstring for what it does and why
#: it counts toward :data:`~league_site.web.theme.JS_BUDGET_BYTES`.
PRE_PAINT_JS = (
    '(function(){var d=document.documentElement;d.dataset.js="1";'
    'try{var t=localStorage.getItem("theme");'
    'if(t==="light"||t==="dark")d.dataset.theme=t}catch(e){}})()'
)

SITE_JS = """\
/* League of Agents — first-party site JS. See league_site/web/scripts.py
   for the contract (theme toggle + reveal-on-scroll) and the 8KB budget
   this file shares with the inline pre-paint snippet. No external
   requests, no globals, no console output. */
(function () {
  "use strict";

  var root = document.documentElement;
  var KEY = "theme";
  var GLYPHS = { light: "\\u2600", dark: "\\u263e", system: "\\u25d0" };
  var NEXT = { light: "dark", dark: "system", system: "light" };

  function current() {
    var t = root.dataset.theme;
    return t === "light" || t === "dark" ? t : "system";
  }

  function apply(state) {
    if (state === "system") {
      delete root.dataset.theme;
      try { localStorage.removeItem(KEY); } catch (e) { /* storage off */ }
    } else {
      root.dataset.theme = state;
      try { localStorage.setItem(KEY, state); } catch (e) { /* storage off */ }
    }
  }

  function paint(button, state) {
    button.textContent = GLYPHS[state];
    button.title = "Theme: " + state;
    button.setAttribute(
      "aria-label",
      "Theme: " + state + " \\u2014 activate to switch to " + NEXT[state]
    );
  }

  function initToggle() {
    var button = document.getElementById("theme-toggle");
    if (!button) { return; }
    paint(button, current());
    button.addEventListener("click", function () {
      var next = NEXT[current()];
      apply(next);
      paint(button, next);
    });
  }

  function initStagger() {
    // Stamp the quiet staggered reveal onto <main>'s direct children —
    // except the hero, which orchestrates its own entrance (t5). Capped
    // at 12 elements; anything past the cap simply renders, unstamped.
    //
    // Two deliberate refusals:
    // * Auto-refreshing pages (the live match watch page reloads every
    //   5s via <meta http-equiv="refresh">) get NO entrance animation —
    //   replaying the cascade on every refresh would make the transcript
    //   a visitor is reading blink out and fade back in, forever.
    // * A page restored mid-scroll (back/forward navigation) starts its
    //   cascade only for what is actually on screen; and the stagger
    //   delay is only ever given to elements visible at load — a delay
    //   on scroll-time reveals would keep below-fold content invisible
    //   for the delay AFTER it entered the viewport.
    if (document.querySelector('meta[http-equiv="refresh" i]')) { return; }
    var main = document.getElementById("main");
    if (!main) { return; }
    var fold = window.innerHeight || 0;
    var kids = main.children;
    var picked = [];
    var i;
    for (i = 0; i < kids.length && picked.length < 12; i++) {
      if (kids[i].classList.contains("hero")) { continue; }
      picked.push({
        el: kids[i],
        onScreen: kids[i].getBoundingClientRect().top < fold
      });
    }
    for (i = 0; i < picked.length; i++) {
      picked[i].el.classList.add("reveal");
      if (picked[i].onScreen) {
        picked[i].el.style.setProperty("--reveal-delay", (i * 60) + "ms");
      }
    }
  }

  function initReveal() {
    var targets = document.querySelectorAll(".reveal");
    var i;
    if (!targets.length) { return; }
    if (!("IntersectionObserver" in window)) {
      for (i = 0; i < targets.length; i++) { targets[i].classList.add("revealed"); }
      return;
    }
    var observer = new IntersectionObserver(function (entries) {
      for (var j = 0; j < entries.length; j++) {
        if (entries[j].isIntersecting) {
          entries[j].target.classList.add("revealed");
          observer.unobserve(entries[j].target);
        }
      }
    }, { threshold: 0.1 });
    for (i = 0; i < targets.length; i++) { observer.observe(targets[i]); }
  }

  function init() {
    initToggle();
    initStagger();
    initReveal();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
"""
