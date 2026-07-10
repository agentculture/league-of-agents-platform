"""Deterministic rating engine and leaderboard.

Three layers, each in its own module:

* :mod:`league_site.ratings.system` — the :class:`RatingSystem` interface
  and :class:`IntegerEloRatingSystem`, a configurable-``K`` Elo variant
  computed with integer arithmetic only, so replaying the same match
  history always produces byte-identical ratings. Also
  :func:`outcome_from_match`, the one adapter from
  :mod:`league_site.matches` (``Match``/``MatchResult``) onto this
  package's ``MatchOutcome`` input shape.
* :mod:`league_site.ratings.ledger` — :class:`RatingLedgerStore`, an
  append-only per-identity history of ``(match_id, delta,
  resulting_rating)``, plus :class:`InMemoryRatingLedgerStore`.
* :mod:`league_site.ratings.leaderboard` — :func:`leaderboard`, a
  deterministically ordered standings view over a ``RatingLedgerStore``,
  and :func:`leaderboard_markdown`, a markdown-table renderer over it.
"""

from __future__ import annotations

from league_site.ratings.leaderboard import LeaderboardRow, leaderboard, leaderboard_markdown
from league_site.ratings.ledger import (
    IdentityRating,
    InMemoryRatingLedgerStore,
    RatingEntry,
    RatingLedgerStore,
)
from league_site.ratings.system import (
    INITIAL_RATING,
    IntegerEloRatingSystem,
    MatchOutcome,
    OutcomeEntry,
    RatingIdentity,
    RatingSystem,
    outcome_from_match,
)

__all__ = [
    "INITIAL_RATING",
    "IdentityRating",
    "InMemoryRatingLedgerStore",
    "IntegerEloRatingSystem",
    "LeaderboardRow",
    "MatchOutcome",
    "OutcomeEntry",
    "RatingEntry",
    "RatingIdentity",
    "RatingLedgerStore",
    "RatingSystem",
    "leaderboard",
    "leaderboard_markdown",
    "outcome_from_match",
]
