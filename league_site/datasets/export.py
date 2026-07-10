"""Versioned JSONL export of finished matches — the open benchmark dataset.

:func:`export_matches` turns a set of completed
:class:`~league_site.matches.match.Match` objects into a versioned JSONL
file: one JSON object per line, first line a header record. Every record is
built strictly off of the field names in
:data:`league_site.datasets.schema.ALLOWLIST` — see that module's docstring
for why this is the scrub mechanism (default-deny), not a pattern-scrubbing
pass. :mod:`league_site.datasets.scrub` runs automatically before anything
is written, as a safety net that proves no denied value made it into the
rendered output.

Output shape is plain JSONL with no nested blobs, one match per line, keys
sorted for determinism (identical input matches always produce byte-identical
files) — suitable for mirroring straight to Hugging Face Datasets. See
``docs/dataset-schema.md`` for the documented, human-readable schema.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from collections.abc import Iterable, Mapping
from typing import Any, TextIO, Union

from league_site.datasets import schema
from league_site.datasets.scrub import scrub_check
from league_site.matches.match import Match, MatchStatus
from league_site.matches.models import Participant, ParticipantKind

#: Default value for ``export_matches(generated_by=...)``.
DEFAULT_GENERATED_BY = f"league-site-datasets/{schema.SCHEMA_VERSION}"

#: ``game_version`` used when the caller's ``game_versions`` mapping has no
#: entry for a match's ``game_id``. The match domain
#: (:mod:`league_site.matches`) does not itself track a per-game version
#: today, so this is supplied out of band at export time — see
#: ``docs/dataset-schema.md``.
UNKNOWN_GAME_VERSION = "unknown"

#: Per-match graded-quality-axis grades: ``match_id -> participant_id ->
#: {axis_name: numeric_grade}``.
QualityAxes = Mapping[str, Mapping[str, Mapping[str, float]]]

#: ``game_id -> version string``.
GameVersions = Mapping[str, str]

_Destination = Union[TextIO, str, "os.PathLike[str]"]


def dataset_filename(version: str, date: dt.date | dt.datetime | str) -> str:
    """Return the canonical export filename for ``version``/``date``.

    ``date`` may be a ``date``, a ``datetime`` (only the date part is used),
    or an already-formatted ``YYYY-MM-DD`` string. Example::

        >>> dataset_filename("1.0", dt.date(2026, 7, 10))
        'matches-v1.0-2026-07-10.jsonl'
    """
    if isinstance(date, dt.datetime):
        date_str = date.date().isoformat()
    elif isinstance(date, dt.date):
        date_str = date.isoformat()
    else:
        date_str = str(date)
    return f"matches-v{version}-{date_str}.jsonl"


def export_matches(
    matches: Iterable[Match],
    out: _Destination,
    *,
    generated_by: str = DEFAULT_GENERATED_BY,
    game_versions: GameVersions | None = None,
    quality_axes: QualityAxes | None = None,
    extra_deny_values: Iterable[str] = (),
) -> int:
    """Write ``matches`` to ``out`` as a versioned, scrub-checked JSONL dataset.

    ``out`` is either a file path (``str``/``os.PathLike``) or an already
    open text stream (anything with a ``.write(str)`` method, e.g.
    ``io.StringIO`` or an open file handle).

    ``game_versions`` supplies each match's ``game_id -> version`` string
    (missing entries fall back to :data:`UNKNOWN_GAME_VERSION`);
    ``quality_axes`` supplies optional graded-quality-dimension grades per
    match/participant (see :data:`league_site.datasets.schema.PARTICIPANT_FIELDS`).
    Both are supplied out of band because the match domain does not carry
    this data on ``Match`` itself.

    ``extra_deny_values`` is forwarded to
    :func:`league_site.datasets.scrub.scrub_check` — literal strings that
    must not appear anywhere in the output, regardless of which field they
    would have come from.

    Every record is rendered with ``json.dumps(..., sort_keys=True)`` so
    identical ``matches`` (same objects, same order) always produce
    byte-identical output.

    Raises ``ValueError`` if any match is not
    :attr:`~league_site.matches.match.MatchStatus.COMPLETED` — this export
    is finished-match data only. Raises
    :class:`~league_site.datasets.scrub.ScrubViolationError` if the
    automated scrub check finds a denied value in the rendered output. In
    both failure cases nothing is written to ``out``.

    Returns the number of match records written (excluding the header line).
    """
    match_list = list(matches)
    for match in match_list:
        if match.status is not MatchStatus.COMPLETED:
            raise ValueError(
                f"match {match.match_id!r} is not completed (status={match.status.value!r}); "
                "the dataset export only accepts finished matches"
            )

    lines = [_dumps(_build_header_record(generated_by, len(match_list)))]
    for match in match_list:
        record = _build_match_record(match, game_versions=game_versions, quality_axes=quality_axes)
        lines.append(_dumps(record))

    text = "\n".join(lines) + "\n"

    # Safety net: proves the allowlist above did not let a secret through.
    # Runs before anything touches ``out`` — a failed check leaves no file.
    scrub_check(match_list, text, extra_deny_values=extra_deny_values)

    _write(out, text)
    return len(match_list)


def _dumps(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _write(out: _Destination, text: str) -> None:
    if hasattr(out, "write"):
        out.write(text)  # type: ignore[union-attr]
    else:
        with open(out, "w", encoding="utf-8") as handle:  # type: ignore[arg-type]
            handle.write(text)


# --- record builders ---------------------------------------------------------
#
# Every builder below looks up field values by name from
# ``schema.ALLOWLIST``, out of a *superset* of getters that intentionally
# also covers fields never listed in the allowlist (e.g. ``game_state``,
# the raw opaque per-game blob). Under the real, shipped allowlist those
# extra getters are never called — a field name only reaches the output if
# ``schema.ALLOWLIST`` names it. This is what lets
# ``tests/test_datasets_scrub.py`` prove the safety net for real: patching
# ``schema.ALLOWLIST`` to add an unsafe field name exercises this exact code
# path and the scrub check catches the resulting leak.


def _build_header_record(generated_by: str, count: int) -> dict[str, Any]:
    getters = {
        "schema_version": lambda: schema.SCHEMA_VERSION,
        "generated_by": lambda: generated_by,
        "count": lambda: count,
    }
    return {name: getters[name]() for name in schema.ALLOWLIST["header"]}


def _build_match_record(
    match: Match,
    *,
    game_versions: GameVersions | None,
    quality_axes: QualityAxes | None,
) -> dict[str, Any]:
    getters: dict[str, Any] = {
        "match_id": lambda: match.match_id,
        "game_id": lambda: match.game_id,
        "game_version": lambda: (game_versions or {}).get(match.game_id, UNKNOWN_GAME_VERSION),
        "created_at": lambda: match.created_at.isoformat(),
        "updated_at": lambda: match.updated_at.isoformat(),
        "turn_count": lambda: len(match.turns),
        "participants": lambda: [
            _build_participant_record(match, participant, quality_axes)
            for participant in match.participants
        ],
        "result": lambda: _build_result_record(match),
        # Intentionally not in schema.ALLOWLIST["match"] — see module note.
        "game_state": lambda: match.game_state,
    }
    return {name: getters[name]() for name in schema.ALLOWLIST["match"]}


def _build_participant_record(
    match: Match,
    participant: Participant,
    quality_axes: QualityAxes | None,
) -> dict[str, Any]:
    is_agent = participant.kind is ParticipantKind.AGENT
    scores = match.result.scores if match.result is not None else {}
    axes = {}
    if quality_axes is not None:
        axes = dict(quality_axes.get(match.match_id, {}).get(participant.participant_id, {}))

    getters = {
        "participant_id": lambda: participant.participant_id,
        "kind": lambda: participant.kind.value,
        "display_name": lambda: participant.display_name,
        "model": lambda: participant.agent_identity.model if is_agent else None,
        "provider": lambda: participant.agent_identity.provider if is_agent else None,
        "hard_score": lambda: scores.get(participant.participant_id),
        "quality_axes": lambda: axes,
    }
    return {name: getters[name]() for name in schema.ALLOWLIST["participant"]}


def _build_result_record(match: Match) -> dict[str, Any]:
    result = match.result
    getters = {
        "completed": lambda: result.completed if result is not None else False,
        "winner_participant_id": lambda: (
            result.winner_participant_id if result is not None else None
        ),
        "summary": lambda: result.summary if result is not None else "",
    }
    return {name: getters[name]() for name in schema.ALLOWLIST["result"]}
