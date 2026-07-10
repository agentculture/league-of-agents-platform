"""Hydrate/persist the league CLI's ``.league/`` state directory.

The league CLI resolves *all* state relative to its current working
directory (``docs/game-integration.md``): teams under ``.league/teams/``, a
match's append-only log under ``.league/matches/<id>/log.jsonl``, and staged
orders under ``.league/matches/<id>/pending/``. Nothing here parses those
files — round-tripping game state through platform storage never needs to
understand the game's own schema, only to copy bytes faithfully. That keeps
this module (and the whole ``league_site.game`` package) free of any
``import league``/``from league`` dependency.

A :data:`Snapshot` is a plain ``dict[str, str]`` mapping a POSIX-style path
*relative to* ``.league/`` (e.g. ``"teams/solo.json"`` or
``"matches/m-1/log.jsonl"``) to that file's exact text content. Being a
plain dict of strings, a snapshot is trivially JSON-safe and archivable
alongside the rest of a match record.

:func:`resolve_workdir_root` resolves the *base* directory per-match
workdirs are created under (the ``root`` passed to :func:`hydrate` /
:func:`persist` above lives one level below that base). Locally this is
just the current working directory, unchanged from before this function
existed; on Lambda, only ``/tmp`` is writable, so the Lambda handler
bootstrap (a separate concern from this module) sets ``LEAGUE_WORKDIR_ROOT``
to point there.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

#: ``relative/posix/path -> file text content``, rooted at ``.league/``.
Snapshot = Mapping[str, str]

_LEAGUE_DIR_NAME = ".league"

#: Env var overriding the base directory :func:`resolve_workdir_root`
#: resolves to. Unset in every local/dev invocation (resolves to the
#: current working directory, unchanged); the Lambda handler bootstrap sets
#: this to ``/tmp`` (or a subdirectory of it) since that's the only
#: writable filesystem path in the Lambda execution environment.
WORKDIR_ROOT_ENV_VAR = "LEAGUE_WORKDIR_ROOT"


def resolve_workdir_root() -> Path:
    """Resolve the base directory per-match workdirs are created under.

    Reads :data:`WORKDIR_ROOT_ENV_VAR`: when set to a non-empty value, that
    path is used verbatim. When unset (or empty — treated the same as
    unset), resolves to the current working directory, matching the
    behavior every caller already got before this function existed.
    """
    override = os.environ.get(WORKDIR_ROOT_ENV_VAR)
    if override:
        return Path(override)
    return Path.cwd()


def hydrate(root: Path | str, snapshot: Snapshot) -> Path:
    """Write ``snapshot`` into a fresh ``<root>/.league/`` tree.

    ``root`` is the match workdir the league CLI will be run with as its
    ``cwd`` — *not* ``.league`` itself. Every path in ``snapshot`` is
    created (parents included); an empty ``snapshot`` leaves no
    ``.league/`` directory at all (a legal starting point for the very
    first ``league team register`` / ``league match new`` call). Returns
    the resolved ``.league`` directory path.
    """
    league_dir = Path(root) / _LEAGUE_DIR_NAME
    for rel_path, content in snapshot.items():
        target = _safe_join(league_dir, rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return league_dir


def persist(root: Path | str) -> dict[str, str]:
    """Read every file under ``<root>/.league/`` back into a :data:`Snapshot`.

    Returns an empty dict if no ``.league/`` directory exists yet. Keys are
    sorted for determinism (two identical trees always persist to an
    identical dict, load-bearing for the round-trip and byte-identical
    replay honesty conditions).
    """
    league_dir = Path(root) / _LEAGUE_DIR_NAME
    if not league_dir.is_dir():
        return {}
    snapshot: dict[str, str] = {}
    for path in sorted(league_dir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(league_dir).as_posix()
            snapshot[rel] = path.read_text(encoding="utf-8")
    return snapshot


def _safe_join(league_dir: Path, rel_path: str) -> Path:
    """Resolve ``rel_path`` under ``league_dir``, rejecting any escape.

    Snapshots only ever originate from :func:`persist` (itself only ever
    walking inside ``.league/``) or from tests, but hydrating is the one
    place untrusted-looking path strings become filesystem writes — worth
    a cheap traversal guard even though every caller today is trusted.
    """
    candidate = (league_dir / rel_path).resolve()
    league_root = league_dir.resolve()
    if candidate != league_root and league_root not in candidate.parents:
        raise ValueError(f"snapshot path {rel_path!r} escapes the .league/ directory")
    return candidate
