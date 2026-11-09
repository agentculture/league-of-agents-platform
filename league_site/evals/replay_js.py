# flake8: noqa: E501
"""Replay script (t11): board, slice story, open moment, live polling, streaming.

Served at ``/evals/replay.js`` on both evals pages. On the index page it only
publishes ``window.LoaEvals`` (``glyph``/``boardSVG`` are reused by the index
script); on a replay page (``<main data-page="replay">``) it also runs the
replay app against the published data layout of :mod:`league_site.evals.schema`:

* ``{base}/{eval}/manifest.json`` polled every 60 s; a match whose ``etag``
  changed is fetched again from the entry's ``file`` (or ``{tag}.json``).
* ``{base}/{eval}/thinking/{tag}-t{turn}.json`` fetched on demand per turn.
* ``{base}/{eval}/live/{tag}/index.json`` polled every 10 s (seats answering now), then
  ``{base}/{eval}/live/{tag}/{agent_id}.json`` for the listed seats only, while
  the match is live, the viewer sits at the latest turn and the tab is visible.

Slices and findings are precomputed by the slicer at export; this script never
recomputes them. All data text reaches the DOM through :func:`esc` (one helper)
or ``textContent``; ``setHTML`` is the only ``innerHTML`` assignment.

Decision logic lives in pure functions on ``window.LoaEvals`` so tests can run
them under node without a DOM. Leading indentation is stripped at import time
to keep the script inside :data:`league_site.evals.wsgi.EVALS_JS_BUDGET_BYTES`.
"""

from __future__ import annotations

