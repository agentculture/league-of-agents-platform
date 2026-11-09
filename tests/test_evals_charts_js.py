"""Tests for the evals index script (``/evals/charts.js``): tables, ticks, tooltips, hit targets.

The numbers come from the real fixtures (hard-a, thief-a4) through ``compute_metrics`` /
``pooled`` / ``slice_match``; a node harness runs the script against a stub DOM, skipped when
node is absent.
"""

# flake8: noqa: E501

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from league_site.evals import metrics, slicer
from league_site.evals.charts_js import CHARTS_JS

FIX = Path(__file__).parent / "fixtures" / "evals"
TAGS = ("thief-a4", "hard-a")
ACTIONS = ["move", "hold", "gather", "deliver", "steal"]

HARNESS = r"""
const vm = require("vm");
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
let tipEl = null;
let innerHTMLWrites = 0;
function el(tag) {
  const e = { tag, children: [], style: {}, attrs: {}, textContent: "", className: "", hidden: false,
    setAttribute(k, v) { this.attrs[k] = v; }, appendChild(c) { this.children.push(c); },
    replaceChildren(...c) { this.children = c; }, offsetWidth: 10, offsetHeight: 10 };
  Object.defineProperty(e, "innerHTML", { set() { innerHTMLWrites++; }, get() { return ""; } });
  return e;
}
const doc = { createElement: el, getElementById() { return tipEl; },
  body: { appendChild(c) { tipEl = c; } }, querySelector() { return null; }, documentElement: {} };
const ctx = vm.createContext({ document: doc });
vm.runInContext(input.js, ctx);
const C = ctx.LoaEvalsCharts;
const sum = input.summary, ms = C.benchMatches(sum);
const col = { blue: "B", red: "R", border: "g", borderStrong: "G", text: "t", muted: "m", surface: "s" };
const out = { tables: {}, ticks: {}, svg: {}, tiles: {}, tip: null, hit: C.HIT_R };
for (const focus of ["all", "hard-a", "thief-a4"]) {
  const scope = C.scopeOf(ms, focus);
  out.tables[focus] = { mix: C.tableMix(scope), tiles: C.tiles(sum, ms, focus) };
  out.svg[focus] = [C.scatterSVG(ms, focus, col).g, C.mixSVG(scope, col).g]
    .concat(C.SM.map(p => C.smSVG(p, ms, focus, col).g), [C.thinkSVG(C.thinkMatch(ms, focus), col).g]);
}
out.tables.scatter = C.tableScatter(ms);
out.tables.sm = C.tableSM(ms);
out.tables.think = {};
for (const m of ms) out.tables.think[m.tag] = C.tableThink(m);
out.tables.tableHTML = C.tableHTML([["a<b", "c"], ["<i>x</i>", "2"]]);
out.ticks.count = C.logTicks(10, 10000, "");
out.ticks.seconds = C.logTicks(1, 1000, "s");
out.ticks.sm = C.SM.filter(p => p.log).map(p => p.ticks.map(t => t.label));
const sc = C.scale(1, 1000, 0, 300, true);
out.ticks.scale = [sc(1), sc(10), sc(1000), sc(0.1), sc(99999)];
C.showTip(["5 <img src=x onerror=1>", "line two"], null);
out.tip = { texts: tipEl.children.map(c => c.textContent), kids: tipEl.children.length, writes: innerHTMLWrites };
console.log(JSON.stringify(out));
"""


def _load(tag: str) -> dict:
    return json.loads((FIX / f"{tag}.json").read_text())


def _summary() -> dict:
    raws = [_load(t) for t in TAGS]
    items = []
    for raw in raws:
        last = raw["turns"][-1]["state"]["points"]
        moments = [
            {"turn": s["turn"], "headline": s["headline"]}
            for s in slicer.slice_match(raw)
            if s["major"]
        ]
        items.append(
            {
                "tag": raw["tag"],
                "label": raw["label"],
                "status": "live" if raw["tag"] == TAGS[0] else raw["status"],
                "reason": raw["reason"],
                "board": "Board",
                "turn": len(raw["turns"]),
                "turn_limit": raw["scenario"]["turn_limit"],
                "file": f"{raw['tag']}.json",
                "score": {"blue": last["blue"], "red": last["red"]},
                "teams": {
                    s: {"name": t["name"], "run": {"reasoning": "full" if s == "blue" else "none"}}
                    for s, t in raw["teams"].items()
                },
                "metrics": metrics.compute_metrics(raw),
                "moments": moments,
            }
        )
    return {
        "schema_version": "1.1",
        "eval": "e",
        "title": "T",
        "generated": "2026-11-09T00:00Z",
        "matches": items,
        "pooled": metrics.pooled(raws),
        "definitions": metrics.DEFINITIONS,
    }


