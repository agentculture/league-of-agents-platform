"""Unit tests for :mod:`league_site.game.workdir` (hydrate/persist).

The real-CLI round-trip honesty condition (h8 — "hydrate a fresh working
directory from stored state, play a turn via the CLI, persist back,
rehydrate in a SECOND directory, and the folded state matches exactly") is
covered end to end in ``tests/test_game_real_cli.py``, gated on the ``league``
CLI being installed. This module covers the pure filesystem mechanics with
no CLI involved at all, so it always runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from league_site.game.workdir import hydrate, persist


def test_persist_on_a_directory_with_no_league_dir_returns_empty() -> None:
    assert persist(Path("/nonexistent/definitely-not-there")) == {}


def test_hydrate_then_persist_round_trips_exactly(tmp_path: Path) -> None:
    snapshot = {
        "teams/solo.json": '{"id":"solo","name":"solo","agents":[]}\n',
        "matches/m-1/log.jsonl": '{"kind":"a"}\n{"kind":"b"}\n',
        "matches/m-1/pending/solo.json": '{"actions":[]}\n',
    }
    hydrate(tmp_path, snapshot)
    assert persist(tmp_path) == snapshot


def test_hydrate_creates_no_league_dir_for_an_empty_snapshot(tmp_path: Path) -> None:
    hydrate(tmp_path, {})
    assert not (tmp_path / ".league").exists()
    assert persist(tmp_path) == {}


def test_hydrate_into_a_fresh_directory_after_persist_is_byte_identical(tmp_path: Path) -> None:
    """The exact shape the h8 honesty condition needs: persist from one
    directory, hydrate a SECOND fresh directory from that snapshot, and the
    two directories' snapshots compare equal."""
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    snapshot = {
        "teams/blue.json": '{"id":"blue"}\n',
        "teams/red.json": '{"id":"red"}\n',
        "matches/m-2/log.jsonl": '{"kind":"match_created"}\n',
    }
    hydrate(dir_a, snapshot)
    persisted = persist(dir_a)
    hydrate(dir_b, persisted)
    assert persist(dir_b) == snapshot


def test_persist_sorts_keys_deterministically(tmp_path: Path) -> None:
    hydrate(
        tmp_path,
        {
            "z.json": "z",
            "a.json": "a",
            "matches/m/log.jsonl": "log",
        },
    )
    assert list(persist(tmp_path).keys()) == ["a.json", "matches/m/log.jsonl", "z.json"]


def test_hydrate_rejects_a_path_traversal_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="escapes"):
        hydrate(tmp_path, {"../../etc/evil": "nope"})
