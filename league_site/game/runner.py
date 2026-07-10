"""Drive the ``league`` CLI as a subprocess — the platform's only contact
with the League of Agents game.

:class:`LeagueRunner` builds argv from a base command (``["league"]`` by
default), runs it with a caller-supplied ``cwd`` (the per-match isolated
workdir — see :mod:`league_site.game.workdir`), and parses stdout as JSON.
A non-zero exit is translated into :class:`LeagueCliError`, carrying the
game's own structured ``{code, message, remediation}`` shape verbatim
(``league.cli._errors.CliError.to_dict()``) whenever the CLI emitted one; an
exit that produced no parseable structured error (CLI missing, timed out,
crashed before the JSON error path could run) raises
:class:`LeagueRunnerError` instead, so callers can always tell "the game
refused this" apart from "the adapter/environment is broken."

Nothing in this module imports ``league`` — it only ever shells out to the
CLI, matching the platform-wide contract that the league game package is
never imported (see ``tests/test_game_import_boundary.py``).
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess  # nosec B404 - the whole point of this module is to run the league CLI
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

#: Env var overriding the base command used to invoke the league CLI, e.g.
#: ``LEAGUE_CLI="python -m league"`` (shell-quoted, split with ``shlex``).
LEAGUE_CLI_ENV_VAR = "LEAGUE_CLI"

#: Fallback base command when neither a constructor arg nor the env var is set.
DEFAULT_BASE_COMMAND: tuple[str, ...] = ("league",)

#: Default per-invocation timeout, in seconds. Generous: `match new`/`act`
#: are fast, but a cold subprocess start on a loaded CI box should not flake.
DEFAULT_TIMEOUT = 60.0

_MAX_ERROR_TEXT = 2000


@dataclass
class LeagueCliError(Exception):
    """A structured failure reported *by the league CLI itself*.

    Mirrors ``league.cli._errors.CliError`` field for field (``code``,
    ``message``, ``remediation``) so a caller reading this exception sees
    exactly what an agent running the CLI directly would have seen on
    stderr, plus the argv/return code that produced it for debugging.
    """

    code: int
    message: str
    remediation: str = ""
    argv: tuple[str, ...] = field(default_factory=tuple)
    returncode: int = 1

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """The game's own ``CliError`` shape — ``{code, message, remediation}``."""
        return {"code": self.code, "message": self.message, "remediation": self.remediation}

    def __str__(self) -> str:
        return self.message


class LeagueRunnerError(RuntimeError):
    """An adapter/environment failure: the CLI could not be run at all, its
    output could not be parsed, or it failed in a shape that carries no
    structured :class:`LeagueCliError`. Distinct from :class:`LeagueCliError`
    on purpose — this is never the game reporting bad input, it is the
    runtime around it misbehaving."""


