"""Upload an eval export (and in-progress seat files) to the evals S3 bucket.

Two operations back the ``league-site evals publish`` / ``evals stream`` verbs:

* :func:`publish` uploads one export directory (``export.export_eval`` output) in a fixed
  order — every ``thinking/*`` file, then the match files, then ``summary.json``, then
  ``manifest.json``, then the root ``index.json`` — so a reader between any two PUTs never
  finds a manifest or summary pointing at a key that is not there yet. Before anything is
  uploaded, every match file and the manifest are re-validated with :mod:`schema`, every
  thinking file is re-scanned with :func:`scan.scan_file`, and every file the manifest or
  summary names must exist; any failure raises :class:`PublishError` and nothing is sent.
  Unchanged objects (sha256 in ``x-amz-meta-sha256``, or the single-part ETag) are skipped;
  immutable objects (``thinking/*``, ``<tag>.<sha8>.json``) are never overwritten — they
  are skipped when present and written with ``If-None-Match: *``.
* :func:`stream_pass` uploads changed ``<live-dir>/<agent_id>.json`` seat files to
  ``<eval>/live/<tag>/<agent_id>.json`` and then ``<eval>/live/<tag>/index.json`` (the seats
  currently answering). Text is scanned with :func:`scan.is_clean` first; a seat whose file
  fails is stopped for that turn (never uploaded again until its turn changes) and never
  listed in the index. A ``done`` seat file is uploaded once more, then deleted once the
  eval manifest changes (a publish ran) or 2 minutes pass.

The S3 client is injected (``put_object``/``head_object``/``get_object``/``delete_object``),
so tests use a fake and the CLI builds a boto3 client from the standard credential chain.
Missing keys answer 403 to the match-host identity (no ``s3:ListBucket``) — treated as
absent. Header values mirror ``infra/cloudflare/evals-worker.js``.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from league_site.evals import scan, schema

CONTENT_TYPE = "application/json; charset=utf-8"
IMMUTABLE = "public, max-age=31536000, immutable"
RUNNING = "public, max-age=15, must-revalidate"
SEAT = "public, max-age=5"
DEFAULT_PREFIX = "evals/"
#: Seconds after a seat's final (``done``) upload before its live file is deleted anyway.
DONE_TTL = 120.0
STATE_FILE = ".stream-state.json"

# Upload rank per kind: lower goes first.
_RANK = {
    "thinking": 0,
    "match-final": 1,
    "match-running": 1,
    "summary": 2,
    "manifest": 3,
    "index": 4,
}
_IMMUTABLE_KINDS = ("thinking", "match-final")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_SEAT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_FINAL = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\.([0-9a-f]{8})\.json$")
_RUNNING = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_-]*)\.json$")
_THINKING = re.compile(r"^thinking/([A-Za-z0-9][A-Za-z0-9._-]*)-t(\d+)\.json$")
_TIMESTAMP = re.compile(r"^[0-9TZ:.+ -]{1,40}$")
_ABSENT_CODES = {"403", "404", "NoSuchKey", "NotFound", "AccessDenied", "Forbidden"}


class PublishError(Exception):
    """The export (or an argument) is unfit to publish; nothing was uploaded."""


def _now() -> float:
    """Wall clock (seconds); a test seam."""
    return time.time()


def classify(rel: str) -> str:
    """Kind of an eval-relative path; ``unknown`` for anything outside the layout."""
    if rel == schema.MANIFEST_NAME:
        return "manifest"
    if rel == "summary.json":
        return "summary"
    if _THINKING.match(rel):
        return "thinking"
    if _FINAL.match(rel):
        return "match-final"
    if _RUNNING.match(rel):
        return "match-running"
    return "unknown"


def cache_control(kind: str) -> str:
    return IMMUTABLE if kind in _IMMUTABLE_KINDS else RUNNING


def normalize_prefix(prefix: str) -> str:
    p = (prefix or "").strip("/")
    if not p or any(s in ("", ".", "..") for s in p.split("/")):
        raise PublishError(f"bad --prefix {prefix!r}: expected e.g. evals/")
    return p + "/"


def check_id(value: str, what: str) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise PublishError(f"bad {what} {value!r}: letters, digits, '-' and '_' only")
    return value


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _error_code(exc: Exception) -> tuple[str, int | None]:
    resp = getattr(exc, "response", None)
    if not isinstance(resp, dict):
        return "", None
    code = str((resp.get("Error") or {}).get("Code", ""))
    return code, (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode")


def _is_absent(exc: Exception) -> bool:
    code, status = _error_code(exc)
    return code in _ABSENT_CODES or status in (403, 404)


def _head(client: Any, bucket: str, key: str) -> dict | None:
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:  # noqa: BLE001 - narrowed below
        if _is_absent(exc):
            return None
        raise


def _same(head: dict, data: bytes) -> bool:
    meta = {str(k).lower(): v for k, v in (head.get("Metadata") or {}).items()}
    if "sha256" in meta:
        return meta["sha256"] == _sha(data)
    etag = str(head.get("ETag", "")).strip('"')
    return bool(etag) and "-" not in etag and etag == hashlib.md5(data).hexdigest()  # noqa: S324


# --- publish ---------------------------------------------------------------------------


@dataclass
class Upload:
    key: str
    rel: str
    kind: str
    size: int
    sha256: str
    action: str = "upload"
    reason: str = "new or changed (remote not compared in dry run)"
    body: bytes = field(default=b"", repr=False)

    @property
    def headers(self) -> dict[str, str]:
        return {"Cache-Control": cache_control(self.kind), "Content-Type": CONTENT_TYPE}

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "size": self.size,
            "sha256": self.sha256,
            "immutable": self.kind in _IMMUTABLE_KINDS,
            "headers": self.headers,
            "action": self.action,
            "reason": self.reason,
        }


@dataclass
class PublishReport:
    eval: str
    bucket: str
    prefix: str
    apply: bool
    compared: bool
    plan: list[Upload]

    def to_dict(self) -> dict[str, Any]:
        items = [u.to_dict() for u in self.plan]
        return {
            "eval": self.eval,
            "bucket": self.bucket,
            "prefix": self.prefix,
            "apply": self.apply,
            "compared": self.compared,
            "uploaded": sum(1 for u in self.plan if u.action == "upload"),
            "skipped": sum(1 for u in self.plan if u.action == "skip"),
            "plan": items,
        }


def _load(path: Path, what: str) -> Any:
    try:
        return json.loads(path.read_bytes())
    except Exception as exc:  # noqa: BLE001
        raise PublishError(f"{what}: unreadable or invalid JSON ({type(exc).__name__})") from exc


def build_plan(export_dir: str | Path, eval_id: str, *, prefix: str = DEFAULT_PREFIX) -> list:
    """Validate the export and return its uploads in publish order. Raises PublishError."""
    check_id(eval_id, "eval id")
    prefix = normalize_prefix(prefix)
    root = Path(export_dir)
    eval_dir = root / eval_id
    if not eval_dir.is_dir():
        raise PublishError(f"no export for eval {eval_id!r} under {root}")
    problems: list[str] = []
    uploads: list[Upload] = []
    for path in sorted(p for p in eval_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(eval_dir).as_posix()
        kind = classify(rel)
        if kind == "unknown":
            problems.append(f"{rel}: not part of the published layout")
            continue
        data = path.read_bytes()
        uploads.append(Upload(prefix + eval_id + "/" + rel, rel, kind, len(data), _sha(data)))
        uploads[-1].body = data
    by_rel = {u.rel: u for u in uploads}
    for u in uploads:
        if u.kind == "thinking":
            rep = scan.scan_file(eval_dir / u.rel)
            if not rep.clean:
                pats = {s: sorted({h.pattern for h in hs}) for s, hs in rep.seats.items() if hs}
                problems.append(f"{u.rel}: failed the leak scan ({rep.reason}; {pats})")
        elif u.kind in ("match-final", "match-running"):
            obj = _load(eval_dir / u.rel, u.rel)
            errs = schema.validate_match(obj)
            if errs:
                problems.append(f"{u.rel}: " + "; ".join(errs[:5]))
            elif obj.get("eval") != eval_id:
                problems.append(f"{u.rel}: eval is {obj.get('eval')!r}, not {eval_id!r}")
    for name, validate in (
        (schema.MANIFEST_NAME, schema.validate_manifest),
        ("summary.json", None),
    ):
        if name not in by_rel:
            problems.append(f"{name}: missing")
            continue
        doc = _load(eval_dir / name, name)
        errs = validate(doc) if validate else ([] if isinstance(doc, dict) else ["not an object"])
        if errs:
            problems.append(f"{name}: " + "; ".join(errs[:5]))
            continue
        if doc.get("eval") != eval_id:
            problems.append(f"{name}: eval is {doc.get('eval')!r}, not {eval_id!r}")
        for entry in doc.get("matches") or []:
            ref = entry.get("file") if isinstance(entry, dict) else None
            if ref is None and name == schema.MANIFEST_NAME and isinstance(entry, dict):
                ref = schema.match_file_name(str(entry.get("tag")))
            if not isinstance(ref, str) or by_rel.get(ref) is None:
                problems.append(f"{name}: references {ref!r}, which is not in the export")
    index_path = root / "index.json"
    if index_path.is_file():
        data = index_path.read_bytes()
        idx = _load(index_path, "index.json")
        evals = idx.get("evals") if isinstance(idx, dict) else None
        if not isinstance(evals, list) or not all(
            isinstance(e, dict) and isinstance(e.get("id"), str) for e in evals
        ):
            problems.append("index.json: expected {evals: [{id, title}]}")
        upload = Upload(prefix + "index.json", "index.json", "index", len(data), _sha(data))
        upload.body = data
        uploads.append(upload)
    if problems:
        raise PublishError("refusing to publish: " + " | ".join(problems))
    uploads.sort(key=lambda u: (_RANK[u.kind], u.rel))
    return uploads


def _merge_index(client: Any, bucket: str, upload: Upload) -> None:
    """Keep evals listed remotely but absent locally (the local export may be partial)."""
    try:
        raw = client.get_object(Bucket=bucket, Key=upload.key)["Body"].read()
        remote = json.loads(raw).get("evals", [])
    except Exception as exc:  # noqa: BLE001
        if _is_absent(exc) or isinstance(exc, (ValueError, AttributeError)):
            return
        raise
    local = json.loads(upload.body)["evals"]
    local_by_id = {e["id"]: e for e in local}
    keep = [e for e in remote if isinstance(e, dict) and isinstance(e.get("id"), str)]
    remote_ids = {e["id"] for e in keep}
    out = [local_by_id.get(e["id"], e) for e in keep]
    out += [e for e in local if e["id"] not in remote_ids]
    data = json.dumps({"evals": out}, ensure_ascii=False, indent=2).encode("utf-8")
    upload.body, upload.size, upload.sha256 = data, len(data), _sha(data)


def _decide(client: Any, bucket: str, upload: Upload) -> None:
    head = _head(client, bucket, upload.key)
    if head is None:
        upload.action, upload.reason = "upload", "not in the bucket"
    elif upload.kind in _IMMUTABLE_KINDS:
        upload.action = "skip"
        upload.reason = (
            "immutable, already present (unchanged)"
            if _same(head, upload.body)
            else "immutable and already present with different content: never overwritten"
        )
    elif _same(head, upload.body):
        upload.action, upload.reason = "skip", "unchanged"
    else:
        upload.action, upload.reason = "upload", "changed"


def _put(client: Any, bucket: str, upload: Upload) -> None:
    kwargs: dict[str, Any] = {
        "Bucket": bucket,
        "Key": upload.key,
        "Body": upload.body,
        "ContentType": CONTENT_TYPE,
        "CacheControl": cache_control(upload.kind),
        "Metadata": {"sha256": upload.sha256},
    }
    if upload.kind in _IMMUTABLE_KINDS:
        kwargs["IfNoneMatch"] = "*"
    try:
        client.put_object(**kwargs)
    except Exception as exc:  # noqa: BLE001
        code, status = _error_code(exc)
        if upload.kind in _IMMUTABLE_KINDS and (code == "PreconditionFailed" or status == 412):
            upload.action, upload.reason = "skip", "immutable, appeared before the write"
            return
        raise


def publish(
    export_dir: str | Path,
    eval_id: str,
    bucket: str,
    *,
    prefix: str = DEFAULT_PREFIX,
    client: Any = None,
    apply: bool = False,
    compare: bool = False,
) -> PublishReport:
    """Plan (and with ``apply``, perform) one export's upload. See the module docstring.

    A dry run makes no client calls unless ``compare`` asks for read-only HEAD/GET checks.
    """
    plan = build_plan(export_dir, eval_id, prefix=prefix)
    report = PublishReport(eval_id, bucket, normalize_prefix(prefix), apply, apply or compare, plan)
    if not (apply or compare):
        return report
    for upload in plan:
        if upload.kind == "index":
            _merge_index(client, bucket, upload)
        _decide(client, bucket, upload)
        if apply and upload.action == "upload":
            _put(client, bucket, upload)
    return report


# --- stream ----------------------------------------------------------------------------


@dataclass
class SeatState:
    turn: int
    blocked: bool = False
    sha: str | None = None
    uploaded: bool = False
    done: bool = False
    done_at: float | None = None
    manifest_tag: str | None = None
    deleted: bool = False


@dataclass
class StreamState:
    seats: dict[str, SeatState] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "StreamState":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls({k: SeatState(**v) for k, v in raw.get("seats", {}).items()})
        except Exception:  # noqa: BLE001 - missing or unreadable: start fresh
            return cls()

    def save(self, path: Path) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps({"seats": {k: asdict(v) for k, v in self.seats.items()}}), encoding="utf-8"
        )
        tmp.replace(path)


def _leak_patterns(*texts: Any) -> list[str]:
    names: set[str] = set()
    for text in texts:
        if not isinstance(text, str):
            names.add("not-text")
            continue
        try:
            names.update(h.pattern for h in scan.scan_text(text))
        except Exception:  # noqa: BLE001 - fail closed
            names.add("scan-error")
    return sorted(names)


def _read_seat(path: Path, log: Callable[[str], None]) -> dict | None:
    agent = path.stem
    if not _SEAT_ID.match(agent):
        log(f"stream: skipped {path.name}: not a seat file name")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - likely mid-write; try again next pass
        log(f"stream: seat {agent}: unreadable this pass, skipped")
        return None
    turn = data.get("turn") if isinstance(data, dict) else None
    if not isinstance(turn, int) or isinstance(turn, bool) or data.get("agent_id") != agent:
        log(f"stream: seat {agent}: unexpected shape or agent_id mismatch, skipped")
        return None
    return data


def _manifest_tag(client: Any, bucket: str, key: str) -> str | None:
    head = _head(client, bucket, key)
    if head is None:
        return None
    return str(head.get("ETag") or head.get("LastModified") or "present")


def stream_pass(
    live_dir: str | Path,
    eval_id: str,
    tag: str,
    bucket: str,
    *,
    prefix: str = DEFAULT_PREFIX,
    client: Any = None,
    apply: bool = False,
    state: StreamState | None = None,
    log: Callable[[str], None] = lambda _m: None,
) -> dict[str, Any]:
    """One pass over ``live_dir``; mutates ``state``. A dry run makes no client calls."""
    check_id(eval_id, "eval id")
    check_id(tag, "match tag")
    prefix = normalize_prefix(prefix)
    state = state if state is not None else StreamState()
    base = f"{prefix}{eval_id}/"
    manifest_key = base + schema.MANIFEST_NAME
    now = _now()
    uploads: list[dict[str, Any]] = []
    deletes: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    live = Path(live_dir)
    files = (
        sorted(p for p in live.glob("*.json") if not p.name.startswith("."))
        if live.is_dir()
        else []
    )
    seen_turns: list[int] = []

    def put(key: str, body: bytes) -> None:
        uploads.append(
            {
                "key": key,
                "size": len(body),
                "headers": {"Cache-Control": SEAT, "Content-Type": CONTENT_TYPE},
            }
        )
        if apply:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType=CONTENT_TYPE,
                CacheControl=SEAT,
                Metadata={"sha256": _sha(body)},
            )

    for path in files:
        data = _read_seat(path, log)
        if data is None:
            continue
        agent, turn = path.stem, data["turn"]
        seen_turns.append(turn)
        seat = state.seats.get(agent)
        if seat is None or seat.turn != turn:
            seat = state.seats[agent] = SeatState(turn=turn)
        if seat.blocked:
            if data.get("done") is True and seat.uploaded and not seat.done:
                seat.done, seat.done_at = True, now
                seat.manifest_tag = _manifest_tag(client, bucket, manifest_key) if apply else None
            continue
        pats = _leak_patterns(data.get("thinking_so_far"), data.get("response_so_far"))
        if pats:
            seat.blocked = True
            blocked.append({"seat": agent, "turn": turn, "patterns": pats})
            log(f"stream: seat {agent} turn {turn} stopped by the leak scan: {', '.join(pats)}")
            continue
        updated = data.get("updated")
        body = json.dumps(
            {
                "tag": tag,
                "turn": turn,
                "agent_id": agent,
                "thinking_so_far": data["thinking_so_far"],
                "response_so_far": data["response_so_far"],
                "updated": (
                    updated if isinstance(updated, str) and _TIMESTAMP.match(updated) else None
                ),
                "done": data.get("done") is True,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        sha = _sha(body)
        if sha == seat.sha or seat.deleted:
            continue
        put(base + schema.live_file_name(tag, agent), body)
        seat.sha, seat.uploaded = sha, True
        if data.get("done") is True:
            seat.done, seat.done_at = True, now
            seat.manifest_tag = _manifest_tag(client, bucket, manifest_key) if apply else None

    current = (
        max(seen_turns) if seen_turns else max((s.turn for s in state.seats.values()), default=0)
    )
    answering = sorted(
        a
        for a, s in state.seats.items()
        if s.uploaded and not s.done and not s.blocked and not s.deleted and s.turn == current
    )
    index = {"tag": tag, "turn": current, "updated": _iso(now), "answering": answering}
    put(base + f"live/{tag}/index.json", json.dumps(index).encode("utf-8"))

    pending = [(a, s) for a, s in state.seats.items() if s.uploaded and s.done and not s.deleted]
    if pending:
        mtag = _manifest_tag(client, bucket, manifest_key) if apply else None
        for agent, seat in pending:
            expired = now - (seat.done_at or now) >= DONE_TTL
            published = apply and mtag != seat.manifest_tag
            if not (expired or published):
                continue
            key = base + schema.live_file_name(tag, agent)
            deletes.append({"key": key, "reason": "publish ran" if published else "done 2 min ago"})
            if apply:
                client.delete_object(Bucket=bucket, Key=key)
            seat.deleted = True
    return {
        "eval": eval_id,
        "match": tag,
        "bucket": bucket,
        "apply": apply,
        "uploads": uploads,
        "deletes": deletes,
        "blocked": blocked,
        "index": index,
    }


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
