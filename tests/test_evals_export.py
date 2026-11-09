"""Tests for league_site.evals.export (bundle + eval config -> published 1.1 layout)."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path

import pytest

from league_site.evals import export, metrics, schema

FIX = Path(__file__).parent / "fixtures" / "evals"
NOW = "2026-11-09T12:00:00Z"
TOOLS_TEXT = "[withheld until the match ends: a seat has tools]"
SCAN_TEXT = "[withheld: failed the leak scan]"
PER_MATCH_ONLY = ("eval", "reason", "generated")


def _fixture(tag):
    return json.loads((FIX / f"{tag}.json").read_text())


def _bundle_match(tag, new_tag=None):
    """A bundle (prototype-format) match built from a per-match fixture."""
    m = _fixture(tag)
    for key in PER_MATCH_ONLY:
        m.pop(key)
    m["teams"] = {s: {"name": t["name"], "model": t["model"]} for s, t in m["teams"].items()}
    m["pending"] = []
    if new_tag:
        m["tag"] = new_tag
        m["id"] = f"m-{new_tag}"
    return m


def _config():
    return json.loads((FIX / "eval-config.json").read_text())


def _write(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def _thinking(tdir, tag, turn, entries):
    tdir.mkdir(parents=True, exist_ok=True)
    return _write(tdir / f"{tag}-t{turn}.json", entries)


@pytest.fixture
def setup(tmp_path):
    """Bundle with hard-a + thief-a4, the example config, and a few thinking files."""
    bundle = {"generated": "2026-11-08T17:31Z", "matches": [_bundle_match("hard-a")]}
    bundle["matches"].append(_bundle_match("thief-a4"))
    tdir = tmp_path / "thinking"
    _thinking(tdir, "hard-a", 1, [{"agent_id": "blue-1", "reasoning": "Move north to cp-1a."}])
    _thinking(
        tdir,
        "hard-a",
        2,
        [
            {"agent_id": "blue-1", "reasoning": "Hold the point."},
            {"agent_id": "blue-2", "reasoning": "Ping build-7.internal.corp.com first."},
        ],
    )
    _thinking(tdir, "thief-a4", 1, [{"agent_id": "blue-3", "reasoning": "Scout east."}])
    paths = {
        "bundle": _write(tmp_path / "bundle.json", bundle),
        "config": _write(tmp_path / "config.json", _config()),
        "thinking": tdir,
        "out": tmp_path / "out",
    }
    return paths


def _run(p, **kw):
    return export.export_eval(p["bundle"], p["config"], p["thinking"], p["out"], now=NOW, **kw)


def _eval_dir(p):
    return p["out"] / "live-evals-prototype"


def _manifest(p):
    return json.loads((_eval_dir(p) / "manifest.json").read_text())


def _match_file(p, tag):
    entry = next(m for m in _manifest(p)["matches"] if m["tag"] == tag)
    return json.loads((_eval_dir(p) / entry["file"]).read_text())


def _texts(match):
    for tr in match["turns"]:
        for item in tr["plans"] + tr["messages"]:
            yield item["text"]


# --- 1. a fixture export validates; an unclean thinking file is dropped --------------------


def test_export_validates_against_schema(setup):
    _run(setup)
    man = _manifest(setup)
    assert schema.validate_manifest(man) == []
    assert man["schema_version"] == schema.SCHEMA_VERSION
    for entry in man["matches"]:
        m = json.loads((_eval_dir(setup) / entry["file"]).read_text())
        assert schema.validate_match(m) == []
        assert m["schema_version"] == schema.SCHEMA_VERSION
        assert m["eval"] == "live-evals-prototype"
        assert "pending" not in m
        assert {"slices", "findings", "metrics"} <= set(m)
        assert m["metrics"] == metrics.compute_metrics(m)


def test_status_reason_run_and_seats_come_from_config(setup):
    _run(setup)
    m = _match_file(setup, "thief-a4")
    cfg = _config()["matches"]["thief-a4"]
    assert (m["status"], m["reason"]) == (cfg["status"], cfg["reason"])
    assert m["teams"]["blue"]["run"] == cfg["teams"]["blue"]["run"]
    assert m["teams"]["red"]["seats"] == cfg["teams"]["red"]["seats"]
    assert m["teams"]["blue"]["model"] == "glm-5.3-flash/tensorfold (thinking)"


def test_unclean_thinking_file_dropped_turn_still_exports(setup):
    report = _run(setup)
    ev = _eval_dir(setup)
    assert (ev / "thinking" / "hard-a-t1.json").exists()
    assert (ev / "thinking" / "thief-a4-t1.json").exists()
    assert not (ev / "thinking" / "hard-a-t2.json").exists()
    m = _match_file(setup, "hard-a")
    assert any(tr["turn"] == 2 for tr in m["turns"])
    assert len(report.dropped) == 1
    drop = report.dropped[0]
    assert drop["file"] == "live-evals-prototype/thinking/hard-a-t2.json"
    assert drop["seats"] == {"blue-2": ["hostname"]}
    blob = json.dumps(report.to_dict())
    assert "corp.com" not in blob and "build-7" not in blob


def test_copied_thinking_is_byte_identical(setup):
    _run(setup)
    src = (setup["thinking"] / "hard-a-t1.json").read_bytes()
    assert (_eval_dir(setup) / "thinking" / "hard-a-t1.json").read_bytes() == src


def test_unclean_chat_and_plan_text_withheld(setup):
    bundle = json.loads(setup["bundle"].read_text())
    tr = bundle["matches"][0]["turns"][3]
    tr["messages"][0]["text"] = "see 10.0.0.12 for the map"
    tr["plans"][0]["text"] = "plan saved in /home/me/plan.txt"
    _write(setup["bundle"], bundle)
    report = _run(setup)
    m = _match_file(setup, "hard-a")
    out = next(t for t in m["turns"] if t["turn"] == tr["turn"])
    assert out["messages"][0]["text"] == SCAN_TEXT
    assert out["plans"][0]["text"] == SCAN_TEXT
    assert out["messages"][1]["text"] == "fixture text"
    kinds = sorted((w["kind"], tuple(w["patterns"])) for w in report.withheld)
    assert kinds == [("message", ("ipv4",)), ("plan", ("absolute-path",))]
    assert "10.0.0.12" not in json.dumps(report.to_dict())


def test_match_missing_from_config_is_skipped(setup):
    cfg = _config()
    del cfg["matches"]["thief-a4"]
    _write(setup["config"], cfg)
    report = _run(setup)
    assert [s["tag"] for s in report.skipped] == ["thief-a4"]
    assert [m["tag"] for m in _manifest(setup)["matches"]] == ["hard-a"]
    assert not (_eval_dir(setup) / "thinking" / "thief-a4-t1.json").exists()


# --- 2. tools-on seats ---------------------------------------------------------------------


def _tools_on(setup, status):
    cfg = _config()
    entry = cfg["matches"]["hard-a"]
    entry["status"] = status
    entry["teams"]["red"]["seats"]["red-2"]["tools"] = True
    _write(setup["config"], cfg)
    return _run(setup)


def test_tools_on_live_match_withholds_private_content(setup):
    report = _tools_on(setup, "live")
    ev = _eval_dir(setup)
    assert (ev / "hard-a.json").exists()
    m = _match_file(setup, "hard-a")
    src = _fixture("hard-a")
    assert [t["state"] for t in m["turns"]] == [t["state"] for t in src["turns"]]
    assert m["turns"][-1]["state"]["points"] == src["turns"][-1]["state"]["points"]
    assert [t["actions"] for t in m["turns"]] == [t["actions"] for t in src["turns"]]
    assert [t["rejected"] for t in m["turns"]] == [t["rejected"] for t in src["turns"]]
    texts = list(_texts(m))
    assert texts and set(texts) == {TOOLS_TEXT}
    assert not list((ev / "thinking").glob("hard-a-*"))
    assert (ev / "thinking" / "thief-a4-t1.json").exists()
    assert report.held_until_end == ["hard-a"]
    assert report.dropped == [] and report.withheld == []


def test_tools_on_finished_match_publishes_normally(setup):
    report = _tools_on(setup, "finished")
    m = _match_file(setup, "hard-a")
    assert set(_texts(m)) == {"fixture text"}
    assert (_eval_dir(setup) / "thinking" / "hard-a-t1.json").exists()
    assert report.held_until_end == []


def test_network_on_counts_as_tools(setup):
    cfg = _config()
    cfg["matches"]["thief-a4"]["status"] = "live"
    cfg["matches"]["thief-a4"]["teams"]["blue"]["seats"]["blue-1"]["network"] = True
    _write(setup["config"], cfg)
    _run(setup)
    assert set(_texts(_match_file(setup, "thief-a4"))) == {TOOLS_TEXT}


# --- 3. manifest, hashed names, summary ----------------------------------------------------


def test_finished_matches_get_hashed_names_live_do_not(setup):
    cfg = _config()
    cfg["matches"]["thief-a4"]["status"] = "live"
    _write(setup["config"], cfg)
    _run(setup)
    ev = _eval_dir(setup)
    files = {m["tag"]: m["file"] for m in _manifest(setup)["matches"]}
    assert files["thief-a4"] == "thief-a4.json"
    data = (ev / files["hard-a"]).read_bytes()
    sha8 = hashlib.sha256(data).hexdigest()[:8]
    assert files["hard-a"] == f"hard-a.{sha8}.json"


def test_manifest_small_for_six_matches_with_etags(setup):
    bundle = json.loads(setup["bundle"].read_text())
    cfg = _config()
    for i in range(2):
        for base in ("hard-a", "thief-a4"):
            tag = f"{base}-x{i}"
            bundle["matches"].append(_bundle_match(base, tag))
            cfg["matches"][tag] = copy.deepcopy(cfg["matches"][base])
    _write(setup["bundle"], bundle)
    _write(setup["config"], cfg)
    _run(setup)
    raw = (_eval_dir(setup) / "manifest.json").read_bytes()
    man = json.loads(raw)
    assert len(man["matches"]) == 6
    assert len(raw) < 5 * 1024
    for e in man["matches"]:
        data = (_eval_dir(setup) / e["file"]).read_bytes()
        assert e["etag"] == hashlib.sha256(data).hexdigest()[:16]
        assert e["turn"] == json.loads(data)["turns"][-1]["turn"]
        assert e["turn_limit"] == json.loads(data)["scenario"]["turn_limit"]


def test_etag_changes_when_match_changes(setup):
    _run(setup)
    before = {m["tag"]: m["etag"] for m in _manifest(setup)["matches"]}
    bundle = json.loads(setup["bundle"].read_text())
    bundle["matches"][1]["turns"].pop()
    _write(setup["bundle"], bundle)
    _run(setup)
    after = {m["tag"]: m["etag"] for m in _manifest(setup)["matches"]}
    assert after["hard-a"] == before["hard-a"]
    assert after["thief-a4"] != before["thief-a4"]


def test_rerun_is_stable_for_finished_files(setup):
    _run(setup)
    first = _manifest(setup)
    bundle = json.loads(setup["bundle"].read_text())
    bundle["generated"] = "2026-11-08T18:00Z"
    _write(setup["bundle"], bundle)
    _run(setup)
    second = _manifest(setup)
    assert [m["file"] for m in first["matches"]] == [m["file"] for m in second["matches"]]


def test_summary_shape(setup):
    _run(setup)
    s = json.loads((_eval_dir(setup) / "summary.json").read_text())
    keys = {"schema_version", "eval", "title", "generated", "matches", "pooled", "definitions"}
    assert set(s) == keys
    assert s["title"] == _config()["title"]
    assert s["definitions"] == metrics.DEFINITIONS
    assert s["generated"] == NOW
    entry = next(m for m in s["matches"] if m["tag"] == "hard-a")
    assert set(entry) == {
        "tag",
        "label",
        "status",
        "reason",
        "board",
        "turn",
        "turn_limit",
        "file",
        "score",
        "teams",
        "metrics",
        "moments",
    }
    match = _match_file(setup, "hard-a")
    assert entry["board"] == "hard"
    assert entry["score"] == match["turns"][-1]["state"]["points"]
    assert entry["teams"]["blue"] == {
        "name": "GLM TensorFold",
        "run": match["teams"]["blue"]["run"],
    }
    assert entry["metrics"] == metrics.compute_metrics(match)
    majors = [{"turn": x["turn"], "headline": x["headline"]} for x in match["slices"] if x["major"]]
    assert entry["moments"] == majors and majors
    both = [_match_file(setup, t) for t in ("hard-a", "thief-a4")]
    assert s["pooled"] == json.loads(json.dumps(metrics.pooled(both)))


def test_index_merges_other_evals(setup):
    setup["out"].mkdir()
    _write(setup["out"] / "index.json", {"evals": [{"id": "older", "title": "Older eval"}]})
    _run(setup)
    _run(setup)
    idx = json.loads((setup["out"] / "index.json").read_text())
    assert idx["evals"] == [
        {"id": "older", "title": "Older eval"},
        {"id": "live-evals-prototype", "title": _config()["title"]},
    ]


# --- 4. failure leaves the previous export intact; no engine imports -----------------------


def _snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_failed_validation_leaves_previous_export(setup):
    _run(setup)
    before = _snapshot(setup["out"])
    cfg = _config()
    cfg["matches"]["thief-a4"]["reason"] = "   "
    _write(setup["config"], cfg)
    with pytest.raises(export.ExportError, match="reason"):
        _run(setup)
    assert _snapshot(setup["out"]) == before
    assert not [p for p in setup["out"].iterdir() if p.name.startswith(".")]


def test_invalid_config_raises(setup):
    cfg = _config()
    del cfg["matches"]["hard-a"]["teams"]["blue"]["seats"]["blue-3"]
    _write(setup["config"], cfg)
    with pytest.raises(export.ExportError, match="blue-3"):
        _run(setup)
    assert not setup["out"].exists()


def test_no_engine_imports():
    tree = ast.parse(Path(export.__file__).read_text())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            mods.add(node.module.split(".")[0])
    assert not mods & {"league", "league_of_agents"}
    assert mods <= {"__future__", "argparse", "dataclasses", "datetime", "hashlib", "json"} | {
        "os",
        "re",
        "shutil",
        "sys",
        "tempfile",
        "pathlib",
        "typing",
        "league_site",
    }


# --- CLI ----------------------------------------------------------------------------------


def _argv(p):
    return [
        "--bundle",
        str(p["bundle"]),
        "--config",
        str(p["config"]),
        "--thinking",
        str(p["thinking"]),
        "--out",
        str(p["out"]),
    ]


def test_cli_exit_1_when_dropped_and_json(setup, capsys):
    assert export.main(_argv(setup) + ["--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["dropped"][0]["file"] == "live-evals-prototype/thinking/hard-a-t2.json"
    assert "live-evals-prototype/manifest.json" in out["written"]


def test_cli_exit_0_when_clean(setup, capsys):
    (setup["thinking"] / "hard-a-t2.json").unlink()
    assert export.main(_argv(setup)) == 0
    assert "written" in capsys.readouterr().out


def test_cli_exit_2_on_invalid_input(setup, capsys):
    setup["config"].write_text("{not json", encoding="utf-8")
    assert export.main(_argv(setup)) == 2
    assert "error" in capsys.readouterr().err


def test_withheld_text_does_not_skew_chat_metrics(setup):
    # Metrics count words from the original turns, so holding text back during a live
    # tools-on match must not turn every message into placeholder words.
    _tools_on(setup, "finished")
    finished = _match_file(setup, "hard-a")["metrics"]
    _tools_on(setup, "live")
    live = _match_file(setup, "hard-a")["metrics"]
    for side in ("blue", "red"):
        assert live[side]["chat_words_per_turn"] == finished[side]["chat_words_per_turn"]
