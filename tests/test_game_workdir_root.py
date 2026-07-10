"""Unit tests for :func:`league_site.game.workdir.resolve_workdir_root`.

Covers the env-driven base-directory resolution that lets a Lambda handler
bootstrap (a separate task — this module only resolves the value, it never
sets the env var itself) point per-match workdirs at ``/tmp``, the only
writable path in the Lambda execution environment, while every local/dev
invocation keeps today's CWD-relative default unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from league_site.game.workdir import WORKDIR_ROOT_ENV_VAR, resolve_workdir_root


def test_resolve_workdir_root_defaults_to_cwd_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(WORKDIR_ROOT_ENV_VAR, raising=False)
    assert resolve_workdir_root() == Path.cwd()


def test_resolve_workdir_root_uses_the_env_var_override_when_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(WORKDIR_ROOT_ENV_VAR, str(tmp_path))
    assert resolve_workdir_root() == tmp_path


def test_resolve_workdir_root_treats_an_empty_env_var_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(WORKDIR_ROOT_ENV_VAR, "")
    assert resolve_workdir_root() == Path.cwd()


def test_resolve_workdir_root_matches_the_lambda_only_writable_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The value a Lambda handler bootstrap would set — ``/tmp`` is the only
    writable filesystem path inside the Lambda execution environment."""
    monkeypatch.setenv(WORKDIR_ROOT_ENV_VAR, "/tmp")
    assert resolve_workdir_root() == Path("/tmp")
