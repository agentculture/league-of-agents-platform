"""Tests for Match <-> DynamoDB-item / S3-archive serialization.

Covers the acceptance criterion "a mid-game match state save->load
round-trips to identical state" from both the DynamoDB item shape
(``to_item``/``from_item``) and the S3 archive JSON shape
(``to_archive_dict``/``from_archive_dict``, round-tripped through real
``json.dumps``/``json.loads``).
"""

from __future__ import annotations

import json

from league_site.matches import (
    Match,
    MatchStatus,
    from_archive_dict,
    from_item,
    to_archive_dict,
    to_item,
)
from league_site.matches.serialization import archive_key
from tests._matches_support import CounterGameEngine, make_participants


def _mid_game_match() -> tuple[Match, CounterGameEngine]:
    human, agent = make_participants()
    engine = CounterGameEngine(target=100, game_id="counter-demo")
    match = Match.create(game_id=engine.game_id, participants=[human, agent], match_id="m-mid-game")
    match.start(engine)
    match.take_turn(engine, human.participant_id, {"delta": 3})
    match.take_turn(engine, agent.participant_id, {"delta": 4})
    match.pause()
    match.resume()
    return match, engine


# --- DynamoDB item mapping ---------------------------------------------------


def test_to_item_uses_single_table_pk_sk_scheme() -> None:
    match, _ = _mid_game_match()
    item = to_item(match)
    assert item["PK"] == "MATCH#m-mid-game"
    assert item["SK"] == "METADATA"
    assert item["entity_type"] == "match"
    assert item["match_id"] == "m-mid-game"


def test_mid_game_match_round_trips_through_to_item_from_item() -> None:
    """Acceptance criterion: mid-game save->load round-trips to identical state."""
    match, _ = _mid_game_match()
    assert match.status is MatchStatus.ACTIVE  # genuinely mid-game, not terminal

    restored = from_item(to_item(match))

    assert restored == match
    assert restored.status is MatchStatus.ACTIVE
    assert restored.game_state == match.game_state
    assert [t.action for t in restored.turns] == [t.action for t in match.turns]


def test_created_match_round_trips() -> None:
    human, agent = make_participants()
    match = Match.create(game_id="counter-demo", participants=[human, agent], match_id="m-created")
    assert from_item(to_item(match)) == match


def test_completed_match_with_result_round_trips() -> None:
    human, agent = make_participants()
    engine = CounterGameEngine(target=2, game_id="counter-demo")
    match = Match.create(game_id="counter-demo", participants=[human, agent], match_id="m-done")
    match.start(engine)
    match.take_turn(engine, human.participant_id, {"delta": 2})
    match.complete(engine)

    restored = from_item(to_item(match))

    assert restored == match
    assert restored.status is MatchStatus.COMPLETED
    assert restored.result == match.result
    assert restored.result is not None
    assert restored.result.scores == {human.participant_id: 2.0}


# --- S3 archive layout -------------------------------------------------------


def test_archive_key_uses_year_and_match_id_scheme() -> None:
    match, _ = _mid_game_match()
    key = archive_key(match)
    assert key == f"archives/{match.created_at.year}/m-mid-game.json"


def test_to_archive_dict_excludes_dynamodb_only_attributes() -> None:
    match, _ = _mid_game_match()
    archived = to_archive_dict(match)
    assert "PK" not in archived
    assert "SK" not in archived
    assert "entity_type" not in archived
    assert archived["match_id"] == match.match_id


def test_archive_round_trips_through_real_json_serialization() -> None:
    match, _ = _mid_game_match()

    raw = json.dumps(to_archive_dict(match))
    restored = from_archive_dict(json.loads(raw))

    assert restored == match


def test_completed_match_archive_round_trips_through_json() -> None:
    human, agent = make_participants()
    engine = CounterGameEngine(target=2, game_id="counter-demo")
    match = Match.create(
        game_id="counter-demo", participants=[human, agent], match_id="m-archive-done"
    )
    match.start(engine)
    match.take_turn(engine, agent.participant_id, {"delta": 5})
    match.complete(engine)

    raw = json.dumps(to_archive_dict(match))
    restored = from_archive_dict(json.loads(raw))

    assert restored == match
    assert restored.result == match.result
