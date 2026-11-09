"""Benchmarks script (t12): the evals index page. Served at ``/evals/charts.js``.

Renders, from one small file, the intro line, the live-match hero, the match ledger, the
benchmarks (KPI tiles, scatter, order mix, small multiples, thinking chart, each with a
"Show as table" view and its metric definitions) and the "how each model was run" table.
Pure helpers (scales, tick builders, table builders returning arrays of rows, SVG builders
that take their colours as an argument) are exposed on ``window.LoaEvalsCharts`` so they run
without a DOM.

Which eval
----------
The index ``<main data-page="index">`` carries ``data-base`` (the data prefix) and optionally
``data-eval``. If ``data-eval`` is present that eval is shown; otherwise the first eval in
``{data-base}/index.json``::

    {"evals": [{"id": str, "title": str}]}

Input contract: ``{data-base}/{eval}/summary.json``
---------------------------------------------------
The page polls ``{data-base}/{eval}/manifest.json`` every 60 s for freshness (its
``generated``) and re-reads ``summary.json`` only when that changes::

    { "schema_version": "1.1", "eval": str, "title": str, "generated": iso,
      "matches": [ { "tag", "label", "status", "reason", "board": str,
                     "turn": int, "turn_limit": int,
                     "file": str,                      # match file, relative to {data-base}/{eval}/
                     "score": {"blue": int, "red": int},
                     "teams": {"blue": {"name", "run": {...}}, "red": {...}},
                     "metrics": <metrics.compute_metrics(match)>,
                     "moments": [{"turn": int, "headline": str}] } ],
      "pooled": <metrics.pooled(matches)>,
      "definitions": <metrics.DEFINITIONS> }

``teams.<side>.run`` is the schema 1.1 run block (model, runtime, thinking, sampling,
reasoning). A match with empty ``metrics`` is left out of the benchmarks but stays in the
ledger. The hero fetches only the live match's file (``{data-base}/{eval}/{file}``) and draws
its board with ``window.LoaEvals.boardSVG(match, turn, "all")`` (provided by replay.js; it must
return ``{vb, g}``: the viewBox string and the inner SVG markup). If that is absent the hero
shows a fallback message and keeps the score and moments.
"""

# flake8: noqa: E501

from __future__ import annotations

