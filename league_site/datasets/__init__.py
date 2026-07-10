"""Open benchmark dataset export: versioned JSONL, privacy-scrubbed.

Turns completed :class:`~league_site.matches.match.Match` records into a
versioned, allowlist-driven JSONL dataset suitable for mirroring to Hugging
Face Datasets. See:

* :mod:`league_site.datasets.schema` — the versioned field allowlist
  (the scrub mechanism: default-deny, not pattern-scrubbing).
* :mod:`league_site.datasets.export` — :func:`export_matches` and
  :func:`dataset_filename`.
* :mod:`league_site.datasets.scrub` — the automated check that proves no
  denied value made it into a rendered export.
* ``docs/dataset-schema.md`` — the documented, human-readable schema.
"""

from __future__ import annotations

from league_site.datasets.export import (
    DEFAULT_GENERATED_BY,
    UNKNOWN_GAME_VERSION,
    dataset_filename,
    export_matches,
)
from league_site.datasets.schema import (
    ALLOWLIST,
    HEADER_FIELDS,
    MATCH_FIELDS,
    PARTICIPANT_FIELDS,
    RESULT_FIELDS,
    SCHEMA_VERSION,
)
from league_site.datasets.scrub import ScrubViolationError, find_deny_values, scrub_check

__all__ = [
    "ALLOWLIST",
    "DEFAULT_GENERATED_BY",
    "HEADER_FIELDS",
    "MATCH_FIELDS",
    "PARTICIPANT_FIELDS",
    "RESULT_FIELDS",
    "SCHEMA_VERSION",
    "ScrubViolationError",
    "UNKNOWN_GAME_VERSION",
    "dataset_filename",
    "export_matches",
    "find_deny_values",
    "scrub_check",
]
