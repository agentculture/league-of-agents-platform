"""Tests for ``league-site match list|show|archive``.

``list``/``show`` are read-only; ``archive`` is the one state-mutating verb
and is dry-run by default (h9). Every dependency is monkeypatched via
:mod:`league_site.cli._commands._stores`'s seams — no real AWS anywhere in
this module.
"""

from __future__ import annotations

import json

import pytest

from league_site.cli import main
from league_site.cli._commands import _stores
from tests._cli_ops_support import FakeArchive, SpyMatchStore, active_match

# --- list --------------------------------------------------------------


def test_match_list_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SpyMatchStore()
    store.save(active_match("m1"))
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (store, False))
    rc = main(["match", "list", "--json"])
    assert rc == 0
    out, err = capsys.readouterr()
    assert err == ""
    payload = json.loads(out)
    assert payload["matches"] == [
        {
            "match_id": "m1",
            "game_id": "counter-demo",
            "status": "active",
            "participant_count": 2,
            "updated_at": payload["matches"][0]["updated_at"],
        }
    ]
    assert "note" not in payload


def test_match_list_ephemeral_note(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (SpyMatchStore(), True))
    rc = main(["match", "list", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["matches"] == []
    assert "MATCHES_TABLE_NAME" in payload["note"]


def test_match_list_text_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SpyMatchStore()
    store.save(active_match("m1"))
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (store, False))
    rc = main(["match", "list"])
    assert rc == 0
    assert "m1" in capsys.readouterr().out


def test_match_list_text_mode_ephemeral_note(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (SpyMatchStore(), True))
    rc = main(["match", "list"])
    assert rc == 0
    assert "MATCHES_TABLE_NAME" in capsys.readouterr().out


# --- show --------------------------------------------------------------


def test_match_show_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SpyMatchStore()
    store.save(active_match("m1"))
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (store, False))
    rc = main(["match", "show", "m1", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["match_id"] == "m1"
    assert payload["status"] == "active"
    assert payload["game_id"] == "counter-demo"


def test_match_show_text_mode_with_ephemeral_note(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SpyMatchStore()
    store.save(active_match("m1"))
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (store, True))
    rc = main(["match", "show", "m1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "match_id: m1" in out
    assert "status: active" in out
    assert "MATCHES_TABLE_NAME" in out


def test_match_show_not_found_errors_on_stderr_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (SpyMatchStore(), False))
    rc = main(["match", "show", "nope", "--json"])
    assert rc == 1
    out, err = capsys.readouterr()
    assert out == ""
    payload = json.loads(err)
    assert payload["code"] == 1
    assert "nope" in payload["message"]


# --- archive -------------------------------------------------------------


def test_match_archive_dry_run_needs_no_aws_and_mutates_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SpyMatchStore()
    store.save(active_match("m1"))
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (store, False))

    def _must_not_be_called() -> str:
        raise AssertionError("resolve_archive_bucket_name must not run on dry-run")

    monkeypatch.setattr(_stores, "resolve_archive_bucket_name", _must_not_be_called)

    rc = main(["match", "archive", "m1", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "dry-run"
    assert payload["apply"] is False
    assert payload["match_id"] == "m1"
    assert store.deleted == []
    assert store.list_ids() == ["m1"]


def test_match_archive_dry_run_text_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SpyMatchStore()
    store.save(active_match("m1"))
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (store, False))
    rc = main(["match", "archive", "m1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "m1" in out
    assert store.deleted == []


def test_match_archive_apply_mutates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SpyMatchStore()
    store.save(active_match("m1"))
    archive = FakeArchive()
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (store, False))
    monkeypatch.setattr(_stores, "resolve_archive_bucket_name", lambda: "league-archive")
    monkeypatch.setattr(_stores, "resolve_archive", lambda bucket_name: archive)

    rc = main(["match", "archive", "m1", "--apply", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "archived"
    assert payload["apply"] is True
    assert store.deleted == ["m1"]
    assert archive.archived == ["m1"]
    assert store.list_ids() == []


def test_match_archive_apply_text_mode_with_ephemeral_note(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SpyMatchStore()
    store.save(active_match("m1"))
    archive = FakeArchive()
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (store, True))
    monkeypatch.setattr(_stores, "resolve_archive_bucket_name", lambda: "league-archive")
    monkeypatch.setattr(_stores, "resolve_archive", lambda bucket_name: archive)

    rc = main(["match", "archive", "m1", "--apply"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "archived m1" in out
    assert store.deleted == ["m1"]


def test_match_archive_apply_without_bucket_env_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SpyMatchStore()
    store.save(active_match("m1"))
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (store, False))
    monkeypatch.delenv("ARCHIVE_BUCKET_NAME", raising=False)

    rc = main(["match", "archive", "m1", "--apply", "--json"])
    assert rc == 2
    out, err = capsys.readouterr()
    assert out == ""
    payload = json.loads(err)
    assert payload["code"] == 2
    assert store.deleted == []


def test_match_archive_not_found(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (SpyMatchStore(), False))
    rc = main(["match", "archive", "nope", "--json"])
    assert rc == 1


def test_match_bare_noun_is_non_empty(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["match"])
    assert rc == 0
    assert capsys.readouterr().out.strip()
