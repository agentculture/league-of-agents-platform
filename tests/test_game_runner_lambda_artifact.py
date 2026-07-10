"""End-to-end proof that :class:`~league_site.game.runner.LeagueRunner` can
resolve and run the league CLI out of a *simulated* Lambda deployment
artifact — a plain directory laid out the way ``pip install --target
"$(ARTIFACTS_DIR)" league-of-agents`` would populate it (see the repo-root
``Makefile``), minus the real dependency (no network, no real
``league-of-agents`` install: a tiny fake ``league`` package stands in for
it).

Unlike ``tests/test_game_runner.py`` (which stubs ``subprocess.run``
entirely), this test runs a real subprocess — the point is to prove the
``LEAGUE_CLI_MODULE`` resolution mode (``sys.executable -m <module>``)
actually finds and executes a package that only exists on ``PYTHONPATH``,
not on ``PATH`` — exactly the constraint Lambda imposes, since a
``pip install --target`` console-script is not guaranteed to land on
``PATH`` inside the deployment package.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from league_site.game.runner import LEAGUE_CLI_ENV_VAR, LEAGUE_CLI_MODULE_ENV_VAR, LeagueRunner

_FAKE_VERSION_BANNER = "league-of-agents-fake 9.9.9"


def _write_fake_installed_league_package(artifacts_dir: Path) -> None:
    """Populate ``artifacts_dir`` the way ``pip install --target
    artifacts_dir league-of-agents`` would: a top-level ``league/`` package
    directory sitting directly under the artifact root, importable via
    ``-m league`` once that root is on ``sys.path`` — never via a ``bin/``
    console-script (the thing Lambda can't rely on)."""
    package_dir = artifacts_dir / "league"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "__main__.py").write_text(f"print({_FAKE_VERSION_BANNER!r})\n", encoding="utf-8")


def test_runner_resolves_and_runs_the_cli_from_a_simulated_lambda_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifacts_dir = tmp_path / "artifacts"
    _write_fake_installed_league_package(artifacts_dir)

    # The isolated per-match workdir the real adapter would run the CLI
    # from — deliberately NOT the artifacts dir, proving resolution doesn't
    # depend on cwd.
    match_workdir = tmp_path / "workdir"
    match_workdir.mkdir()

    monkeypatch.delenv(LEAGUE_CLI_ENV_VAR, raising=False)
    monkeypatch.setenv(LEAGUE_CLI_MODULE_ENV_VAR, "league")
    # What the Lambda handler bootstrap (a separate task) would set so the
    # installed target dir is importable by a subprocess: PYTHONPATH, not
    # PATH.
    monkeypatch.setenv("PYTHONPATH", str(artifacts_dir))

    runner = LeagueRunner()
    assert runner.command == (sys.executable, "-m", "league")

    text = runner.run_text(["--version"], cwd=match_workdir)

    assert text.strip() == _FAKE_VERSION_BANNER
