"""Tests for the live-evals data contract and its fixtures."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from league_site.evals import schema

FIXTURES = Path(__file__).parent / "fixtures" / "evals"
TAGS = ["hard-a", "thief-a4"]


def load(tag: str) -> dict:
    return json.loads((FIXTURES / f"{tag}.json").read_text())


@pytest.mark.parametrize("tag", TAGS)
def test_fixture_validates(tag):
    assert schema.validate_match(load(tag)) == []


@pytest.mark.parametrize("tag", TAGS)
def test_fixture_status_and_privacy(tag):
    m = load(tag)
    assert m["status"] in schema.STATUSES and m["reason"]
    for side in m["teams"].values():
        assert side["seats"]
        assert all(s == {"tools": False, "network": False} for s in side["seats"].values())
    for turn in m["turns"]:
        for item in turn["messages"] + turn["plans"]:
            assert item["text"] == "fixture text"


@pytest.mark.parametrize("tag", TAGS)
def test_missing_or_bad_fields_fail(tag):
    base = load(tag)
    for key in ("status", "reason"):
        m = copy.deepcopy(base)
        del m[key]
        assert schema.validate_match(m), key
    m = copy.deepcopy(base)
    m["status"] = "bogus"
    assert any("bogus" in p for p in schema.validate_match(m))


def test_non_object_and_bad_types():
    assert schema.validate_match([])
    m = load("hard-a")
    m["seed"] = "x"
    m["turns"][0]["fog"]["blue"].pop("vis")
    assert len(schema.validate_match(m)) == 2


def test_is_supported():
    major = schema.MAJOR
    assert schema.is_supported(schema.SCHEMA_VERSION)
    assert schema.is_supported(f"{major}.99")
    assert not schema.is_supported(f"{major + 1}.0")
    assert not schema.is_supported("junk")


def test_manifest_validates():
    manifest = {
        "schema_version": schema.SCHEMA_VERSION,
        "eval": "e1",
        "generated": "2026-11-08T00:00:00Z",
        "matches": [
            {"tag": "hard-a", "status": "finished", "turn": 32, "turn_limit": 40, "etag": "abc"}
        ],
    }
    assert schema.validate_manifest(manifest) == []
    manifest["matches"][0]["status"] = "bogus"
    del manifest["eval"]
    assert len(schema.validate_manifest(manifest)) == 2
    assert schema.validate_manifest(None)
