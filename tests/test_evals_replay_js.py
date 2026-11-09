# flake8: noqa: E501
"""Tests for :mod:`league_site.evals.replay_js` (the replay page script).

The decision logic lives in pure functions on ``window.LoaEvals``; a node
harness evaluates the real ``REPLAY_JS`` with no DOM and calls them. Those
tests skip when node is absent; the static safety checks always run.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from league_site.evals import slicer
from league_site.evals.replay_js import REPLAY_JS

_NODE = shutil.which("node")
_FIXTURES = Path(__file__).parent / "fixtures" / "evals"
needs_node = pytest.mark.skipif(_NODE is None, reason="node not available for the JS harness")

# The harness evaluates REPLAY_JS without a document, then runs the test's
# snippet (an async function body) with ``E`` bound to window.LoaEvals and
# ``input`` to the JSON payload read from stdin; its result is printed as JSON.
_HARNESS = """
"use strict";
const [src, snippet] = [process.argv[1], process.argv[2]];
const payload = require("fs").readFileSync(0, "utf8");
global.window = {};
eval(src);
const E = window.LoaEvals, input = JSON.parse(payload);
const AsyncFunction = (async () => {}).constructor;
new AsyncFunction("E", "input", snippet)(E, input)
  .then((out) => process.stdout.write(JSON.stringify(out === undefined ? null : out)));
