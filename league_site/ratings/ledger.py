"""Append-only rating ledger: one history per identity.

A :class:`RatingLedgerStore` never mutates a past entry — recording a
match appends exactly one new :class:`RatingEntry` to every identity that
took part, derived from that identity's rating immediately before the
match. Replaying the same sequence of match outcomes through a fresh
store therefore always rebuilds byte-identical ledgers, since each
entry's ``resulting_rating`` is a pure function of the identity's prior
state plus the current match — see ``tests/test_ratings_ledger.py`` for
the replay-determinism property test.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from league_site.ratings.system import INITIAL_RATING, MatchOutcome, RatingIdentity, RatingSystem


@dataclass(frozen=True)
class RatingEntry:
    """One append-only ledger line: the effect of a single match on a rating."""

    match_id: str
    delta: int
    resulting_rating: int


@dataclass(frozen=True)
class IdentityRating:
    """Current standing for one identity: rating, match count, and full history."""

    identity: RatingIdentity
    rating: int
    match_count: int
    history: tuple[RatingEntry, ...] = ()

    @classmethod
    def initial(cls, identity: RatingIdentity) -> IdentityRating:
        """The standing for an identity that has never had a match recorded."""
        return cls(identity=identity, rating=INITIAL_RATING, match_count=0, history=())


class RatingLedgerStore(ABC):
    """Persistence interface for per-identity rating ledgers."""

    @abstractmethod
    def get(self, identity: RatingIdentity) -> IdentityRating:
        """Return ``identity``'s current standing.

        Never raises for an identity with no recorded history — returns
        :meth:`IdentityRating.initial` instead, so callers (including
        :meth:`record_match` itself) don't need a separate existence check.
        """

    @abstractmethod
    def record_match(
        self, outcome: MatchOutcome, rating_system: RatingSystem
    ) -> dict[RatingIdentity, RatingEntry]:
        """Rate ``outcome`` against current standings and append one entry per identity.

        Looks up every identity in ``outcome.entries`` (defaulting absent
        ones to :data:`~league_site.ratings.system.INITIAL_RATING`), asks
        ``rating_system`` for deltas, and appends one new
        :class:`RatingEntry` to each identity's history — this identity's
        rating immediately after the match is now ``get(identity).rating``.
        Returns the entries just appended, keyed by identity.
        """

    @abstractmethod
    def all_identities(self) -> list[RatingIdentity]:
        """Return every identity with at least one recorded match.

        Order is insertion order (first-recorded first) — callers that need
        a specific display order should use
        :func:`league_site.ratings.leaderboard.leaderboard`, not this.
        """


class InMemoryRatingLedgerStore(RatingLedgerStore):
    """Reference ``RatingLedgerStore`` backed by a process-local dict."""

    def __init__(self) -> None:
        self._ledgers: dict[RatingIdentity, IdentityRating] = {}

    def get(self, identity: RatingIdentity) -> IdentityRating:
        return self._ledgers.get(identity, IdentityRating.initial(identity))

    def record_match(
        self, outcome: MatchOutcome, rating_system: RatingSystem
    ) -> dict[RatingIdentity, RatingEntry]:
        # dict.fromkeys dedupes while preserving first-seen order, without
        # routing through a `set` (whose iteration order depends on Python's
        # per-process string hash seed) — current_ratings itself doesn't
        # need to be ordered for correctness (delta computation sorts its
        # own inputs, see IntegerEloRatingSystem), but keeping every
        # intermediate step order-stable-by-construction makes the
        # determinism guarantee easy to audit end to end.
        involved = list(dict.fromkeys(entry.identity for entry in outcome.entries))
        current_ratings = {identity: self.get(identity).rating for identity in involved}
        deltas = rating_system.compute_deltas(current_ratings, outcome)

        applied: dict[RatingIdentity, RatingEntry] = {}
        for identity in involved:
            prior = self.get(identity)
            delta = deltas[identity]
            resulting_rating = prior.rating + delta
            entry = RatingEntry(
                match_id=outcome.match_id, delta=delta, resulting_rating=resulting_rating
            )
            self._ledgers[identity] = IdentityRating(
                identity=identity,
                rating=resulting_rating,
                match_count=prior.match_count + 1,
                history=prior.history + (entry,),
            )
            applied[identity] = entry
        return applied

    def all_identities(self) -> list[RatingIdentity]:
        return list(self._ledgers.keys())