@dataclass
class LeagueRunner:
    """Runs the league CLI as a subprocess with a given ``cwd``.

    ``base_command`` overrides both the ``LEAGUE_CLI`` env var and the
    ``["league"]`` default — the list-form escape hatch tests use to point
    at a fake/spy or an alternate interpreter (e.g.
    ``["python", "-m", "league"]``). ``env`` is forwarded to
    :func:`subprocess.run`'s ``env=`` verbatim (``None`` inherits the
    current process environment, the default).
    """

    base_command: Sequence[str] | None = None
    timeout: float = DEFAULT_TIMEOUT
    env: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        self._base: tuple[str, ...] = (
            tuple(self.base_command) if self.base_command else _default_base_command()
        )

    @property
    def command(self) -> tuple[str, ...]:
        """The resolved base command (after env/default fallback) actually run."""
        return self._base

    def run(self, args: Sequence[str], *, cwd: Path | str, timeout: float | None = None) -> Any:
        """Run ``args`` and return the parsed JSON stdout.

        The caller is responsible for including ``--json`` in ``args`` when
        JSON output is wanted (every verb this adapter uses supports it) —
        this method never guesses at argv, it only executes it and parses
        the result, per the module's "no magic" contract.
        """
        completed = self._exec(args, cwd=cwd, timeout=timeout)
        if completed.returncode != 0:
            raise _error_from_failure(self._argv(args), completed, expect_json=True)
        return _parse_json(self._argv(args), completed.stdout)

    def run_text(
        self, args: Sequence[str], *, cwd: Path | str, timeout: float | None = None
    ) -> str:
        """Run ``args`` and return raw stdout text (no JSON parsing).

        Used for the handful of verbs that never speak JSON regardless of
        flags — today, only ``--version`` (an argparse ``action="version"``
        built-in that prints plain text and exits before any subcommand,
        hence before any ``--json`` flag would matter).
        """
        completed = self._exec(args, cwd=cwd, timeout=timeout)
        if completed.returncode != 0:
            raise _error_from_failure(self._argv(args), completed, expect_json=False)
        return completed.stdout

    def _argv(self, args: Sequence[str]) -> tuple[str, ...]:
        return (*self._base, *args)

    def _exec(
        self, args: Sequence[str], *, cwd: Path | str, timeout: float | None
    ) -> subprocess.CompletedProcess[str]:
        argv = self._argv(args)
        try:
            return subprocess.run(  # nosec B603 - argv is built from static verbs + caller data
                list(argv),
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout if timeout is not None else self.timeout,
                env=dict(self.env) if self.env is not None else None,
                check=False,
            )
        except FileNotFoundError as exc:
            raise LeagueRunnerError(
                f"league CLI not found (argv[0]={argv[0]!r}); set {LEAGUE_CLI_ENV_VAR} or "
                "install the league-of-agents package"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise LeagueRunnerError(
                f"league CLI timed out after {exc.timeout}s: {list(argv)}"
            ) from exc


def _default_base_command() -> tuple[str, ...]:
    override = os.environ.get(LEAGUE_CLI_ENV_VAR)
    if override:
        return tuple(shlex.split(override))
    return DEFAULT_BASE_COMMAND


def _parse_json(argv: tuple[str, ...], text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LeagueRunnerError(
            f"league CLI produced non-JSON stdout for {list(argv)}: {text[:_MAX_ERROR_TEXT]!r}"
        ) from exc


def _error_from_failure(
    argv: tuple[str, ...], completed: subprocess.CompletedProcess[str], *, expect_json: bool
) -> Exception:
    stderr = completed.stderr or ""
    if expect_json:
        try:
            payload = json.loads(stderr)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and "code" in payload and "message" in payload:
            return LeagueCliError(
                code=payload["code"],
                message=payload["message"],
                remediation=payload.get("remediation", ""),
                argv=argv,
                returncode=completed.returncode,
            )
    return LeagueRunnerError(
        f"league CLI exited {completed.returncode} for {list(argv)}: {stderr[:_MAX_ERROR_TEXT]!r}"
    )


def game_version(runner: LeagueRunner, *, cwd: Path | str) -> str:
    """Best-effort game CLI version string.

    Prefers ``league --version`` (argparse's own plain-text ``%(prog)s
    VERSION`` output — the game's smallest, fastest surface); falls back to
    ``league whoami --json``'s ``"version"`` field if that fails for any
    reason (a CLI old enough to lack ``--version``, or one whose banner
    format changes). Never raises: an unresolvable version comes back as
    ``"unknown"`` rather than blocking match creation.
    """
    try:
        text = runner.run_text(["--version"], cwd=cwd)
        parsed = _parse_version_banner(text)
        if parsed:
            return parsed
    except (LeagueRunnerError, LeagueCliError):
        pass
    try:
        payload = runner.run(["whoami", "--json"], cwd=cwd)
    except (LeagueRunnerError, LeagueCliError):
        return "unknown"
    if isinstance(payload, dict):
        version = payload.get("version")
        if isinstance(version, str) and version:
            return version
    return "unknown"


def _parse_version_banner(text: str) -> str | None:
    """``"league-of-agents 0.13.1\\n"`` -> ``"0.13.1"``."""
    stripped = text.strip()
    if not stripped:
        return None
    return stripped.rsplit(" ", 1)[-1]