"""


def _js(snippet: str, payload: Any = None) -> Any:
    result = subprocess.run(
        [_NODE or "node", "-e", _HARNESS, "--", REPLAY_JS, snippet],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(result.stdout)


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES / f"{name}.json").read_text())


# ---------------------------------------------------------------------------
# 1. Live advance: at-end viewers follow, scrubbed-back viewers stay put
# ---------------------------------------------------------------------------


@needs_node
def test_at_end_viewer_advances_and_scrubbed_viewer_stays() -> None:
    out = _js("""
        return {
          endAdv: E.shouldAdvance(true, 12, 13), endTurn: E.nextTurn(true, 12, 13),
          backAdv: E.shouldAdvance(false, 5, 13), backTurn: E.nextTurn(false, 5, 13),
          sameAdv: E.shouldAdvance(true, 13, 13), sameTurn: E.nextTurn(true, 13, 13),
          firstLoad: E.nextTurn(true, 0, 24),
        };
        """)
    assert out["endAdv"] is True and out["endTurn"] == 13
    assert out["backAdv"] is False and out["backTurn"] == 5
    assert out["sameAdv"] is False and out["sameTurn"] == 13
    assert out["firstLoad"] == 24


# ---------------------------------------------------------------------------
# 2. In-progress polling only while live, at the latest turn, tab visible
# ---------------------------------------------------------------------------


@needs_node
def test_in_progress_files_polled_only_when_live_latest_and_visible() -> None:
    cases = [
        ({"status": "live", "atLatest": True, "hidden": False}, True),
        ({"status": "live", "atLatest": False, "hidden": False}, False),
        ({"status": "live", "atLatest": True, "hidden": True}, False),
        ({"status": "finished", "atLatest": True, "hidden": False}, False),
        ({"status": "queued", "atLatest": True, "hidden": False}, False),
        ({"status": "tie", "atLatest": True, "hidden": False}, False),
    ]
    got = _js("return input.map(E.shouldPollLive);", [c for c, _ in cases])
    assert got == [want for _, want in cases]


@needs_node
def test_live_file_path_follows_the_published_layout() -> None:
    out = _js("""
        const p = E.paths;
        return [p.manifest("/evals/data", "ev1"), p.live("/evals/data", "ev1", "a6", "blue-2"),
                p.thinking("/evals/data", "ev1", "a6", 7),
                p.match("/b", "ev1", {file: "a6.1a2b3c4d.json"}, "a6"),
                p.match("/b", "ev1", {}, "a6"), p.match("/b", "ev1", {file: "../x.json"}, "a6")];
        """)
    assert out == [
        "/evals/data/ev1/manifest.json",
        "/evals/data/ev1/live/a6/blue-2.json",
        "/evals/data/ev1/thinking/a6-t7.json",
        "/b/ev1/a6.1a2b3c4d.json",
        "/b/ev1/a6.json",
        "/b/ev1/a6.json",
    ]


# ---------------------------------------------------------------------------
# 3. Stale badge and schema guard
# ---------------------------------------------------------------------------


@needs_node
def test_live_match_two_hours_old_shows_no_update_message() -> None:
    now = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
    old = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%MZ")
    fresh = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%MZ")
    out = _js(
        """
        const now = Date.parse(input.now);
        return {
          stale: E.statusInfo("live", 9, 40, input.old, now),
          fresh: E.statusInfo("live", 9, 40, input.fresh, now),
          done: E.statusInfo("finished", 32, 40, input.old, now),
          tie: E.statusInfo("tie", 24, 40, input.old, now),
          isStale: E.isStale(input.old, now, "live"),
        };
        """,
        {"now": now.isoformat(), "old": old, "fresh": fresh},
    )
    assert out["stale"]["word"] == "No update since 10:00 UTC"
    assert "Live" not in out["stale"]["word"]
    assert out["fresh"] == {"cls": "live", "word": "Live, turn 9 of 40"}
    assert out["done"]["word"] == "Finished"
    assert out["tie"]["word"] == "Stopped (tie)"
    assert out["isStale"] is True


@needs_node
def test_unsupported_schema_major_is_refused() -> None:
    got = _js(
        "return input.map(E.supportedVersion);", ["1.1", "1.0", "1", "2.0", "0.9", "", None, "x"]
    )
    assert got == [True, True, True, False, False, False, False, False]


def test_unsupported_version_message_is_in_the_script() -> None:
    assert "This page can't read data version ${esc(v)}. Reload later." in REPLAY_JS


# ---------------------------------------------------------------------------
# 4. Reasoning asymmetry
# ---------------------------------------------------------------------------

_RUNS = {
    "blue": {
        "model": "GLM-5.3-Flash",
        "runtime": "self-hosted TensorFold",
        "thinking": "on",
        "sampling": "temperature 1.0",
        "reasoning": "full",
    },
    "red": {
        "model": "claude-sonnet-5-5",
        "runtime": "Claude Code CLI (claude -p)",
        "thinking": "effort medium",
        "sampling": "default",
        "reasoning": "none",
    },
}


@needs_node
def test_reasoning_none_team_shows_the_claude_p_note() -> None:
    match = _fixture("hard-a")
    for side, run in _RUNS.items():
        match["teams"][side]["run"] = run
    k = {"agent_id": "red-1", "completion_tokens": 2400, "reasoning_tokens": None}
    out = _js(
        """
        const m = input.match;
        return {kinds: [E.reasoningKind(m, "blue"), E.reasoningKind(m, "red")],
                note: E.noReasoningNote(m.teams.red, input.k),
                bare: E.noReasoningNote(m.teams.red, null)};
        """,
        {"match": {"teams": match["teams"], "turns": []}, "k": k},
    )
    assert out["kinds"] == ["full", "none"]
    assert out["bare"] == (
        "Sonnet runs through Claude Code CLI (claude -p), which returns no reasoning text."
    )
    assert out["note"].startswith(out["bare"]) and "2.4k tokens" in out["note"]


@needs_node
def test_reasoning_kind_falls_back_to_has_text_without_a_run_block() -> None:
    match = _fixture("thief-a4")
    out = _js(
        'return [E.reasoningKind(input, "blue"), E.reasoningKind(input, "red")];',
        {"teams": match["teams"], "turns": match["turns"][:3]},
    )
    assert out == ["full", "none"]


@needs_node
def test_full_team_loads_reasoning_from_the_thinking_path() -> None:
    out = _js("""
        const seen = [];
        const files = {"/evals/data/ev/thinking/hard-a-t17.json": [
          {agent_id: "blue-1", reasoning: "Take the Crown at T17."},
          {agent_id: "blue-2", reasoning: "Hold cp-2b."}]};
        const fakeFetch = (url) => { seen.push(url); const d = files[url];
          return Promise.resolve({ok: !!d, status: d ? 200 : 404, json: () => Promise.resolve(d)}); };
        const cache = new Map();
        const url = E.paths.thinking("/evals/data", "ev", "hard-a", 17);
        const a = await E.loadReasoning(fakeFetch, url, "blue-1", cache);
        const b = await E.loadReasoning(fakeFetch, url, "blue-2", cache);
        const none = await E.loadReasoning(fakeFetch, url, "blue-9", cache);
        let missing = null;
        try { await E.loadReasoning(fakeFetch, E.paths.thinking("/evals/data", "ev", "hard-a", 18), "blue-1", cache); }
        catch (e) { missing = e.message; }
        return {a, b, none, missing, seen};
        """)
    assert out["a"] == "Take the Crown at T17."
    assert out["b"] == "Hold cp-2b."
    assert out["none"] is None
    assert out["missing"] == "missing"
    # One fetch per turn file (cached across seats), plus the missing one.
    assert out["seen"] == [
        "/evals/data/ev/thinking/hard-a-t17.json",
        "/evals/data/ev/thinking/hard-a-t18.json",
    ]


# ---------------------------------------------------------------------------
# 5. boardSVG on the hard-a fixture, with the slice's marks
# ---------------------------------------------------------------------------


@needs_node
def test_board_svg_renders_hard_a_turn_17_with_slice_marks() -> None:
    match = _fixture("hard-a")
    sl = next(s for s in slicer.slice_match(match) if s["turn"] == 17)
    assert sl["entities"], "hard-a turn 17 should be a slice with entities"
    out = _js(
        """
        const m = input.match;
        const full = E.boardSVG(m, 17, "all", {axes: true, interactive: true, marks: input.marks});
        const fog = E.boardSVG(m, 17, "blue", {});
        const start = E.boardSVG(m, 0, "red", {});
        return {vb: full.vb, g: full.g, fogLen: fog.g.length, startLen: start.g.length,
                glyph: E.glyph("thief", 10, 10, 5, "red", "none")};
        """,
        {"match": match, "marks": sl["entities"]},
    )
    w, h = match["scenario"]["w"], match["scenario"]["h"]
    assert out["vb"] == f"0 0 {w * 40 + 40} {h * 40 + 40}"
    for ent in sl["entities"]:
        x, y = ent["pos"]
        mark = (
            f'class="mark" data-key="{ent["key"]}" cx="{20 + x * 40 + 20}" cy="{20 + y * 40 + 20}"'
        )
        assert mark in out["g"]
    assert 'data-unit="blue-u1"' in out["g"]
    assert "var(--team-blue)" in out["g"] and "var(--fog)" not in out["g"]
    assert out["fogLen"] > 0 and out["startLen"] > 0
    assert out["glyph"].startswith("<polygon")


@needs_node
def test_board_svg_escapes_entity_text() -> None:
    match = _fixture("hard-a")
    out = _js('return E.boardSVG(input, 17, "all", {marks: [{key: "<x>", pos: [1, 1]}]}).g;', match)
    assert "<x>" not in out and "&lt;x&gt;" in out


@needs_node
def test_slice_quotes_escape_chat_text() -> None:
    match = _fixture("hard-a")
    match["turns"][16]["messages"] = [
        {"from": "blue-1", "team_id": "blue", "text": "<img src=x onerror=alert(1)> crown now"}
    ]
    sl = {"turn": 17, "entities": [{"key": "crown", "pos": [10, 8], "team": "blue"}]}
    out = _js("return E.sliceQuotes(input.m, input.sl);", {"m": match, "sl": sl})
    assert out[0]["team"] == "blue"
    assert "<img" not in out[0]["html"] and "&lt;img" in out[0]["html"]
    assert "<mark>crown</mark>" in out[0]["html"]


# ---------------------------------------------------------------------------
# Static safety checks (no node needed)
# ---------------------------------------------------------------------------


def test_innerhtml_is_assigned_only_inside_the_set_html_helper() -> None:
    assignments = re.findall(r"\.innerHTML\s*=", REPLAY_JS)
    assert len(assignments) == 1
    assert "function setHTML(el, h) { if (el) el.innerHTML = h; }" in REPLAY_JS
    for banned in ("insertAdjacentHTML", "outerHTML", "document.write", "eval("):
        assert banned not in REPLAY_JS


def test_data_text_fields_are_never_interpolated_unescaped() -> None:
    fields = (
        "text|reasoning|reason|headline|name|runtime|model|thinking|sampling|value"
        "|thinking_so_far|response_so_far|label|from|who|key|id|role|carrying|updated"
    )
    pattern = re.compile(r"\$\{\s*[\w.]*\.(?:%s)\s*\}" % fields)
    # Lines that build markup must wrap data fields in esc(); plain-string lines
    # (titles, labels) are escaped as a whole where they enter markup.
    markup = [line for line in REPLAY_JS.splitlines() if "<" in line]
    raw = [m for line in markup for m in pattern.findall(line)]
    assert raw == [], raw
    assert REPLAY_JS.count("esc(") > 40


def test_script_keeps_the_ready_marker_and_fits_with_room_for_charts() -> None:
    assert 'document.documentElement.dataset.evals = "ready"' in REPLAY_JS
    assert len(REPLAY_JS.encode("utf-8")) < 40 * 1024