@pytest.fixture(scope="module")
def result() -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not available")
    payload = json.dumps({"js": CHARTS_JS, "summary": _summary()})
    proc = subprocess.run(
        ["node", "-e", HARNESS], input=payload, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# ---- expected values, mirrored in plain Python ----


def pct(v: float) -> str:
    return f"{int(v * 100 + 0.5)}%"


def fmt_n(v: float | None) -> str:
    if v is None:
        return "n/a"
    if v >= 10000:
        return f"{int(v / 1000 + 0.5)}k"
    if v >= 1000:
        return f"{v / 1000:.1f}k"
    return str(int(v + 0.5))


def fmt_s(ms: float | None) -> str:
    if ms is None:
        return "n/a"
    if ms >= 60000:
        return f"{int(ms // 60000)}m {int((ms % 60000) / 1000 + 0.5)}s"
    return f"{int(ms / 1000 + 0.5)}s"


def short(name: str) -> str:
    return name.split(" ")[0]


def test_scatter_table_matches_metrics(result):
    summ = _summary()
    rows = [["Match", "Side", "In enemy half", "Near an enemy", "Points per turn"]]
    want = []
    for m in summ["matches"]:
        for side in ("blue", "red"):
            o = m["metrics"][side]
            want.append(
                [
                    m["tag"],
                    short(m["teams"][side]["name"]),
                    pct(o["enemy_half_share"]),
                    pct(o["contact_share"]),
                    f"{o['points_per_turn']:.2f}",
                    str(o["flips"]),
                    str(o["steals"]),
                ]
            )
    got = result["tables"]["scatter"]
    assert got[0][:5] == rows[0]
    assert got[1:] == want


@pytest.mark.parametrize("focus", ["all", "hard-a", "thief-a4"])
def test_mix_table_matches_metrics(result, focus):
    summ = _summary()
    scope = [m for m in summ["matches"] if focus in ("all", m["tag"])]
    got = result["tables"][focus]["mix"]
    for i, action in enumerate(ACTIONS, start=1):
        cells = [action]
        for side in ("blue", "red"):
            n = sum(sum(m["metrics"][side]["mix"].values()) for m in scope)
            c = sum(m["metrics"][side]["mix"].get(action, 0) for m in scope)
            cells.append(f"{c} ({pct(c / n if n else 0)})")
        assert got[i] == cells


def test_small_multiples_table_matches_metrics(result):
    got = result["tables"]["sm"]
    assert got[0][2:] == [
        "Share of points",
        "Orders accepted as legal",
        "Team chat per turn, words",
        "Median answer time per seat",
    ]
    i = 1
    for m in _summary()["matches"]:
        for side in ("blue", "red"):
            o = m["metrics"][side]
            want = [
                m["tag"],
                short(m["teams"][side]["name"]),
                pct(o["share"]),
                pct(o["accepted"]),
                fmt_n(o["chat_words_per_turn"]),
                fmt_s(int(o["median_answer_ms"] / 1000 + 0.5) * 1000),
            ]
            assert got[i] == want
            i += 1


@pytest.mark.parametrize("tag", TAGS)
def test_thinking_table_matches_metrics(result, tag):
    m = next(x for x in _summary()["matches"] if x["tag"] == tag)
    got = result["tables"]["think"][tag]
    assert len(got) == 1 + len(m["metrics"]["blue"]["per_turn"])
    for row, b, r in zip(
        got[1:], m["metrics"]["blue"]["per_turn"], m["metrics"]["red"]["per_turn"]
    ):
        assert row == [
            str(b["turn"]),
            fmt_n(b["thinking_tokens"]),
            fmt_n(b["max_seat_thinking_tokens"]),
            fmt_s(b["median_answer_ms"]),
            fmt_s(r["median_answer_ms"]),
        ]


def test_tiles_match_pooled_and_focus(result):
    summ = _summary()
    pooled = summ["pooled"]
    tiles = {t["key"]: t for t in result["tables"]["all"]["tiles"]}
    assert tiles["chat_words_per_turn"]["blue"] == fmt_n(pooled["blue"]["chat_words_per_turn"])
    assert tiles["median_answer_ms"]["red"] == fmt_s(pooled["red"]["median_answer_ms"])
    for side in ("blue", "red"):
        assert tiles["rejected"][side] == f"{pooled[side]['rejected']} of {pooled[side]['orders']}"
    assert tiles["median_thinking_tokens"]["red"] == "not reported"
    one = next(m for m in summ["matches"] if m["tag"] == "hard-a")["metrics"]
    foc = {t["key"]: t for t in result["tables"]["hard-a"]["tiles"]}
    assert foc["rejected"]["blue"] == f"{one['blue']['rejected']} of {one['blue']['orders']}"
    assert foc["median_answer_ms"]["blue"] == fmt_s(one["blue"]["median_answer_ms"])
    assert all(
        t["def"] == metrics.DEFINITIONS[t["key"]] for t in foc.values() if t["key"] != "rejected"
    )


def test_table_html_escapes(result):
    html = result["tables"]["tableHTML"]
    assert "a&lt;b" in html and "&lt;i&gt;x&lt;/i&gt;" in html and "<i>" not in html
    assert "Show as table" in html


def test_log_tick_builders(result):
    assert [t["label"] for t in result["ticks"]["count"]] == ["10", "100", "1,000", "10,000"]
    assert [t["label"] for t in result["ticks"]["seconds"]] == ["1 s", "10 s", "100 s", "1,000 s"]
    assert result["ticks"]["sm"] == [
        ["10", "100", "1,000", "10,000"],
        ["1 s", "10 s", "100 s", "1,000 s"],
    ]
    assert [t["v"] for t in result["ticks"]["seconds"]] == [1, 10, 100, 1000]
    lo, mid, hi, under, over = result["ticks"]["scale"]
    assert (lo, hi) == (0, 300) and abs(mid - 100) < 1e-9 and under == 0 and over == 300


def test_tooltip_uses_text_content(result):
    assert result["tip"]["texts"] == ["5 <img src=x onerror=1>", "line two"]
    assert result["tip"]["kids"] == 2 and result["tip"]["writes"] == 0
    body = CHARTS_JS[CHARTS_JS.index("function showTip") : CHARTS_JS.index("function hideTip")]
    assert "textContent" in body and "innerHTML" not in body


def test_hit_targets_at_least_24px(result):
    assert result["hit"] >= 12
    for focus, svgs in result["svg"].items():
        total = 0
        for g in svgs:
            radii = [int(r) for r in re.findall(r'class="hit" cx="[^"]+" cy="[^"]+" r="(\d+)"', g)]
            assert all(r >= 12 for r in radii), (focus, radii)
            total += len(radii)
        assert total > 0
    scatter = result["svg"]["all"][0]
    assert scatter.count('class="hit"') == 2 * len(TAGS)


def test_team_identity_shape_and_colour(result):
    scatter = result["svg"]["all"][0]
    assert "<circle cx=" in scatter and "<rect x=" in scatter
    assert 'fill="B"' in scatter and 'fill="R"' in scatter
    # chart text never takes a team colour
    assert not re.search(r'<text[^>]*fill="(B|R)"', scatter)
    assert 'role="img"' in scatter and "tabindex" in scatter


def test_script_reads_tokens_and_documents_contract():
    assert "var(--team-blue)" not in CHARTS_JS and '"--team-blue"' in CHARTS_JS
    assert "keydown" in CHARTS_JS and "ArrowRight" in CHARTS_JS
    import league_site.evals.charts_js as mod

    assert "summary.json" in mod.__doc__ and "data-eval" in mod.__doc__
