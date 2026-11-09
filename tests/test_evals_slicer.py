"""Tests for the deterministic match slicer."""

import ast
import copy
import json
from pathlib import Path

import pytest

from league_site.evals import slicer

FIX = Path(__file__).parent / "fixtures" / "evals"


def load(name):
    return json.loads((FIX / f"{name}.json").read_text())


@pytest.fixture(scope="module")
def hard():
    return load("hard-a")


@pytest.fixture(scope="module")
def thief():
    return load("thief-a4")


def by_turn(slices):
    return {s["turn"]: s for s in slices}


def test_hard_a_key_turns(hard):
    sl = slicer.slice_match(hard)
    major = by_turn([s for s in sl if s["major"]])
    assert {17, 18, 23} <= set(major)
    assert "GLM completes ms-hold-2a (+8)" in major[17]["headline"]
    assert "GLM delivers 2" in major[17]["headline"]
    assert major[23]["delta"]["blue"] == 12
    assert major[18]["delta"]["red"] == 10
    assert sl[0]["opening"] and sl[0]["turn"] == 1
    assert sl[0]["headline"].startswith("Opening moves")
    ent = next(e for e in major[17]["entities"] if e["key"] == "ms-hold-2a")
    assert ent["team"] == "blue" and len(ent["pos"]) == 2


def test_slice_shape_and_coverage(hard):
    sl = slicer.slice_match(hard)
    keys = {
        "from",
        "to",
        "turn",
        "items",
        "entities",
        "delta",
        "score",
        "lead_change",
        "mirrored",
        "rejected",
        "lost",
        "major",
        "opening",
        "headline",
    }
    assert all(set(s) == keys for s in sl)
    assert sl[0]["from"] == 1 and sl[-1]["to"] == len(hard["turns"])
    for a, b in zip(sl, sl[1:]):
        assert b["from"] == a["to"] + 1
    assert sl[-1]["score"] == hard["turns"][-1]["state"]["points"]


def test_deterministic_and_no_mutation(hard, thief):
    for m in (hard, thief):
        before = copy.deepcopy(m)
        a = slicer.slice_match(m)
        b = slicer.slice_match(m)
        assert a == b
        assert m == before
        assert slicer.findings(m, a) == slicer.findings(m, b)
        assert m == before


def test_findings_turns_valid(hard, thief):
    for m in (hard, thief):
        fx = slicer.findings(m, slicer.slice_match(m))
        assert fx
        for f in fx:
            assert set(f) == {"value", "text", "turn"}
            assert isinstance(f["turn"], int) and 1 <= f["turn"] <= len(m["turns"])
    fx = slicer.findings(thief, slicer.slice_match(thief))
    assert any("both sides scored the same" in f["text"] for f in fx)


def test_thief_rejected_counts(thief):
    want = {"blue": 0, "red": 0}
    for tr in thief["turns"]:
        for r in tr["rejected"]:
            if not r.get("passive"):
                want[r["unit_id"].split("-")[0]] += 1
    sl = slicer.slice_match(thief)
    got = {t: sum(s["rejected"][t] for s in sl) for t in want}
    assert got == want
    fx = slicer.findings(thief, sl)
    assert any(f["value"] == f"{want['blue']} : {want['red']}" for f in fx)


def test_quote_keys():
    sl = {"entities": [{"key": "cp-3a", "pos": [4, 7], "team": "blue"}]}
    assert slicer.quote_keys(sl) == ["cp-3a", "[4,7]", "[4, 7]"]


def test_empty_match():
    assert slicer.slice_match({"turns": []}) == []


def test_no_model_or_network_imports():
    tree = ast.parse(Path(slicer.__file__).read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add((n.module or "").split(".")[0])
    banned = {"anthropic", "openai", "requests", "httpx", "urllib", "socket", "boto3", "http"}
    assert not mods & banned
