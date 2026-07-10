"""Versioned, allowlist-driven schema for the open benchmark dataset export.

Only fields explicitly listed in :data:`ALLOWLIST` ever leave the platform
through :mod:`league_site.datasets.export`. This is a *default-deny* design:
a new attribute added to :class:`league_site.matches.match.Match` (or any
object it references) is invisible to the exporter until someone
deliberately adds its name here — there is no pattern-scrubbing step that
tries to guess what is "safe" after the fact. See
:mod:`league_site.datasets.scrub` for the automated check that proves this
guarantee holds at export time.

Record shape
------------
One exported match record has the fields in :data:`MATCH_FIELDS`. Its
``participants`` value is a list of objects with the fields in
:data:`PARTICIPANT_FIELDS`; its ``result`` value is an object with the
fields in :data:`RESULT_FIELDS`. The first line of every export file is a
header object with the fields in :data:`HEADER_FIELDS` (see
:func:`league_site.datasets.export.export_matches`).

Versioning policy
------------------
:data:`SCHEMA_VERSION` follows ``MAJOR.MINOR``:

* **MINOR** bump — an additive, backward-compatible change: a new optional
  field appended to one of the field tuples below. Existing consumers keep
  working; the field is simply new to them.
* **MAJOR** bump — a breaking change: a field is renamed, removed, or its
  meaning/type changes in a way existing consumers must handle explicitly.

The current version is embedded in every export's header record and in the
dataset filename (see :func:`league_site.datasets.export.dataset_filename`),
so a downstream consumer (e.g. a Hugging Face Datasets loader) can pin to a
specific schema version and detect drift.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Current schema version. Bump per the versioning policy above whenever a
#: field tuple below changes.
SCHEMA_VERSION = "1.0"

#: Top-level fields of one exported match record.
MATCH_FIELDS: tuple[str, ...] = (
    "match_id",
    "game_id",
    "game_version",
    "created_at",
    "updated_at",
    "turn_count",
    "participants",
    "result",
)

#: Fields of each entry in a match record's ``participants`` list.
#:
#: ``model``/``provider`` are ``null`` for human participants. ``hard_score``
#: is the participant's numeric score from the match result (``null`` if the
#: engine did not score this participant). ``quality_axes`` is an optional,
#: forward-compatible map of graded-quality-dimension name to numeric grade
#: (e.g. an LLM-judge rubric); it is ``{}`` until a rating/grading pipeline
#: supplies values via :func:`league_site.datasets.export.export_matches`'s
#: ``quality_axes`` argument — the match domain
#: (:mod:`league_site.matches`) does not itself carry this data today.
PARTICIPANT_FIELDS: tuple[str, ...] = (
    "participant_id",
    "kind",
    "display_name",
    "model",
    "provider",
    "hard_score",
    "quality_axes",
)

#: Fields of a match record's ``result`` object.
RESULT_FIELDS: tuple[str, ...] = (
    "completed",
    "winner_participant_id",
    "summary",
)

#: Fields of the first (header) line of an export file.
HEADER_FIELDS: tuple[str, ...] = (
    "schema_version",
    "generated_by",
    "count",
)

#: The full, explicit allowlist. Export code (:mod:`league_site.datasets.export`)
#: is driven entirely off of these four tuples — nothing else is ever read
#: off a raw :class:`~league_site.matches.match.Match` into the output.
ALLOWLIST: Mapping[str, tuple[str, ...]] = {
    "header": HEADER_FIELDS,
    "match": MATCH_FIELDS,
    "participant": PARTICIPANT_FIELDS,
    "result": RESULT_FIELDS,
}
