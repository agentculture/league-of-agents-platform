"""Benchmark metrics for live-evals matches, computed from the exported match data only.

Pure functions over a per-match dict (see :mod:`league_site.evals.schema`); stdlib only, no I/O.
Ported from the approved reference page's JavaScript.
"""

from __future__ import annotations

from statistics import median
from typing import Any

SIDES: tuple[str, str] = ("blue", "red")

DEFINITIONS: dict[str, str] = {
    "orders": "How many orders the side's units were given over the match.",
    "rejected": "Orders the engine refused as illegal, not counting passive rejections.",
    "lost": "Seat answers whose order never made it to the engine.",
    "accepted": "The share of orders that were legal: one minus rejected over orders.",
    "points": "Points on the board at the end of the last published turn.",
    "share": "The side's points as a share of all points scored; 50% when nobody scored.",
    "points_per_turn": "Final points divided by the number of turns played.",
    "enemy_half_share": (
        "The share of unit-turns spent closer to the enemy spawn than to the side's own spawn."
    ),
    "contact_share": "The share of unit-turns with an enemy unit within 2 cells (Manhattan).",
    "flips": "Control points the side captured that the enemy owned the turn before.",
    "steals": "Resource thefts the side carried out.",
    "median_answer_ms": "The median time, in milliseconds, a seat took to answer a turn.",
    "median_thinking_tokens": (
        "The median thinking tokens a seat spent on one answer, where the model reports them."
    ),
    "chat_words_per_turn": "Words the side's seats wrote in team chat, divided by turns played.",
}


def _team(item: dict[str, Any], *id_keys: str) -> str:
    tid = item.get("team_id")
    if tid:
        return tid
    for key in id_keys:
        val = item.get(key)
        if val:
            return str(val).split("-", 1)[0]
    return ""


def _md(a: list[int], b: list[int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _med(values: list[float]) -> float | None:
    return median(values) if values else None


def _side_metrics(match: dict[str, Any], tm: str) -> dict[str, Any]:
    en = "red" if tm == "blue" else "blue"
    turns = match["turns"]
    initial = match["initial"]
    spawn: dict[str, list[int]] = {}
    for u in initial["units"]:
        spawn.setdefault(u["team_id"], u["pos"])
    orders = rejected = lost = fwd = contact = unit_turns = steals = flips = words = 0
    mix: dict[str, int] = {}
    lat: list[float] = []
    rtok: list[float] = []
    per_turn: list[dict[str, Any]] = []
    for i, tr in enumerate(turns):
        for a in tr["actions"]:
            if _team(a, "unit_id") == tm:
                orders += 1
                mix[a["action"]] = mix.get(a["action"], 0) + 1
        rejected += sum(
            1 for r in tr["rejected"] if not r.get("passive") and _team(r, "unit_id") == tm
        )
        units = tr["state"]["units"]
        enemies = [u for u in units if u["team_id"] == en]
        for u in (u for u in units if u["team_id"] == tm):
            unit_turns += 1
            if _md(u["pos"], spawn[en]) < _md(u["pos"], spawn[tm]):
                fwd += 1
            if any(_md(e["pos"], u["pos"]) <= 2 for e in enemies):
                contact += 1
        prev = turns[i - 1]["state"] if i else initial
        for e in tr["events"]:
            data = e.get("data", {})
            if data.get("team_id") != tm:
                continue
            if e["kind"] == "resource_stolen":
                steals += 1
            elif e["kind"] == "control_point_captured":
                pc = next((c for c in prev["control_points"] if c["id"] == data.get("cp_id")), None)
                if pc and pc.get("owner") == en:
                    flips += 1
        turn_lat = [x["elapsed_ms"] for x in tr["latency"] if _team(x, "agent_id") == tm]
        lat.extend(turn_lat)
        seats = [k for k in tr.get("think") or [] if _team(k, "agent_id") == tm]
        lost += sum(1 for k in seats if k.get("order_lost"))
        toks = [k["reasoning_tokens"] for k in seats if k.get("reasoning_tokens") is not None]
        rtok.extend(toks)
        per_turn.append(
            {
                "turn": tr["turn"],
                "thinking_tokens": sum(toks) if toks else None,
                "max_seat_thinking_tokens": max(toks) if toks else None,
                "median_answer_ms": _med(turn_lat),
            }
        )
        words += sum(len(m["text"].split()) for m in tr["messages"] if _team(m, "from") == tm)
    pts = turns[-1]["state"]["points"]
    total = (pts.get("blue") or 0) + (pts.get("red") or 0)
    points = pts.get(tm) or 0
    n = len(turns)
    return {
        "orders": orders,
        "mix": mix,
        "rejected": rejected,
        "lost": lost,
        "accepted": 1 - rejected / orders if orders else 1,
        "points": points,
        "share": points / total if total else 0.5,
        "points_per_turn": points / n,
        "enemy_half_share": fwd / unit_turns if unit_turns else 0,
        "contact_share": contact / unit_turns if unit_turns else 0,
        "flips": flips,
        "steals": steals,
        "median_answer_ms": _med(lat),
        "median_thinking_tokens": _med(rtok),
        "chat_words_per_turn": words / n,
        "per_turn": per_turn,
        "_latencies": lat,
        "_thinking": rtok,
        "_words": words,
    }


def compute_metrics(match: dict[str, Any]) -> dict[str, Any]:
    """Per-side metrics for one match; empty when the match has no turns."""
    if not match.get("turns") or not match.get("initial"):
        return {}
    out = {}
    for tm in SIDES:
        side = _side_metrics(match, tm)
        for private in ("_latencies", "_thinking", "_words"):
            side.pop(private)
        out[tm] = side
    return out


def pooled(matches: list[dict[str, Any]]) -> dict[str, Any]:
    """Pool orders, rejections, words, turns, latencies and thinking tokens across matches."""
    out: dict[str, Any] = {}
    for tm in SIDES:
        orders = rejected = words = turns = 0
        lat: list[float] = []
        rtok: list[float] = []
        for m in matches:
            if not m.get("turns") or not m.get("initial"):
                continue
            s = _side_metrics(m, tm)
            orders += s["orders"]
            rejected += s["rejected"]
            words += s["_words"]
            turns += len(m["turns"])
            lat.extend(s["_latencies"])
            rtok.extend(s["_thinking"])
        out[tm] = {
            "orders": orders,
            "rejected": rejected,
            "words": words,
            "turns": turns,
            "chat_words_per_turn": words / max(1, turns),
            "median_answer_ms": _med(lat),
            "median_thinking_tokens": _med(rtok),
            "rejected_share": rejected / orders if orders else 0,
        }
    return out
