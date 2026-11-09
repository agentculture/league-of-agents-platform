"""Free-text leak scanner for published model reasoning. Fails closed."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_HEX = "0-9a-fA-F"
_TLDS = (
    "com|net|org|io|dev|ai|app|co|edu|gov|info|biz|us|uk|de|fr|eu|cloud|xyz|site|online|tech|"
    "local|internal|lan|corp|ts\\.net"
)

# (name, compiled regex); order is report order.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("api-key-sk", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}")),
    ("aws-key-id", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    (
        "github-token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    ),
    ("slack-token", re.compile(r"\bxox[bp]-[A-Za-z0-9-]{10,}")),
    ("long-hex", re.compile(rf"\b[{_HEX}]{{32,}}\b")),
    ("long-base64", re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{32,}={0,2}")),
    (
        "hostname",
        re.compile(rf"\b(?:[A-Za-z0-9](?:[A-Za-z0-9-]{{0,61}}[A-Za-z0-9])?\.)+(?:{_TLDS})\b"),
    ),
    (
        "ipv4",
        re.compile(
            r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
            r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?![\d]|\.\d)"
        ),
    ),
    (
        "ipv6",
        re.compile(
            rf"(?<![\w:])(?:(?:[{_HEX}]{{1,4}}:){{2,7}}[{_HEX}]{{1,4}}"
            rf"|(?:[{_HEX}]{{1,4}}:){{1,6}}:(?:[{_HEX}]{{1,4}}(?::[{_HEX}]{{1,4}}){{0,5}})?"
            rf"|::(?:[{_HEX}]{{1,4}}:){{0,6}}[{_HEX}]{{1,4}})(?![\w:])"
        ),
    ),
    (
        "absolute-path",
        re.compile(r"(?<![\w.:/-])/(?:home|Users|root|tmp|var|etc)/|(?<![\w])[A-Za-z]:\\"),
    ),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")),
]


@dataclass(frozen=True)
class Hit:
    pattern: str
    excerpt: str  # redacted: at most first 4 chars + ellipsis
    offset: int


@dataclass
class FileReport:
    path: str
    clean: bool
    reason: str = ""
    seats: dict[str, list[Hit]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "clean": self.clean,
            "reason": self.reason,
            "seats": {
                s: [{"pattern": h.pattern, "excerpt": h.excerpt, "offset": h.offset} for h in hs]
                for s, hs in self.seats.items()
            },
        }


def _redact(s: str) -> str:
    return s[:4] + "…"


def scan_text(text: str) -> list[Hit]:
    hits: list[Hit] = []
    for name, rx in _PATTERNS:
        for m in rx.finditer(text):
            hits.append(Hit(name, _redact(m.group(0)), m.start()))
    hits.sort(key=lambda h: (h.offset, h.pattern))
    return hits


def is_clean(text: Any) -> bool:
    if not isinstance(text, str):
        return False
    try:
        return not scan_text(text)
    except Exception:  # noqa: BLE001 - fail closed on anything
        return False


def scan_file(path: str | Path) -> FileReport:
    p = str(path)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return FileReport(p, False, f"unreadable or invalid JSON: {type(exc).__name__}")
    if not isinstance(data, list):
        return FileReport(p, False, "unexpected shape: top level is not a list")
    seats: dict[str, list[Hit]] = {}
    try:
        for i, entry in enumerate(data):
            if not isinstance(entry, dict):
                return FileReport(p, False, f"unexpected shape: entry {i} is not an object")
            agent, reasoning = entry.get("agent_id"), entry.get("reasoning")
            if not isinstance(agent, str) or not isinstance(reasoning, str):
                return FileReport(p, False, f"unexpected shape: entry {i} fields not strings")
            seat = seats.setdefault(agent, [])
            seat.extend(scan_text(reasoning))
            seat.extend(Hit("agent-id-" + h.pattern, h.excerpt, h.offset) for h in scan_text(agent))
    except Exception as exc:  # noqa: BLE001
        return FileReport(p, False, f"scan error: {type(exc).__name__}")
    clean = not any(seats.values())
    return FileReport(p, clean, "" if clean else "hits found", seats)


def _expand(args: list[str]) -> list[str]:
    out: list[str] = []
    for a in args:
        pa = Path(a)
        out.extend(sorted(str(f) for f in pa.glob("*.json")) if pa.is_dir() else [a])
    return out


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    reports = [scan_file(f) for f in _expand(args)]
    ok = bool(reports) and all(r.clean for r in reports)
    print(json.dumps({"clean": ok, "files": [r.to_dict() for r in reports]}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
