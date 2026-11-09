"""Tests for the free-text leak scanner (fail-closed)."""

import json

import pytest

from league_site.evals import scan
from league_site.evals.scan import Hit, is_clean, scan_file, scan_text

PREFIX = "I think blue-u2 should hold [10,8] this turn. "


def names(text):
    return {h.pattern for h in scan_text(text)}


@pytest.mark.parametrize(
    "planted,pattern",
    [
        ("key sk-ant-api03-abcdefghijklmnop1234", "api-key-sk"),
        ("key sk-abcdefghijklmnopqrstuv", "api-key-sk"),
        ("id AKIAIOSFODNN7EXAMPLE", "aws-key-id"),
        ("tok ghp_abcdefghijklmnopqrstuvwxyz0123", "github-token"),
        ("tok github_pat_abcdefghijklmnopqrstuv", "github-token"),
        ("tok xoxb-1234567890-abcdefgh", "slack-token"),
        ("blob " + "a1b2c3d4" * 4, "long-hex"),
        ("blob QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo0123", "long-base64"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private-key"),
        ("see build.example.com now", "hostname"),
        ("host nas.home.lan up", "hostname"),
        ("host box.tail1234.ts.net up", "hostname"),
        ("ip 192.168.1.20 here", "ipv4"),
        ("ip 8.8.8.8", "ipv4"),
        ("ip 2001:db8::1 here", "ipv6"),
        ("ip fe80::1", "ipv6"),
        ("file /home/arduino/x.py", "absolute-path"),
        ("file /Users/bob/x", "absolute-path"),
        ("file /tmp/foo", "absolute-path"),
        ("file C:\\Users\\bob", "absolute-path"),
        ("mail ori@example.com please", "email"),
    ],
)
def test_planted_caught(planted, pattern):
    text = PREFIX + planted + " and then advance."
    assert pattern in names(text)
    assert not is_clean(text)


def test_excerpt_redacted_and_offset():
    text = PREFIX + "key sk-ant-api03-abcdefghijklmnop1234"
    hit = next(h for h in scan_text(text) if h.pattern == "api-key-sk")
    assert hit.excerpt == "sk-a…"
    assert hit.offset == text.index("sk-ant")
    with pytest.raises(Exception):
        hit.offset = 1  # frozen


@pytest.mark.parametrize(
    "text",
    [
        "move to [10,8] then [10, 8]",
        "blue-u2 and red-6 attack",
        "On T22 and t9 we lost ground",
        "capture cp-3b first, then ms-hold-2a",
        "score is 29-29, ratio 0.42",
        "version 1.5 or 3.2.1 maybe",
        "I should hold; they have 3 units. e.g. a scout, i.e. cheap.",
        "Plan: blue-u2 -> [3,4], red-6 at [10, 8] on T22 (cp-3b), 29-29, 0.42.",
    ],
)
def test_ordinary_game_text_clean(text):
    assert scan_text(text) == []
    assert is_clean(text)


def test_non_str_not_clean():
    for bad in (None, 5, b"abc", ["a"], {"a": 1}):
        assert is_clean(bad) is False


def test_exception_not_clean(monkeypatch):
    class Boom:
        def finditer(self, _):
            raise RuntimeError("boom")

    monkeypatch.setattr(scan, "_PATTERNS", [("x", Boom())])
    assert is_clean("harmless text") is False


def write(tmp_path, data, name="t.json"):
    p = tmp_path / name
    p.write_text(data if isinstance(data, str) else json.dumps(data))
    return p


def test_scan_file_clean(tmp_path):
    p = write(tmp_path, [{"agent_id": "a1", "reasoning": "hold [10,8]"}])
    r = scan_file(p)
    assert r.clean and r.seats == {"a1": []}


def test_scan_file_hits_per_seat(tmp_path):
    p = write(
        tmp_path,
        [
            {"agent_id": "a1", "reasoning": "fine"},
            {"agent_id": "a2", "reasoning": "mail me at x@example.com"},
        ],
    )
    r = scan_file(p)
    assert not r.clean
    assert r.seats["a1"] == []
    assert "email" in {h.pattern for h in r.seats["a2"]}
    assert isinstance(r.seats["a2"][0], Hit)


@pytest.mark.parametrize(
    "data",
    ["{not json", {"a": 1}, [1], [{"agent_id": "a"}], [{"agent_id": 1, "reasoning": "x"}]],
)
def test_scan_file_bad_shape_not_clean(tmp_path, data):
    r = scan_file(write(tmp_path, data))
    assert not r.clean and r.reason


def test_scan_file_missing(tmp_path):
    r = scan_file(tmp_path / "nope.json")
    assert not r.clean and r.reason


def test_cli(tmp_path, capsys):
    write(tmp_path, [{"agent_id": "a1", "reasoning": "ok [1,2]"}], "good.json")
    assert scan.main([str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["clean"] is True
    write(tmp_path, [{"agent_id": "a1", "reasoning": "/home/bob/x"}], "bad.json")
    assert scan.main([str(tmp_path)]) == 1
    out = json.loads(capsys.readouterr().out)
    bad = next(f for f in out["files"] if f["path"].endswith("bad.json"))
    assert bad["seats"]["a1"][0]["pattern"] == "absolute-path"
