"""Tests for versioned JSONL match export.

Covers the acceptance criteria "export of a set of completed matches
produces JSONL matching the documented schema" and "two exports of the same
matches are byte-identical" (determinism). Scrub/privacy behavior lives in
``test_datasets_scrub.py``.
"""

from __future__ import annotations

import datetime as dt
import io
import json
from typing import Any

import pytest

from league_site.datasets.export import dataset_filename, export_matches
from league_site.datasets.schema import HEADER_FIELDS, PARTICIPANT_FIELDS, RESULT_FIELDS
from league_site.matches import (
    AgentIdentity,
    Match,
    MatchResult,
    MatchStatus,
    Participant,
    ParticipantKind,
)
from tests._matches_support import CounterGameEngine

_CREATED = dt.datetime(2026, 7, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
_UPDATED = dt.datetime(2026, 7, 1, 12, 30, 0, tzinfo=dt.timezone.utc)


def _make_match(
    match_id: str = "m1",
    *,
    game_id: str = "counter-demo",
    human_name: str = "Ada",
    agent_name: str = "Sonnet",
    scores: dict[str, float] | None = None,
    summary: str = "agent wins on points",
    status: MatchStatus = MatchStatus.COMPLETED,
    game_state: Any = None,
) -> Match:
    human = Participant(
        display_name=human_name, kind=ParticipantKind.HUMAN, participant_id=f"{match_id}-human"
    )
    agent = Participant(
        display_name=agent_name,
        kind=ParticipantKind.AGENT,
        agent_identity=AgentIdentity(model="claude-sonnet-5", provider="anthropic"),
        participant_id=f"{match_id}-agent",
    )
    if scores is None:
        scores = {human.participant_id: 3.0, agent.participant_id: 7.0}
    result = (
        MatchResult(
            completed=True,
            winner_participant_id=agent.participant_id,
            scores=scores,
            summary=summary,
        )
        if status is MatchStatus.COMPLETED
        else None
    )
    return Match(
        match_id=match_id,
        game_id=game_id,
        participants=(human, agent),
        status=status,
        game_state=game_state,
        turns=[],
        result=result,
        created_at=_CREATED,
        updated_at=_UPDATED,
    )


def _lines(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line]


# --- schema-matching export --------------------------------------------------


def test_export_writes_header_line_matching_allowlist() -> None:
    out = io.StringIO()
    count = export_matches([_make_match("m1")], out, generated_by="test-suite")
    records = _lines(out.getvalue())

    assert count == 1
    header = records[0]
    assert set(header) == set(HEADER_FIELDS)
    assert header["schema_version"] == "1.0"
    assert header["generated_by"] == "test-suite"
    assert header["count"] == 1


def test_export_match_record_has_exactly_the_allowlisted_top_level_fields() -> None:
    out = io.StringIO()
    export_matches([_make_match("m1")], out)
    _, record = _lines(out.getvalue())

    assert set(record) == {
        "match_id",
        "game_id",
        "game_version",
        "created_at",
        "updated_at",
        "turn_count",
        "participants",
        "result",
    }
    assert record["match_id"] == "m1"
    assert record["game_id"] == "counter-demo"
    assert record["created_at"] == _CREATED.isoformat()
    assert record["updated_at"] == _UPDATED.isoformat()
    assert record["turn_count"] == 0


def test_export_result_record_has_exactly_the_allowlisted_fields() -> None:
    out = io.StringIO()
    export_matches([_make_match("m1", summary="clean win")], out)
    _, record = _lines(out.getvalue())

    assert set(record["result"]) == set(RESULT_FIELDS)
    assert record["result"]["completed"] is True
    assert record["result"]["summary"] == "clean win"
    assert record["result"]["winner_participant_id"] == "m1-agent"


def test_export_participant_records_have_exactly_the_allowlisted_fields() -> None:
    out = io.StringIO()
    export_matches([_make_match("m1")], out)
    _, record = _lines(out.getvalue())

    for participant in record["participants"]:
        assert set(participant) == set(PARTICIPANT_FIELDS)


def test_export_human_participant_has_null_model_and_provider() -> None:
    out = io.StringIO()
    export_matches([_make_match("m1")], out)
    _, record = _lines(out.getvalue())

    human = next(p for p in record["participants"] if p["kind"] == "human")
    assert human["display_name"] == "Ada"
    assert human["model"] is None
    assert human["provider"] is None
    assert human["hard_score"] == 3.0


def test_export_agent_participant_carries_model_and_provider() -> None:
    out = io.StringIO()
    export_matches([_make_match("m1")], out)
    _, record = _lines(out.getvalue())

    agent = next(p for p in record["participants"] if p["kind"] == "agent")
    assert agent["display_name"] == "Sonnet"
    assert agent["model"] == "claude-sonnet-5"
    assert agent["provider"] == "anthropic"
    assert agent["hard_score"] == 7.0


def test_export_turn_count_reflects_number_of_turns() -> None:
    human = Participant(display_name="Ada", kind=ParticipantKind.HUMAN, participant_id="p-h")
    agent = Participant(
        display_name="Sonnet",
        kind=ParticipantKind.AGENT,
        agent_identity=AgentIdentity(model="claude-sonnet-5", provider="anthropic"),
        participant_id="p-a",
    )
    engine = CounterGameEngine(target=1, game_id="counter-demo")
    match = Match.create(game_id=engine.game_id, participants=[human, agent], match_id="m-turns")
    match.start(engine)
    match.take_turn(engine, human.participant_id, {"delta": 1})
    match.complete(engine)

    out = io.StringIO()
    export_matches([match], out)
    _, record = _lines(out.getvalue())
    assert record["turn_count"] == 1


def test_export_defaults_game_version_to_unknown() -> None:
    out = io.StringIO()
    export_matches([_make_match("m1")], out)
    _, record = _lines(out.getvalue())
    assert record["game_version"] == "unknown"


def test_export_game_versions_mapping_supplies_per_game_version() -> None:
    out = io.StringIO()
    export_matches(
        [_make_match("m1", game_id="counter-demo")],
        out,
        game_versions={"counter-demo": "2.3.0"},
    )
    _, record = _lines(out.getvalue())
    assert record["game_version"] == "2.3.0"


def test_export_quality_axes_are_attached_per_match_and_participant() -> None:
    match = _make_match("m1")
    out = io.StringIO()
    export_matches(
        [match],
        out,
        quality_axes={"m1": {"m1-agent": {"clarity": 4.5, "sportsmanship": 5.0}}},
    )
    _, record = _lines(out.getvalue())

    agent = next(p for p in record["participants"] if p["kind"] == "agent")
    human = next(p for p in record["participants"] if p["kind"] == "human")
    assert agent["quality_axes"] == {"clarity": 4.5, "sportsmanship": 5.0}
    assert human["quality_axes"] == {}


def test_export_quality_axes_default_to_empty_dict_when_not_supplied() -> None:
    out = io.StringIO()
    export_matches([_make_match("m1")], out)
    _, record = _lines(out.getvalue())
    assert all(p["quality_axes"] == {} for p in record["participants"])


# --- validation ---------------------------------------------------------------


def test_export_rejects_a_match_that_is_not_completed() -> None:
    match = _make_match("m1", status=MatchStatus.ACTIVE)
    out = io.StringIO()
    with pytest.raises(ValueError, match="not completed"):
        export_matches([match], out)
    assert out.getvalue() == ""


# --- determinism ---------------------------------------------------------------


def test_export_is_byte_identical_across_two_runs_of_the_same_matches() -> None:
    matches = [_make_match("m1"), _make_match("m2", human_name="Grace", agent_name="Opus")]

    out1 = io.StringIO()
    out2 = io.StringIO()
    export_matches(matches, out1, generated_by="test-suite")
    export_matches(matches, out2, generated_by="test-suite")

    assert out1.getvalue() == out2.getvalue()
    assert out1.getvalue() != ""


def test_export_field_order_within_each_json_object_is_sorted() -> None:
    out = io.StringIO()
    export_matches([_make_match("m1")], out)
    header_line, match_line = out.getvalue().splitlines()

    assert list(json.loads(header_line)) == sorted(json.loads(header_line))
    assert list(json.loads(match_line)) == sorted(json.loads(match_line))


def test_export_accepts_a_file_path_destination(tmp_path: Any) -> None:
    destination = tmp_path / "matches.jsonl"
    export_matches([_make_match("m1")], destination)

    assert destination.exists()
    records = _lines(destination.read_text(encoding="utf-8"))
    assert records[0]["schema_version"] == "1.0"
    assert records[1]["match_id"] == "m1"


# --- dataset_filename -----------------------------------------------------


def test_dataset_filename_with_date_object() -> None:
    assert dataset_filename("1.0", dt.date(2026, 7, 10)) == "matches-v1.0-2026-07-10.jsonl"


def test_dataset_filename_with_datetime_object() -> None:
    stamp = dt.datetime(2026, 7, 10, 23, 59, tzinfo=dt.timezone.utc)
    assert dataset_filename("1.0", stamp) == "matches-v1.0-2026-07-10.jsonl"


def test_dataset_filename_with_iso_string() -> None:
    assert dataset_filename("1.0", "2026-07-10") == "matches-v1.0-2026-07-10.jsonl"
