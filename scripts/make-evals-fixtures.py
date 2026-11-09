#!/usr/bin/env python3
"""Convert the prototype evals export into per-match test fixtures.

Usage: make-evals-fixtures.py [EXPORT_JSON]

Privacy: every message/plan ``text`` becomes "fixture text"; thinking files are
never read. Output is minified JSON in tests/fixtures/evals/.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT_EXPORT = Path(
    "/tmp/claude-1000/-home-arduino-git-league-of-agents-platform/"
    "ac9194e3-ae00-496b-8db3-560071f01b03/scratchpad/artifact-files/"
    "e9c6ffe0-b1bb-4d34-ae24-174240fca801/data.json"
)
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "evals"

OVERRIDES = {
    "hard-a": (
        "finished",
        "Stopped by the operator at turn 32 as a GLM win, 42–22: "
        "the lead was 0.42 of the swing still available.",
    ),
    "thief-a4": (
        "tie",
        "Stopped at turn 24, 29–29, and called a tie: "
        "stale enemy sightings were shown as current.",
    ),
}


def scrub(items: list) -> list:
    return [{**i, "text": "fixture text"} if "text" in i else dict(i) for i in items]


def convert(src: dict, generated: str) -> dict:
    status, reason = OVERRIDES[src["tag"]]
    teams = {}
    for side, team in src["teams"].items():
        agents = sorted(
            {
                t["agent_id"]
                for turn in src["turns"]
                for t in turn["think"]
                if t["agent_id"].startswith(side)
            }
        )
        seats = {a: {"tools": False, "network": False} for a in agents}
        teams[side] = {"name": team["name"], "model": team["model"], "seats": seats}
    turns = []
    for t in src["turns"]:
        t = dict(t)
        t["plans"] = scrub(t["plans"])
        t["messages"] = scrub(t["messages"])
        turns.append(t)
    return {
        "id": src["id"],
        "tag": src["tag"],
        "label": src["label"],
        "eval": "live-evals-prototype",
        "teams": teams,
        "scenario": src["scenario"],
        "seed": src["seed"],
        "fog": src["fog"],
        "status": status,
        "reason": reason,
        "initial": src["initial"],
        "initial_fog": src["initial_fog"],
        "turns": turns,
        "generated": generated,
    }


def main() -> None:
    export = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXPORT
    data = json.loads(export.read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    for src in data["matches"]:
        if src["tag"] not in OVERRIDES:
            continue
        path = OUT / f"{src['tag']}.json"
        path.write_text(json.dumps(convert(src, data["generated"]), separators=(",", ":")))
        print(f"{path} {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