_SRC = r"""
(function () {
"use strict";
const G = typeof window !== "undefined" ? window : globalThis;
const SIDES = ["blue", "red"];
const ROLE = { scout: "Scout", harvester: "Harvester", defender: "Defender", thief: "Thief" };
const WORD = { live: "Live", finished: "Finished", tie: "Stopped (tie)", stopped: "Stopped", void: "Void", queued: "Queued" };
const HOUR = 3600000;
// the one escaping helper: every data string that goes into markup passes through it
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const V = n => `var(--${n})`;
const teamCol = id => id === "blue" ? V("team-blue") : id === "red" ? V("team-red") : V("text-muted");
const sty = (f, s, w) => ` style="fill:${f};stroke:${s || "none"}${w ? ";stroke-width:" + w : ""}"`;
const short = n => String(n || "").split(" ")[0];
const unitOf = a => String(a).replace(/-(\d+)$/, "-u$1");
const agentOf = u => String(u).replace(/-u(\d+)$/, "-$1");
const sideOf = id => String(id).split("-")[0];
const fmtS = ms => ms == null ? "" : ms >= 60000 ? `${Math.floor(ms / 60000)}m ${Math.round(ms % 60000 / 1000)}s` : `${Math.round(ms / 1000)}s`;
const fmtTok = n => n == null ? null : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);

/* ---------- pure decisions (tested under node) ---------- */
function supportedVersion(v) { const m = /^(\d+)(\.\d+)*$/.exec(String(v == null ? "" : v).trim()); return !!m && +m[1] === 1; }
function utcHM(iso) { const t = Date.parse(iso); return isFinite(t) ? new Date(t).toISOString().slice(11, 16) : "?"; }
function isStale(gen, now, status) { if (status !== "live") return false; const t = Date.parse(gen); return isFinite(t) && now - t > HOUR; }
function shouldAdvance(prevAtEnd, prevTurn, n) { return !!prevAtEnd && n > prevTurn; }
function nextTurn(prevAtEnd, prevTurn, n) { return shouldAdvance(prevAtEnd, prevTurn, n) || prevAtEnd ? n : Math.max(0, Math.min(prevTurn, n)); }
function shouldPollLive(o) { return o.status === "live" && !!o.atLatest && !o.hidden; }
function statusInfo(status, n, limit, gen, now) {
  if (isStale(gen, now, status)) return { cls: "stale", word: `No update since ${utcHM(gen)} UTC` };
  if (status === "live") return { cls: "live", word: `Live, turn ${n} of ${limit}` };
  return { cls: status, word: WORD[status] || String(status) };
}
const safeFile = f => typeof f === "string" && /^[\w][\w.-]*\.json$/.test(f) ? f : null;
const paths = {
  manifest: (b, e) => `${b}/${e}/manifest.json`,
  match: (b, e, entry, tag) => `${b}/${e}/${(entry && safeFile(entry.file)) || tag + ".json"}`,
  thinking: (b, e, tag, t) => `${b}/${e}/thinking/${tag}-t${t}.json`,
  live: (b, e, tag, a) => `${b}/${e}/live/${tag}/${a}.json`,
  liveIndex: (b, e, tag) => `${b}/${e}/live/${tag}/index.json`,
};
// Seats to fetch: only those the live index lists as answering, on the chosen team.
function answeringSeats(index, seats, turn) {
  if (!index || !Array.isArray(index.answering) || !(+index.turn > turn)) return [];
  return index.answering.filter(a => seats.includes(a));
}
function reasoningKind(m, side) {
  const run = m.teams[side] && m.teams[side].run;
  if (run && run.reasoning) return run.reasoning;
  return (m.turns || []).some(tr => (tr.think || []).some(k => sideOf(k.agent_id) === side && k.has_text)) ? "full" : "none";
}
function noReasoningNote(team, k) {
  const run = team.run || {}, tok = k ? fmtTok(k.completion_tokens) : null;
  return `${short(team.name)} runs through ${run.runtime || "its runtime"}, which returns no reasoning text.` +
    (tok ? ` This seat used ${tok} tokens${k.reasoning_tokens ? `, ${fmtTok(k.reasoning_tokens)} of them thinking` : ""}.` : "");
}
function loadReasoning(fetchFn, url, agent, cache) {
  let p = cache && cache.get(url);
  if (!p) {
    p = fetchFn(url).then(r => { if (!r.ok) throw new Error(r.status === 404 ? "missing" : "HTTP " + r.status); return r.json(); });
    if (cache) { cache.set(url, p); p.catch(() => cache.delete(url)); }
  }
  return p.then(d => { const x = (Array.isArray(d) ? d : []).find(y => y && y.agent_id === agent); return x && x.reasoning ? String(x.reasoning) : null; });
}
const stateAt = (m, t) => t === 0 ? m.initial : m.turns[t - 1].state;
const fogAt = (m, t, view) => { if (view === "all") return null; const f = t === 0 ? m.initial_fog : m.turns[t - 1].fog; return f ? f[view] : null; };

/* ---------- role glyphs: shape carries role, fill carries team ---------- */
function glyph(role, cx, cy, r, fill, stroke, dash) {
  const d = (dash ? ` stroke-dasharray="3 2"` : "") + sty(fill, stroke, 2);
  if (role === "scout") return `<polygon points="${cx},${cy - r * 1.1} ${cx + r},${cy + r * .8} ${cx - r},${cy + r * .8}"${d}/>`;
  if (role === "defender") return `<rect x="${cx - r * .9}" y="${cy - r * .9}" width="${r * 1.8}" height="${r * 1.8}" rx="${r * .35}"${d}/>`;
  if (role === "thief") return `<polygon points="${cx},${cy - r * 1.15} ${cx + r * 1.05},${cy} ${cx},${cy + r * 1.15} ${cx - r * 1.05},${cy}"${d}/>`;
  return `<circle cx="${cx}" cy="${cy}" r="${r}"${d}/>`;
}

/* ---------- board: fog, role shapes, cargo pips, crown ring, slice marks ---------- */
function boardSVG(m, t, view, opts) {
  opts = opts || {};
  const sc = m.scenario, W = sc.w, H = sc.h, C = 40, P = opts.axes ? 20 : 6;
  const s = stateAt(m, t), prev = t > 0 ? stateAt(m, t - 1) : null, F = fogAt(m, t, view || "all");
  const X = x => P + x * C + C / 2, Y = y => P + y * C + C / 2, gold = V("gold"), muted = V("text-muted");
  let g = "";
  for (let x = 0; x < W; x++) for (let y = 0; y < H; y++) {
    const i = x * H + y, vis = !F || F.vis[i] === "1", seen = !F || F.seen[i] === "1", at = `x="${P + x * C + 1.5}" y="${P + y * C + 1.5}" width="${C - 3}" height="${C - 3}" rx="7"`;
    g += `<rect ${at}${sty(V("tile"))}/>`;
    if (!vis) g += `<rect ${at}${sty(V("fog"))} opacity="${seen ? .2 : .5}"/>`;
  }
  if (opts.axes) {
    for (let x = 0; x < W; x += 2) g += `<text x="${X(x)}" y="${P - 6}" text-anchor="middle" font-size="10"${sty(muted)}>${x}</text>`;
    for (let y = 0; y < H; y += 2) g += `<text x="${P - 7}" y="${Y(y) + 3.5}" text-anchor="middle" font-size="10"${sty(muted)}>${y}</text>`;
  }
  const seenCell = p => !F || F.seen[p[0] * H + p[1]] === "1";
  (s.missions || []).filter(q => q.kind === "deliver" && (seenCell(q.pos) || q.team_id === view)).forEach(q => {
    g += `<rect x="${P + q.pos[0] * C + 4}" y="${P + q.pos[1] * C + 4}" width="${C - 8}" height="${C - 8}" rx="5" stroke-dasharray="4 3"${sty("none", q.team_id ? teamCol(q.team_id) : gold, 2)}/>`;
  });
  ((F ? F.resource_nodes : s.resource_nodes) || []).forEach(n => {
    const cx = X(n.pos[0]), cy = Y(n.pos[1]), left = n.remaining == null ? "?" : n.remaining;
    g += `<g><title>Resource node: ${esc(left)} left${F && n.turn < t ? ` (seen turn ${esc(n.turn)})` : ""}</title><path d="M${cx - 8},${cy + 6} L${cx - 3},${cy - 7} L${cx + 3},${cy - 7} L${cx + 8},${cy + 6} Z"${sty(gold)} opacity=".85"/><text x="${cx}" y="${cy + 17}" text-anchor="middle" font-size="9.5" font-weight="600"${sty(gold)}>${esc(left)}</text></g>`;
  });
  ((F ? F.control_points : s.control_points) || []).forEach(c => {
    const cx = X(c.pos[0]), cy = Y(c.pos[1]), r = 15, own = c.owner;
    const live = (s.control_points || []).find(q => q.id === c.id) || c;
    const streak = !F ? (live.hold || []).filter(h => h[1] > 0).map(h => `${h[0]} ${h[1]}`).join(", ") : "";
    const hm = (s.missions || []).find(q => q.kind === "hold" && q.pos[0] === c.pos[0] && q.pos[1] === c.pos[1]);
    const tip = `Control point ${c.id}: ${own ? "held by " + own : "unowned"}${streak ? `, hold streak ${streak}` : ""}${hm ? `, hold mission ${hm.status === "open" ? `open (+${hm.reward == null ? hm.amount : hm.reward})` : "done"}` : ""}${F && t > c.turn ? `, seen turn ${c.turn}` : ""}`;
    g += `<g><title>${esc(tip)}</title><polygon points="${cx},${cy - r} ${cx + r},${cy} ${cx},${cy + r} ${cx - r},${cy}"${sty(own ? teamCol(own) : "none", own ? teamCol(own) : V("border-strong"), 2)} fill-opacity="${own ? .22 : 0}"/>`;
    if (hm && hm.status === "open") g += `<circle cx="${cx + r - 2}" cy="${cy - r + 2}" r="5.5"${sty(V("surface"), gold, 1.5)}/><text x="${cx + r - 2}" y="${cy - r + 5}" text-anchor="middle" font-size="7.5" font-weight="700"${sty(gold)}>+</text>`;
    g += "</g>";
  });
  if (s.crown) {
    const k = s.crown, cx = X(k.pos[0]), cy = Y(k.pos[1]), rr = 17;
    const pts = [0, 1, 2, 3, 4, 5].map(i => `${cx + rr * Math.cos(Math.PI / 3 * i + Math.PI / 6)},${cy + rr * Math.sin(Math.PI / 3 * i + Math.PI / 6)}`).join(" ");
    const hold = (k.hold || []).filter(h => h[1] > 0)[0], c = k.taken_by ? teamCol(k.taken_by) : V("crown");
    let arc = "";
    if (hold && !k.taken_by) {
      const a = hold[1] / k.hold_turns * 2 * Math.PI, R = rr + 4;
      arc = `<path class="crown-ring" d="M${cx},${cy - R} A${R},${R} 0 ${a > Math.PI ? 1 : 0} 1 ${cx + R * Math.sin(a)},${cy - R * Math.cos(a)}" stroke-linecap="round"${sty("none", teamCol(hold[0]), 3)}/>`;
    }
    const tip = `The Crown: ${k.value} points${k.taken_by ? `, taken by ${k.taken_by}` : hold ? `, ${hold[0]} holding ${hold[1]} of ${k.hold_turns} turns` : ", unheld"}`;
    g += `<g><title>${esc(tip)}</title><polygon points="${pts}"${sty(c, c, 2)} fill-opacity=".16"/>${arc}<path d="M${cx - 7},${cy + 4} L${cx - 7},${cy - 4} L${cx - 3.5},${cy} L${cx},${cy - 6} L${cx + 3.5},${cy} L${cx + 7},${cy - 4} L${cx + 7},${cy + 4} Z"${sty(c)}/></g>`;
  }
  const units = s.units || [];
  const visible = F ? units.filter(u => u.team_id === view).concat((F.units || []).filter(u => u.pos && u.team_id !== view).map(u => Object.assign({}, u, { stale: u.turn < t }))) : units;
  const prevPos = {};
  if (prev) (prev.units || []).forEach(u => { prevPos[u.id] = u.pos; });
  visible.filter(u => !u.stale).forEach(u => {
    const p = prevPos[u.id];
    if (p && (p[0] !== u.pos[0] || p[1] !== u.pos[1])) {
      const on = opts.interactive && u.id === opts.sel;
      g += `<line x1="${X(p[0])}" y1="${Y(p[1])}" x2="${X(u.pos[0])}" y2="${Y(u.pos[1])}" stroke-dasharray="${on ? "0" : "3 4"}" stroke-linecap="round" opacity="${on ? .95 : .55}"${sty("none", teamCol(u.team_id), on ? 3 : 1.6)}/>`;
    }
  });
  const stack = {};
  visible.forEach(u => {
    const key = u.pos.join(","), i = (stack[key] = (stack[key] || 0) + 1) - 1;
    const cx = X(u.pos[0]) + ([0, 9, -9, 0, 9, -9][i] || 0), cy = Y(u.pos[1]) + ([0, 7, 7, -8, -8, -8][i] || 0), col = teamCol(u.team_id);
    const label = u.stale ? `${u.id}, ${u.role}, last seen at ${u.pos} on turn ${u.turn}` : `${u.id}, ${u.role}, at ${u.pos}, carrying ${u.carrying == null ? "unknown" : u.carrying}`;
    const on = opts.interactive && opts.sel === u.id, pick = opts.interactive && !u.stale, cargo = Math.min(u.carrying || 0, 4);
    g += `<g${pick ? ` class="unit" tabindex="-1" role="button" data-unit="${esc(u.id)}"` : ""} aria-label="${esc(label)}" opacity="${u.stale ? .55 : 1}"><title>${esc(label)}</title>`;
    if (on) g += `<circle cx="${cx}" cy="${cy}" r="15"${sty("none", V("accent"), 3)}/>`;
    g += glyph(u.role, cx, cy, 9, u.stale ? "none" : col, u.stale ? col : V("surface"), u.stale);
    if (u.stale) g += `<text x="${cx}" y="${cy - 13}" text-anchor="middle" font-size="8.5" font-weight="600"${sty(col)}>t${esc(u.turn)}</text>`;
    for (let j = 0; j < cargo; j++) g += `<circle class="pip" cx="${cx - (cargo - 1) * 3 + j * 6}" cy="${cy + 14}" r="2.3"${sty(gold)}/>`;
    g += "</g>";
  });
  (opts.marks || []).filter(k => k && k.pos).forEach(k => {
    g += `<circle class="mark" data-key="${esc(k.key)}" cx="${X(k.pos[0])}" cy="${Y(k.pos[1])}" r="${C * .62}" stroke-dasharray="6 4"${sty("none", V("accent"), 3)}/>`;
  });
  return { vb: `0 0 ${W * C + P * 2} ${H * C + P * 2}`, g };
}

/* what the seats said about a slice: messages and plans naming its objects */
function sliceQuotes(m, sl) {
  const tr = m.turns[sl.turn - 1];
  if (!tr) return [];
  const ents = (sl.entities || []).filter(e => e && e.pos);
  const keys = (sl.entities || []).map(e => String(e.key)).concat(ents.map(e => `[${e.pos[0]},${e.pos[1]}]`), ents.map(e => `[${e.pos[0]}, ${e.pos[1]}]`));
  const out = [];
  SIDES.forEach(tm => {
    const pool = (tr.messages || []).filter(x => (x.team_id || sideOf(x.from)) === tm).map(x => ({ who: x.from, text: String(x.text || "") }))
      .concat((tr.plans || []).filter(x => x.team_id === tm).map(x => ({ who: "team plan", text: String(x.text || "") })));
    let best = null, at = -1, hit = "";
    for (const q of pool) { for (const k of keys) { const j = q.text.toLowerCase().indexOf(k.toLowerCase()); if (j >= 0) { best = q; at = j; hit = k; break; } } if (best) break; }
    if (!best && sl.opening) { best = pool.find(q => q.who === "team plan") || pool[0]; at = 0; }
    if (!best) return;
    const a = Math.max(0, at - 90), b = Math.min(best.text.length, at + 170);
    let html = esc((a > 0 ? "…" : "") + best.text.slice(a, b) + (b < best.text.length ? "…" : ""));
    if (hit) html = html.split(esc(hit)).join(`<mark>${esc(hit)}</mark>`);
    out.push({ team: tm, who: best.who, html });
  });
  return out;
}

G.LoaEvals = { esc, glyph, boardSVG, supportedVersion, isStale, shouldAdvance, nextTurn, shouldPollLive, answeringSeats, statusInfo, utcHM, paths, reasoningKind, noReasoningNote, loadReasoning, sliceQuotes, short };
if (typeof document === "undefined") return;
document.documentElement.dataset.evals = "ready";
const main = document.querySelector('main[data-page="replay"]');
if (!main) return;

/* ---------- replay app ---------- */
const $ = id => document.getElementById(id);
function setHTML(el, h) { if (el) el.innerHTML = h; }
const show = (el, on) => { el.style.display = on ? "" : "none"; };
const B = main.dataset.base || "/evals/data", EV = main.dataset.eval, TAG = main.dataset.match;
let ENTRY = null, M = null, T = 0, VIEW = "full", SIDE = "blue", SEL = null, SL = null, ETAG = null, DEAD = false, timer = null, wired = false;
let STREAM = {}, STREAM_ON = false;
const thinkCache = new Map();
const nT = () => M ? M.turns.length : 0;
const status = () => M ? M.status : ENTRY ? ENTRY.status : "queued";
const limit = () => (ENTRY && ENTRY.turn_limit) || (M && M.scenario && M.scenario.turn_limit) || nT();
const tname = id => short(M.teams[id].name);

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) { const e = new Error("HTTP " + r.status); e.status = r.status; throw e; }
  return r.json();
}
function only(html) {
  DEAD = true; stop();
  setHTML(main.querySelector(".wrap"), `<nav class="crumb" aria-label="Breadcrumb"><a href="/evals/#${esc(EV)}">&larr; All evals</a></nav><div class="queued-box">${html}</div>`);
}
function guard(v) {
  if (supportedVersion(v)) return true;
  only(`<h2>This page can't read data version ${esc(v)}. Reload later.</h2>`);
  return false;
}
function headHTML(st) {
  const sc = M && M.scenario, si = statusInfo(st, nT(), limit(), M ? M.generated : "", Date.now());
  const reason = (M && M.reason) || "";
  const notes = M ? SIDES.map(id => { const tm = M.teams[id], run = tm.run || {}; return reasoningKind(M, id) === "full" ? `${short(tm.name)}'s full reasoning is published.` : `${short(tm.name)} runs through ${run.runtime || "its runtime"}, which returns no reasoning text.`; }).join(" ") : "";
  return `<div style="display:grid;gap:8px"><div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap"><span class="chip ${esc(si.cls)}" id="status-chip">${esc(si.word)}</span>` +
    (sc ? `<span class="small muted">${esc(sc.w)}×${esc(sc.h)}, ${esc(sc.turn_limit)} turns, fog ${M.fog ? "on" : "off"}</span>` : "") + `</div>` +
    `<h1>${M ? `${esc(tname("blue"))} against ${esc(tname("red"))}` : esc(TAG)}</h1>` +
    `<p class="fair" id="reason"><b>${esc(WORD[st] || st)}.</b> ${esc(reason)} ${esc(notes)}</p></div>`;
}
function playerCard(id) {
  const tm = M.teams[id], run = tm.run || {}, pts = (stateAt(M, T).points || {})[id] || 0;
  const row = (k, v) => v ? `<dt>${k}</dt><dd>${esc(v)}</dd>` : "";
  return `<section class="player ${id}" aria-label="${id} team"><div class="who"><div><h2>${esc(short(tm.name))}</h2><span class="small muted">${id === "blue" ? "Blue" : "Red"}, ${esc(run.model || tm.model)}</span></div><span class="pts num" data-pts="${id}" aria-label="${pts} points">${pts}</span></div>` +
    `<dl class="howrun">${row("Runtime", run.runtime)}${row("Thinking", run.thinking)}${row("Sampling", run.sampling)}${row("Reasoning", run.reasoning ? (run.reasoning === "full" ? "Full text published" : "No reasoning text") : "")}</dl></section>`;
}
function render() {
  const st = status();
  document.title = `${TAG} · League of Agents Evals`;
  if (!M || !M.initial || !M.scenario) {
    setHTML($("scoreboard"), headHTML(st) + `<div class="queued-box"><h2>Not started yet</h2><p class="muted">${esc((M && M.reason) || "This match has not started. The page checks for it every minute.")}</p></div>`);
    show(main.querySelector(".stage"), false);
    return;
  }
  show(main.querySelector(".stage"), true);
  const fx = Array.isArray(M.findings) ? M.findings : [];
  setHTML($("scoreboard"), headHTML(st) +
    `<div class="scoreboard">${playerCard("blue")}<div class="clock"><span class="small muted">Turn</span><span class="t num" id="clockt">${T}</span><span class="small muted num">of ${esc(limit())}</span></div>${playerCard("red")}</div>` +
    (fx.length ? `<section aria-label="Findings" class="findings">${fx.map(x => `<button type="button" class="finding" data-turn="${+x.turn || 0}"><span class="big num">${esc(x.value)}</span><span class="what">${esc(x.text)}</span></button>`).join("")}</section>` : ""));
  if (!wired) wire();
  drawTurn();
}
function wire() {
  wired = true;
  setHTML($("board-host"), `<svg id="board" role="img" aria-label="Game board"></svg>`);
  const ctl = main.querySelector(".tl-controls"), extra = document.createElement("div");
  extra.style.display = "contents";
  const lg = (svg, label) => `<span><svg viewBox="0 0 16 16" aria-hidden="true">${svg}</svg>${label}</span>`;
  setHTML(extra, `<svg id="chart" role="img" aria-label="Points by turn"></svg><p class="fognote" id="fognote"></p><div class="legend" aria-label="Legend">` +
    lg(glyph("scout", 8, 8.5, 5.5, V("text-muted"), "none"), "Scout") + lg(glyph("harvester", 8, 8, 5.5, V("text-muted"), "none"), "Harvester") +
    lg(glyph("defender", 8, 8, 5.5, V("text-muted"), "none"), "Defender") + lg(glyph("thief", 8, 8, 5.5, V("text-muted"), "none"), "Thief") +
    lg(`<polygon points="8,2 14,8 8,14 2,8"${sty("none", V("text-muted"), 1.6)}/>`, "Control point") + lg(`<path d="M2,13 L6,3 L10,3 L14,13Z"${sty(V("gold"))}/>`, "Resources") +
    lg(`<circle cx="8" cy="8" r="2.5"${sty(V("gold"))}/>`, "Cargo") + lg(`<circle cx="8" cy="8" r="6"${sty("none", V("accent"), 2)}/>`, "Where this moment happened") + `</div>`);
  ctl.after(extra);
  main.querySelectorAll("[data-view]").forEach(b => b.addEventListener("click", () => { VIEW = b.dataset.view; drawTurn(); }));
  $("turn").addEventListener("input", e => setT(+e.target.value));
  $("prev").addEventListener("click", () => setT(T - 1));
  $("next").addEventListener("click", () => setT(T + 1));
  $("play").addEventListener("click", togglePlay);
  $("board").addEventListener("click", e => { const u = e.target.closest("[data-unit]"); if (u) { SEL = u.dataset.unit; SIDE = sideOf(SEL); drawTurn(); } });
  const chart = $("chart");
  const scrub = e => { const r = chart.getBoundingClientRect(); setT(Math.round(((e.clientX - r.left) / r.width * 900 - 36) / 824 * limit())); };
  chart.addEventListener("pointerdown", e => { scrub(e); chart.setPointerCapture(e.pointerId); });
  chart.addEventListener("pointermove", e => { if (e.buttons) scrub(e); });
  $("scoreboard").addEventListener("click", e => { const f = e.target.closest(".finding"); if (f) jump(+f.dataset.turn); });
  const story = $("story");
  story.addEventListener("click", e => {
    const b = e.target.closest("[data-jump]"); if (b) { jump(+b.dataset.jump); return; }
    const sd = e.target.closest("[data-side]"); if (sd) { SIDE = sd.dataset.side; SEL = null; drawTurn(); streamTick(); return; }
    const se = e.target.closest("[data-seat]"); if (se) { SEL = se.dataset.seat; drawTurn(); requestAnimationFrame(() => { const x = story.querySelector(`.seat[data-seat="${CSS.escape(SEL)}"]`); if (x) x.focus(); }); return; }
    const mo = e.target.closest(".more"); if (mo) { const open = mo.previousElementSibling.classList.toggle("clamp"); mo.textContent = open ? "Show all" : "Show less"; mo.setAttribute("aria-expanded", String(!open)); }
  });
  story.addEventListener("keydown", e => {
    const b = e.target.closest(".seat"); if (!b || (e.key !== "ArrowDown" && e.key !== "ArrowUp")) return;
    e.preventDefault(); const list = [...story.querySelectorAll(".seat")], i = list.indexOf(b);
    list[(i + (e.key === "ArrowDown" ? 1 : -1) + list.length) % list.length].click();
  });
  document.addEventListener("keydown", e => {
    if (DEAD || !M || !M.initial || e.altKey || e.ctrlKey || e.metaKey || (e.target.closest && e.target.closest("input, textarea, select, .seat, pre"))) return;
    const map = { ArrowLeft: T - 1, ArrowRight: T + 1, Home: 0, End: nT() };
    if (e.key in map) { e.preventDefault(); setT(map[e.key]); }
  });
}
const sliceAt = t => SL ? SL.filter(s => s.from <= Math.max(1, t)).pop() : null;
// On narrow screens the board is sticky at the top: record its height so an opened
// moment scrolls to start just below it instead of underneath it.
function boardHeight() {
  const bc = main.querySelector(".boardcol");
  const narrow = matchMedia("(max-width: 960px)").matches;
  main.style.setProperty("--boardcol-h", narrow && bc ? bc.offsetHeight + "px" : "0px");
  return narrow;
}
function jump(t) {
  setT(t);
  requestAnimationFrame(() => {
    const a = main.querySelector(".slice.active"), narrow = boardHeight();
    if (a) a.scrollIntoView({ block: narrow ? "start" : "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  });
}
function setT(t) {
  const was = T === nT();
  T = Math.max(0, Math.min(nT(), t | 0));
  drawTurn();
  if (was !== (T === nT())) streamTick();
}
function drawTurn() {
  if (!M || !M.initial) return;
  const n = nT(), s = stateAt(M, T), act = sliceAt(T), fogSide = VIEW === "fog" && M.initial_fog ? SIDE : "all";
  const r = $("turn"); r.max = n; r.value = T; r.setAttribute("aria-valuetext", T === 0 ? "Start position" : `Turn ${T} of ${n}`);
  $("clockt").textContent = T;
  main.querySelectorAll(".player .pts").forEach(el => { const v = (s.points || {})[el.dataset.pts] || 0; el.textContent = v; el.setAttribute("aria-label", `${v} points`); });
  main.querySelectorAll("[data-view]").forEach(b => { b.disabled = !M.initial_fog && b.dataset.view === "fog"; b.setAttribute("aria-pressed", String(b.dataset.view === VIEW)); });
  $("fognote").textContent = !M.initial_fog ? "This board has no fog of war." : fogSide === "all" ? "True positions of every unit." :
    `What ${tname(SIDE)} knows after this turn. Clear tiles are in sight, light shade was seen before, dark was never seen. Hollow units are last sightings, marked with the turn they were seen.`;
  const b = boardSVG(M, T, fogSide, { axes: true, interactive: true, sel: SEL, marks: act && T === act.turn ? act.entities : [] });
  const svg = $("board"); svg.setAttribute("viewBox", b.vb); setHTML(svg, b.g);
  svg.setAttribute("aria-label", `Board ${T === 0 ? "at the start" : "after turn " + T}, ${fogSide === "all" ? "full view" : tname(SIDE) + "'s view"}`);
  drawChart(act); drawStory(act);
}
function drawChart(act) {
  const lim = Math.max(1, limit()), n = nT(), W = 900, H = 190, L = 36, R = 40, Tp = 12, Bt = 44;
  const series = id => [0].concat(M.turns.map(t => (t.state.points || {})[id] || 0));
  const b = series("blue"), r = series("red"), top = Math.ceil(Math.max(10, ...b, ...r) / 10) * 10;
  const x = i => L + (W - L - R) * i / lim, y = v => Tp + (H - Tp - Bt) * (1 - v / top), muted = V("text-muted");
  let g = "";
  for (let v = 0; v <= top; v += top / 2) g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"${sty("none", V("border"))}/><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end" font-size="13"${sty(muted)}>${v}</text>`;
  for (let t = 0; t <= lim; t += 10) g += `<text x="${x(t)}" y="${H - 4}" text-anchor="middle" font-size="13"${sty(muted)}>${t === 0 ? "start" : "turn " + t}</text>`;
  if (n < lim) g += `<rect x="${x(n)}" y="${Tp}" width="${x(lim) - x(n)}" height="${H - Tp - Bt}"${sty(V("surface-2"))} opacity=".7"/><text x="${(x(n) + x(lim)) / 2}" y="${Tp + 18}" text-anchor="middle" font-size="13"${sty(muted)}>${status() === "live" ? "still to play" : "not played"}</text>`;
  const path = (a, c) => { const d = a.map((v, i) => `${i ? "H" + x(i) + "V" : "M" + x(i) + ","}${y(v)}`).join(""); return `<path d="${d}V${y(0)}H${x(0)}Z"${sty(c)} opacity=".08"/><path d="${d}" stroke-linejoin="round"${sty("none", c, 2.5)}/>`; };
  g += path(b, teamCol("blue")) + path(r, teamCol("red"));
  [[b, "blue", r], [r, "red", b]].forEach(([a, id, o]) => { const i = a.length - 1; g += `<circle cx="${x(i)}" cy="${y(a[i])}" r="4.5"${sty(teamCol(id))}/><text x="${x(i) + 8}" y="${y(a[i]) + (a[i] >= o[i] ? -5 : 14)}" font-size="14" font-weight="650"${sty(V("text"))}>${a[i]}</text>`; });
  const lane = H - Bt + 16;
  g += `<line x1="${x(0)}" x2="${x(n)}" y1="${lane}" y2="${lane}"${sty("none", V("border-strong"), 1)}/>`;
  (SL || []).forEach(sl => { const on = sl === act; g += `<circle cx="${x(sl.turn)}" cy="${lane}" r="${on ? 6 : sl.major ? 4.5 : 3}"${sty(on ? V("accent") : sl.major ? V("text") : V("surface"), on ? V("accent") : V("border-strong"), 1.5)}><title>Turn ${+sl.turn}: ${esc(sl.headline)}</title></circle>`; });
  g += `<line x1="${x(T)}" x2="${x(T)}" y1="${Tp - 4}" y2="${lane}"${sty("none", V("accent"), 2)}/>`;
  const svg = $("chart"); svg.setAttribute("viewBox", `0 0 ${W} ${H}`); setHTML(svg, g);
  svg.setAttribute("aria-label", `Points by turn. After turn ${T}: ${tname("blue")} ${b[T]}, ${tname("red")} ${r[T]}.`);
}
function drawStory(act) {
  const tm = id => `<b class="${id === "blue" ? "bt" : "rt"}">${esc(tname(id))}</b>`;
  const showDetail = T > 0 || (status() === "live" && T === nT());
  let h;
  if (!SL) {
    h = `<p class="muted">Key moments appear once this match is exported.</p>` + (showDetail ? detailHTML() : "");
  } else {
    h = `<h2 class="sr">Match story</h2><ol class="story" style="list-style:none;margin:0;padding:0">` + SL.map(sl => {
      const on = sl === act, d = SIDES.filter(x => sl.delta && sl.delta[x]).map(x => `${tm(x)} +${+sl.delta[x]}`).join(", "), extras = [];
      SIDES.forEach(x => { const rj = (sl.rejected || {})[x], ls = (sl.lost || {})[x]; if (rj) extras.push(`${rj} ${tname(x)} order${rj > 1 ? "s" : ""} rejected`); if (ls) extras.push(`${ls} ${tname(x)} order${ls > 1 ? "s" : ""} lost`); });
      const q = sliceQuotes(M, sl).map(x => `<blockquote class="quote ${x.team}"><cite>${esc(tname(x.team))}, ${esc(x.who)}, turn ${+sl.turn}</cite>${x.html}</blockquote>`).join("");
      const sc = sl.score || {};
      return `<li class="slice ${sl.major ? "major" : "minor"}${on ? " active" : ""}"><button type="button" class="tick" data-jump="${+sl.turn}" aria-label="Show turn ${+sl.turn}" aria-current="${on ? "step" : "false"}">${+sl.turn}</button><div class="body">` +
        `<button type="button" class="hl" data-jump="${+sl.turn}">${esc(sl.headline)}</button>` +
        `<div class="meta"><span>${sl.from === sl.to ? `Turn ${+sl.from}` : `Turns ${+sl.from}–${+sl.to}`}</span><span class="num">${tm("blue")} ${+sc.blue || 0}–${+sc.red || 0} ${tm("red")}</span>${d ? `<span>${d}</span>` : ""}${extras.length ? `<span>${esc(extras.join(", "))}</span>` : ""}</div>` +
        (q ? `<div class="said">${q}</div>` : "") + (on && T > 0 ? detailHTML() : "") + `</div></li>`;
    }).join("") + `</ol>` + (T === 0 && showDetail ? detailHTML() : "");
  }
  setHTML($("story"), h);
  drawThink(); drawStream();
}
function note(from, text, cls) {
  const long = String(text || "").length > 420;
  return `<div class="note ${cls || ""}"><span class="from">${esc(from)}</span><span class="${long ? "clamp" : ""}">${esc(text)}</span>${long ? `<button type="button" class="more" aria-expanded="false">Show all</button>` : ""}</div>`;
}
/* the open moment: seats, reasoning, streaming and chat for turn T, beside the board */
function detailHTML() {
  const tr = T > 0 ? M.turns[T - 1] : null, s = stateAt(M, T), units = (s.units || []).filter(u => u.team_id === SIDE);
  if (!SEL || sideOf(SEL) !== SIDE) SEL = units.length ? units[0].id : null;
  const lat = {}, act = {}, rej = {}, thk = {};
  if (tr) {
    (tr.latency || []).forEach(l => { lat[l.unit_id || unitOf(l.agent_id)] = l.elapsed_ms; });
    (tr.actions || []).forEach(a => { act[a.unit_id] = a; });
    (tr.rejected || []).forEach(r => { if (!r.passive) rej[r.unit_id] = r.reason; });
    (tr.think || []).forEach(k => { thk[unitOf(k.agent_id)] = k; });
  }
  const maxLat = Math.max(1, ...Object.values(lat).filter(v => v != null)), col = teamCol(SIDE);
  const seats = units.map(u => {
    const a = act[u.id], k = thk[u.id], r = rej[u.id], ms = lat[u.id];
    const order = !tr ? "Start position" : k && k.order_lost ? "Order lost: the reply could not be read" : a ? `${a.action}${a.to ? " to " + a.to.join(", ") : ""}` : "No order";
    return `<button type="button" class="seat" role="option" aria-selected="${SEL === u.id}" data-seat="${esc(u.id)}"><svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">${glyph(u.role, 12, 12, 7.5, col, V("surface"))}</svg>` +
      `<span class="role">${esc(ROLE[u.role] || u.role)} <span class="muted small">${esc(u.id)}${u.carrying ? `, carrying ${esc(u.carrying)}` : ""}</span></span>` +
      `<span class="order${r || (k && k.order_lost) ? " rej" : ""}">${esc(order)}${r ? `, rejected: ${esc(r)}` : ""}</span>` +
      `<span class="time num">${ms != null ? `<span>${fmtS(ms)}</span><span class="bar" aria-hidden="true"><i style="width:${Math.max(4, ms / maxLat * 100)}%;background:${col}"></i></span>` : ""}</span></button>`;
  }).join("");
  const talk = tr ? (tr.plans || []).filter(p => p.team_id === SIDE).map(p => note("Team plan", p.text, "plan"))
    .concat((tr.messages || []).filter(x => (x.team_id || sideOf(x.from)) === SIDE).map(x => note(x.from, x.text))) : [];
  return `<div class="detail" aria-label="${T ? `Turn ${T} in detail` : "Start position"}"><div class="detail-head"><div class="seg" role="group" aria-label="Team">` +
    SIDES.map(id => `<button type="button" data-side="${id}" aria-pressed="${SIDE === id}">${esc(tname(id))}'s seats</button>`).join("") +
    `</div><p class="small muted">${T ? `Turn ${T}. ` : ""}Each unit is its own seat and model conversation. Bars compare answer times.</p></div>` +
    `<div class="seatlist" role="listbox" aria-label="Seats">${seats}</div><div class="think" id="think"></div><div class="think" id="stream" aria-live="polite" style="display:none"></div>` +
    (tr ? `<div class="chat"><h3>${esc(tname(SIDE))}'s plan and chat, turn ${T}</h3>${talk.length ? `<div class="chatscroll" tabindex="0" aria-label="Plan and chat">${talk.join("")}</div>` : `<p class="small muted">No plan or messages this turn.</p>`}</div>` : "") + `</div>`;
}
async function drawThink() {
  const box = $("think");
  if (!box) return;
  if (!SEL || T === 0) { setHTML(box, ""); show(box, false); return; }
  show(box, true);
  const tr = M.turns[T - 1], team = M.teams[SIDE], agent = agentOf(SEL);
  const k = (tr.think || []).find(x => x.agent_id === agent), l = (tr.latency || []).find(x => (x.unit_id || unitOf(x.agent_id)) === SEL);
  const tok = k ? fmtTok(k.completion_tokens) : null, rtok = k ? fmtTok(k.reasoning_tokens) : null;
  const head = `<div class="think-head"><h3>${esc(SEL)}, turn ${T}</h3><span class="small muted num">${l && l.elapsed_ms != null ? `answered in ${fmtS(l.elapsed_ms)}` : ""}${tok ? `, ${tok} tokens${rtok ? ` (${rtok} thinking)` : ""}` : ""}</span></div>`;
  if (reasoningKind(M, SIDE) !== "full") { setHTML(box, head + `<p class="asym">${esc(noReasoningNote(team, k))}</p>`); return; }
  setHTML(box, head + `<pre class="reasoning" tabindex="0" aria-label="Reasoning">Loading reasoning…</pre>`);
  const want = SEL + "@" + T + "@" + SIDE, url = paths.thinking(B, EV, TAG, T);
  let text, err = null;
  try { text = await loadReasoning(fetch, url, agent, thinkCache); } catch (e) { err = e; }
  if (want !== SEL + "@" + T + "@" + SIDE) return;
  const pre = box.querySelector(".reasoning");
  if (!pre) return;
  if (err) pre.textContent = err.message === "missing" ? "This turn's reasoning is not published. It was either not exported yet or held back by the scanner." : `Reasoning could not load (${err.message}). Step to another turn and back to retry.`;
  else if (!text) pre.textContent = "No reasoning was kept for this seat on this turn.";
  else setHTML(pre, esc(text).replace(/\b(T\d{1,2}|turn \d{1,2})\b/g, "<mark>$1</mark>"));
}
function drawStream() {
  const box = $("stream");
  if (!box) return;
  if (!STREAM_ON || !SEL || T !== nT()) { show(box, false); setHTML(box, ""); return; }
  const f = STREAM[agentOf(SEL)], next = nT() + 1;
  show(box, true);
  if (!f) { setHTML(box, `<p class="small muted">${esc(SEL)} is not answering turn ${next} right now.${Object.keys(STREAM).length ? " Other seats on this team are." : ""}</p>`); return; }
  const full = reasoningKind(M, SIDE) === "full";
  setHTML(box, `<div class="think-head"><h3>Answering turn ${+f.turn || next} now</h3><span class="small muted num">${esc(SEL)}, updated ${esc(String(f.updated || "").slice(11, 19))} UTC</span></div>` +
    (full && f.thinking_so_far ? `<p class="small muted">Thinking so far</p><pre class="reasoning" tabindex="0" aria-label="Thinking so far">${esc(f.thinking_so_far)}</pre>` : "") +
    (f.response_so_far ? `<p class="small muted">Response so far</p><pre class="reasoning" tabindex="0" aria-label="Response so far">${esc(f.response_so_far)}</pre>` : "") +
    (!full ? `<p class="asym">${esc(noReasoningNote(M.teams[SIDE], null))} Only its response streams.</p>` : ""));
  box.querySelectorAll(".reasoning").forEach(p => { p.scrollTop = p.scrollHeight; });
}
async function streamTick() {
  const on = !DEAD && !!M && !!M.initial && shouldPollLive({ status: status(), atLatest: T === nT(), hidden: document.hidden });
  if (!on) { if (STREAM_ON) { STREAM_ON = false; STREAM = {}; drawStream(); } return; }
  const side = SIDE, n = nT(), seats = Object.keys((M.teams[side] || {}).seats || {});
  const idx = await fetch(paths.liveIndex(B, EV, TAG), { cache: "no-cache" }).then(r => r.ok ? r.json() : null).catch(() => null);
  const agents = answeringSeats(idx, seats, n);
  const got = await Promise.all(agents.map(a => fetch(paths.live(B, EV, TAG, a), { cache: "no-cache" }).then(r => r.ok ? r.json() : null).catch(() => null)));
  if (side !== SIDE || n !== nT() || T !== n) return;
  const next = {};
  got.forEach((f, i) => { if (f && f.agent_id === agents[i] && +f.turn > n) next[agents[i]] = f; });
  STREAM = next; STREAM_ON = true; drawStream();
}
function stop() { if (timer) { clearInterval(timer); timer = null; } const p = $("play"); if (p) { p.textContent = "Play"; p.setAttribute("aria-label", "Play"); } }
function togglePlay() {
  if (timer) { stop(); return; }
  if (T >= nT()) T = 0;
  const p = $("play"); p.textContent = "Pause"; p.setAttribute("aria-label", "Pause");
  timer = setInterval(() => { if (T >= nT()) stop(); else setT(T + 1); }, 1100);
}
async function loadMatch() {
  let m;
  try { m = await getJSON(paths.match(B, EV, ENTRY, TAG)); } catch (e) {
    if (ENTRY.status === "queued") { ETAG = ENTRY.etag; render(); return; }
    only(`<h2>Match data did not load</h2><p class="muted">The match file returned ${esc(e.message)}. Reload later.</p>`); return;
  }
  if (m.schema_version != null && !guard(m.schema_version)) return;
  const atEnd = !M || T === nT();
  T = nextTurn(atEnd, M ? T : 0, (m.turns || []).length);
  M = m; ETAG = ENTRY.etag; SL = Array.isArray(m.slices) && m.slices.length ? m.slices : null;
  render();
  streamTick();
}
async function poll(first) {
  if (DEAD || (!first && (document.hidden || !["live", "queued"].includes(status())))) return;
  let man;
  try { man = await getJSON(paths.manifest(B, EV)); } catch (e) {
    if (first) only(`<h2>Match data did not load</h2><p class="muted">The eval's manifest returned ${esc(e.message)}. If it keeps failing, this eval has not been published yet.</p>`);
    return;
  }
  if (!guard(man.schema_version)) return;
  const entry = (man.matches || []).find(x => x && x.tag === TAG);
  if (!entry) { if (first) only(`<h2>Match not found</h2><p class="muted">This eval does not list a match called ${esc(TAG)}.</p>`); return; }
  ENTRY = entry;
  if (first || entry.etag !== ETAG) await loadMatch();
  else if (M) { const chip = $("status-chip"), si = statusInfo(status(), nT(), limit(), M.generated, Date.now()); if (chip) { chip.className = "chip " + si.cls; chip.textContent = si.word; } }
}
poll(true);
setInterval(poll, 60000);
setInterval(streamTick, 10000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); streamTick(); });
})();
"""


def _compact(src: str) -> str:
    """Drop leading indentation, blank lines and whole-line ``//`` comments."""
    lines = (line.strip() for line in src.strip().splitlines())
    return "\n".join(line for line in lines if line and not line.startswith("//")) + "\n"


REPLAY_JS = _compact(_SRC)
