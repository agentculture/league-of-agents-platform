"""Tests for :mod:`league_site.capacity.guard`.

Exercises the acceptance criterion "with the store at cap, ``check_capacity``
refuses (structured ``Refusal``) and under cap it allows" (h5) directly
against :class:`~league_site.matches.store.InMemoryMatchStore`.
"""

from __future__ import annotations

from league_site.capacity.config import CapacityConfig
from league_site.capacity.guard import ALLOW, Allow, Refusal, check_capacity
from league_site.matches import InMemoryMatchStore, Match, MatchStatus
from tests._matches_support import CounterGameEngine, make_participants


def _match(match_id: str, status: MatchStatus) -> Match:
    human, agent = make_participants()
    engine = CounterGameEngine(target=1000, game_id="counter-demo")
    match = Match.create(game_id=engine.game_id, participants=[human, agent], match_id=match_id)
    if status is MatchStatus.CREATED:
        return match
    match.start(engine)
    if status is MatchStatus.ACTIVE:
        return match
    if status is MatchStatus.PAUSED:
        match.pause()
        return match
    if status is MatchStatus.COMPLETED:
        match.take_turn(engine, human.participant_id, {"delta": 3})
        match.complete(engine)
        return match
    raise AssertionError(f"unhandled status {status!r}")


def _config(*, max_concurrent: int, max_stored: int) -> CapacityConfig:
    return CapacityConfig(
        max_concurrent_matches=max_concurrent,
        max_stored_matches=max_stored,
        max_match_age_days_hot=3,
        max_archive_age_days=180,
    )


def test_allow_is_truthy_and_refusal_is_falsy() -> None:
    assert bool(ALLOW) is True
    assert bool(Refusal(reason="x", current=1, limit=1)) is False


def test_empty_store_is_allowed() -> None:
    store = InMemoryMatchStore()
    config = _config(max_concurrent=5, max_stored=5)

    result = check_capacity(store, config)

    assert result == ALLOW
    assert isinstance(result, Allow)


def test_under_both_caps_is_allowed() -> None:
    store = InMemoryMatchStore()
    store.save(_match("m-1", MatchStatus.ACTIVE))
    store.save(_match("m-2", MatchStatus.COMPLETED))
    config = _config(max_concurrent=5, max_stored=5)

    assert check_capacity(store, config) == ALLOW


def test_refuses_at_the_concurrent_cap() -> None:
    store = InMemoryMatchStore()
    store.save(_match("m-1", MatchStatus.ACTIVE))
    store.save(_match("m-2", MatchStatus.PAUSED))
    config = _config(max_concurrent=2, max_stored=100)

    result = check_capacity(store, config)

    assert isinstance(result, Refusal)
    assert result.reason == "max_concurrent_matches"
    assert result.current == 2
    assert result.limit == 2


def test_allows_just_under_the_concurrent_cap() -> None:
    store = InMemoryMatchStore()
    store.save(_match("m-1", MatchStatus.ACTIVE))
    config = _config(max_concurrent=2, max_stored=100)

    assert check_capacity(store, config) == ALLOW


def test_paused_matches_count_toward_the_concurrent_cap() -> None:
    store = InMemoryMatchStore()
    store.save(_match("m-1", MatchStatus.PAUSED))
    store.save(_match("m-2", MatchStatus.PAUSED))
    config = _config(max_concurrent=2, max_stored=100)

    result = check_capacity(store, config)

    assert isinstance(result, Refusal)
    assert result.reason == "max_concurrent_matches"


def test_created_and_completed_matches_do_not_count_toward_the_concurrent_cap() -> None:
    store = InMemoryMatchStore()
    store.save(_match("m-1", MatchStatus.CREATED))
    store.save(_match("m-2", MatchStatus.COMPLETED))
    config = _config(max_concurrent=1, max_stored=100)

    assert check_capacity(store, config) == ALLOW


def test_refuses_at_the_stored_cap_even_when_under_the_concurrent_cap() -> None:
    store = InMemoryMatchStore()
    store.save(_match("m-1", MatchStatus.COMPLETED))
    store.save(_match("m-2", MatchStatus.COMPLETED))
    config = _config(max_concurrent=100, max_stored=2)

    result = check_capacity(store, config)

    assert isinstance(result, Refusal)
    assert result.reason == "max_stored_matches"
    assert result.current == 2
    assert result.limit == 2


def test_allows_just_under_the_stored_cap() -> None:
    store = InMemoryMatchStore()
    store.save(_match("m-1", MatchStatus.COMPLETED))
    config = _config(max_concurrent=100, max_stored=2)

    assert check_capacity(store, config) == ALLOW


def test_concurrent_cap_is_checked_before_the_stored_cap() -> None:
    """Both caps are simultaneously exceeded; the concurrent refusal wins."""
    store = InMemoryMatchStore()
    store.save(_match("m-1", MatchStatus.ACTIVE))
    store.save(_match("m-2", MatchStatus.ACTIVE))
    config = _config(max_concurrent=1, max_stored=1)

    result = check_capacity(store, config)

    assert isinstance(result, Refusal)
    assert result.reason == "max_concurrent_matches"


def test_check_capacity_does_not_mutate_the_store() -> None:
    store = InMemoryMatchStore()
    store.save(_match("m-1", MatchStatus.ACTIVE))
    config = _config(max_concurrent=1, max_stored=1)

    before = sorted(store.list_ids())
    check_capacity(store, config)
    after = sorted(store.list_ids())

    assert before == after == ["m-1"]


def test_check_capacity_is_pure_and_repeatable() -> None:
    store = InMemoryMatchStore()
    store.save(_match("m-1", MatchStatus.ACTIVE))
    config = _config(max_concurrent=1, max_stored=1)

    first = check_capacity(store, config)
    second = check_capacity(store, config)

    assert first == second
