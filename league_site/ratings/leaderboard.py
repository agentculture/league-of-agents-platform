"""Deterministic leaderboard view over a :class:`RatingLedgerStore`.

``leaderboard()`` is the data provider a page or tool renders;
:func:`leaderboard_markdown` is a small renderer on top of it so the web
registry can serve standings as a markdown table without duplicating the
ordering logic.
"""

from __future__ import annotations

from dataclasses import dataclass

from league_site.ratings.ledger import RatingLedgerStore
from league_site.ratings.system import RatingIdentity


@dataclass(frozen=True)
class LeaderboardRow:
    """One ranked row: an identity's standing plus its 1-based rank."""

    rank: int
    identity: RatingIdentity
    rating: int
    match_count: int


def leaderboard(store: RatingLedgerStore, limit: int | None = None) -> list[LeaderboardRow]:
    """Return standings for every recorded identity in ``store``, ranked.

    Ordering: rating descending; ties broken ascending by
    :meth:`RatingIdentity.sort_key` (i.e. by ``kind``, then
    ``display_name``, then ``model``, then ``provider`` — see that method's
    docstring for why this tie-break is total and needs no further
    fallback). Because the ordering never depends on ``store``'s internal
    iteration order, calling this twice against ledgers built from the same
    match history — in any order the individual matches were recorded in a
    single history — returns identical results.

    Recording a new match through
    :meth:`~league_site.ratings.ledger.RatingLedgerStore.record_match` is
    immediately visible to the very next call here: this function always
    reads ``store`` fresh, there is no separate cache or refresh step.
    """
    standings = [store.get(identity) for identity in store.all_identities()]
    standings.sort(key=lambda standing: (-standing.rating, standing.identity.sort_key()))
    if limit is not None:
        standings = standings[:limit]
    return [
        LeaderboardRow(
            rank=rank,
            identity=standing.identity,
            rating=standing.rating,
            match_count=standing.match_count,
        )
        for rank, standing in enumerate(standings, start=1)
    ]


def leaderboard_markdown(store: RatingLedgerStore, limit: int | None = None) -> str:
    """Render :func:`leaderboard` as a markdown table.

    Includes a ``Model``/``Provider`` column pair so agent standings carry
    their full benchmark identity; humans render ``-`` in both.
    """
    rows = leaderboard(store, limit=limit)
    lines = [
        "| Rank | Identity | Kind | Model | Provider | Rating | Matches |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        identity = row.identity
        lines.append(
            f"| {row.rank} | {identity.display_name} | {identity.kind.value} "
            f"| {identity.model or '-'} | {identity.provider or '-'} "
            f"| {row.rating} | {row.match_count} |"
        )
    return "\n".join(lines) + "\n"
