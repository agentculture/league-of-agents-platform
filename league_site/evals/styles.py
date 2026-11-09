# flake8: noqa: E501
"""CSS for the evals pages (live match hero, replay, benchmarks).

Ported from the approved design reference. Built only from ``var(--...)``
tokens that live in :mod:`league_site.web.theme` (palette, game tokens, radii,
easing, fonts) so both colour schemes and the manual theme toggle apply with
no per-scheme rules here. The reference's page-level ``html``/``body``/``*``
rules are omitted: the site theme already owns them.
"""

from __future__ import annotations

EVALS_CSS = """\
/* Layout: dawn-arena family page. Index = live match as hero + eval ledger; replay = board (the star) beside the seat panel, timeline-as-scrubber under it, the turn's talk below. */
.wrap { max-width: 1320px; margin: 0 auto; padding-block: 28px 64px; padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); display: grid; gap: 28px; }
h1, h2, h3 { font-family: var(--font-display); font-weight: 450; margin: 0; text-wrap: balance; letter-spacing: -.01em; line-height: 1.15; }
h1 { font-size: clamp(2rem, 4.2vw, 3.1rem); font-variation-settings: "opsz" 144; }
h2 { font-size: 1.45rem; font-variation-settings: "opsz" 72; }
h3 { font-size: 1.1rem; font-variation-settings: "opsz" 36; }
p { margin: 0; }
a { color: var(--accent); text-underline-offset: 3px; }
.muted { color: var(--text-muted); }
.num { font-variant-numeric: tabular-nums; }
.small { font-size: .875rem; }
button, .btn { font: inherit; font-size: .9rem; cursor: pointer; color: var(--text); background: var(--surface); border: 1px solid var(--border-strong); border-radius: 999px; padding: .4rem .95rem; text-decoration: none; display: inline-flex; gap: .4rem; align-items: center; transition: background .2s var(--ease-out), border-color .2s var(--ease-out); }
button:hover, .btn:hover { background: var(--surface-2); }
button[aria-pressed="true"] { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
button:disabled { opacity: .45; cursor: not-allowed; }
:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; border-radius: 6px; }
.btn.primary { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); font-weight: 500; }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }

/* status chips: shape + word, never colour alone */
.chip { display: inline-flex; align-items: center; gap: .4rem; font-size: .8rem; font-weight: 500; padding: .15rem .65rem; border-radius: 999px; border: 1px solid var(--border-strong); white-space: nowrap; }
.chip::before { content: ""; width: .5rem; height: .5rem; border-radius: 50%; background: currentColor; }
.chip.live { color: var(--accent); border-color: var(--accent); }
.chip.live::before { animation: pulse 2.4s var(--ease-out) infinite; }
.chip.void, .chip.tie, .chip.queued { color: var(--text-muted); }
.chip.void::before { border-radius: 0; height: 2px; }
.chip.queued::before { background: none; border: 1.5px solid currentColor; }
.chip.win { color: var(--text); }
@keyframes pulse { 0%, 100% { box-shadow: 0 0 0 0 var(--accent-glow); } 50% { box-shadow: 0 0 0 6px transparent; } }

/* ---------- index ---------- */
.masthead { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.brand { font-family: var(--font-display); font-size: 1.15rem; color: var(--text); text-decoration: none; display: inline-flex; gap: .55rem; align-items: center; }
.brand svg { width: 26px; height: 26px; }
.intro { display: grid; gap: 14px; max-width: 46rem; }
.intro p { font-size: 1.08rem; color: var(--text-muted); max-width: 40rem; }
.hero { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); gap: 0; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
.hero-board { padding: 18px; background: var(--surface-2); display: grid; }
.hero-board svg { width: 100%; height: auto; }
.hero-copy { padding: 26px 28px; display: grid; gap: 16px; align-content: start; }
.vs { display: grid; grid-template-columns: 1fr auto 1fr; gap: 10px; align-items: end; }
.vs .side { display: grid; gap: 2px; }
.vs .side.r { text-align: right; }
.vs .pts { font-family: var(--font-display); font-size: 3.4rem; line-height: 1; font-weight: 400; font-variation-settings: "opsz" 144; }
.vs .b .pts { color: var(--team-blue); } .vs .r .pts { color: var(--team-red); }
.vs .dash { font-family: var(--font-display); font-size: 2rem; color: var(--text-muted); padding-bottom: .4rem; }
.ledger { overflow-x: auto; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
table { border-collapse: collapse; width: 100%; font-size: .92rem; }
th, td { text-align: left; padding: .7rem .9rem; vertical-align: top; border-bottom: 1px solid var(--border); }
th { font-weight: 500; color: var(--text-muted); font-size: .82rem; }
tr:last-child td { border-bottom: 0; }
td .sub { display: block; font-size: .82rem; color: var(--text-muted); }
.team-dot { display: inline-block; width: .7rem; height: .7rem; margin-right: .4rem; vertical-align: baseline; }
.team-dot.blue { background: var(--team-blue); border-radius: 50%; }
.team-dot.red { background: var(--team-red); border-radius: 2px; }
.section-head { display: grid; gap: 6px; }
.rules { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 12px 28px; color: var(--text-muted); font-size: .92rem; }
.rules b { color: var(--text); font-weight: 500; }

/* ---------- replay ---------- */
.crumb { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; justify-content: space-between; }
.matchbar { display: flex; gap: 8px; flex-wrap: wrap; }
.matchbar button { font-size: .82rem; }
.scoreboard { display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); gap: 18px; align-items: stretch; }
.player { display: grid; gap: 6px; padding: 16px 20px; border-radius: var(--radius); background: var(--surface); border: 1px solid var(--border); position: relative; }
.player.blue { background: linear-gradient(90deg, var(--team-blue-wash), transparent 70%), var(--surface); }
.player.red { background: linear-gradient(270deg, var(--team-red-wash), transparent 70%), var(--surface); text-align: right; }
.player .who { display: flex; gap: 12px; align-items: baseline; justify-content: space-between; }
.player.red .who { flex-direction: row-reverse; }
.player .pts { font-family: var(--font-display); font-size: 3rem; line-height: .9; font-variation-settings: "opsz" 144; }
.player.blue .pts { color: var(--team-blue); } .player.red .pts { color: var(--team-red); }
.howrun { display: grid; grid-template-columns: auto 1fr; gap: 2px 12px; font-size: .82rem; color: var(--text-muted); }
.howrun dt { color: var(--text-muted); } .howrun dd { margin: 0; color: var(--text); }
.player.red .howrun { justify-self: end; text-align: left; }
.clock { display: grid; place-items: center; align-content: center; gap: 6px; text-align: center; min-width: 9rem; }
.clock .t { font-family: var(--font-display); font-size: 2.2rem; line-height: 1; font-variation-settings: "opsz" 144; }
.fair { font-size: .9rem; color: var(--text-muted); border-left: 3px solid var(--border-strong); padding: .2rem 0 .2rem .9rem; max-width: 62rem; }
.fair b { color: var(--text); font-weight: 500; }

/* replay: sticky board column (board + timeline) beside the match story */
.stage { display: grid; grid-template-columns: minmax(0, 11fr) minmax(0, 9fr); gap: 22px; align-items: start; }
.boardcol { position: sticky; top: calc(env(safe-area-inset-top, 0px) + 12px); display: grid; gap: 10px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px; }
.boardtools { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; justify-content: space-between; }
.seg { display: inline-flex; gap: 4px; flex-wrap: wrap; }
.seg button { font-size: .82rem; padding: .3rem .8rem; }
#board { width: 100%; height: auto; max-height: 62vh; display: block; touch-action: manipulation; }
#board .unit { cursor: pointer; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: .78rem; color: var(--text-muted); }
.legend span { display: inline-flex; gap: 5px; align-items: center; }
.legend svg { width: 14px; height: 14px; }
.fognote { font-size: .8rem; color: var(--text-muted); }
.tl-controls { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.tl-controls button { font-size: .8rem; padding: .28rem .75rem; }
.tl-controls input[type=range] { flex: 1; min-width: 120px; accent-color: var(--accent); }
#chart { width: 100%; height: auto; display: block; cursor: crosshair; }
.turnhead { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.turnhead h3 { font-size: 1.25rem; }

.findings { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px; }
.finding { all: unset; box-sizing: border-box; cursor: pointer; display: grid; gap: 2px; padding: 12px 14px; border-radius: var(--radius-sm); background: var(--surface); border: 1px solid var(--border); }
.finding:hover { border-color: var(--border-strong); }
.finding:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
.finding .big { font-family: var(--font-display); font-size: 1.6rem; line-height: 1.1; font-variation-settings: "opsz" 96; }
.finding .what { font-size: .85rem; color: var(--text-muted); }

.story { display: grid; gap: 0; position: relative; }
.story::before { content: ""; position: absolute; left: 21px; top: 18px; bottom: 18px; width: 2px; background: var(--border); }
.slice { position: relative; display: grid; grid-template-columns: 44px minmax(0, 1fr); gap: 0 12px; padding: 10px 0; }
.slice .tick { all: unset; cursor: pointer; z-index: 1; width: 44px; height: 44px; border-radius: 50%; display: grid; place-items: center; background: var(--surface); border: 2px solid var(--border-strong); font-size: .78rem; font-weight: 650; font-variant-numeric: tabular-nums; color: var(--text-muted); }
.slice .tick:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
.slice.major .tick { color: var(--text); }
.slice.active .tick { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
.slice .body { min-width: 0; display: grid; gap: 6px; padding-top: 3px; }
.slice .hl { all: unset; cursor: pointer; font-family: var(--font-display); font-size: 1.08rem; line-height: 1.3; font-variation-settings: "opsz" 36; text-wrap: balance; }
.slice .hl:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; border-radius: 4px; }
.slice .meta { font-size: .82rem; color: var(--text-muted); display: flex; gap: 4px 12px; flex-wrap: wrap; }
.slice .meta b { font-weight: 650; }
.slice .meta .bt, .slice .meta .rt { color: var(--text); }
.said { display: grid; gap: 6px; }
.quote { margin: 0; font-size: .88rem; padding: 6px 10px; border-radius: 0 var(--radius-sm) var(--radius-sm) 0; background: var(--surface); overflow-wrap: anywhere; }
.quote.blue { border-left: 3px solid var(--team-blue); } .quote.red { border-left: 3px solid var(--team-red); }
.quote cite { display: block; font-style: normal; font-size: .75rem; color: var(--text-muted); }
.quote mark { background: var(--accent-glow); color: inherit; border-radius: 3px; }
.slice.minor .hl { font-family: var(--font-body); font-size: .9rem; color: var(--text-muted); }

.detail { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; margin-top: 6px; }
.detail-head { padding: 12px 14px; display: grid; gap: 8px; border-bottom: 1px solid var(--border); }
.seatlist { display: grid; }
.seat { all: unset; box-sizing: border-box; display: grid; grid-template-columns: 26px minmax(0, 1fr) auto; gap: 2px 10px; align-items: center; padding: 8px 14px; cursor: pointer; border-bottom: 1px solid var(--border); }
.seat:hover { background: var(--surface-2); }
.seat[aria-selected="true"] { background: var(--accent-glow); }
.seat:focus-visible { outline: 3px solid var(--accent); outline-offset: -3px; }
.seat .role { font-weight: 500; font-size: .9rem; }
.seat .order { grid-column: 2; font-size: .8rem; color: var(--text-muted); overflow-wrap: anywhere; }
.seat .order.rej { color: var(--bad); }
.seat .time { grid-row: 1 / span 2; grid-column: 3; text-align: right; font-size: .78rem; color: var(--text-muted); display: grid; gap: 3px; justify-items: end; }
.bar { height: 5px; border-radius: 3px; background: var(--border); width: 64px; overflow: hidden; }
.bar i { display: block; height: 100%; border-radius: 3px; }
.think { padding: 12px 14px; display: grid; gap: 8px; border-bottom: 1px solid var(--border); }
.think-head { display: flex; justify-content: space-between; gap: 10px; align-items: baseline; flex-wrap: wrap; }
.reasoning { font-family: var(--font-mono); font-size: .76rem; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; background: var(--surface-2); border-radius: var(--radius-sm); padding: 10px 12px; max-height: 22rem; overflow: auto; margin: 0; }
.reasoning mark { background: var(--accent-glow); color: inherit; border-radius: 3px; }
.asym { font-size: .85rem; color: var(--text-muted); background: var(--surface-2); border-radius: var(--radius-sm); padding: 10px 12px; }
.chat { padding: 12px 14px; display: grid; gap: 8px; }
.chatscroll { display: grid; gap: 8px; max-height: 24rem; overflow: auto; padding-right: 4px; }
.note { background: var(--surface-2); border-radius: var(--radius-sm); padding: 8px 10px; font-size: .86rem; overflow-wrap: anywhere; display: grid; gap: 3px; }
.note.plan { outline: 1px dashed var(--border-strong); outline-offset: -1px; }
.note .from { font-size: .75rem; color: var(--text-muted); }
.clamp { display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; }
.note button.more { justify-self: start; font-size: .72rem; padding: .1rem .55rem; }
.queued-box { background: var(--surface); border: 1px dashed var(--border-strong); border-radius: var(--radius); padding: 28px; display: grid; gap: 8px; }
.moments { display: grid; gap: 8px; }
.moments a { display: grid; grid-template-columns: 3.2rem 1fr; gap: 8px; text-decoration: none; color: var(--text); font-size: .9rem; }
.moments a span:first-child { color: var(--text-muted); font-variant-numeric: tabular-nums; }

@media (max-width: 960px) {
  .hero, .stage { grid-template-columns: 1fr; }
  .boardcol { top: env(safe-area-inset-top, 0px); z-index: 5; padding: 8px; gap: 6px; box-shadow: var(--shadow-lift); }
  #board { max-height: 34vh; }
  .boardcol .legend, .boardcol .fognote, #chart { display: none; }
  .scoreboard { grid-template-columns: 1fr 1fr; }
  .clock { grid-column: 1 / -1; grid-row: 1; flex-direction: row; display: flex; justify-content: center; gap: 14px; }
}
@media (max-width: 560px) {
  .scoreboard { grid-template-columns: 1fr; }
  .player.red { text-align: left; background: linear-gradient(90deg, var(--team-red-wash), transparent 70%), var(--surface); }
  .player.red .who { flex-direction: row; }
  .player.red .howrun { justify-self: start; }
  .howrun { display: none; }
  .vs .pts { font-size: 2.6rem; }
  th, td { padding: .55rem .6rem; }
}
/* benchmarks */
.bench { display: grid; gap: 22px; }
.filters { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.filters .lbl { font-size: .85rem; color: var(--text-muted); margin-right: 4px; }
.filters button { font-size: .8rem; padding: .28rem .8rem; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
.kpi { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 14px 16px; display: grid; gap: 8px; align-content: start; }
.kpi .q { font-size: .85rem; color: var(--text-muted); }
.kpi .pair { display: grid; gap: 4px; }
.kpi .row { display: flex; align-items: baseline; gap: 8px; }
.kpi .v { font-family: var(--font-body); font-size: 1.75rem; font-weight: 500; line-height: 1.1; }
.kpi .row .who { font-size: .85rem; color: var(--text-muted); }
.kpi .note2 { font-size: .8rem; color: var(--text-muted); }
.key { display: inline-block; width: 10px; height: 10px; flex: none; }
.key.blue { background: var(--team-blue); border-radius: 50%; }
.key.red { background: var(--team-red); border-radius: 2px; }
.charts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.chartcard { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 18px; display: grid; gap: 8px; align-content: start; min-width: 0; }
.chartcard.wide { grid-column: 1 / -1; }
.chartcard h3 { font-size: 1.1rem; }
.chartcard .sub { font-size: .85rem; color: var(--text-muted); max-width: 60ch; }
.chartcard svg.viz { width: 100%; height: auto; display: block; overflow: visible; }
.chartcard svg.viz [data-tip] { cursor: default; }
.chartcard svg.viz [data-tip]:focus { outline: none; }
.chartcard svg.viz [data-tip]:focus-visible .hit, .chartcard svg.viz [data-tip]:hover .hit { stroke: var(--text); stroke-width: 1; stroke-opacity: .35; }
.lg { display: flex; gap: 6px 16px; flex-wrap: wrap; font-size: .82rem; color: var(--text-muted); }
.lg span { display: inline-flex; align-items: center; gap: 6px; }
.small-multiples { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 22px; }
.small-multiples h4 { margin: 0; font-size: .9rem; font-weight: 500; }
details.tv summary { cursor: pointer; font-size: .82rem; color: var(--accent); width: fit-content; }
details.tv .tw { overflow-x: auto; margin-top: 6px; }
details.tv table { font-size: .82rem; }
details.tv td, details.tv th { padding: .35rem .6rem; font-variant-numeric: tabular-nums; }
#tip { position: fixed; z-index: 50; pointer-events: none; background: var(--surface); color: var(--text); border: 1px solid var(--border-strong); border-radius: 8px; padding: 8px 10px; font-size: .8rem; box-shadow: var(--shadow-lift); max-width: 260px; display: grid; gap: 3px; }
#tip .tv1 { font-weight: 650; font-size: .95rem; }
#tip .tr { display: flex; gap: 6px; align-items: center; }
#tip .tr i { width: 12px; height: 2px; display: inline-block; }
"""
