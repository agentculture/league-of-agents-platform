"""Tests for ``league-site ops telemetry|capacity|cleanup|deploy``.

Every AWS-shaped dependency (``MatchStore``, the S3 archive, the raw S3
client, ``bash infra/deploy.sh``) is monkeypatched via the individually
patchable seams in :mod:`league_site.cli._commands._stores` and
:data:`league_site.cli._commands.ops._run_subprocess` — no real AWS or
subprocess anywhere in this module.
"""

from __future__ import annotations

import json

import pytest

from league_site.cli import main
from league_site.cli._commands import _stores, ops
from tests._cli_ops_support import (
    FakeArchive,
    FakeS3Client,
    SpyMatchStore,
    active_match,
    hot_stale_completed_match,
)

# --- telemetry ---------------------------------------------------------------


def test_ops_telemetry_json_ephemeral_note(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SpyMatchStore()
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (store, True))
    rc = main(["ops", "telemetry", "--json"])
    assert rc == 0
    out, err = capsys.readouterr()
    assert err == ""
    payload = json.loads(out)
    assert payload["completed_matches"] == 0
    assert any("MATCHES_TABLE_NAME" in note for note in payload["notes"])


def test_ops_telemetry_counts_completed_matches(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SpyMatchStore()
    store.save(hot_stale_completed_match("m1"))
    store.save(active_match("m2"))
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (store, False))
    rc = main(["ops", "telemetry", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["completed_matches"] == 1


def test_ops_telemetry_text_mode(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["ops", "telemetry"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "completed_matches:" in out


# --- capacity ------------------------------------------------------------


def test_ops_capacity_json_shape(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SpyMatchStore()
    store.save(active_match("m1"))
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (store, False))
    rc = main(["ops", "capacity", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["current"] == {"concurrent_matches": 1, "stored_matches": 1}
    assert payload["would_allow_new_match"] is True
    assert payload["refusal_reason"] is None
    assert set(payload["config"]) == {
        "max_concurrent_matches",
        "max_stored_matches",
        "max_match_age_days_hot",
        "max_archive_age_days",
        "ceiling_usd",
    }


def test_ops_capacity_reports_refusal_over_cap(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SpyMatchStore()
    store.save(active_match("m1"))
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (store, False))
    monkeypatch.setenv("LEAGUE_CAPACITY_MAX_CONCURRENT_MATCHES", "1")
    rc = main(["ops", "capacity", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["would_allow_new_match"] is False
    assert payload["refusal_reason"] == "max_concurrent_matches"


def test_ops_capacity_text_mode_with_refusal_and_ephemeral_note(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SpyMatchStore()
    store.save(active_match("m1"))
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (store, True))
    monkeypatch.setenv("LEAGUE_CAPACITY_MAX_CONCURRENT_MATCHES", "1")
    rc = main(["ops", "capacity"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "would_allow_new_match: False" in out
    assert "refusal_reason: max_concurrent_matches" in out
    assert "MATCHES_TABLE_NAME" in out


# --- cleanup ---------------------------------------------------------------


def _patch_cleanup_deps(
    monkeypatch: pytest.MonkeyPatch,
    store: SpyMatchStore,
    archive: FakeArchive,
    s3_client: FakeS3Client,
) -> None:
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (store, False))
    monkeypatch.setattr(_stores, "resolve_archive_bucket_name", lambda: "league-archive")
    monkeypatch.setattr(_stores, "resolve_archive", lambda bucket_name: archive)
    monkeypatch.setattr(_stores, "resolve_s3_client", lambda: s3_client)


def test_ops_cleanup_dry_run_mutates_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SpyMatchStore()
    store.save(hot_stale_completed_match("m1"))
    archive = FakeArchive()
    s3_client = FakeS3Client()
    _patch_cleanup_deps(monkeypatch, store, archive, s3_client)

    rc = main(["ops", "cleanup", "--json"])
    assert rc == 0
    out, err = capsys.readouterr()
    assert err == ""
    payload = json.loads(out)
    assert payload["dry_run"] is True
    assert payload["action_count"] == 1
    assert store.deleted == []
    assert archive.archived == []
    assert s3_client.deleted_keys == []
    assert store.list_ids() == ["m1"]


def test_ops_cleanup_apply_mutates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SpyMatchStore()
    store.save(hot_stale_completed_match("m1"))
    archive = FakeArchive()
    s3_client = FakeS3Client()
    _patch_cleanup_deps(monkeypatch, store, archive, s3_client)

    rc = main(["ops", "cleanup", "--apply", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is False
    assert store.deleted == ["m1"]
    assert archive.archived == ["m1"]
    assert store.list_ids() == []


def test_ops_cleanup_text_mode_with_ephemeral_note(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SpyMatchStore()
    store.save(hot_stale_completed_match("m1"))
    archive = FakeArchive()
    s3_client = FakeS3Client()
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (store, True))
    monkeypatch.setattr(_stores, "resolve_archive_bucket_name", lambda: "league-archive")
    monkeypatch.setattr(_stores, "resolve_archive", lambda bucket_name: archive)
    monkeypatch.setattr(_stores, "resolve_s3_client", lambda: s3_client)

    rc = main(["ops", "cleanup"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "archive_hot_stale" in out
    assert "MATCHES_TABLE_NAME" in out


def test_ops_cleanup_requires_archive_bucket_env(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ARCHIVE_BUCKET_NAME", raising=False)
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (SpyMatchStore(), True))
    rc = main(["ops", "cleanup", "--json"])
    assert rc == 2
    out, err = capsys.readouterr()
    assert out == ""
    payload = json.loads(err)
    assert payload["code"] == 2


# --- deploy --------------------------------------------------------------


class _FakeCompletedProcess:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


def test_ops_deploy_dry_run_does_not_invoke_bash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(ops, "_run_subprocess", lambda *a, **k: calls.append((a, k)))
    rc = main(["ops", "deploy", "--json"])
    assert rc == 0
    assert calls == []
    out, err = capsys.readouterr()
    assert err == ""
    payload = json.loads(out)
    assert payload["apply"] is False
    assert payload["would_run"][0] == "bash"
    assert payload["would_run"][1].endswith("infra/deploy.sh")


def test_ops_deploy_apply_invokes_and_forwards_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeCompletedProcess:
        calls.append((command, kwargs))
        return _FakeCompletedProcess(3)

    monkeypatch.setattr(ops, "_run_subprocess", fake_run)
    rc = main(["ops", "deploy", "--apply", "--json"])
    assert rc == 3
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[0] == "bash"
    assert command[1].endswith("infra/deploy.sh")
    assert kwargs["stdout"] is not None  # redirected to stderr in --json mode
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"apply": True, "command": command, "returncode": 3}


def test_ops_deploy_stage_and_budget_email_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeCompletedProcess:
        calls.append((command, kwargs))
        return _FakeCompletedProcess(0)

    monkeypatch.setattr(ops, "_run_subprocess", fake_run)
    rc = main(["ops", "deploy", "staging", "--budget-alert-email", "ops@example.com", "--apply"])
    assert rc == 0
    command, _kwargs = calls[0]
    assert command[-2:] == ["staging", "ops@example.com"]


def test_ops_deploy_budget_email_without_stage_defaults_stage_to_prod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeCompletedProcess:
        calls.append((command, kwargs))
        return _FakeCompletedProcess(0)

    monkeypatch.setattr(ops, "_run_subprocess", fake_run)
    rc = main(["ops", "deploy", "--budget-alert-email", "ops@example.com", "--apply"])
    assert rc == 0
    command, _kwargs = calls[0]
    assert command[-2:] == ["prod", "ops@example.com"]


def test_ops_deploy_text_mode_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["ops", "deploy"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "infra/deploy.sh" in out


def test_ops_bare_noun_is_non_empty(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["ops"])
    assert rc == 0
    assert capsys.readouterr().out.strip()
