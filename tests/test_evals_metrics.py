"""Tests for league_site.evals.metrics (benchmark numbers from exported match data)."""

from __future__ import annotations

import ast
import json
import statistics
from pathlib import Path

import pytest

from league_site.evals import metrics

FIX = Path(__file__).parent / "fixtures" / "evals"


def _load(name):
    return json.loads((FIX / f"{name}.json").read_text())


@pytest.fixture(scope="module")
def thief():
    return _load("thief-a4")


@pytest.fixture(scope="module")
def hard():
    return _load("hard-a")


def test_thief_rejected_of_orders(thief):
    m = metrics.compute_metrics(thief)
    assert (m["blue"]["rejected"], m["blue"]["orders"]) == (1, 144)
    assert (m["red"]["rejected"], m["red"]["orders"]) == (16, 144)
    assert m["red"]["accepted"] == pytest.approx(1 - 16 / 144)


def test_thief_independent_values(thief):
    m = metrics.compute_metrics(thief)
    spawn = {}
    for u in thief["initial"]["units"]:
        spawn.setdefault(u["team_id"], u["pos"])

    def d(a, b):
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    for tm, en in (("blue", "red"), ("red", "blue")):
        n = fwd = 0
        lat = []
        for t in thief["turns"]:
            for u in t["state"]["units"]:
                if u["team_id"] == tm:
                    n += 1
                    fwd += d(u["pos"], spawn[en]) < d(u["pos"], spawn[tm])
            lat += [x["elapsed_ms"] for x in t["latency"] if x["agent_id"].startswith(tm + "-")]
        pts = thief["turns"][-1]["state"]["points"]
        assert m[tm]["enemy_half_share"] == pytest.approx(fwd / n)
        assert m[tm]["median_answer_ms"] == statistics.median(lat)
        assert m[tm]["points"] == pts[tm]
        assert m[tm]["share"] == pytest.approx(pts[tm] / (pts["blue"] + pts["red"]))
        assert m[tm]["points_per_turn"] == pytest.approx(pts[tm] / len(thief["turns"]))
        assert len(m[tm]["per_turn"]) == len(thief["turns"])
        assert sum(m[tm]["mix"].values()) == m[tm]["orders"]


def test_team_by_prefix_when_no_team_id():
    match = {
        "initial": {
            "units": [{"team_id": "blue", "pos": [0, 0]}, {"team_id": "red", "pos": [9, 9]}]
        },
        "turns": [
            {
                "turn": 1,
                "state": {"units": [], "points": {"blue": 0, "red": 0}},
                "actions": [{"unit_id": "red-u1", "action": "move"}],
                "rejected": [{"unit_id": "red-u1", "passive": True}, {"unit_id": "red-u1"}],
                "latency": [{"agent_id": "blue-1", "elapsed_ms": 10}],
                "think": [{"agent_id": "red-1", "reasoning_tokens": None, "order_lost": True}],
                "messages": [{"from": "blue-1", "text": "a b  c"}],
                "events": [],
            }
        ],
    }
    m = metrics.compute_metrics(match)
    assert m["red"]["orders"] == 1 and m["red"]["rejected"] == 1 and m["red"]["lost"] == 1
    assert m["blue"]["chat_words_per_turn"] == 3 and m["red"]["chat_words_per_turn"] == 0
    assert m["red"]["median_thinking_tokens"] is None
    assert m["blue"]["share"] == 0.5


def test_definitions_cover_every_metric(thief):
    for side in metrics.compute_metrics(thief).values():
        for key in side:
            if key not in ("per_turn", "mix"):
                assert metrics.DEFINITIONS.get(key), key


def test_module_does_no_io():
    tree = ast.parse(Path(metrics.__file__).read_text())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add((node.module or "").split(".")[0])
    assert mods <= {"__future__", "statistics", "typing"}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & {"open", "os", "environ", "getenv"}


def test_pooled_totals_are_sums(hard, thief):
    p = metrics.pooled([hard, thief])
    for tm in metrics.SIDES:
        parts = [metrics.compute_metrics(x)[tm] for x in (hard, thief)]
        assert p[tm]["orders"] == sum(x["orders"] for x in parts)
        assert p[tm]["rejected"] == sum(x["rejected"] for x in parts)
        assert p[tm]["turns"] == len(hard["turns"]) + len(thief["turns"])
        words = sum(
            x["chat_words_per_turn"] * len(g["turns"]) for x, g in zip(parts, (hard, thief))
        )
        assert p[tm]["words"] == pytest.approx(words)
