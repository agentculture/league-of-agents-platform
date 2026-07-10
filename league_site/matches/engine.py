"""Game engine interface: turn exchanges only.

:class:`~league_site.matches.match.Match` is game-agnostic — it drives any
``GameEngine`` implementation through this interface without knowing that
game's rules. A new game plugs in by implementing ``GameEngine``; no change
to the match state machine, store, or serialization is required.

By design this interface has **no tick/frame/step-loop concept**. State only
advances in response to :meth:`GameEngine.apply_turn`, one participant
action at a time — there is no ``update(dt)``, ``tick()``, ``render()``, or
any other realtime hook anywhere on this class. ``tests/test_matches_engine.py``
asserts this holds by inspecting the class's public surface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from league_site.matches.models import Participant


class GameEngine(ABC):
    """Turn-exchange game interface.

    ``state`` and ``action`` are opaque to the match state machine — each
    concrete engine defines its own shape for them (a plain ``dict`` is
    recommended so matches stay archivable to JSON; see
    :mod:`league_site.matches.serialization`).
    """

    @property
    @abstractmethod
    def game_id(self) -> str:
        """Stable identifier for the game this engine implements (e.g. ``"tic-tac-toe"``)."""

    @abstractmethod
    def initial_state(self, participants: Sequence[Participant]) -> Any:
        """Return the opaque initial game state for a freshly started match."""

    @abstractmethod
    def apply_turn(self, state: Any, participant_id: str, action: Any) -> Any:
        """Return the next game state after ``participant_id`` plays ``action``."""

    @abstractmethod
    def is_over(self, state: Any) -> bool:
        """Return ``True`` once ``state`` is terminal."""

    @abstractmethod
    def score(self, state: Any) -> dict[str, float]:
        """Return ``participant_id -> score`` for ``state`` (terminal or not)."""
