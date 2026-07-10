"""Tests for the automated privacy/secret scrub check.

Covers the acceptance criterion: a match seeded with fake secrets (a token
smuggled into an unexported field, an api key smuggled into another
unexported field) exports with the secrets absent and the scrub check
passing; a deliberately-leaky allowlist makes the scrub check FAIL the
export.

Also covers the design principle from ``schema.py``'s docstring — the
allowlist is the scrub mechanism (default-deny), *not* pattern-scrubbing —
by proving a legitimate field value that merely contains a suspicious
substring (e.g. a display name containing "token") is exported unmangled.
"""

from __future__ import annotations

import datetime as dt
import io
from dataclasses import dataclass
from pathlib import Path

import pytest

import league_site.datasets.schema as schema
from league_site.datasets.export import export_matches
from league_site.datasets.scrub import ScrubViolationError, find_deny_values, scrub_check
from league_site.matches import (
    AgentIdentity,
    Match,
    MatchResult,
    MatchStatus,
    Participant,
    ParticipantKind,
    TurnRecord,
)

_CREATED = dt.datetime(2026, 7, 1, 12, 0, 0, tzinfo=dt.timezone.utc)

_FAKE_API_KEY = "sk-fake-9f8e7d6c5b4a3210"
_FAKE_SESSION_TOKEN = "tok-fake-live-abcdef123456"
_FAKE_SEEDED_SECRET = "seed-secret-value-zzz999"


def _seeded_match(match_id: str = "m-secrets") -> Match:
    """A completed match with fake secrets smuggled into unexported fields.

    * ``game_state`` (opaque, not in the allowlist) carries a nested
      ``api_key`` — simulates a game engine leaking a real credential into
      its opaque state blob.
    * A turn's ``action`` (also opaque, not in the allowlist — only
      ``turn_count`` is exported) carries a ``session_token``.
    * The human participant's ``display_name`` — an *allowlisted*,
      legitimately-exported field — contains the substring "token" without
      being a secret at all, to prove the scrub check does not
      pattern-match field *values*, only field *names* plus explicit seeds.
    """
    human = Participant(
        display_name="tokenizer-fan-42",
        kind=ParticipantKind.HUMAN,
        participant_id="p-human",
    )
    agent = Participant(
        display_name="Sonnet",
        kind=ParticipantKind.AGENT,
        agent_identity=AgentIdentity(model="claude-sonnet-5", provider="anthropic"),
        participant_id="p-agent",
    )
    return Match(
        match_id=match_id,
        game_id="counter-demo",
        participants=(human, agent),
        status=MatchStatus.COMPLETED,
        game_state={"debug": {"api_key": _FAKE_API_KEY}},
        turns=[
            TurnRecord(
                turn_number=1,
                participant_id=human.participant_id,
                action={"delta": 1, "session_token": _FAKE_SESSION_TOKEN},
                timestamp=_CREATED,
            )
        ],
        result=MatchResult(
            completed=True,
            winner_participant_id=agent.participant_id,
            scores={human.participant_id: 1.0, agent.participant_id: 2.0},
            summary="agent wins",
        ),
        created_at=_CREATED,
        updated_at=_CREATED,
    )


# --- positive: secrets in unexported fields never reach the output -----------


def test_export_excludes_secrets_smuggled_into_unexported_fields() -> None:
    match = _seeded_match()
    out = io.StringIO()

    export_matches([match], out)
    text = out.getvalue()

    assert _FAKE_API_KEY not in text
    assert _FAKE_SESSION_TOKEN not in text


def test_export_does_not_mangle_a_legit_field_containing_a_suspicious_substring() -> None:
    """The allowlist is default-deny, not pattern-scrubbing.

    A display name containing "token" is real, exported data — it must
    come through unmodified, proving the scrub check does not redact
    allowlisted content just because its value looks token-ish.
    """
    match = _seeded_match()
    out = io.StringIO()

    export_matches([match], out)
    text = out.getvalue()

    assert "tokenizer-fan-42" in text


def test_export_still_passes_the_scrub_check_directly() -> None:
    match = _seeded_match()
    out = io.StringIO()
    export_matches([match], out)

    # Re-running the check standalone (as an auditor might) also passes.
    scrub_check([match], out.getvalue())


