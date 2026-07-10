"""Automated privacy/secret scrub check for dataset exports.

The primary scrub mechanism is the allowlist in
:mod:`league_site.datasets.schema`: default-deny, not pattern-scrubbing (see
that module's docstring). This module is the safety net that *proves* the
allowlist did its job. It walks the raw ``Match`` objects an export was
built from, looking for anything that smells like a secret — a field name
containing ``key``, ``token``, ``secret``, ``password``, or ``credential``
(case-insensitive) anywhere in the nested structure (including opaque,
game-defined blobs like ``game_state`` and turn ``action`` payloads) — plus
whatever literal values the caller explicitly seeds. It then asserts none of
those values appear anywhere in the rendered export text. If one does, the
export fails loudly (:class:`ScrubViolationError`) instead of silently
shipping a leak.

This check is deliberately **not** used to redact content from otherwise
allowlisted fields (e.g. a participant's ``display_name``). A value that
merely *contains* the substring ``token`` is not itself proof of a leak —
only a value keyed by a suspicious field **name**, or a value the caller
explicitly seeds as sensitive, is treated as denied. Legitimate exported
content is never rewritten or hidden by this module.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable
from enum import Enum
from typing import Any

#: Field-name substrings (case-insensitive) that mark a value as sensitive.
_DENY_FIELD_PATTERN = re.compile(r"(key|token|secret|password|credential)", re.IGNORECASE)

#: Values shorter than this are ignored during the presence check: short,
#: generic strings ("id", "ok", "1") would false-positive against almost any
#: JSON output and are never meaningful secrets on their own.
_MIN_VALUE_LENGTH = 4


class ScrubViolationError(Exception):
    """Raised when a denied value is found in rendered export output.

    Carries the offending ``field_path`` — a dotted path into the raw match
    structure that produced the value, or ``"<seeded>"`` for a value passed
    via ``extra_deny_values`` — so the failure is actionable.
    """

    def __init__(self, field_path: str, value: Any) -> None:
        self.field_path = field_path
        self.value = value
        super().__init__(
            f"scrub check failed: a value from {field_path!r} appears in the rendered "
            "export output (export aborted; nothing was written)"
        )


def find_deny_values(obj: Any, *, _path: str = "$") -> dict[str, Any]:
    """Recursively collect ``{path: value}`` for keys matching the deny pattern.

    Walks dataclasses (field by field), ``dict`` (key by key), and
    ``list``/``tuple`` (index by index). Anything else — strings, numbers,
    ``None``, enums — is a leaf and is not recursed into further.
    """
    found: dict[str, Any] = {}

    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        for f in dataclasses.fields(obj):
            value = getattr(obj, f.name)
            child_path = f"{_path}.{f.name}"
            if _DENY_FIELD_PATTERN.search(f.name):
                found[child_path] = value
            found.update(find_deny_values(value, _path=child_path))
    elif isinstance(obj, dict):
        for key, value in obj.items():
            child_path = f"{_path}.{key}"
            if isinstance(key, str) and _DENY_FIELD_PATTERN.search(key):
                found[child_path] = value
            found.update(find_deny_values(value, _path=child_path))
    elif isinstance(obj, (list, tuple)):
        for index, value in enumerate(obj):
            found.update(find_deny_values(value, _path=f"{_path}[{index}]"))

    return found


def _stringify(value: Any) -> str | None:
    """Render ``value`` for a substring presence check, or ``None`` to skip it."""
    if value is None or isinstance(value, Enum):
        return None
    text = value if isinstance(value, str) else str(value)
    if len(text) < _MIN_VALUE_LENGTH:
        return None
    return text


def scrub_check(
    raw_items: Iterable[Any],
    output_text: str,
    *,
    extra_deny_values: Iterable[str] = (),
) -> None:
    """Assert no denied value derived from ``raw_items`` appears in ``output_text``.

    ``raw_items`` are the raw (pre-export) objects the export was built
    from — typically the ``Match`` instances passed to
    :func:`league_site.datasets.export.export_matches`. ``extra_deny_values``
    are literal strings the caller wants checked regardless of which field
    they came from (e.g. a seeded test secret whose field name would not
    otherwise match the deny pattern).

    Raises :class:`ScrubViolationError` on the first violation found;
    returns ``None`` if the output is clean.
    """
    for item in raw_items:
        for path, value in find_deny_values(item).items():
            text = _stringify(value)
            if text is not None and text in output_text:
                raise ScrubViolationError(path, value)

    for seeded in extra_deny_values:
        text = _stringify(seeded)
        if text is not None and text in output_text:
            raise ScrubViolationError("<seeded>", seeded)
