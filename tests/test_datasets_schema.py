"""Tests for the versioned, allowlist-driven dataset export schema."""

from __future__ import annotations

from league_site.datasets.schema import (
    ALLOWLIST,
    HEADER_FIELDS,
    MATCH_FIELDS,
    PARTICIPANT_FIELDS,
    RESULT_FIELDS,
    SCHEMA_VERSION,
)


def test_schema_version_is_1_0() -> None:
    assert SCHEMA_VERSION == "1.0"


def test_allowlist_has_exactly_the_four_documented_sections() -> None:
    assert set(ALLOWLIST) == {"header", "match", "participant", "result"}
    assert ALLOWLIST["header"] == HEADER_FIELDS
    assert ALLOWLIST["match"] == MATCH_FIELDS
    assert ALLOWLIST["participant"] == PARTICIPANT_FIELDS
    assert ALLOWLIST["result"] == RESULT_FIELDS


def test_match_fields_cover_the_documented_benchmark_schema() -> None:
    assert MATCH_FIELDS == (
        "match_id",
        "game_id",
        "game_version",
        "created_at",
        "updated_at",
        "turn_count",
        "participants",
        "result",
    )


def test_participant_fields_cover_hard_scores_and_quality_axes() -> None:
    assert PARTICIPANT_FIELDS == (
        "participant_id",
        "kind",
        "display_name",
        "model",
        "provider",
        "hard_score",
        "quality_axes",
    )


def test_result_fields() -> None:
    assert RESULT_FIELDS == ("completed", "winner_participant_id", "summary")


def test_header_fields() -> None:
    assert HEADER_FIELDS == ("schema_version", "generated_by", "count")


def test_no_field_tuple_has_duplicate_names() -> None:
    for section, fields in ALLOWLIST.items():
        assert len(fields) == len(set(fields)), f"duplicate field name in {section!r}"


def test_no_allowlisted_field_name_looks_like_a_secret() -> None:
    """Sanity check: legitimate exported field names never look sensitive.

    If this ever fails, someone added a field named e.g. ``api_key`` to the
    allowlist directly — the scrub check in
    :mod:`league_site.datasets.scrub` would then have to fight the
    allowlist on every export, which is exactly the situation the
    default-deny design is meant to avoid.
    """
    deny_substrings = ("key", "token", "secret", "password", "credential")
    for fields in ALLOWLIST.values():
        for name in fields:
            lowered = name.lower()
            assert not any(sub in lowered for sub in deny_substrings), name
