"""Shared test-only fakes for the ``league_site.cli._commands`` operator surface.

Not collected by pytest (module name doesn't match ``test_*``); imported
directly by the ``test_cli_ops_*`` modules. Mirrors the fake-dependency
style ``tests/_matches_support.py`` and ``tests/test_cleanup_handler.py``
already use for :mod:`league_site.aws_lambda.cleanup` — no real AWS
anywhere in this suite.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from league_site.matches.match import Match
from league_site.matches.store import InMemoryMatchStore, MatchStore
from tests._matches_support import CounterGameEngine, make_participants


class SpyMatchStore(MatchStore):
    """Wraps an :class:`InMemoryMatchStore`, recording every ``save``/``delete`` call.

    Lets a test assert "dry-run mutates nothing" / "--apply mutates" by
    checking ``.saved``/``.deleted`` call logs, rather than reaching into a
    plain :class:`InMemoryMatchStore`'s private internals.
    """

    def __init__(self) -> None:
        self._inner = InMemoryMatchStore()
        self.saved: list[str] = []
        self.deleted: list[str] = []

    def save(self, match: Match) -> None:
        self.saved.append(match.match_id)
        self._inner.save(match)

    def load(self, match_id: str) -> Match:
        return self._inner.load(match_id)

    def delete(self, match_id: str) -> None:
        self.deleted.append(match_id)
        self._inner.delete(match_id)

    def list_ids(self) -> list[str]:
        return self._inner.list_ids()


class FakeArchive:
    """Stand-in for :class:`league_site.matches.aws.S3MatchArchive`: records ``archive()`` calls."""

    def __init__(self) -> None:
        self.archived: list[str] = []

    def archive(self, match: Match) -> str:
        self.archived.append(match.match_id)
        return f"archives/{match.created_at.year}/{match.match_id}.json"


class FakeS3Client:
    """Minimal stand-in for a boto3 S3 client used by the archive-aging pass.

    ``list_objects_v2`` always reports an empty, non-truncated page (no
    aged archives to find) — good enough for CLI-layer tests, which only
    need to observe whether ``delete_object`` was called, not exercise the
    pagination itself (that is already covered by
    ``tests/test_cleanup_handler.py``).
    """

    def __init__(self) -> None:
        self.deleted_keys: list[str] = []

    def list_objects_v2(
        self, *, Bucket: str, Prefix: str = "", **kwargs: Any
    ) -> dict[str, Any]:  # noqa: N803
        return {"Contents": [], "IsTruncated": False}

    def delete_object(self, *, Bucket: str, Key: str) -> None:  # noqa: N803
        self.deleted_keys.append(Key)


def active_match(match_id: str = "m-active") -> Match:
    """A fresh ``ACTIVE`` match with one human + one agent participant."""
    human, agent = make_participants()
    engine = CounterGameEngine(target=1000, game_id="counter-demo")
    match = Match.create(game_id=engine.game_id, participants=[human, agent], match_id=match_id)
    match.start(engine)
    return match


def hot_stale_completed_match(match_id: str = "m-stale") -> Match:
    """A ``COMPLETED`` match whose ``updated_at`` is far enough in the past to be
    hot-stale under every plausible ``max_match_age_days_hot`` config — deterministic
    without injecting a fixed ``now`` into the command under test.
    """
    human, agent = make_participants()
    engine = CounterGameEngine(target=1, game_id="counter-demo")
    match = Match.create(game_id=engine.game_id, participants=[human, agent], match_id=match_id)
    match.start(engine)
    match.take_turn(engine, human.participant_id, {"delta": 5})
    match.complete(engine)
    match.updated_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    return match
