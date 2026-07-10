"""Unit tests for :mod:`league_site.game.runner` (``LeagueRunner``).

Exercises argv construction, JSON parsing, and error translation entirely
against a stubbed :func:`subprocess.run` — no real ``league`` binary
involved, so these always run (the real-CLI behavior they mimic is
cross-checked in ``tests/test_game_real_cli.py``, gated on the CLI being
installed).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from league_site.game.runner import (
    DEFAULT_BASE_COMMAND,
    LEAGUE_CLI_ENV_VAR,
    LeagueCliError,
    LeagueRunner,
    LeagueRunnerError,
    game_version,
)


def _completed(
    argv: list[str], *, returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)


def test_default_base_command_is_league(tmp_path: Path) -> None:
    runner = LeagueRunner()
    assert runner.command == DEFAULT_BASE_COMMAND == ("league",)


def test_base_command_constructor_override_wins(tmp_path: Path) -> None:
    runner = LeagueRunner(base_command=["python", "-m", "league"])
    assert runner.command == ("python", "-m", "league")


def test_league_cli_env_var_overrides_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LEAGUE_CLI_ENV_VAR, "python3 -m league")
    runner = LeagueRunner()
    assert runner.command == ("python3", "-m", "league")


def test_constructor_override_wins_over_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LEAGUE_CLI_ENV_VAR, "should-not-be-used")
    runner = LeagueRunner(base_command=["league"])
    assert runner.command == ("league",)


def test_run_builds_argv_from_base_command_and_parses_json_stdout(tmp_path: Path) -> None:
    runner = LeagueRunner(base_command=["league"])
    payload = {"match_id": "m-1", "applied": True}
    with mock.patch(
        "subprocess.run", return_value=_completed([], stdout=json.dumps(payload))
    ) as run:
        result = runner.run(["match", "show", "m-1", "--json"], cwd=tmp_path)
    assert result == payload
    (call,) = run.call_args_list
    argv = call.args[0]
    assert argv == ["league", "match", "show", "m-1", "--json"]
    assert call.kwargs["cwd"] == str(tmp_path)
    assert call.kwargs["capture_output"] is True
    assert call.kwargs["text"] is True


def test_run_raises_league_cli_error_on_structured_json_stderr(tmp_path: Path) -> None:
    runner = LeagueRunner()
    error_payload = {"code": 1, "message": "bad scenario", "remediation": "check the id"}
    with mock.patch(
        "subprocess.run",
        return_value=_completed([], returncode=1, stderr=json.dumps(error_payload)),
    ):
        with pytest.raises(LeagueCliError) as excinfo:
            runner.run(["match", "new", "--scenario", "nope", "--json"], cwd=tmp_path)
    err = excinfo.value
    assert err.code == 1
    assert err.message == "bad scenario"
    assert err.remediation == "check the id"
    assert err.to_dict() == error_payload
    assert str(err) == "bad scenario"


def test_run_raises_runner_error_on_non_json_stderr(tmp_path: Path) -> None:
    runner = LeagueRunner()
    with mock.patch(
        "subprocess.run", return_value=_completed([], returncode=2, stderr="segfault or whatever")
    ):
        with pytest.raises(LeagueRunnerError, match="exited 2"):
            runner.run(["match", "show", "m-1", "--json"], cwd=tmp_path)


def test_run_raises_runner_error_on_non_json_stdout(tmp_path: Path) -> None:
    runner = LeagueRunner()
    with mock.patch("subprocess.run", return_value=_completed([], stdout="not json at all")):
        with pytest.raises(LeagueRunnerError, match="non-JSON stdout"):
            runner.run(["match", "show", "m-1", "--json"], cwd=tmp_path)


def test_run_raises_runner_error_when_the_binary_is_missing(tmp_path: Path) -> None:
    runner = LeagueRunner(base_command=["definitely-not-a-real-binary"])
    with mock.patch("subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(LeagueRunnerError, match="not found"):
            runner.run(["whoami", "--json"], cwd=tmp_path)


def test_run_raises_runner_error_on_timeout(tmp_path: Path) -> None:
    runner = LeagueRunner(timeout=5.0)
    with mock.patch(
        "subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["league"], timeout=5.0)
    ):
        with pytest.raises(LeagueRunnerError, match="timed out"):
            runner.run(["whoami", "--json"], cwd=tmp_path)


def test_run_text_returns_raw_stdout_without_json_parsing(tmp_path: Path) -> None:
    runner = LeagueRunner()
    with mock.patch(
        "subprocess.run", return_value=_completed([], stdout="league-of-agents 0.13.1\n")
    ):
        text = runner.run_text(["--version"], cwd=tmp_path)
    assert text == "league-of-agents 0.13.1\n"


def test_run_text_raises_runner_error_on_nonzero_exit(tmp_path: Path) -> None:
    runner = LeagueRunner()
    with mock.patch("subprocess.run", return_value=_completed([], returncode=1, stderr="boom")):
        with pytest.raises(LeagueRunnerError, match="exited 1"):
            runner.run_text(["--version"], cwd=tmp_path)


class _FakeRunner:
    """Minimal stand-in for ``game_version``'s duck-typed runner param."""

    def __init__(
        self,
        *,
        version_text: str | None = None,
        version_error: Exception | None = None,
        whoami_payload: dict[str, Any] | None = None,
        whoami_error: Exception | None = None,
    ) -> None:
        self._version_text = version_text
        self._version_error = version_error
        self._whoami_payload = whoami_payload
        self._whoami_error = whoami_error
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def run_text(self, args: list[str], *, cwd: Any, timeout: float | None = None) -> str:
        self.calls.append(("run_text", tuple(args)))
        if self._version_error is not None:
            raise self._version_error
        assert self._version_text is not None
        return self._version_text

    def run(self, args: list[str], *, cwd: Any, timeout: float | None = None) -> Any:
        self.calls.append(("run", tuple(args)))
        if self._whoami_error is not None:
            raise self._whoami_error
        return self._whoami_payload


