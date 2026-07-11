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
    * **Board sim** (``initSim``, task t7) — a first-party game engine that
      brings the landing hero to life, playing the authored scene turn by
      turn so a visitor watches an actual match unfold. It is documented in
      full below.

The board sim (``initSim``) — how the hero scene comes alive
------------------------------------------------------------------------
The hero (:mod:`league_site.web.hero`) ships as a static poster of a
mid-game moment. ``initSim`` stages per-team orders and resolves them
against that scene, one turn at a time.

*Activation gates.* The sim is a motion enhancement and runs only when
BOTH hold, checked once at init:

* ``document.documentElement.dataset.js === "1"`` — JS is on (the
  pre-paint snippet stamped ``html[data-js]``); and
* ``window.matchMedia("(prefers-reduced-motion: reduce)")`` exists and
  does **not** match — motion is welcome.

With either gate absent the authored poster frame stands untouched. A
``change`` listener on the media query tears the sim down and restores the
poster the instant motion is refused mid-session. On a page with no hero
board the sim simply finds no ``.hero-svg`` and returns.

*Interface consumed* (authoritative in :mod:`league_site.web.hero`'s
docstring; ``tests/test_web_scripts.py`` cross-checks every hook against
``hero.HERO_HTML`` so drift fails loudly): the ``.hero-svg`` board of 40px
cells (cell ``[col,row]`` center = ``(60 + 40*col, 80 + 40*row)``, 8x5
field); ``.hp-unit[data-unit|data-team|data-role]`` moved by rewriting the
``transform`` *attribute* (never the CSS property — a guarded
``transition: transform`` on ``.hp-unit`` eases each rewrite);
``.hp-post[data-owner]`` captured by setting ``data-owner`` (CSS recolors
the ownership ring); ``.hp-res`` nodes and ``.hero-gather`` beams for the
economy; the ``#hero-score-blue`` / ``#hero-score-red`` ``[data-term]``
tspans (missions + control + resources = total); ``#hero-ticker``; and the
eyebrow's ``#hero-turn`` counter.

*Game faithfulness* (spec honesty h3 — the board never shows a move the
real engine can't make; grounded in ``docs/game-integration.md`` and
``tests/fixtures/grid_match_score.json``): units move to an adjacent cell
per turn (no teleports); only harvesters gather/deliver (``canGather``);
scouts and defenders capture posts (``canCapture``); ``hold`` is always
legal. Orders stage per team and resolve once both teams have staged, so
every unit's move applies together on each tick. Score moves for real —
resources tick on a harvester delivery, missions on a capture, control is
recomputed each turn from the live owned-post count.