def test_extra_deny_values_are_checked_independent_of_field_name() -> None:
    """A caller can pin a known-secret literal even under an innocuous field name."""
    human = Participant(display_name="Ada", kind=ParticipantKind.HUMAN, participant_id="p-h")
    agent = Participant(
        display_name="Sonnet",
        kind=ParticipantKind.AGENT,
        agent_identity=AgentIdentity(model="claude-sonnet-5", provider="anthropic"),
        participant_id="p-a",
    )
    match = Match(
        match_id="m-seed",
        game_id="counter-demo",
        participants=(human, agent),
        status=MatchStatus.COMPLETED,
        # "note" does not match the key/token/secret/password/credential
        # deny pattern, yet still must never appear in the output.
        game_state={"note": _FAKE_SEEDED_SECRET},
        turns=[],
        result=MatchResult(
            completed=True,
            winner_participant_id=agent.participant_id,
            scores={human.participant_id: 1.0, agent.participant_id: 2.0},
        ),
        created_at=_CREATED,
        updated_at=_CREATED,
    )
    out = io.StringIO()

    export_matches([match], out, extra_deny_values=[_FAKE_SEEDED_SECRET])
    assert _FAKE_SEEDED_SECRET not in out.getvalue()


# --- negative: a leaky allowlist fails the export -----------------------------


def test_leaky_allowlist_makes_the_scrub_check_fail_the_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``ALLOWLIST`` is ever edited to include an unsafe field, export fails loudly."""
    match = _seeded_match()
    leaky_allowlist = dict(schema.ALLOWLIST)
    leaky_allowlist["match"] = schema.MATCH_FIELDS + ("game_state",)
    monkeypatch.setattr(schema, "ALLOWLIST", leaky_allowlist)

    out = io.StringIO()
    with pytest.raises(ScrubViolationError):
        export_matches([match], out)

    # Nothing was written — the export aborted before touching `out`.
    assert out.getvalue() == ""


def test_leaky_allowlist_does_not_leave_a_partial_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    match = _seeded_match()
    leaky_allowlist = dict(schema.ALLOWLIST)
    leaky_allowlist["match"] = schema.MATCH_FIELDS + ("game_state",)
    monkeypatch.setattr(schema, "ALLOWLIST", leaky_allowlist)

    destination = tmp_path / "leaky.jsonl"
    with pytest.raises(ScrubViolationError):
        export_matches([match], destination)

    assert not destination.exists()


# --- lower-level scrub mechanics ----------------------------------------------


def test_find_deny_values_walks_nested_dataclasses_dicts_and_lists() -> None:
    match = _seeded_match()
    found = find_deny_values(match)

    assert any(value == _FAKE_API_KEY for value in found.values())
    assert any(value == _FAKE_SESSION_TOKEN for value in found.values())


def test_find_deny_values_ignores_clean_data() -> None:
    human = Participant(display_name="Ada", kind=ParticipantKind.HUMAN, participant_id="p-h")
    assert find_deny_values(human) == {}


def test_scrub_check_raises_scrub_violation_error_with_field_path() -> None:
    with pytest.raises(ScrubViolationError) as excinfo:
        scrub_check([{"api_key": "abcd1234"}], "...abcd1234...")
    assert "api_key" in excinfo.value.field_path


def test_scrub_check_passes_when_denied_value_absent_from_output() -> None:
    scrub_check([{"api_key": "abcd1234"}], "nothing sensitive here")


def test_scrub_check_ignores_short_values_to_avoid_false_positives() -> None:
    # A short value like "1" would trivially match almost any output text.
    scrub_check([{"api_key": "1"}], "turn 1 of the match")


def test_scrub_check_raises_for_a_leaked_extra_deny_value() -> None:
    with pytest.raises(ScrubViolationError) as excinfo:
        scrub_check(
            [], "...leaked-explicit-secret-999...", extra_deny_values=["leaked-explicit-secret-999"]
        )
    assert excinfo.value.field_path == "<seeded>"


def test_find_deny_values_matches_a_dataclass_field_named_like_a_secret() -> None:
    @dataclass
    class _Credentials:
        api_secret: str

    found = find_deny_values(_Credentials(api_secret="abcd1234"))
    assert found == {"$.api_secret": "abcd1234"}


def test_stringify_skips_enum_values_even_under_a_deny_named_key() -> None:
    # A deny-named key whose value is an Enum (not a string secret) must not
    # crash or false-positive the presence check.
    scrub_check([{"secret_kind": ParticipantKind.AGENT}], "agent")
