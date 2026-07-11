"""Turn-event derivation: what actually happened between two board states.

The league CLI's own event log (``resource_gathered``, ``control_point_captured``,
``mission_completed``, …) lives in the match's ``log.jsonl`` and is *not*
exposed by ``league match show --json`` — and the adapter's contract is
CLI-reads only (``docs/game-integration.md``). So the platform derives the
human-facing "what happened last turn" feed itself:
:func:`diff_turn_events` compares the board projections of the state
*before* a turn with the ones *after* it (both already mirrored verbatim
into the match state by
:meth:`league_site.game.adapter.GridLaneEngine._state_from_show`) and emits
a JSON-safe event list. One resolved turn includes every side's effects —
the participant's orders *and* the house bot's — which is exactly the feed
a human needs after the board moved under them.

Every event is a plain dict with a ``kind`` key; unknown kinds must render
harmlessly downstream (forward compatibility if this list grows). Kinds:

* ``post_captured`` — a control point's ``owner`` changed to a team.
* ``post_lost`` — a control point's ``owner`` reverted to none.
* ``gathered`` — a unit's ``carrying`` went up (``amount`` = the delta).
* ``delivered`` — a team's banked ``resources`` went up (``amount``).
* ``node_exhausted`` — a resource node's ``remaining`` hit zero.
* ``unit_fell`` — a unit alive before the turn is dead/gone after it.
* ``mission_completed`` — a mission's ``completed_by`` gained a team.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["diff_turn_events"]


def diff_turn_events(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The events between board-projection states *before* and *after*.

    Both arguments are adapter match states (or any mappings carrying the
    same projections: ``control_points``, ``units``, ``resource_nodes``,
    ``missions``, ``team_resources``). Missing or malformed projections
    yield no events of that kind rather than raising — a state persisted
    before a projection existed simply has no history to tell.
    """
    events: list[dict[str, Any]] = []
    events += _post_events(before, after)
    events += _mission_events(before, after)
    events += _gather_events(before, after)
    events += _deliver_events(before, after)
    events += _node_events(before, after)
    events += _fallen_events(before, after)
    return events


def _entries_by_id(state: Mapping[str, Any], key: str) -> dict[str, Mapping[str, Any]]:
    value = state.get(key)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for entry in value:
        if isinstance(entry, Mapping) and isinstance(entry.get("id"), str):
            result[entry["id"]] = entry
    return result


def _post_events(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    old = _entries_by_id(before, "control_points")
    events: list[dict[str, Any]] = []
    for post_id, post in _entries_by_id(after, "control_points").items():
        if post_id not in old:
            continue
        old_owner, new_owner = old[post_id].get("owner"), post.get("owner")
        if old_owner == new_owner:
            continue
        if isinstance(new_owner, str) and new_owner:
            events.append({"kind": "post_captured", "post_id": post_id, "team": new_owner})
        elif isinstance(old_owner, str) and old_owner:
            events.append({"kind": "post_lost", "post_id": post_id, "team": old_owner})
    return events


def _mission_events(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    old = _entries_by_id(before, "missions")
    events: list[dict[str, Any]] = []
    for mission_id, mission in _entries_by_id(after, "missions").items():
        new_teams = _team_list(mission.get("completed_by"))
        old_teams = _team_list(old.get(mission_id, {}).get("completed_by"))
        for team in new_teams - old_teams:
            event: dict[str, Any] = {
                "kind": "mission_completed",
                "mission_id": mission_id,
                "team": team,
            }
            reward = mission.get("reward")
            if isinstance(reward, int) and not isinstance(reward, bool):
                event["reward"] = reward
            events.append(event)
    return events


def _team_list(value: Any) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return set()
    return {team for team in value if isinstance(team, str) and team}


def _gather_events(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    old = _entries_by_id(before, "units")
    events: list[dict[str, Any]] = []
    for unit_id, unit in _entries_by_id(after, "units").items():
        gained = _carrying(unit) - _carrying(old.get(unit_id, {}))
        if gained > 0:
            events.append(
                {
                    "kind": "gathered",
                    "unit_id": unit_id,
                    "team": str(unit.get("team_id") or ""),
                    "amount": gained,
                }
            )
    return events


def _carrying(unit: Mapping[str, Any]) -> int:
    value = unit.get("carrying")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _deliver_events(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    old, new = before.get("team_resources"), after.get("team_resources")
    if not isinstance(old, Mapping) or not isinstance(new, Mapping):
        return []
    events: list[dict[str, Any]] = []
    for team, banked in new.items():
        if not isinstance(banked, int) or isinstance(banked, bool):
            continue
        previous = old.get(team)
        previous = previous if isinstance(previous, int) and not isinstance(previous, bool) else 0
        if banked > previous:
            events.append({"kind": "delivered", "team": str(team), "amount": banked - previous})
    return events


def _node_events(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    old = _entries_by_id(before, "resource_nodes")
    events: list[dict[str, Any]] = []
    for node_id, node in _entries_by_id(after, "resource_nodes").items():
        remaining = node.get("remaining")
        was = old.get(node_id, {}).get("remaining")
        if (
            isinstance(remaining, int)
            and not isinstance(remaining, bool)
            and remaining <= 0
            and isinstance(was, int)
            and not isinstance(was, bool)
            and was > 0
        ):
            events.append({"kind": "node_exhausted", "node_id": node_id})
    return events


def _fallen_events(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    new = _entries_by_id(after, "units")
    events: list[dict[str, Any]] = []
    for unit_id, unit in _entries_by_id(before, "units").items():
        if not unit.get("alive", True):
            continue
        survivor = new.get(unit_id)
        if survivor is None or not survivor.get("alive", True):
            events.append(
                {
                    "kind": "unit_fell",
                    "unit_id": unit_id,
                    "team": str(unit.get("team_id") or ""),
                }
            )
    return events