*Pacing and loop.* One resolved turn every ~2.75s (the CSS transform
transition does the settling); a full loop of 12 turns, then a quiet reset
— the live layer fades, the authored poster is restored, the PRNG
reseeds, and the sim plays again. Consecutive loops differ: a tiny
xorshift PRNG seeded from ``Date.now()`` drives target choice, tie-breaks,
and commentary. The poster is the truth at load — every attribute the sim
mutates is snapshotted at init and restored exactly on each reset, so the
still is idempotent and never corrupted.

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
:data:`~league_site.web.theme.JS_BUDGET_BYTES` (16KB) is the ceiling for
first-party JS **combined** — ``len(SITE_JS) + len(PRE_PAINT_JS)``. The
pre-paint snippet lives inline in every page rather than at ``/site.js``,
but it is still JS the visitor pays for, so it counts against the same
budget; ``tests/test_web_theme_budget.py`` and ``tests/test_web_scripts.py``
enforce both the combined ceiling and the snippet's own ~300-byte cap.
This codebase treats bytes as contractual — the sim is written lean, well
under the ceiling.
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
   for the contract (theme toggle + reveal-on-scroll + the t7 board sim that
   plays the landing hero) and the 16KB budget this file shares with the
   inline pre-paint snippet. No external requests, no globals, no console
   output. */
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
    try {
      if (document.querySelector('meta[http-equiv="refresh"]')) { return; }
    } catch (e) { /* selector threw: treat as no refresh meta */ }
    var main = document.getElementById("main");
    if (!main) { return; }
    var fold = window.innerHeight || 0;
    var kids = main.children;
    var picked = [];
    var i;
    var rect;
    for (i = 0; i < kids.length && picked.length < 12; i++) {
      if (kids[i].classList.contains("hero")) { continue; }
      rect = kids[i].getBoundingClientRect();
      picked.push({
        el: kids[i],
        onScreen: rect.top < fold && rect.bottom > 0
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

  /* The board sim (t7): plays the hero scene turn by turn — full contract in the docstring. */
  var COLS = 8, ROWS = 5, TURN_MS = 2750, LOOP_TURNS = 12, TEAMS = ["blue", "red"];
  var ROLE_MSG = {
    scout: ["eyes on the ridge", "flank is clear"],
    harvester: ["node looks rich", "hauling to base"],
    defender: ["holding the line", "dug in at home"]
  };
  var EVENT_MSG = {
    capture: ["post is ours", "flipped the ring"],
    deliver: ["resources home", "supply run done"],
    gather: ["node tapped", "loaded up"]
  };
  function center(col, row) { return [60 + 40 * col, 80 + 40 * row]; }
  function readCell(el) {
    var s = el.getAttribute("transform") || "";
    var o = s.indexOf("("), c = s.indexOf(",", o), e = s.indexOf(")", c);
    var x = parseFloat(s.slice(o + 1, c)), y = parseFloat(s.slice(c + 1, e));
    return [Math.round((x - 60) / 40), Math.round((y - 80) / 40)];
  }
  function placeCell(el, col, row) {
    var p = center(col, row);
    el.setAttribute("transform", "translate(" + p[0] + "," + p[1] + ")");
  }
  function clamp(n, lo, hi) { return n < lo ? lo : (n > hi ? hi : n); }
  function manh(a, b) { return Math.abs(a.col - b.col) + Math.abs(a.row - b.row); }
  function canGather(role) { return role === "harvester"; }
  function canCapture(role) { return role === "scout" || role === "defender"; }

  function initSim() {
    if (document.documentElement.dataset.js !== "1") { return; }   // gate: html[data-js]
    var mq = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)");
    if (!mq || mq.matches) { return; }                             // gate: no-preference
    var svg = document.querySelector(".hero-svg");
    if (!svg) { return; }
    var unitEls = svg.querySelectorAll(".hp-unit");
    if (!unitEls.length) { return; }

    var seed = (Date.now() >>> 0) || 1;
    function reseed(n) { seed = (((Date.now() >>> 0) ^ (n * 2654435761)) >>> 0) || 1; }
    function rnd() {
      seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5;
      seed >>>= 0;
      return seed / 4294967296;
    }
    function pick(list) { return list[Math.floor(rnd() * list.length)]; }

    var snaps = [];
    function snapAttr(el, name) { snaps.push([0, el, name, el.getAttribute(name)]); }
    function snapText(el) { snaps.push([1, el, null, el.textContent]); }
    function snapHTML(el) { snaps.push([2, el, null, el.innerHTML]); }
    function restore() {
      for (var r = 0; r < snaps.length; r++) {
        var s = snaps[r], el = s[1], v = s[3];
        if (s[0] === 0) {
          if (v === null) { el.removeAttribute(s[2]); } else { el.setAttribute(s[2], v); }
        } else if (s[0] === 1) { el.textContent = v; } else { el.innerHTML = v; }
      }
    }

    var posts = [], resources = [], beams = {}, home = {}, i, el, cell, ow;
    var postEls = svg.querySelectorAll(".hp-post");
    for (i = 0; i < postEls.length; i++) {
      el = postEls[i];
      snapAttr(el, "data-owner");
      cell = readCell(el);
      posts.push({ el: el, col: cell[0], row: cell[1] });
      ow = el.getAttribute("data-owner");
      if (ow === "blue" || ow === "red") { home[ow] = { col: cell[0], row: cell[1] }; }
    }
    var resEls = svg.querySelectorAll(".hp-res");
    for (i = 0; i < resEls.length; i++) {
      cell = readCell(resEls[i]);
      resources.push({ col: cell[0], row: cell[1] });
    }
    var beamEls = svg.querySelectorAll(".hero-gather");
    for (i = 0; i < beamEls.length; i++) {
      el = beamEls[i];
      snapAttr(el, "x1"); snapAttr(el, "y1"); snapAttr(el, "x2"); snapAttr(el, "y2");
      beams[el.getAttribute("data-team")] = el;
    }
    for (i = 0; i < unitEls.length; i++) { snapAttr(unitEls[i], "transform"); }

    var turnEl = document.getElementById("hero-turn");
    var tickerEl = document.getElementById("hero-ticker");
    if (turnEl) { snapText(turnEl); }
    if (tickerEl) { snapHTML(tickerEl); }

    var scoreCells = {}, score0 = {}, score = {}, team, scoreEl, spans, sp;
    for (i = 0; i < TEAMS.length; i++) {
      team = TEAMS[i];
      scoreEl = document.getElementById("hero-score-" + team);
      scoreCells[team] = {};
      if (!scoreEl) { continue; }
      spans = scoreEl.querySelectorAll("[data-term]");
      for (sp = 0; sp < spans.length; sp++) {
        scoreCells[team][spans[sp].getAttribute("data-term")] = spans[sp];
        snapText(spans[sp]);
      }
      score0[team] = {
        missions: parseInt(scoreCells[team].missions.textContent, 10) || 0,
        resources: parseInt(scoreCells[team].resources.textContent, 10) || 0
      };
    }

    var units = [], baseTurn = turnEl ? (parseInt(turnEl.textContent, 10) || 12) : 12;
    var current = baseTurn, loops = 0, timer = null, pending = null;

    function nearestRes(u) {
      var best = null, bd = 999, k, d;
      for (k = 0; k < resources.length; k++) {
        d = manh(u, resources[k]);
        if (d < bd) { bd = d; best = resources[k]; }
      }
      return best;
    }
    function randomRes() {
      return resources.length ? resources[Math.floor(rnd() * resources.length)] : null;
    }
    function buildUnits() {
      units.length = 0;
      for (var k = 0; k < unitEls.length; k++) {
        var ue = unitEls[k], c2 = readCell(ue), role = ue.getAttribute("data-role");
        var u = {
          el: ue, id: ue.getAttribute("data-unit"), team: ue.getAttribute("data-team"),
          role: role, col: c2[0], row: c2[1], job: "seek", tgt: null
        };
        if (canGather(role)) {
          u.tgt = nearestRes(u);
          if (u.tgt && manh(u, u.tgt) <= 1) { u.job = "gather"; }
        }
        units.push(u);
      }
    }
    function resetState() {
      buildUnits();
      score = {};
      for (var k = 0; k < TEAMS.length; k++) {
        var t = TEAMS[k], s0 = score0[t];
        score[t] = s0
          ? { missions: s0.missions, resources: s0.resources }
          : { missions: 0, resources: 0 };
      }
    }
    function stepToward(u, gc, gr) {
      var dc = gc - u.col, dr = gr - u.row, horiz, nc = u.col, nr = u.row;
      if (dc === 0 && dr === 0) { return [nc, nr]; }
      if (Math.abs(dc) > Math.abs(dr)) { horiz = true; }
      else if (Math.abs(dr) > Math.abs(dc)) { horiz = false; }
      else { horiz = rnd() < 0.5; }
      if (horiz) { nc += dc > 0 ? 1 : -1; } else { nr += dr > 0 ? 1 : -1; }
      return [clamp(nc, 0, COLS - 1), clamp(nr, 0, ROWS - 1)];
    }
    function captureTarget(u) {
      var best = null, bd = 99, k, p, d;
      for (k = 0; k < posts.length; k++) {
        p = posts[k];
        if (p.el.getAttribute("data-owner") === u.team) { continue; }
        d = manh(u, p);
        if (u.role === "defender" && d > 3) { continue; }
        if (d < bd) { bd = d; best = p; }
      }
      return best;
    }
    function planUnit(u) {
      if (canGather(u.role)) {
        if (u.job === "gather") { return [u.col, u.row]; }
        var g = (u.job === "return" ? home[u.team] : u.tgt) || home[u.team];
        if (!g) { return [u.col, u.row]; }
        return stepToward(u, g.col, g.row);
      }
      var post = captureTarget(u);
      if (post) { return stepToward(u, post.col, post.row); }
      var goal = u.role === "scout" ? randomRes() : home[u.team];
      return goal ? stepToward(u, goal.col, goal.row) : [u.col, u.row];
    }
    function showBeam(u) {
      var b = beams[u.team];
      if (!b || !u.tgt) { return; }
      var a = center(u.col, u.row), c = center(u.tgt.col, u.tgt.row);
      b.setAttribute("x1", a[0]); b.setAttribute("y1", a[1]);
      b.setAttribute("x2", c[0]); b.setAttribute("y2", c[1]);
      b.style.display = "";
    }
    function hideBeam(t) { var b = beams[t]; if (b) { b.style.display = "none"; } }
    function deliver(u, events) {
      var h = home[u.team];
      if (!h || manh(u, h) > 1) { return; }
      if (score[u.team]) { score[u.team].resources += 1; }
      u.job = "seek"; u.tgt = randomRes();
      events.push({ u: u, kind: "deliver" });
    }
    function resolveUnit(u, events) {
      if (canGather(u.role)) {
        if (u.job === "gather") {
          hideBeam(u.team); u.job = "return"; events.push({ u: u, kind: "gather" });
        } else if (u.job === "seek") {
          if (u.tgt && manh(u, u.tgt) <= 1) { u.job = "gather"; showBeam(u); }
        } else if (u.job === "return") {
          deliver(u, events);
        }
        return;
      }
      if (!canCapture(u.role)) { return; }
      for (var k = 0; k < posts.length; k++) {
        var p = posts[k];
        if (u.col === p.col && u.row === p.row && p.el.getAttribute("data-owner") !== u.team) {
          p.el.setAttribute("data-owner", u.team);
          if (score[u.team]) { score[u.team].missions += 1; }
          events.push({ u: u, kind: "capture" });
        }
      }
    }
    function writeScore() {
      for (var t = 0; t < TEAMS.length; t++) {
        var tm = TEAMS[t], cells = scoreCells[tm], ctrl = 0, k;
        if (!score[tm] || !cells.total) { continue; }
        for (k = 0; k < posts.length; k++) {
          if (posts[k].el.getAttribute("data-owner") === tm) { ctrl += 1; }
        }
        var m = score[tm].missions, rs = score[tm].resources;
        if (cells.missions) { cells.missions.textContent = m; }
        if (cells.control) { cells.control.textContent = ctrl; }
        if (cells.resources) { cells.resources.textContent = rs; }
        cells.total.textContent = m + ctrl + rs;
      }
    }
    function postTicker(events) {
      if (!tickerEl || !units.length) { return; }
      var u, msg, ev;
      if (events.length) {
        ev = events[Math.floor(rnd() * events.length)];
        u = ev.u; msg = pick(EVENT_MSG[ev.kind]);
      } else {
        u = units[Math.floor(rnd() * units.length)]; msg = pick(ROLE_MSG[u.role]);
      }
      tickerEl.innerHTML = '<tspan class="ht-unit">' + u.id + "</tspan> \\u00b7 " +
        u.role + " \\u2014 \\u201c" + msg + "\\u201d";
    }
    function tick() {
      if (mq.matches) { stop(); return; }
      current += 1;
      var events = [], intents = [], reserved = {}, k, u, nc, nr, key;
      for (k = 0; k < units.length; k++) { intents.push(planUnit(units[k])); }
      for (k = 0; k < units.length; k++) { reserved[units[k].col + ":" + units[k].row] = 1; }
      for (k = 0; k < units.length; k++) {
        u = units[k]; nc = intents[k][0]; nr = intents[k][1];
        if (nc === u.col && nr === u.row) { continue; }
        key = nc + ":" + nr;
        if (reserved[key]) { continue; }
        delete reserved[u.col + ":" + u.row]; reserved[key] = 1;
        u.col = nc; u.row = nr; placeCell(u.el, nc, nr);
      }
      for (k = 0; k < units.length; k++) { resolveUnit(units[k], events); }
      writeScore(); postTicker(events);
      if (turnEl) { turnEl.textContent = current; }
      if (current - baseTurn >= LOOP_TURNS) { loopReset(); }
    }
    function clearTimers() {
      if (timer) { window.clearInterval(timer); timer = null; }
      if (pending) { window.clearTimeout(pending); pending = null; }
    }
    function clearBeams() {
      for (var b in beams) { if (beams.hasOwnProperty(b)) { beams[b].style.display = ""; } }
    }
    function loopReset() {
      clearTimers();
      svg.style.transition = "opacity 0.5s ease";
      svg.style.opacity = "0.2";
      pending = window.setTimeout(function () {
        pending = null;
        restore(); clearBeams();
        loops += 1; reseed(loops); resetState();
        current = baseTurn;
        if (turnEl) { turnEl.textContent = current; }
        svg.style.opacity = "";
        timer = window.setInterval(tick, TURN_MS);
      }, 560);
    }
    function stop() {
      clearTimers(); restore(); clearBeams();
      svg.style.opacity = ""; svg.style.transition = "";
    }
    function onMQ() { if (mq.matches) { stop(); } }
    if (mq.addEventListener) { mq.addEventListener("change", onMQ); }
    else if (mq.addListener) { mq.addListener(onMQ); }

    resetState();
    timer = window.setInterval(tick, TURN_MS);
  }

  function init() {
    initToggle();
    initStagger();
    initReveal();
    initSim();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
"""