def test_game_version_prefers_the_version_banner(tmp_path: Path) -> None:
    fake = _FakeRunner(version_text="league-of-agents 0.13.1\n")
    assert game_version(fake, cwd=tmp_path) == "0.13.1"
    assert fake.calls == [("run_text", ("--version",))]


def test_game_version_falls_back_to_whoami_json_on_runner_error(tmp_path: Path) -> None:
    fake = _FakeRunner(
        version_error=LeagueRunnerError("no --version on this build"),
        whoami_payload={"nick": "x", "version": "0.14.0", "backend": "unknown", "model": "unknown"},
    )
    assert game_version(fake, cwd=tmp_path) == "0.14.0"


def test_game_version_falls_back_to_whoami_json_on_cli_error(tmp_path: Path) -> None:
    fake = _FakeRunner(
        version_error=LeagueCliError(code=1, message="unknown flag"),
        whoami_payload={"version": "0.14.0"},
    )
    assert game_version(fake, cwd=tmp_path) == "0.14.0"


def test_game_version_is_unknown_when_whoami_payload_is_not_a_dict(tmp_path: Path) -> None:
    fake = _FakeRunner(version_error=LeagueRunnerError("nope"))
    fake._whoami_payload = ["not", "a", "dict"]  # type: ignore[assignment]
    assert game_version(fake, cwd=tmp_path) == "unknown"


def test_game_version_is_unknown_when_both_paths_fail(tmp_path: Path) -> None:
    fake = _FakeRunner(
        version_error=LeagueRunnerError("nope"),
        whoami_error=LeagueRunnerError("also nope"),
    )
    assert game_version(fake, cwd=tmp_path) == "unknown"


def test_game_version_handles_empty_version_banner(tmp_path: Path) -> None:
    fake = _FakeRunner(version_text="   \n", whoami_payload={"version": "0.14.0"})
    assert game_version(fake, cwd=tmp_path) == "0.14.0"
