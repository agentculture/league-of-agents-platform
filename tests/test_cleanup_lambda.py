"""Tests for :func:`league_site.aws_lambda.cleanup.handler` — the Lambda entrypoint.

Only the plumbing (env vars -> dependencies -> run_cleanup -> dict) is this
module's concern; the sweep logic itself is exercised directly in
``tests/test_cleanup_handler.py``. Every AWS-shaped object here is a fake or
a monkeypatched stand-in — no real ``boto3`` call is ever made.
"""

from __future__ import annotations

import pytest

import league_site.aws_lambda.cleanup as cleanup_module


class _StubStore:
    def __init__(self, table_name: str) -> None:
        self.table_name = table_name


class _StubArchive:
    def __init__(self, bucket_name: str) -> None:
        self.bucket_name = bucket_name


class _StubBoto3:
    def __init__(self, s3_client: object) -> None:
        self._s3_client = s3_client

    def client(self, service_name: str) -> object:
        assert service_name == "s3"
        return self._s3_client


def _patch_dependencies(monkeypatch: pytest.MonkeyPatch, *, run_cleanup=None) -> dict:
    captured: dict = {}

    def fake_run_cleanup(**kwargs):
        captured.update(kwargs)
        if run_cleanup is not None:
            return run_cleanup(**kwargs)
        return cleanup_module.CleanupReport(dry_run=kwargs["dry_run"], actions=())

    monkeypatch.setattr(cleanup_module, "DynamoDBMatchStore", _StubStore)
    monkeypatch.setattr(cleanup_module, "S3MatchArchive", _StubArchive)
    monkeypatch.setattr(cleanup_module, "boto3", _StubBoto3(s3_client=object()))
    monkeypatch.setattr(cleanup_module, "run_cleanup", fake_run_cleanup)
    monkeypatch.setenv("MATCHES_TABLE_NAME", "league-matches")
    monkeypatch.setenv("ARCHIVE_BUCKET_NAME", "league-archive")
    return captured


def test_handler_wires_table_and_bucket_names_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_dependencies(monkeypatch)

    cleanup_module.handler({}, context=None)

    assert captured["match_store"].table_name == "league-matches"
    assert captured["archive"].bucket_name == "league-archive"
    assert captured["bucket_name"] == "league-archive"


def test_handler_defaults_dry_run_to_false_when_event_omits_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_dependencies(monkeypatch)

    cleanup_module.handler({}, context=None)

    assert captured["dry_run"] is False


def test_handler_accepts_a_none_event_and_still_defaults_dry_run_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_dependencies(monkeypatch)

    cleanup_module.handler(None, context=None)

    assert captured["dry_run"] is False


def test_handler_honors_dry_run_true_in_the_event(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_dependencies(monkeypatch)

    result = cleanup_module.handler({"dry_run": True}, context=None)

    assert captured["dry_run"] is True
    assert result["dry_run"] is True


def test_handler_builds_capacity_config_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_dependencies(monkeypatch)
    monkeypatch.setenv("LEAGUE_CAPACITY_MAX_CONCURRENT_MATCHES", "7")

    cleanup_module.handler({}, context=None)

    assert captured["config"].max_concurrent_matches == 7


def test_handler_returns_the_report_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_dependencies(monkeypatch)

    result = cleanup_module.handler({}, context=None)

    assert result == {"dry_run": False, "action_count": 0, "actions": []}


def test_handler_raises_runtime_error_when_boto3_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cleanup_module, "boto3", None)
    monkeypatch.setattr(cleanup_module, "_IMPORT_ERROR", ImportError("no boto3"))

    with pytest.raises(RuntimeError, match="aws"):
        cleanup_module.handler({}, context=None)


def test_module_imports_and_boto3_is_available_via_the_aws_extra() -> None:
    assert cleanup_module.boto3 is not None