_SRC = r"""
(function () {
  "use strict";
  var root = typeof window !== "undefined" ? window : globalThis;
  var SIDES = ["blue", "red"];
  var ACTIONS = ["move", "hold", "gather", "deliver", "steal"];
  var HIT_R = 12;
  var STATUS_WORD = { live: "Live", finished: "Finished", tie: "Stopped, tie", void: "Void", queued: "Queued", stopped: "Stopped" };
  var LABELS = {
    chat_words_per_turn: "Team chat per turn", median_answer_ms: "Median answer time", rejected: "Illegal orders",
    median_thinking_tokens: "Median thinking tokens", enemy_half_share: "Enemy-half share", points_per_turn: "Points per turn",
    orders: "Orders", share: "Share of points", accepted: "Orders accepted"
  };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function short(n) { return String(n || "").split(" ")[0]; }
  function pct(v) { return Math.round(v * 100) + "%"; }
  function commas(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ","); }
  function fmtN(v) {
    if (v == null) return "n/a";
    return v >= 10000 ? Math.round(v / 1000) + "k" : v >= 1000 ? (v / 1000).toFixed(1) + "k" : String(Math.round(v));
  }
  function fmtS(ms) {
    if (ms == null) return "n/a";
    return ms >= 60000 ? Math.floor(ms / 60000) + "m " + Math.round((ms % 60000) / 1000) + "s" : Math.round(ms / 1000) + "s";
  }
  function logTicks(lo, hi, unit) {
    var out = [];
    for (var v = lo; v <= hi; v *= 10) out.push({ v: v, label: commas(v) + (unit ? " " + unit : "") });
    return out;
  }
  function scale(lo, hi, a, b, log) {
    var f = log ? Math.log10 : function (v) { return v; };
    return function (v) {
      var c = Math.min(hi, Math.max(lo, v));
      return a + ((b - a) * (f(c) - f(lo))) / (f(hi) - f(lo));
    };
  }
  function linTicks(lo, hi, n) {
    var out = [];
    for (var i = 0; i <= n; i++) out.push(lo + ((hi - lo) * i) / n);
    return out;
  }

  /* ---------- data helpers ---------- */
  function benchMatches(sum) {
    return ((sum && sum.matches) || []).filter(function (m) { return m.metrics && m.metrics.blue && m.metrics.red; });
  }
  function scopeOf(ms, focus) {
    return focus === "all" ? ms : ms.filter(function (m) { return m.tag === focus; });
  }
  function names(ms) {
    var m = ms[0];
    return { blue: short(m && m.teams.blue.name) || "Blue", red: short(m && m.teams.red.name) || "Red" };
  }
  function liveOf(ms) {
    return ms.filter(function (m) { return m.status === "live"; })[0] || null;
  }
  function pool(sum, ms, focus, tm) {
    if (focus === "all" && sum.pooled && sum.pooled[tm]) return sum.pooled[tm];
    var s = scopeOf(ms, focus)[0].metrics[tm];
    return { orders: s.orders, rejected: s.rejected, chat_words_per_turn: s.chat_words_per_turn,
      median_answer_ms: s.median_answer_ms, median_thinking_tokens: s.median_thinking_tokens,
      rejected_share: s.orders ? s.rejected / s.orders : 0 };
  }
  function tiles(sum, ms, focus) {
    var b = pool(sum, ms, focus, "blue"), r = pool(sum, ms, focus, "red"), n = names(ms), d = sum.definitions || {};
    var hi = b.chat_words_per_turn >= r.chat_words_per_turn ? "blue" : "red", lo = hi === "blue" ? r : b, top = hi === "blue" ? b : r;
    var ratio = lo.chat_words_per_turn > 0 ? Math.round(top.chat_words_per_turn / lo.chat_words_per_turn) : 0;
    return [
      { key: "chat_words_per_turn", q: "Team chat per turn, in words", blue: fmtN(b.chat_words_per_turn), red: fmtN(r.chat_words_per_turn),
        note: ratio > 1 ? n[hi] + "'s seats talk " + ratio + "x as much." : "", def: d.chat_words_per_turn },
      { key: "median_answer_ms", q: "Median time for a seat to answer", blue: fmtS(b.median_answer_ms), red: fmtS(r.median_answer_ms),
        note: "Time is not scored.", def: d.median_answer_ms },
      { key: "rejected", q: "Orders rejected as illegal", blue: b.rejected + " of " + b.orders, red: r.rejected + " of " + r.orders,
        note: pct(b.rejected_share) + " against " + pct(r.rejected_share) + ".", def: d.rejected },
      { key: "median_thinking_tokens", q: "Median thinking tokens per seat answer",
        blue: b.median_thinking_tokens != null ? fmtN(b.median_thinking_tokens) : "not reported",
        red: r.median_thinking_tokens != null ? fmtN(r.median_thinking_tokens) : "not reported",
        note: "Only models that report thinking counts appear here.", def: d.median_thinking_tokens }
    ];
  }
  function mixOf(scope, tm) {
    var c = {}, n = 0;
    scope.forEach(function (m) {
      var mix = m.metrics[tm].mix || {};
      Object.keys(mix).forEach(function (k) { c[k] = (c[k] || 0) + mix[k]; n += mix[k]; });
    });
    return { c: c, n: n };
  }
  function mixShare(mm, a) { return mm.n ? (mm.c[a] || 0) / mm.n : 0; }

  var SM = [
    { key: "share", title: "Share of points", get: function (o) { return o.share; }, fmt: pct, lo: 0, hi: 1, ticks: [{ v: 0, label: "0%" }, { v: 0.5, label: "50%" }, { v: 1, label: "100%" }], ref: 0.5 },
    { key: "accepted", title: "Orders accepted as legal", get: function (o) { return o.accepted; }, fmt: pct, lo: 0.8, hi: 1, ticks: [{ v: 0.8, label: "80%" }, { v: 0.9, label: "90%" }, { v: 1, label: "100%" }] },
    { key: "wpt", title: "Team chat per turn, words (log scale)", get: function (o) { return o.chat_words_per_turn; }, fmt: fmtN, log: true, lo: 10, hi: 10000, ticks: logTicks(10, 10000, "") },
    { key: "lat", title: "Median answer time per seat (log scale)", get: function (o) { return o.median_answer_ms == null ? null : o.median_answer_ms / 1000; }, fmt: function (v) { return v == null ? "n/a" : fmtS(Math.round(v) * 1000); }, log: true, lo: 1, hi: 1000, ticks: logTicks(1, 1000, "s") }
  ];

  /* ---------- table builders (arrays of rows; the first row is the header) ---------- */
  function tableScatter(ms) {
    var n = names(ms), rows = [["Match", "Side", "In enemy half", "Near an enemy", "Points per turn", "Points taken from opponent", "Steals"]];
    ms.forEach(function (m) {
      SIDES.forEach(function (tm) {
        var o = m.metrics[tm];
        rows.push([m.tag, short(m.teams[tm].name) || n[tm], pct(o.enemy_half_share), pct(o.contact_share), o.points_per_turn.toFixed(2), String(o.flips), String(o.steals)]);
      });
    });
    return rows;
  }
  function tableMix(scope) {
    var n = names(scope), mb = mixOf(scope, "blue"), mr = mixOf(scope, "red"), rows = [["Order", n.blue, n.red]];
    ACTIONS.forEach(function (a) {
      rows.push([a, (mb.c[a] || 0) + " (" + pct(mixShare(mb, a)) + ")", (mr.c[a] || 0) + " (" + pct(mixShare(mr, a)) + ")"]);
    });
    return rows;
  }
  function tableSM(ms) {
    var rows = [["Match", "Side"].concat(SM.map(function (p) { return p.title.replace(/ \(log scale\)/, ""); }))];
    ms.forEach(function (m) {
      SIDES.forEach(function (tm) {
        rows.push([m.tag, short(m.teams[tm].name)].concat(SM.map(function (p) { return p.fmt(p.get(m.metrics[tm])); })));
      });
    });
    return rows;
  }
  function thinkSide(m) {
    var has = function (tm) { return m.metrics[tm].per_turn.some(function (p) { return p.thinking_tokens != null; }); };
    return has("blue") || !has("red") ? "blue" : "red";
  }
  function thinkMatch(ms, focus) {
    if (focus !== "all") return scopeOf(ms, focus)[0] || null;
    return liveOf(ms) || ms[0] || null;
  }
  function tableThink(m) {
    if (!m) return [];
    var tm = thinkSide(m), other = tm === "blue" ? "red" : "blue", n = short(m.teams[tm].name), o = short(m.teams[other].name);
    var rows = [["Turn", n + " thinking tokens", "Largest single seat", n + " median answer", o + " median answer"]];
    m.metrics[tm].per_turn.forEach(function (p, i) {
      var q = m.metrics[other].per_turn[i];
      rows.push([String(p.turn), fmtN(p.thinking_tokens), fmtN(p.max_seat_thinking_tokens), fmtS(p.median_answer_ms), fmtS(q && q.median_answer_ms)]);
    });
    return rows;
  }
  function tableHTML(rows) {
    if (!rows.length) return "";
    var head = "<tr>" + rows[0].map(function (c) { return '<th scope="col">' + esc(c) + "</th>"; }).join("") + "</tr>";
    var body = rows.slice(1).map(function (r) { return "<tr>" + r.map(function (c) { return "<td>" + esc(c) + "</td>"; }).join("") + "</tr>"; }).join("");
    return '<details class="tv"><summary>Show as table</summary><div class="tw"><table><thead>' + head + "</thead><tbody>" + body + "</tbody></table></div></details>";
  }

  /* ---------- SVG builders (pure: colours come in as c) ---------- */
  function tipAttr(lines) {
    return 'data-tip="' + esc(JSON.stringify(lines)) + '" tabindex="0" role="img" aria-label="' + esc(lines.join(", ")) + '"';
  }
  function keyMark(c, team, cx, cy, r) {
    var fill = team === "blue" ? c.blue : c.red;
    return team === "blue"
      ? '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="' + fill + '" stroke="' + c.surface + '" stroke-width="2"/>'
      : '<rect x="' + (cx - r) + '" y="' + (cy - r) + '" width="' + r * 2 + '" height="' + r * 2 + '" rx="1.5" fill="' + fill + '" stroke="' + c.surface + '" stroke-width="2"/>';
  }
  function hit(cx, cy) { return '<circle class="hit" cx="' + cx + '" cy="' + cy + '" r="' + HIT_R + '" fill="transparent"/>'; }
  function txt(x, y, anchor, size, fill, s) {
    return '<text x="' + x + '" y="' + y + '" text-anchor="' + anchor + '" font-size="' + size + '" fill="' + fill + '">' + esc(s) + "</text>";
  }
  function axisX(c, x, y0, y1, ticks) {
    return ticks.map(function (t) {
      return '<line x1="' + x(t.v) + '" x2="' + x(t.v) + '" y1="' + y0 + '" y2="' + y1 + '" stroke="' + c.border + '" stroke-width="1"/>' + txt(x(t.v), y1 + 16, "middle", 12, c.muted, t.label);
    }).join("");
  }
  function axisY(c, y, x0, x1, ticks) {
    return ticks.map(function (t) {
      return '<line x1="' + x0 + '" x2="' + x1 + '" y1="' + y(t.v) + '" y2="' + y(t.v) + '" stroke="' + c.border + '" stroke-width="1"/>' + txt(x0 - 8, y(t.v) + 4, "end", 12, c.muted, t.label);
    }).join("");
  }
  function scatterSVG(ms, focus, c) {
    var W = 520, H = 330, L = 44, R = 16, T = 12, B = 40, xs = [], ys = [];
    ms.forEach(function (m) { SIDES.forEach(function (tm) { xs.push(m.metrics[tm].enemy_half_share); ys.push(m.metrics[tm].points_per_turn); }); });
    var xMax = Math.max(0.3, Math.ceil(Math.max.apply(null, xs) * 10) / 10), yMax = Math.max(1, Math.ceil(Math.max.apply(null, ys)));
    var x = scale(0, xMax, L, W - R), y = scale(0, yMax, H - B, T);
    var yt = []; for (var i = 0; i <= yMax; i++) yt.push({ v: i, label: String(i) });
    var g = axisY(c, y, L, W - R, yt) + axisX(c, x, T, H - B, [0, xMax / 2, xMax].map(function (v) { return { v: v, label: pct(v) }; }));
    g += txt(W - R, H - 4, "end", 12, c.muted, "more time in the enemy half");
    var dim = function (m) { return focus !== "all" && m.tag !== focus; };
    ms.forEach(function (m) {
      var a = m.metrics.blue, b = m.metrics.red;
      g += '<line x1="' + x(a.enemy_half_share) + '" y1="' + y(a.points_per_turn) + '" x2="' + x(b.enemy_half_share) + '" y2="' + y(b.points_per_turn) + '" stroke="' + c.borderStrong + '" stroke-width="1" opacity="' + (dim(m) ? 0.25 : 0.8) + '"/>';
    });
    ms.forEach(function (m) {
      SIDES.forEach(function (tm) {
        var o = m.metrics[tm], cx = x(o.enemy_half_share), cy = y(o.points_per_turn);
        g += "<g " + tipAttr([o.points_per_turn.toFixed(2) + " points per turn", short(m.teams[tm].name) + " in " + m.tag, pct(o.enemy_half_share) + " of unit-turns in the enemy half", pct(o.contact_share) + " within 2 cells of an enemy", o.flips + " points taken from the opponent, " + o.steals + " steals"]) + ' opacity="' + (dim(m) ? 0.25 : 1) + '">' + hit(cx, cy) + keyMark(c, tm, cx, cy, 5) + "</g>";
      });
    });
    var lab = focus !== "all" ? ms.filter(function (m) { return m.tag === focus; })[0] : ms.slice().sort(function (p, q) {
      return Math.max(q.metrics.blue.points_per_turn, q.metrics.red.points_per_turn) - Math.max(p.metrics.blue.points_per_turn, p.metrics.red.points_per_turn);
    })[0];
    if (lab) {
      var t = lab.metrics.blue.points_per_turn >= lab.metrics.red.points_per_turn ? "blue" : "red", o2 = lab.metrics[t];
      g += txt(x(o2.enemy_half_share) + 10, y(o2.points_per_turn) - 8, "start", 12, c.text, short(lab.teams[t].name) + ", " + lab.tag);
    }
    return { vb: "0 0 " + W + " " + H, g: g };
  }
  function mixSVG(scope, c) {
    var W = 520, rowH = 44, L = 74, R = 20, T = 8, B = 30, H = T + ACTIONS.length * rowH + B, n = names(scope);
    var mb = mixOf(scope, "blue"), mr = mixOf(scope, "red"), all = [];
    ACTIONS.forEach(function (a) { all.push(mixShare(mb, a), mixShare(mr, a)); });
    var xMax = Math.min(1, Math.max(0.1, Math.ceil(Math.max.apply(null, all) * 10) / 10)), x = scale(0, xMax, L, W - R);
    var g = axisX(c, x, T, H - B, [0, xMax / 2, xMax].map(function (v) { return { v: v, label: pct(v) }; }));
    ACTIONS.forEach(function (a, i) {
      var cy = T + i * rowH + rowH / 2, vb = mixShare(mb, a), vr = mixShare(mr, a);
      g += txt(L - 10, cy + 4, "end", 13, c.text, a);
      g += '<line x1="' + x(Math.min(vb, vr)) + '" x2="' + x(Math.max(vb, vr)) + '" y1="' + cy + '" y2="' + cy + '" stroke="' + c.borderStrong + '" stroke-width="2"/>';
      [["blue", vb, mb], ["red", vr, mr]].forEach(function (e) {
        g += "<g " + tipAttr([pct(e[1]) + " of orders", n[e[0]] + ": " + a, (e[2].c[a] || 0) + " of " + e[2].n + " orders"]) + ">" + hit(x(e[1]), cy) + keyMark(c, e[0], x(e[1]), cy, 5) + "</g>";
      });
    });
    return { vb: "0 0 " + W + " " + H, g: g };
  }
  function smSVG(p, ms, focus, c) {
    var rowH = 30, L = 74, R = 18, T = 6, B = 28, W = 520, H = T + ms.length * rowH + B;
    var x = scale(p.lo, p.hi, L, W - R, p.log);
    var g = axisX(c, x, T, H - B, p.ticks);
    if (p.ref != null) g += '<line x1="' + x(p.ref) + '" x2="' + x(p.ref) + '" y1="' + T + '" y2="' + (H - B) + '" stroke="' + c.borderStrong + '" stroke-width="1"/>';
    ms.forEach(function (m, i) {
      var cy = T + i * rowH + rowH / 2, vb = p.get(m.metrics.blue), vr = p.get(m.metrics.red), dim = focus !== "all" && m.tag !== focus;
      g += '<g opacity="' + (dim ? 0.3 : 1) + '">' + txt(L - 10, cy + 4, "end", 12, c.text, m.tag);
      if (vb != null && vr != null) g += '<line x1="' + x(Math.min(vb, vr)) + '" x2="' + x(Math.max(vb, vr)) + '" y1="' + cy + '" y2="' + cy + '" stroke="' + c.borderStrong + '" stroke-width="2"/>';
      [["blue", vb], ["red", vr]].forEach(function (e) {
        if (e[1] == null) return;
        g += "<g " + tipAttr([p.fmt(e[1]), short(m.teams[e[0]].name) + " in " + m.tag, p.title]) + ">" + hit(x(e[1]), cy) + keyMark(c, e[0], x(e[1]), cy, 5) + "</g>";
      });
      g += "</g>";
    });
    return { vb: "0 0 " + W + " " + H, g: g };
  }
  var THINK = { W: 1040, H: 240, L: 52, R: 20, T: 14, B: 34 };
  function thinkGeom(m, pts) {
    var k = THINK, lim = Math.max(m.turn_limit || 0, pts.length, 1);
    var raw = Math.max(1000, Math.max.apply(null, pts.map(function (p) { return p.thinking_tokens || 0; })));
    var mag = Math.pow(10, Math.floor(Math.log10(raw))), step = mag * (raw / mag > 5 ? 2 : 1), yMax = Math.ceil(raw / step) * step;
    return { lim: lim, yMax: yMax, x: scale(0, lim, k.L, k.W - k.R), y: scale(0, yMax, k.H - k.B, k.T) };
  }
  function thinkSVG(m, c) {
    var k = THINK, tm = thinkSide(m), pts = m.metrics[tm].per_turn, G = thinkGeom(m, pts), x = G.x, y = G.y;
    var g = axisY(c, y, k.L, k.W - k.R, [0, G.yMax / 2, G.yMax].map(function (v) { return { v: v, label: fmtN(v) }; }));
    for (var t = 0; t <= G.lim; t += 10) g += txt(x(t), k.H - 10, "middle", 12, c.muted, t ? "turn " + t : "start");
    var have = pts.filter(function (p) { return p.thinking_tokens != null; }), col = tm === "blue" ? c.blue : c.red;
    if (have.length) {
      var d = have.map(function (p, i) { return (i ? "L" : "M") + x(p.turn) + "," + y(p.thinking_tokens); }).join("");
      g += '<path d="' + d + "L" + x(have[have.length - 1].turn) + "," + y(0) + "L" + x(have[0].turn) + "," + y(0) + 'Z" fill="' + col + '" opacity=".1"/><path d="' + d + '" fill="none" stroke="' + col + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>';
      var peak = have.reduce(function (a, b) { return b.thinking_tokens > a.thinking_tokens ? b : a; }), last = have[have.length - 1];
      g += keyMark(c, tm, x(last.turn), y(last.thinking_tokens), 4.5) + txt(x(peak.turn), y(peak.thinking_tokens) - 10, "middle", 12, c.text, "peak " + fmtN(peak.thinking_tokens) + ", turn " + peak.turn);
    } else g += txt(k.W / 2, k.H / 2, "middle", 13, c.muted, "No thinking token counts were recorded for this match.");
    g += '<line id="xh" x1="0" x2="0" y1="' + k.T + '" y2="' + (k.H - k.B) + '" stroke="' + c.text + '" stroke-opacity=".4" visibility="hidden"/><rect id="xhit" x="' + k.L + '" y="' + k.T + '" width="' + (k.W - k.L - k.R) + '" height="' + (k.H - k.T - k.B) + '" fill="transparent" tabindex="0" aria-label="Move across to read each turn; arrow keys step turns"/>';
    return { vb: "0 0 " + k.W + " " + k.H, g: g };
  }
  function thinkTip(m, t) {
    var tm = thinkSide(m), other = tm === "blue" ? "red" : "blue", a = m.metrics[tm].per_turn, b = m.metrics[other].per_turn;
    var p = a.filter(function (q) { return q.turn === t; })[0], q = b.filter(function (r) { return r.turn === t; })[0];
    if (!p) return null;
    return [p.thinking_tokens != null ? fmtN(p.thinking_tokens) + " thinking tokens" : "no token count", "Turn " + t,
      p.median_answer_ms != null ? short(m.teams[tm].name) + " median answer " + fmtS(p.median_answer_ms) : "",
      q && q.median_answer_ms != null ? short(m.teams[other].name) + " median answer " + fmtS(q.median_answer_ms) : ""].filter(Boolean);
  }

  /* ---------- the one tooltip: text goes in with textContent only ---------- */
  function showTip(lines, ev) {
    var t = document.getElementById("tip");
    if (!t) {
      t = document.createElement("div");
      t.id = "tip";
      t.setAttribute("role", "status");
      document.body.appendChild(t);
    }
    t.replaceChildren.apply(t, lines.map(function (l, i) {
      var d = document.createElement("div");
      d.className = i ? "tl" : "tv1";
      d.textContent = l;
      return d;
    }));
    t.hidden = false;
    var px = 20, py = 20;
    if (ev && ev.clientX != null && String(ev.type).indexOf("pointer") === 0) { px = ev.clientX; py = ev.clientY; }
    else if (ev && ev.target && ev.target.getBoundingClientRect) { var bb = ev.target.getBoundingClientRect(); px = bb.right; py = bb.top; }
    var tw = t.offsetWidth || 0, th = t.offsetHeight || 0, iw = root.innerWidth || 1000, ih = root.innerHeight || 800;
    t.style.left = Math.min(iw - tw - 8, px + 14) + "px";
    t.style.top = Math.max(8, Math.min(ih - th - 8, py + 14)) + "px";
  }
  function hideTip() { var t = document.getElementById("tip"); if (t) t.hidden = true; }

  /* ---------- page ---------- */
  function readColors() {
    var cs = getComputedStyle(document.documentElement), v = function (n) { return cs.getPropertyValue(n).trim() || "currentColor"; };
    return { blue: v("--team-blue"), red: v("--team-red"), border: v("--border"), borderStrong: v("--border-strong"), text: v("--text"), muted: v("--text-muted"), surface: v("--surface") };
  }
  var $ = function (s) { return document.querySelector(s); };
  var S = { sum: null, ms: [], focus: "all", base: "", evalId: "", gen: "", heroTag: "" };

  function chip(status) { return '<span class="chip ' + esc(status) + '">' + esc(STATUS_WORD[status] || status) + "</span>"; }
  function keyEl(tm) { return '<span class="key ' + tm + '" aria-hidden="true"></span>'; }
  function legend(n) { return '<div class="lg"><span>' + keyEl("blue") + esc(n.blue) + "</span><span>" + keyEl("red") + esc(n.red) + "</span></div>"; }
  function defsHTML(sum, keys) {
    var d = sum.definitions || {};
    return '<dl class="defs" style="margin:0;display:grid;gap:3px;font-size:.8rem;color:var(--text-muted)">' + keys.filter(function (k) { return d[k]; }).map(function (k) {
      return '<div><dt style="display:inline;color:var(--text)">' + esc(LABELS[k] || k) + ".</dt> <dd style=\"display:inline;margin:0\">" + esc(d[k]) + "</dd></div>";
    }).join("") + "</dl>";
  }
  function paint(sel, built) { var s = $(sel); if (s) { s.setAttribute("viewBox", built.vb); s.innerHTML = built.g; } }

  function benchHTML() {
    var ms = S.ms, sum = S.sum, n = names(ms), scope = scopeOf(ms, S.focus), tm = thinkMatch(ms, S.focus);
    var chips = '<button type="button" data-bf="all" aria-pressed="' + (S.focus === "all") + '">All matches</button>' + ms.map(function (m) {
      return '<button type="button" data-bf="' + esc(m.tag) + '" aria-pressed="' + (S.focus === m.tag) + '">' + esc(m.tag) + "</button>";
    }).join("");
    var tl = tiles(sum, ms, S.focus).map(function (t) {
      return '<div class="kpi"><span class="q">' + esc(t.q) + '</span><div class="pair"><div class="row">' + keyEl("blue") + '<span class="v">' + esc(t.blue) + '</span><span class="who">' + esc(n.blue) + '</span></div><div class="row">' + keyEl("red") + '<span class="v">' + esc(t.red) + '</span><span class="who">' + esc(n.red) + "</span></div></div>" + (t.note ? '<span class="note2">' + esc(t.note) + "</span>" : "") + (t.def ? '<span class="note2">' + esc(t.def) + "</span>" : "") + "</div>";
    }).join("");
    var where = S.focus === "all" ? "all matches pooled" : esc(S.focus);
    var tn = tm ? short(tm.teams[thinkSide(tm)].name) : "";
    return '<div class="section-head"><h2>How the models played</h2><p class="muted small">Every number is computed from the published turn data. Blue is a circle, red a square. Pick a match to focus the tiles, order mix and thinking chart on it; the comparison charts highlight it.</p></div>' +
      '<div class="filters" role="group" aria-label="Focus on a match"><span class="lbl">Focus</span>' + chips + "</div>" +
      '<div class="kpis">' + tl + "</div>" +
      '<div class="charts">' +
      '<div class="chartcard"><h3>Aggression against effectiveness</h3><p class="sub">Across: share of unit-turns spent in the opponent\'s half. Up: points scored per turn. One mark per side per match; a line joins the two sides of a match.</p>' + legend(n) + '<svg class="viz" id="v-scatter" role="group" aria-label="Scatter of aggression against effectiveness"></svg>' + tableHTML(tableScatter(ms)) + defsHTML(sum, ["enemy_half_share", "points_per_turn"]) + "</div>" +
      '<div class="chartcard"><h3>Strategy: what each side orders</h3><p class="sub">Share of all orders by type, ' + where + ".</p>" + legend(n) + '<svg class="viz" id="v-mix" role="group" aria-label="Order mix by type"></svg>' + tableHTML(tableMix(scope)) + defsHTML(sum, ["orders"]) + "</div>" +
      '<div class="chartcard wide"><h3>Match by match</h3><p class="sub">Four measures, each on its own scale. Answer time and chat use a log scale because the models can differ by orders of magnitude.</p>' + legend(n) + '<div class="small-multiples" id="v-sm"></div>' + tableHTML(tableSM(ms)) + defsHTML(sum, ["share", "accepted", "chat_words_per_turn", "median_answer_ms"]) + "</div>" +
      (tm ? '<div class="chartcard wide"><h3>' + esc(tn) + "'s thinking over " + esc(tm.tag) + '</h3><p class="sub">Thinking tokens per turn, summed over the side\'s seats. Move across the chart or use the arrow keys to read each turn.</p><svg class="viz" id="v-think" role="group" aria-label="Thinking tokens per turn"></svg>' + tableHTML(tableThink(tm)) + defsHTML(sum, ["median_thinking_tokens"]) + "</div>" : "") +
      "</div>";
  }
  function drawBench() {
    var host = $("#benchmarks"), c = readColors(), ms = S.ms;
    if (!host) return;
    if (!ms.length) { host.innerHTML = '<p class="muted">Benchmarks appear once a match has published turns.</p>'; return; }
    host.innerHTML = '<div class="bench">' + benchHTML() + "</div>";
    paint("#v-scatter", scatterSVG(ms, S.focus, c));
    paint("#v-mix", mixSVG(scopeOf(ms, S.focus), c));
    var sm = $("#v-sm");
    sm.innerHTML = SM.map(function (p) { return "<div><h4>" + esc(p.title) + '</h4><svg class="viz" data-sm="' + p.key + '" role="group" aria-label="' + esc(p.title) + ' by match"></svg></div>'; }).join("");
    SM.forEach(function (p) { paint('[data-sm="' + p.key + '"]', smSVG(p, ms, S.focus, c)); });
    var tm = thinkMatch(ms, S.focus);
    if (tm) { paint("#v-think", thinkSVG(tm, c)); wireThink(tm); }
  }
  function wireThink(m) {
    var s = $("#v-think"), h = s.querySelector("#xhit"), xh = s.querySelector("#xh"), pts = m.metrics[thinkSide(m)].per_turn, G = thinkGeom(m, pts), k = THINK, kt = pts.length;
    var show = function (t, ev) {
      var lines = thinkTip(m, t);
      if (!lines) { xh.setAttribute("visibility", "hidden"); return hideTip(); }
      xh.setAttribute("x1", G.x(t)); xh.setAttribute("x2", G.x(t)); xh.setAttribute("visibility", "visible");
      showTip(lines, ev);
    };
    var off = function () { xh.setAttribute("visibility", "hidden"); hideTip(); };
    h.addEventListener("pointermove", function (e) {
      var bb = s.getBoundingClientRect(), vx = ((e.clientX - bb.left) / bb.width) * k.W;
      show(Math.max(1, Math.min(pts.length, Math.round(((vx - k.L) / (k.W - k.L - k.R)) * G.lim))), e);
    });
    h.addEventListener("pointerleave", off);
    h.addEventListener("focus", function (e) { show(kt, e); });
    h.addEventListener("blur", off);
    h.addEventListener("keydown", function (e) {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      kt = Math.max(1, Math.min(pts.length, kt + (e.key === "ArrowRight" ? 1 : -1)));
      show(kt, e);
    });
  }

  function href(m) { return "/evals/" + encodeURIComponent(S.evalId) + "/" + encodeURIComponent(m.tag) + "/"; }
  function ledgerHTML(sum) {
    var rows = sum.matches.map(function (m) {
      var sc = m.score ? '<span class="num">' + m.score.blue + "&ndash;" + m.score.red + "</span>" : '<span class="muted">-</span>';
      var turns = m.turn ? '<span class="sub num">turn ' + m.turn + (m.turn_limit ? " of " + m.turn_limit : "") + "</span>" : "";
      return '<tr><td><a href="' + href(m) + '">' + esc(m.tag) + '</a><span class="sub">' + esc(m.label || "") + '</span></td><td><span class="team-dot blue" aria-hidden="true"></span>' + esc(short(m.teams.blue.name)) + '</td><td><span class="team-dot red" aria-hidden="true"></span>' + esc(short(m.teams.red.name)) + "</td><td>" + sc + turns + "</td><td>" + chip(m.status) + '</td><td class="small muted" style="min-width:16rem">' + esc(m.reason || "") + "</td></tr>";
    }).join("");
    return '<div class="section-head"><h2>' + esc(sum.title || sum.eval) + '</h2></div><div class="ledger"><table><thead><tr><th scope="col">Match</th><th scope="col">Blue</th><th scope="col">Red</th><th scope="col">Score</th><th scope="col">Status</th><th scope="col">Why it ended this way</th></tr></thead><tbody>' + rows + "</tbody></table></div>";
  }
  function playersHTML(sum) {
    var seen = {}, rows = [];
    sum.matches.slice().sort(function (a, b) { return (b.status === "live") - (a.status === "live"); }).forEach(function (m) {
      SIDES.forEach(function (tm) {
        var t = m.teams[tm], run = t.run || {};
        if (seen[t.name]) return;
        seen[t.name] = 1;
        var rs = run.reasoning === "full" ? "Full text, per seat, per turn" : run.reasoning === "none" ? "Not published" : "-";
        rows.push("<tr><td>" + keyEl(tm) + " " + esc(short(t.name)) + '<span class="sub">' + esc(run.model || t.model || "") + "</span></td><td>" + esc(run.runtime || "-") + "</td><td>" + esc(run.thinking || "-") + "</td><td>" + esc(run.sampling || "-") + "</td><td>" + esc(rs) + "</td></tr>");
      });
    });
    return '<div class="section-head"><h2>How each model was run</h2></div><div class="ledger"><table><thead><tr><th scope="col">Player</th><th scope="col">Runtime</th><th scope="col">Thinking</th><th scope="col">Sampling</th><th scope="col">Reasoning shown</th></tr></thead><tbody>' + rows.join("") + "</tbody></table></div>";
  }
  function heroShell(m) {
    var n = { blue: short(m.teams.blue.name), red: short(m.teams.red.name) }, sc = m.score || { blue: 0, red: 0 };
    var mom = (m.moments || []).slice(-4).map(function (x) {
      return '<a href="' + href(m) + '"><span>Turn ' + x.turn + "</span><span>" + esc(x.headline) + "</span></a>";
    }).join("");
    return '<section class="hero" aria-label="Live match"><div class="hero-board" id="hero-board"><p class="muted small">Loading the board...</p></div><div class="hero-copy"><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">' + chip(m.status) + '<span class="small muted num">Turn ' + m.turn + " of " + m.turn_limit + "</span></div><h2>" + esc(n.blue) + " against " + esc(n.red) + '</h2><div class="vs"><div class="side b"><span class="small muted">' + esc(n.blue) + ', blue</span><span class="pts num">' + sc.blue + '</span></div><span class="dash" aria-hidden="true">&ndash;</span><div class="side r"><span class="small muted">' + esc(n.red) + ', red</span><span class="pts num">' + sc.red + '</span></div></div><div class="moments" aria-label="Key moments so far">' + mom + '</div><div><a class="btn primary" href="' + href(m) + '">Watch ' + esc(m.tag) + "</a></div></div></section>";
  }
  function drawHero(m) {
    var host = $("#hero");
    if (!host) return;
    if (!m) { host.innerHTML = ""; return; }
    host.innerHTML = heroShell(m);
    var box = $("#hero-board"), fail = function (msg) { box.innerHTML = '<p class="muted small">' + esc(msg) + "</p>"; };
    if (!root.LoaEvals || typeof root.LoaEvals.boardSVG !== "function") return fail("The board needs the replay script, which did not load. The score and moments are still current.");
    fetch(S.base + "/" + S.evalId + "/" + m.file).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }).then(function (full) {
      var b = root.LoaEvals.boardSVG(full, full.turns.length, "all");
      box.innerHTML = '<svg viewBox="' + esc(b.vb) + '" role="img" aria-label="Board of ' + esc(m.tag) + " after turn " + full.turns.length + '">' + b.g + "</svg>";
    }).catch(function () { fail("The board could not be loaded right now."); });
  }
  function render(sum) {
    S.sum = sum; S.evalId = sum.eval; S.ms = benchMatches(sum);
    if (S.focus !== "all" && !S.ms.some(function (m) { return m.tag === S.focus; })) S.focus = "all";
    var empty = $("#evals-empty"); if (empty) empty.hidden = true;
    var intro = $(".intro");
    if (intro && !$("#eval-title")) { var p = document.createElement("p"); p.id = "eval-title"; p.textContent = sum.title || sum.eval; intro.appendChild(p); }
    var played = sum.matches.filter(function (m) { return m.file && m.turn > 0; });
    var live = liveOf(played) || played[0] || null, key = live ? live.tag + ":" + live.turn : "";
    if (key !== S.heroTag) { S.heroTag = key; drawHero(live); }
    $("#ledger").innerHTML = ledgerHTML(sum);
    $("#players").innerHTML = playersHTML(sum);
    drawBench();
  }
  function getJSON(url) { return fetch(url).then(function (r) { if (!r.ok) throw new Error(url + " " + r.status); return r.json(); }); }
  function tipEvt(e) {
    var g = e.target.closest && e.target.closest("[data-tip]");
    if (g) showTip(JSON.parse(g.getAttribute("data-tip")), e);
  }
  function boot() {
    var main = $('[data-page="index"]');
    if (!main) return;
    S.base = (main.getAttribute("data-base") || "/evals/data").replace(/\/$/, "");
    document.documentElement.dataset.evals = "ready";
    document.addEventListener("pointerover", tipEvt); document.addEventListener("pointermove", tipEvt);
    document.addEventListener("focusin", tipEvt);
    document.addEventListener("pointerout", function (e) { var g = e.target.closest && e.target.closest("[data-tip]"); if (g && !g.contains(e.relatedTarget)) hideTip(); });
    document.addEventListener("focusout", function (e) { if (e.target.closest && e.target.closest("[data-tip]")) hideTip(); });
    document.addEventListener("click", function (e) {
      var b = e.target.closest && e.target.closest("[data-bf]");
      if (b && S.sum) { S.focus = b.getAttribute("data-bf"); drawBench(); var again = $('[data-bf="' + S.focus + '"]'); if (again) again.focus(); }
    });
    var redraw = function () { if (S.sum) drawBench(); };
    if (root.MutationObserver) new MutationObserver(redraw).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    if (root.matchMedia) root.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redraw);
    var pick = main.getAttribute("data-eval") ? Promise.resolve(main.getAttribute("data-eval")) : getJSON(S.base + "/index.json").then(function (ix) { return ix.evals && ix.evals[0] && ix.evals[0].id; });
    var load = function () {
      pick.then(function (id) {
        if (!id) return;
        S.evalId = id;
        return getJSON(S.base + "/" + id + "/manifest.json").then(function (man) {
          if (S.gen && S.gen === man.generated) return;
          return getJSON(S.base + "/" + id + "/summary.json").then(function (sum) { S.gen = man.generated; render(sum); });
        });
      }).catch(function () { var e = $("#evals-empty"); if (e && !S.sum) { e.hidden = false; e.textContent = "The eval data could not be loaded right now."; } });
    };
    load();
    setInterval(load, 60000);
  }

  root.LoaEvalsCharts = {
    HIT_R: HIT_R, SM: SM, esc: esc, short: short, pct: pct, fmtN: fmtN, fmtS: fmtS, commas: commas,
    logTicks: logTicks, linTicks: linTicks, scale: scale, benchMatches: benchMatches, scopeOf: scopeOf, tiles: tiles,
    tableScatter: tableScatter, tableMix: tableMix, tableSM: tableSM, tableThink: tableThink, tableHTML: tableHTML,
    scatterSVG: scatterSVG, mixSVG: mixSVG, smSVG: smSVG, thinkSVG: thinkSVG, thinkTip: thinkTip, thinkMatch: thinkMatch,
    showTip: showTip, hideTip: hideTip
  };
  if (typeof document !== "undefined" && document.querySelector) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
  }
})();
"""


def _compact(src: str) -> str:
    """Drop leading indentation, blank lines and whole-line ``//`` comments."""
    lines = (line.strip() for line in src.strip().splitlines())
    return "\n".join(line for line in lines if line and not line.startswith("//")) + "\n"


CHARTS_JS = _compact(_SRC)
