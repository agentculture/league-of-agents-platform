"""Versioned contract for the live-evals data files.

Four JSON shapes are described here, all stdlib-only:

* **per-match file** (:data:`MATCH_FIELDS`): one match, with its teams, seats,
  scenario, initial state and ``turns`` (fields in :data:`TURN_FIELDS`). The
  ``slices`` and ``metrics`` blocks are optional and their inner shape is left
  open (dicts/lists); later tasks fill them.
* **per-eval manifest** (:data:`MANIFEST_FIELDS`): one entry per match with
  tag, status, latest turn, turn limit and etag.
* **thinking file**: a list of ``{agent_id, reasoning}`` objects
  (:data:`THINKING_FIELDS`).
* **in-progress seat file** (:data:`SEAT_FILE_FIELDS`): the partial output of
  a seat that is still thinking.

Each side of ``teams`` is ``{name, model, seats}``; ``seats`` maps an agent id
to per-seat settings (:data:`SEAT_SETTINGS_FIELDS`, both booleans).

Versioning policy
-----------------
:data:`SCHEMA_VERSION` follows ``MAJOR.MINOR``:

* **MINOR** bump: an additive, backward-compatible change (a new optional
  field). Consumers of the same major keep working.
* **MAJOR** bump: a breaking change (rename, removal, changed meaning/type).
  See :func:`is_supported`.

``validate_match`` and ``validate_manifest`` return human-readable problems;
an empty list means valid. They check presence and basic types only.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "1.0"
MAJOR = int(SCHEMA_VERSION.split(".")[0])

STATUSES: tuple[str, ...] = ("live", "finished", "tie", "void", "queued", "stopped")

MATCH_FIELDS: tuple[str, ...] = (
    "id",
    "tag",
    "label",
    "eval",
    "teams",
    "scenario",
    "seed",
    "fog",
    "status",
    "reason",
    "initial",
    "initial_fog",
    "turns",
    "generated",
)
MATCH_OPTIONAL_FIELDS: tuple[str, ...] = ("slices", "metrics")

SEAT_SETTINGS_FIELDS: tuple[str, ...] = ("tools", "network")
TEAM_FIELDS: tuple[str, ...] = ("name", "model", "seats")

TURN_FIELDS: tuple[str, ...] = (
    "turn",
    "state",
    "fog",
    "plans",
    "messages",
    "actions",
    "rejected",
    "latency",
    "think",
    "events",
)
FOG_SIDE_FIELDS: tuple[str, ...] = ("vis", "seen", "units", "control_points", "resource_nodes")

MANIFEST_FIELDS: tuple[str, ...] = ("schema_version", "eval", "generated", "matches")
MANIFEST_MATCH_FIELDS: tuple[str, ...] = ("tag", "status", "turn", "turn_limit", "etag")

THINKING_FIELDS: tuple[str, ...] = ("agent_id", "reasoning")
SEAT_FILE_FIELDS: tuple[str, ...] = (
    "tag",
    "turn",
    "agent_id",
    "thinking_so_far",
    "response_so_far",
    "updated",
)


def is_supported(version: str) -> bool:
    """True when ``version`` has the same MAJOR as :data:`SCHEMA_VERSION`."""
    try:
        return int(str(version).split(".")[0]) == MAJOR
    except ValueError:
        return False


_KINDS: dict[str, type] = {"str": str, "dict": dict, "list": list, "bool": bool}


def _check(obj: dict, path: str, spec: dict[str, str], out: list[str]) -> None:
    for name, kind in spec.items():
        if name not in obj:
            out.append(f"{path}{name}: missing")
            continue
        value = obj[name]
        if kind == "int":
            ok = isinstance(value, int) and not isinstance(value, bool)
        else:
            ok = isinstance(value, _KINDS[kind])
        if not ok:
            out.append(f"{path}{name}: expected {kind}")


_MATCH_SPEC = {
    "id": "str",
    "tag": "str",
    "label": "str",
    "eval": "str",
    "teams": "dict",
    "scenario": "dict",
    "seed": "int",
    "fog": "bool",
    "status": "str",
    "reason": "str",
    "initial": "dict",
    "initial_fog": "dict",
    "turns": "list",
    "generated": "str",
}
_TURN_SPEC = {
    "turn": "int",
    "state": "dict",
    "fog": "dict",
    "plans": "list",
    "messages": "list",
    "actions": "list",
    "rejected": "list",
    "latency": "list",
    "think": "list",
    "events": "list",
}


def _check_team(side: str, team: Any, out: list[str]) -> None:
    path = f"teams.{side}."
    if not isinstance(team, dict):
        out.append(f"teams.{side}: expected object")
        return
    _check(team, path, {"name": "str", "model": "str", "seats": "dict"}, out)
    seats = team.get("seats")
    for seat, cfg in (seats.items() if isinstance(seats, dict) else ()):
        if isinstance(cfg, dict):
            _check(cfg, f"{path}seats.{seat}.", {"tools": "bool", "network": "bool"}, out)
        else:
            out.append(f"{path}seats.{seat}: expected object")


def _check_turn(i: int, turn: Any, out: list[str]) -> None:
    if not isinstance(turn, dict):
        out.append(f"turns[{i}]: expected object")
        return
    _check(turn, f"turns[{i}].", _TURN_SPEC, out)
    fogs = turn.get("fog")
    for side, fog in (fogs.items() if isinstance(fogs, dict) else ()):
        if not isinstance(fog, dict):
            out.append(f"turns[{i}].fog.{side}: expected object")
            continue
        out.extend(f"turns[{i}].fog.{side}.{n}: missing" for n in FOG_SIDE_FIELDS if n not in fog)


def validate_match(obj: Any) -> list[str]:
    """Return problems found in a per-match file (empty list = valid)."""
    if not isinstance(obj, dict):
        return ["match: expected object"]
    out: list[str] = []
    _check(obj, "", _MATCH_SPEC, out)
    if isinstance(obj.get("status"), str) and obj["status"] not in STATUSES:
        out.append(f"status: unknown status {obj['status']!r}")
    if isinstance(obj.get("reason"), str) and not obj["reason"].strip():
        out.append("reason: must not be empty")
    for key in MATCH_OPTIONAL_FIELDS:
        if key in obj and not isinstance(obj[key], (dict, list)):
            out.append(f"{key}: expected dict or list")
    teams = obj.get("teams")
    for side, team in (teams.items() if isinstance(teams, dict) else ()):
        _check_team(side, team, out)
    turns = obj.get("turns")
    for i, turn in enumerate(turns if isinstance(turns, list) else ()):
        _check_turn(i, turn, out)
    return out


def validate_manifest(obj: Any) -> list[str]:
    """Return problems found in a per-eval manifest (empty list = valid)."""
    if not isinstance(obj, dict):
        return ["manifest: expected object"]
    out: list[str] = []
    spec = {"schema_version": "str", "eval": "str", "generated": "str", "matches": "list"}
    _check(obj, "", spec, out)
    entry_spec = {"tag": "str", "status": "str", "turn": "int", "turn_limit": "int", "etag": "str"}
    matches = obj.get("matches")
    for i, m in enumerate(matches if isinstance(matches, list) else ()):
        if not isinstance(m, dict):
            out.append(f"matches[{i}]: expected object")
            continue
        _check(m, f"matches[{i}].", entry_spec, out)
        if isinstance(m.get("status"), str) and m["status"] not in STATUSES:
            out.append(f"matches[{i}].status: unknown status {m['status']!r}")
    return out
