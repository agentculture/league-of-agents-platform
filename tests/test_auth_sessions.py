"""Tests for league_site.auth.sessions: issue/verify/expire of session tokens."""

from __future__ import annotations

import pytest

from league_site.auth._signing import MissingSecretError, sign_payload
from league_site.auth.sessions import DEFAULT_TTL_SECONDS, Session, issue, verify

_IDENTITY = {
    "provider": "github",
    "subject": "12345",
    "handle": "octocat",
    "display_name": "The Octocat",
}


@pytest.fixture(autouse=True)
def _session_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEAGUE_SESSION_SECRET", "test-session-secret")


def test_issue_and_verify_round_trip_carries_identity() -> None:
    token = issue(_IDENTITY, now=1_000)
    session = verify(token, now=1_001)
    assert session == Session(
        subject="12345",
        provider="github",
        display="The Octocat",
        issued_at=1_000,
        expiry=1_000 + DEFAULT_TTL_SECONDS,
    )


def test_display_falls_back_to_handle_then_subject() -> None:
    token = issue({"provider": "google", "subject": "9", "handle": "nine"}, now=0)
    session = verify(token, now=1)
    assert session is not None
    assert session.display == "nine"

    token = issue({"provider": "google", "subject": "9"}, now=0)
    session = verify(token, now=1)
    assert session is not None
    assert session.display == "9"


def test_tampered_token_verifies_as_none() -> None:
    token = issue(_IDENTITY, now=1_000)
    encoded, _, signature = token.partition(".")
    tampered = f"{encoded}.{signature[:-1]}x" if signature else f"{encoded}.x"
    assert verify(tampered, now=1_001) is None


def test_expired_token_verifies_as_none() -> None:
    token = issue(_IDENTITY, now=1_000, ttl_seconds=60)
    assert verify(token, now=1_000 + 60) is None  # exactly at expiry: expired
    assert verify(token, now=1_000 + 59) is not None  # one second before: still valid


def test_wrong_secret_verifies_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    token = issue(_IDENTITY, now=1_000)
    monkeypatch.setenv("LEAGUE_SESSION_SECRET", "a-different-secret")
    assert verify(token, now=1_001) is None


def test_missing_secret_raises_naming_the_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEAGUE_SESSION_SECRET", raising=False)
    with pytest.raises(MissingSecretError) as excinfo:
        issue(_IDENTITY)
    assert "LEAGUE_SESSION_SECRET" in str(excinfo.value)


def test_garbage_token_verifies_as_none() -> None:
    assert verify("not-a-real-token") is None
    assert verify("") is None


def test_session_with_wrong_field_types_verifies_as_none() -> None:
    forged = sign_payload(
        {
            "subject": "1",
            "provider": "github",
            "display": "x",
            "issued_at": "not-a-number",
            "expiry": "also-not-a-number",
        },
        b"test-session-secret",
    )
    assert verify(forged, now=0) is None
