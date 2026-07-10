"""Tests for the shared HMAC signing primitive in league_site.auth._signing."""

from __future__ import annotations

import hashlib
import hmac as hmac_mod

import pytest

from league_site.auth._signing import (
    MissingSecretError,
    _b64encode,
    read_secret,
    sign_payload,
    verify_payload,
)


def test_sign_and_verify_round_trips() -> None:
    token = sign_payload({"a": 1, "b": "two"}, b"secret")
    assert verify_payload(token, b"secret") == {"a": 1, "b": "two"}


def test_wrong_secret_rejected() -> None:
    token = sign_payload({"a": 1}, b"secret")
    assert verify_payload(token, b"other-secret") is None


def test_tampered_payload_rejected() -> None:
    token = sign_payload({"a": 1}, b"secret")
    encoded, _, signature = token.partition(".")
    tampered = f"{encoded}x.{signature}"
    assert verify_payload(tampered, b"secret") is None


def test_tampered_signature_rejected() -> None:
    token = sign_payload({"a": 1}, b"secret")
    encoded, _, signature = token.partition(".")
    tampered = f"{encoded}.{signature}x"
    assert verify_payload(tampered, b"secret") is None


@pytest.mark.parametrize("malformed", ["", "not-a-token", "a.b.c", "..", "!!!.!!!"])
def test_malformed_token_rejected(malformed: str) -> None:
    assert verify_payload(malformed, b"secret") is None


def test_signature_segment_with_invalid_base64_rejected() -> None:
    # A single data character can never be valid base64 (needs >= 2 to encode a byte);
    # the signature decode must fail closed (return None), not raise.
    assert verify_payload("YQ.a", b"secret") is None


def test_payload_segment_that_decodes_to_invalid_json_rejected() -> None:
    # A correctly base64-encoded, correctly signed segment whose bytes are valid
    # UTF-8 but not valid JSON at all must still fail closed.
    encoded = _b64encode(b"not json at all")
    signature = hmac_mod.new(b"secret", encoded.encode("ascii"), hashlib.sha256).digest()
    token = f"{encoded}.{_b64encode(signature)}"
    assert verify_payload(token, b"secret") is None


def test_payload_segment_that_decodes_to_non_dict_json_rejected() -> None:
    # A syntactically valid, correctly signed token whose payload is a JSON array
    # (not an object) must still be rejected - the shape contract is a dict.
    encoded = _b64encode(b"[1, 2, 3]")
    signature = hmac_mod.new(b"secret", encoded.encode("ascii"), hashlib.sha256).digest()
    token = f"{encoded}.{_b64encode(signature)}"
    assert verify_payload(token, b"secret") is None


def test_read_secret_returns_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEAGUE_TEST_SECRET", "shh")
    assert read_secret("LEAGUE_TEST_SECRET") == b"shh"


def test_read_secret_missing_raises_naming_the_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEAGUE_TEST_SECRET", raising=False)
    with pytest.raises(MissingSecretError) as excinfo:
        read_secret("LEAGUE_TEST_SECRET")
    assert "LEAGUE_TEST_SECRET" in str(excinfo.value)
    assert excinfo.value.env_var == "LEAGUE_TEST_SECRET"


def test_read_secret_empty_string_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEAGUE_TEST_SECRET", "")
    with pytest.raises(MissingSecretError):
        read_secret("LEAGUE_TEST_SECRET")
