"""Domain errors for :mod:`league_site.matches`.

Kept dependency-free (no imports from sibling modules) so every module in
this package can raise them without risking an import cycle.
"""

from __future__ import annotations


class MatchError(Exception):
    """Base class for all match-domain errors."""


class InvalidTransitionError(MatchError):
    """Raised when a :class:`~league_site.matches.match.Match` transition is
    attempted from a status that does not allow it.

    ``current_status`` is the ``.value`` of the offending
    :class:`~league_site.matches.match.MatchStatus`, passed as a plain string
    to avoid this module importing ``match.py`` (which imports this module).
    """

    def __init__(self, action: str, current_status: str) -> None:
        self.action = action
        self.current_status = current_status
        super().__init__(f"cannot {action!r} while match status is {current_status!r}")


class MatchNotFoundError(MatchError):
    """Raised by a :class:`~league_site.matches.store.MatchStore` when a
    ``match_id`` has no persisted record."""

    def __init__(self, match_id: str) -> None:
        self.match_id = match_id
        super().__init__(f"no match found with id {match_id!r}")
