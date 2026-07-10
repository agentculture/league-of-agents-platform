"""``MatchStore`` interface plus an in-memory reference implementation.

Real backends live in separate adapter modules — see
:mod:`league_site.matches.aws` for the DynamoDB/S3 skeleton — so this module
and the domain model stay pure stdlib.
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod

from league_site.matches.errors import MatchNotFoundError
from league_site.matches.match import Match
from league_site.matches.serialization import from_item, to_item


class MatchStore(ABC):
    """Persistence interface for matches. Implementations own how/where state lives."""

    @abstractmethod
    def save(self, match: Match) -> None:
        """Persist ``match``, overwriting any existing record with the same ``match_id``."""

    @abstractmethod
    def load(self, match_id: str) -> Match:
        """Return the persisted match. Raises :class:`MatchNotFoundError` if absent."""

    @abstractmethod
    def delete(self, match_id: str) -> None:
        """Remove a persisted match. Raises :class:`MatchNotFoundError` if absent."""

    @abstractmethod
    def list_ids(self) -> list[str]:
        """Return all persisted match ids, in unspecified order."""


class InMemoryMatchStore(MatchStore):
    """Reference ``MatchStore`` backed by a process-local dict.

    Round-trips every match through :func:`~league_site.matches.serialization.to_item`
    / :func:`~league_site.matches.serialization.from_item` (deep-copied on both
    save and load) so the persisted record is fully isolated from the
    caller's live ``Match`` object — the same fidelity guarantee a real
    DynamoDB-backed store would have, and the property the "mid-game
    save/load round-trips to identical state" requirement exercises.
    """

    def __init__(self) -> None:
        self._items: dict[str, dict[str, object]] = {}

    def save(self, match: Match) -> None:
        self._items[match.match_id] = copy.deepcopy(to_item(match))

    def load(self, match_id: str) -> Match:
        item = self._items.get(match_id)
        if item is None:
            raise MatchNotFoundError(match_id)
        return from_item(copy.deepcopy(item))

    def delete(self, match_id: str) -> None:
        try:
            del self._items[match_id]
        except KeyError as exc:
            raise MatchNotFoundError(match_id) from exc

    def list_ids(self) -> list[str]:
        return list(self._items.keys())
