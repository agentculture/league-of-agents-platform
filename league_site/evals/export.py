"""Live-evals exporter: the match host's bundle + an eval config -> the published 1.1 layout.

Inputs
------
* **bundle** (written by the match host's builder after every turn)::

    {"generated": iso, "matches": [{id, tag, label, fog, teams: {side: {name, model}}, seed,
                                    status, scenario, initial, initial_fog, turns, pending}]}

  The bundle's ``status`` and ``pending`` are ignored: status is editorial and comes from the
  config; ``pending`` is never published.
* **thinking dir**: per-turn reasoning files ``<tag>-t<N>.json``, each a list of
  ``{agent_id, reasoning}``.
* **eval config** (kept by the operator; see ``tests/fixtures/evals/eval-config.json``)::

    {"eval": "<eval id>", "title": "<shown on the index>",
     "matches": {"<tag>": {
         "status": one of schema.STATUSES, "reason": "<why: the fairness log>",
         "board": "<board name>",
         "teams": {"<side>": {
             "run": {model, runtime, thinking, sampling, reasoning},   # schema.TEAM_RUN_FIELDS
             "seats": {"<agent_id>": {"tools": bool, "network": bool}}}}}}}

  Status and reason are never guessed. A bundle match with no config entry is skipped and
  reported. Every seat seen in a match's turns must be listed under its side's ``seats``.

Output (``<out>/<eval>/``, see the published layout in :mod:`league_site.evals.schema`)
--------------------------------------------------------------------------------------
* ``<tag>.json`` while live/queued, else ``<tag>.<sha8>.json`` (immutable; reused byte for
  byte on later runs when only the bundle's ``generated`` changed);
* ``thinking/<tag>-t<N>.json``, copied only when :func:`scan.scan_file` says clean;
* ``manifest.json`` (polled) and ``summary.json`` (read by the index page);
* ``<out>/index.json`` lists every eval (merged, never overwritten).

Privacy rules: chat and plan text that fails :func:`scan.is_clean` is replaced with
:data:`SCAN_WITHHELD`; while a match is live and any seat has tools or network on, every chat
and plan text becomes :data:`TOOLS_WITHHELD` and no thinking files are published for it.
Reports name seats and pattern names only, never the matched text.

Everything is built and validated in memory first; files go to a staging directory that is
swapped into place at the end, so a failed export leaves the previous output intact.

CLI: ``python -m league_site.evals.export --bundle B --config C --thinking T --out O [--json]``
exits 1 when anything was dropped or withheld by the scanner, 2 on invalid input, else 0.
Stdlib only; no engine or harness imports, no network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from league_site.evals import metrics, scan, schema, slicer

SCAN_WITHHELD = "[withheld: failed the leak scan]"
TOOLS_WITHHELD = "[withheld until the match ends: a seat has tools]"
UNHASHED_STATUSES = ("live", "queued")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DUMP = {"ensure_ascii": False, "separators": (",", ":")}


class ExportError(ValueError):
    """Invalid input, or an export that would not validate. Nothing was written."""


@dataclass
class ExportReport:
    eval: str = ""
    written: list[str] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)
    withheld: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    held_until_end: list[str] = field(default_factory=list)

    @property
    def flagged(self) -> bool:
        """True when the scanner dropped a file or withheld a text."""
        return bool(self.dropped or self.withheld)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now(now: Any) -> str:
    if isinstance(now, str):
        return now
    dt = now or datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dumps(obj: Any) -> bytes:
    return json.dumps(obj, **_DUMP).encode("utf-8")


def _load_json(path: str | Path, what: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExportError(f"{what}: cannot read {path}: {type(exc).__name__}") from exc


def _check_config(cfg: Any) -> list[str]:
    if not isinstance(cfg, dict):
        return ["config: expected object"]
    out: list[str] = []
    for key in ("eval", "title"):
        if not isinstance(cfg.get(key), str) or not cfg[key].strip():
            out.append(f"config.{key}: expected non-empty string")
    if isinstance(cfg.get("eval"), str) and cfg["eval"] and not _NAME.match(cfg["eval"]):
        out.append("config.eval: not a safe file name")
    matches = cfg.get("matches")
    if not isinstance(matches, dict):
        return out + ["config.matches: expected object"]
    for tag, m in matches.items():
        path = f"config.matches.{tag}"
        if not _NAME.match(tag):
            out.append(f"{path}: tag is not a safe file name")
        if not isinstance(m, dict):
            out.append(f"{path}: expected object")
            continue
        if m.get("status") not in schema.STATUSES:
            out.append(f"{path}.status: expected one of {', '.join(schema.STATUSES)}")
        for key in ("reason", "board"):
            if not isinstance(m.get(key), str):
                out.append(f"{path}.{key}: expected string")
        teams = m.get("teams")
        if not isinstance(teams, dict):
            out.append(f"{path}.teams: expected object")
            continue
        for side, team in teams.items():
            tp = f"{path}.teams.{side}"
            if not isinstance(team, dict):
                out.append(f"{tp}: expected object")
                continue
            if not isinstance(team.get("run"), dict):
                out.append(f"{tp}.run: expected object")
            seats = team.get("seats")
            if not isinstance(seats, dict):
                out.append(f"{tp}.seats: expected object")
                continue
            for seat, s in seats.items():
                if not isinstance(s, dict) or not all(
                    isinstance(s.get(k), bool) for k in schema.SEAT_SETTINGS_FIELDS
                ):
                    out.append(f"{tp}.seats.{seat}: expected {{tools: bool, network: bool}}")
    return out


def _seen_seats(turns: list[dict]) -> set[str]:
    seen = set()
    for tr in turns:
        for k in tr.get("think") or []:
            if isinstance(k, dict) and k.get("agent_id"):
                seen.add(str(k["agent_id"]))
    return seen


def _tools_on(teams: dict) -> bool:
    return any(
        s.get("tools") or s.get("network")
        for team in teams.values()
        for s in team.get("seats", {}).values()
    )


def _texts(tr: dict):
    for kind, key, who in (("plan", "plans", "team_id"), ("message", "messages", "from")):
        for item in tr.get(key) or []:
            if isinstance(item, dict) and "text" in item:
                yield kind, item, item.get(who)


def _build_match(
    src: dict, entry: dict, eval_id: str, generated: str, report: ExportReport
) -> tuple[dict, bool]:
    """The per-match file (without its name); second value: private content held live."""
    tag = src.get("tag")
    turns = [dict(tr) if isinstance(tr, dict) else tr for tr in src.get("turns") or []]
    teams: dict[str, dict] = {}
    for side, team in (src.get("teams") or {}).items():
        cfg_team = entry["teams"].get(side)
        if cfg_team is None:
            raise ExportError(f"config.matches.{tag}.teams.{side}: missing")
        teams[side] = {
            "name": team.get("name"),
            "model": team.get("model"),
            "seats": {a: dict(s) for a, s in cfg_team["seats"].items()},
            "run": dict(cfg_team["run"]),
        }
    configured = {a for t in teams.values() for a in t["seats"]}
    missing = sorted(_seen_seats(t for t in turns if isinstance(t, dict)) - configured)
    if missing:
        raise ExportError(f"config.matches.{tag}: seats not configured: {', '.join(missing)}")
    held = entry["status"] == "live" and _tools_on(teams)
    for tr in turns:
        if not isinstance(tr, dict):
            continue
        for key in ("plans", "messages"):
            tr[key] = [dict(x) if isinstance(x, dict) else x for x in tr.get(key) or []]
        for kind, item, who in _texts(tr):
            if held:
                item["text"] = TOOLS_WITHHELD
            elif not scan.is_clean(item["text"]):
                text = item["text"]
                try:
                    pats = sorted({h.pattern for h in scan.scan_text(text)})
                except Exception:  # noqa: BLE001 - fail closed
                    pats = []
                item["text"] = SCAN_WITHHELD
                report.withheld.append(
                    {
                        "tag": tag,
                        "turn": tr.get("turn"),
                        "kind": kind,
                        "who": who,
                        "patterns": pats or ["unscannable"],
                    }
                )
    match: dict[str, Any] = {"schema_version": schema.SCHEMA_VERSION}
    for key in schema.MATCH_FIELDS:
        match[key] = src.get(key)
    match.update(
        eval=eval_id,
        teams=teams,
        status=entry["status"],
        reason=entry["reason"],
        turns=turns,
        generated=generated,
    )
    problems = schema.validate_match(match)
    if problems:
        raise ExportError(f"match {tag}: " + "; ".join(problems))
    slices = slicer.slice_match(match)
    match["slices"] = slices
    match["findings"] = slicer.findings(match, slices)
    match["metrics"] = metrics.compute_metrics(match)
    return match, held


def _previous(eval_dir: Path, tag: str) -> tuple[str, bytes, dict] | None:
    """The previously published immutable file for ``tag``, if any."""
    try:
        man = json.loads((eval_dir / schema.MANIFEST_NAME).read_text(encoding="utf-8"))
        entry = next(m for m in man["matches"] if m.get("tag") == tag)
        name = entry["file"]
        if name == schema.match_file_name(tag) or not _NAME.match(name):
            return None
        data = (eval_dir / name).read_bytes()
        return name, data, json.loads(data)
    except Exception:  # noqa: BLE001 - no usable previous file
        return None


def _match_bytes(match: dict, eval_dir: Path) -> tuple[str, bytes]:
    tag = match["tag"]
    data = _dumps(match)
    if match["status"] in UNHASHED_STATUSES:
        return schema.match_file_name(tag), data
    prev = _previous(eval_dir, tag)
    if prev:
        name, old, old_obj = prev
        if isinstance(old_obj, dict) and {**old_obj, "generated": match["generated"]} == match:
            return name, old
    return schema.match_file_name(tag, hashlib.sha256(data).hexdigest()[:8]), data


def _thinking(tag: str, match: dict, thinking_dir: Path | None, report, out_rel: str):
    files: dict[str, bytes] = {}
    if thinking_dir is None or not thinking_dir.is_dir():
        return files
    for tr in match["turns"]:
        rel = schema.thinking_file_name(tag, tr["turn"])
        src = thinking_dir / Path(rel).name
        if not src.is_file():
            continue
        rep = scan.scan_file(src)
        if rep.clean:
            files[rel] = src.read_bytes()
            continue
        seats = {s: sorted({h.pattern for h in hs}) for s, hs in rep.seats.items() if hs}
        report.dropped.append(
            {
                "file": f"{out_rel}/{rel}",
                "tag": tag,
                "turn": tr["turn"],
                "reason": rep.reason,
                "seats": seats,
            }
        )
    return files


def _summary_entry(match: dict, entry: dict, name: str) -> dict:
    turns = match["turns"]
    state = turns[-1]["state"] if turns else match["initial"]
    pts = state.get("points") or {}
    return {
        "tag": match["tag"],
        "label": match["label"],
        "status": match["status"],
        "reason": match["reason"],
        "board": entry["board"],
        "turn": turns[-1]["turn"] if turns else 0,
        "turn_limit": match["scenario"].get("turn_limit", 0),
        "file": name,
        "score": {s: pts.get(s, 0) for s in slicer.SIDES},
        "teams": {s: {"name": t["name"], "run": t["run"]} for s, t in match["teams"].items()},
        "metrics": match["metrics"],
        "moments": [
            {"turn": sl["turn"], "headline": sl["headline"]}
            for sl in match["slices"]
            if sl["major"]
        ],
    }


def _write_tree(root: Path, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def _publish(out_dir: Path, eval_id: str, files: dict[str, bytes], index: bytes) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / eval_id
    staging = Path(tempfile.mkdtemp(prefix=f".{eval_id}.new-", dir=out_dir))
    backup = None
    try:
        os.chmod(staging, 0o755)
        _write_tree(staging, files)
        tmp_index = staging / ".index.json"
        tmp_index.write_bytes(index)
        if target.exists():
            backup = Path(tempfile.mkdtemp(prefix=f".{eval_id}.old-", dir=out_dir))
            os.rename(target, backup / eval_id)
        os.rename(staging, target)
        os.replace(target / ".index.json", out_dir / "index.json")
    except BaseException:
        if backup is not None and not target.exists() and (backup / eval_id).exists():
            os.rename(backup / eval_id, target)
        shutil.rmtree(staging, ignore_errors=True)
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)
        raise
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)


def _index(out_dir: Path, eval_id: str, title: str) -> bytes:
    evals: list[dict] = []
    try:
        old = json.loads((out_dir / "index.json").read_text(encoding="utf-8"))
        evals = [e for e in old.get("evals", []) if isinstance(e, dict) and "id" in e]
    except Exception:  # noqa: BLE001 - missing or unreadable index starts fresh
        evals = []
    for e in evals:
        if e["id"] == eval_id:
            e["title"] = title
            break
    else:
        evals.append({"id": eval_id, "title": title})
    return json.dumps({"evals": evals}, ensure_ascii=False, indent=2).encode("utf-8")


def export_eval(
    bundle_path: str | Path,
    config_path: str | Path,
    thinking_dir: str | Path | None,
    out_dir: str | Path,
    *,
    now: Any = None,
) -> ExportReport:
    """Export one eval; see the module docstring. Raises :class:`ExportError` on bad input."""
    bundle = _load_json(bundle_path, "bundle")
    cfg = _load_json(config_path, "config")
    problems = _check_config(cfg)
    if problems:
        raise ExportError("; ".join(problems))
    if not isinstance(bundle, dict) or not isinstance(bundle.get("matches"), list):
        raise ExportError("bundle: expected {generated, matches: [...]}")
    out_dir = Path(out_dir)
    tdir = Path(thinking_dir) if thinking_dir else None
    eval_id, generated = cfg["eval"], _now(now)
    bundle_generated = str(bundle.get("generated") or generated)
    eval_dir = out_dir / eval_id
    report = ExportReport(eval=eval_id)
    files: dict[str, bytes] = {}
    entries: list[dict] = []
    summaries: list[dict] = []
    exported: list[dict] = []
    seen: set[str] = set()
    for src in bundle["matches"]:
        tag = src.get("tag") if isinstance(src, dict) else None
        if not isinstance(tag, str) or tag not in cfg["matches"]:
            report.skipped.append({"tag": str(tag), "reason": "no entry in the eval config"})
            continue
        if tag in seen:
            raise ExportError(f"bundle: duplicate match tag {tag}")
        seen.add(tag)
        entry = cfg["matches"][tag]
        match, held = _build_match(src, entry, eval_id, bundle_generated, report)
        name, data = _match_bytes(match, eval_dir)
        problems = schema.validate_match(json.loads(data))
        if problems:
            raise ExportError(f"match {tag}: " + "; ".join(problems))
        files[name] = data
        if held:
            report.held_until_end.append(tag)
        else:
            files.update(_thinking(tag, match, tdir, report, eval_id))
        turns = match["turns"]
        entries.append(
            {
                "tag": tag,
                "status": match["status"],
                "turn": turns[-1]["turn"] if turns else 0,
                "turn_limit": match["scenario"].get("turn_limit", 0),
                "etag": hashlib.sha256(data).hexdigest()[:16],
                "file": name,
            }
        )
        summaries.append(_summary_entry(match, entry, name))
        exported.append(match)
    for tag in cfg["matches"]:
        if tag not in seen:
            report.skipped.append(
                {"tag": tag, "reason": "in the eval config but not in the bundle"}
            )
    manifest = {
        "schema_version": schema.SCHEMA_VERSION,
        "eval": eval_id,
        "generated": generated,
        "matches": entries,
    }
    problems = schema.validate_manifest(manifest)
    if problems:
        raise ExportError("manifest: " + "; ".join(problems))
    files[schema.MANIFEST_NAME] = _dumps(manifest)
    summary = {
        "schema_version": schema.SCHEMA_VERSION,
        "eval": eval_id,
        "title": cfg["title"],
        "generated": generated,
        "matches": summaries,
        "pooled": metrics.pooled(exported),
        "definitions": metrics.DEFINITIONS,
    }
    files["summary.json"] = _dumps(summary)
    _publish(out_dir, eval_id, files, _index(out_dir, eval_id, cfg["title"]))
    report.written = [f"{eval_id}/{rel}" for rel in files] + ["index.json"]
    return report


def _print_text(report: ExportReport) -> None:
    print(f"eval {report.eval}: {len(report.written)} files written")
    for rel in report.written:
        print(f"  written  {rel}")
    for d in report.dropped:
        seats = ", ".join(f"{s}: {'/'.join(p)}" for s, p in d["seats"].items()) or d["reason"]
        print(f"  DROPPED  {d['file']} ({seats})")
    for w in report.withheld:
        print(
            f"  WITHHELD {w['tag']} turn {w['turn']} {w['kind']} from {w['who']} "
            f"({'/'.join(w['patterns'])})"
        )
    for tag in report.held_until_end:
        print(f"  held     {tag}: chat, plans and thinking held until the match ends (tools on)")
    for s in report.skipped:
        print(f"  skipped  {s['tag']}: {s['reason']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m league_site.evals.export", description=__doc__)
    ap.add_argument("--bundle", required=True, help="the match host's bundle JSON")
    ap.add_argument("--config", required=True, help="the eval config JSON")
    ap.add_argument("--thinking", default=None, help="directory of <tag>-t<N>.json files")
    ap.add_argument("--out", required=True, help="data base directory (holds <eval>/)")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args(argv)
    try:
        report = export_eval(args.bundle, args.config, args.thinking, args.out)
    except ExportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_text(report)
    return 1 if report.flagged else 0


if __name__ == "__main__":
    sys.exit(main())
