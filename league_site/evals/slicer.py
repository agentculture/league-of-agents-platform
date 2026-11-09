"""Deterministic match slicer: a pure function of a per-match export.

Ported from the approved reference page's JavaScript. Stdlib only; no model
client, no network. Headlines are templates over events, team display names
(the first word of the team name) and numbers; nothing is generated.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

SIDES = ("blue", "red")


def short(name: str) -> str:
    """Display name of a team: the first word of its name."""
    parts = str(name).split()
    return parts[0] if parts else str(name)


def _side(agent_id: str) -> str:
    return str(agent_id).split("-")[0]


def _find(items: list[dict], value: Any) -> dict | None:
    return next((x for x in items if x.get("id") == value), None)


def _reward(state: dict, mission_id: str) -> Any:
    ms = _find(state.get("missions") or [], mission_id)
    if not ms:
        return None
    reward = ms.get("reward")
    return reward if reward is not None else ms.get("amount")


def _entity(pos: Any, team: str, key: str) -> dict:
    return {"key": key, "pos": list(pos) if pos is not None else None, "team": team}


def _turn_events(tr: dict, prev: dict, name) -> tuple[list[dict], list[dict]]:
    s = tr["state"]
    items: list[dict] = []
    ents: list[dict] = []
    deliv = {"blue": 0, "red": 0}
    for e in tr.get("events") or []:
        d = e.get("data") or {}
        kind, tm = e.get("kind"), d.get("team_id")
        if kind == "control_point_captured":
            items.append({"team": tm, "text": f"{name(tm)} takes {d.get('cp_id')}"})
            cp = _find(s.get("control_points") or [], d.get("cp_id"))
            if cp:
                ents.append(_entity(cp.get("pos"), tm, d.get("cp_id")))
        elif kind == "mission_completed":
            r = _reward(s, d.get("mission_id"))
            tail = f" (+{r})" if r else ""
            items.append({"team": tm, "text": f"{name(tm)} completes {d.get('mission_id')}{tail}"})
            ms = _find(s.get("missions") or [], d.get("mission_id"))
            if ms:
                ents.append(_entity(ms.get("pos"), tm, d.get("mission_id")))
        elif kind == "resource_delivered" and tm in deliv:
            deliv[tm] += d.get("amount") or 0
        elif kind == "resource_stolen":
            items.append(
                {"team": tm, "text": f"{name(tm)}'s thief steals from {d.get('victim_id')}"}
            )
            unit = _find(s.get("units") or [], d.get("unit_id"))
            if unit:
                ents.append(_entity(unit.get("pos"), tm, d.get("victim_id")))
        elif kind == "crown_held" and d.get("turns") == 1:
            items.append({"team": tm, "text": f"{name(tm)} steps onto the Crown"})
            if s.get("crown"):
                ents.append(_entity(s["crown"].get("pos"), tm, "crown"))
    for tm in SIDES:
        if deliv[tm]:
            items.append({"team": tm, "text": f"{name(tm)} delivers {deliv[tm]}"})
    crown, pcrown = s.get("crown"), prev.get("crown")
    if crown and crown.get("taken_by") and not (pcrown and pcrown.get("taken_by")):
        tb = crown["taken_by"]
        items.append({"team": tb, "text": f"{name(tb)} takes the Crown (+{crown.get('value')})"})
        ents.append(_entity(crown.get("pos"), tb, "crown"))
    return items, ents


def _headline(sl: dict, teams: dict) -> str:
    if sl["items"]:
        texts = [x["text"] for x in sl["items"]]
        if sl["mirrored"] and len(texts) >= 2:
            return "Both sides mirror each other: " + ", ".join(texts)
        return ". ".join(texts)
    lc = sl["lead_change"]
    if lc:
        return "The scores level" if lc == "tie" else f"{short(teams[lc]['name'])} takes the lead"
    if sl["opening"]:
        return "Opening moves: both teams spread out from their spawn corners"
    return "A quiet stretch"


def slice_match(match: dict) -> list[dict]:
    """Cut a match into slices; a pure function that never mutates ``match``."""
    turns = match.get("turns") or []
    if not turns:
        return []
    teams = match["teams"]

    def name(tm: str) -> str:
        return short(teams[tm]["name"])

    slices: list[dict] = []
    cur: dict | None = None
    lead = "tie"
    lost_seen = {"blue": False, "red": False}
    for i, tr in enumerate(turns):
        s = tr["state"]
        prev = turns[i - 1]["state"] if i else match["initial"]
        p = s.get("points") or {"blue": 0, "red": 0}
        pp = prev.get("points") or {"blue": 0, "red": 0}
        delta = {t: p[t] - pp.get(t, 0) for t in SIDES}
        items, ents = _turn_events(tr, prev, name)
        rej = {t: 0 for t in SIDES}
        lost = {t: 0 for t in SIDES}
        for r in tr.get("rejected") or []:
            if not r.get("passive") and _side(r.get("unit_id", "")) in rej:
                rej[_side(r["unit_id"])] += 1
        for k in tr.get("think") or []:
            if k.get("order_lost") and _side(k.get("agent_id", "")) in lost:
                lost[_side(k["agent_id"])] += 1
        for tm in SIDES:
            if lost[tm] and not lost_seen[tm]:
                lost_seen[tm] = True
                items.append({"team": tm, "text": f"The harness starts losing {name(tm)}'s orders"})
        nl = "blue" if p["blue"] > p["red"] else "red" if p["red"] > p["blue"] else "tie"
        lead_change = nl if i > 0 and nl != lead else None
        lead = nl
        mirrored = delta["blue"] == delta["red"] and delta["blue"] != 0
        if cur is None or items or lead_change:
            cur = {
                "from": tr["turn"],
                "to": tr["turn"],
                "turn": tr["turn"],
                "items": items,
                "entities": ents,
                "delta": delta,
                "score": dict(p),
                "lead_change": lead_change,
                "mirrored": mirrored,
                "rejected": {t: 0 for t in SIDES},
                "lost": {t: 0 for t in SIDES},
                "major": bool(items) or bool(lead_change),
                "opening": not slices,
            }
            slices.append(cur)
        else:
            cur["to"] = tr["turn"]
            cur["score"] = dict(p)
        for t in SIDES:
            cur["rejected"][t] += rej[t]
            cur["lost"][t] += lost[t]
    for sl in slices:
        sl["headline"] = _headline(sl, teams)
    return slices


def quote_keys(slice_: dict) -> list[str]:
    """Strings a page should search chat for: entity keys, then "[x,y]" and "[x, y]"."""
    ents = slice_.get("entities") or []
    keys = [e["key"] for e in ents]
    pos = [e["pos"] for e in ents if e.get("pos")]
    keys += [f"[{p[0]},{p[1]}]" for p in pos]
    keys += [f"[{p[0]}, {p[1]}]" for p in pos]
    return keys


def _median(values: list[int]) -> int | None:
    if not values:
        return None
    return sorted(values)[len(values) // 2]


def _fmt_s(ms: int) -> str:
    if ms >= 60000:
        return f"{ms // 60000}m {round(ms % 60000 / 1000)}s"
    return f"{round(ms / 1000)}s"


def findings(match: dict, slices: list[dict]) -> list[dict]:
    """Findings drawn from the slices; each carries the turn that backs it."""
    turns = match.get("turns") or []
    if not turns:
        return []
    names = {t: short(match["teams"][t]["name"]) for t in SIDES}
    rej = {t: 0 for t in SIDES}
    lost = {t: 0 for t in SIDES}
    first_rej: dict[str, int | None] = {t: None for t in SIDES}
    first_lost: dict[str, int | None] = {t: None for t in SIDES}
    reasons: Counter = Counter()
    lat: dict[str, list[int]] = {t: [] for t in SIDES}
    for tr in turns:
        for r in tr.get("rejected") or []:
            tm = _side(r.get("unit_id", ""))
            if r.get("passive") or tm not in rej:
                continue
            rej[tm] += 1
            reasons[r.get("reason")] += 1
            first_rej[tm] = first_rej[tm] or tr["turn"]
        for k in tr.get("think") or []:
            tm = _side(k.get("agent_id", ""))
            if k.get("order_lost") and tm in lost:
                lost[tm] += 1
                first_lost[tm] = first_lost[tm] or tr["turn"]
        for ln in tr.get("latency") or []:
            if ln.get("team_id") in lat and ln.get("elapsed_ms") is not None:
                lat[ln["team_id"]].append(ln["elapsed_ms"])
    out: list[dict] = []
    swings = [(abs(s["delta"]["blue"] - s["delta"]["red"]), s) for s in slices if s["major"]]
    if swings:
        sw, big = max(swings, key=lambda x: x[0])
        if sw > 0:
            out.append(
                {
                    "value": f"+{sw}",
                    "text": f"Biggest swing, turn {big['turn']}: {big['headline']}",
                    "turn": big["turn"],
                }
            )
    text = f"Orders rejected, {names['blue']} to {names['red']}"
    if reasons:
        text += f". Most common: “{reasons.most_common(1)[0][0]}”"
    backing = first_rej["red"] if rej["red"] >= rej["blue"] else first_rej["blue"]
    out.append({"value": f"{rej['blue']} : {rej['red']}", "text": text, "turn": backing or 1})
    if lost["blue"] + lost["red"]:
        out.append(
            {
                "value": str(lost["blue"] + lost["red"]),
                "text": (
                    f"Orders lost by the harness ({names['blue']} {lost['blue']}, "
                    f"{names['red']} {lost['red']})."
                ),
                "turn": first_lost["blue"] or first_lost["red"],
            }
        )
    mirrored = [s for s in slices if s["mirrored"]]
    if mirrored:
        out.append(
            {
                "value": str(len(mirrored)),
                "text": "Turns where both sides scored the same amount on the mirrored board",
                "turn": mirrored[0]["turn"],
            }
        )
    mb, mr = _median(lat["blue"]), _median(lat["red"])
    if mb and mr:
        out.append(
            {
                "value": f"{_fmt_s(mb)} : {_fmt_s(mr)}",
                "text": f"Median answer time per seat, {names['blue']} to {names['red']}",
                "turn": len(turns),
            }
        )
    return out
