"""Tests for league_site.byok.vault: SecretKey redaction + InMemoryKeyVault.

Covers the task's acceptance criteria:

* A pasted key: stored, used, revoked — after revocation get() fails.
* Key material never appears in repr/str, and a logging test proves a
  logged record containing a SecretKey leaks nothing.
"""

from __future__ import annotations

import logging

import pytest

from league_site.byok.vault import (
    InMemoryKeyVault,
    KeyHandleInfo,
    KeyNotFoundError,
    SecretKey,
    coerce_secret,
)

SUPER_SECRET = "sk-super-secret-value-should-never-leak-anywhere"  # nosec B105 - test fixture


# --- SecretKey -----------------------------------------------------------------


def test_secret_key_rejects_empty_material() -> None:
    with pytest.raises(ValueError):
        SecretKey("")


def test_secret_key_reveal_returns_the_raw_material() -> None:
    key = SecretKey(SUPER_SECRET)
    assert key.reveal() == SUPER_SECRET


def test_secret_key_repr_never_contains_the_material() -> None:
    key = SecretKey(SUPER_SECRET)
    assert SUPER_SECRET not in repr(key)
    assert repr(key) == "SecretKey(***redacted***)"


def test_secret_key_str_never_contains_the_material() -> None:
    key = SecretKey(SUPER_SECRET)
    assert SUPER_SECRET not in str(key)
    assert str(key) == "SecretKey(***redacted***)"


def test_secret_key_str_interpolation_never_leaks_material() -> None:
    key = SecretKey(SUPER_SECRET)
    assert SUPER_SECRET not in f"key={key}"
    assert SUPER_SECRET not in f"key={key!r}"
    assert SUPER_SECRET not in "key=%s" % (key,)


def test_secret_key_equality_compares_material_not_identity() -> None:
    assert SecretKey(SUPER_SECRET) == SecretKey(SUPER_SECRET)
    assert SecretKey(SUPER_SECRET) != SecretKey("something-else")
    assert SecretKey(SUPER_SECRET) != "not-a-secret-key"


def test_secret_key_is_unhashable() -> None:
    with pytest.raises(TypeError):
        hash(SecretKey(SUPER_SECRET))


def test_coerce_secret_wraps_plain_strings_and_passes_through_secret_keys() -> None:
    wrapped = coerce_secret(SUPER_SECRET)
    assert isinstance(wrapped, SecretKey)
    assert wrapped.reveal() == SUPER_SECRET

    already = SecretKey(SUPER_SECRET)
    assert coerce_secret(already) is already


def test_logging_a_record_containing_a_secret_key_leaks_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """h12: a caplog test proving key material never appears in a logged record."""
    key = SecretKey(SUPER_SECRET)
    logger = logging.getLogger("league_site.byok.test")

    with caplog.at_level(logging.INFO):
        logger.info("stored byok key: %s", key)
        logger.info("stored byok key repr: %r", key)
        logger.warning("vault entry: %s", {"handle": "byok_abc123", "material": key})

    assert caplog.records, "expected at least one log record to have been captured"
    for record in caplog.records:
        formatted = record.getMessage()
        assert SUPER_SECRET not in formatted
        assert SUPER_SECRET not in str(record.__dict__)
        assert SUPER_SECRET not in repr(record)
    # And the redaction marker is what actually shows up in place of the secret.
    assert "***redacted***" in caplog.text


# --- InMemoryKeyVault: put / get / revoke lifecycle -----------------------------


def test_put_returns_an_opaque_handle_distinct_from_the_key_material() -> None:
    vault = InMemoryKeyVault()
    handle = vault.put("player-1", "anthropic", SUPER_SECRET)
    assert isinstance(handle, str)
    assert handle
    assert SUPER_SECRET not in handle


def test_put_accepts_a_plain_string_or_an_already_wrapped_secret_key() -> None:
    vault = InMemoryKeyVault()
    handle_from_str = vault.put("player-1", "anthropic", SUPER_SECRET)
    handle_from_secret = vault.put("player-1", "anthropic", SecretKey(SUPER_SECRET))

    assert vault.get(handle_from_str).reveal() == SUPER_SECRET
    assert vault.get(handle_from_secret).reveal() == SUPER_SECRET


def test_get_round_trips_the_exact_key_material() -> None:
    vault = InMemoryKeyVault()
    handle = vault.put("player-1", "openai", SUPER_SECRET)

    fetched = vault.get(handle)

    assert isinstance(fetched, SecretKey)
    assert fetched.reveal() == SUPER_SECRET


def test_get_unknown_handle_raises_key_not_found() -> None:
    vault = InMemoryKeyVault()
    with pytest.raises(KeyNotFoundError):
        vault.get("byok_does_not_exist")


def test_stored_pasted_key_is_used_then_revoked_and_then_unusable() -> None:
    """The full BYOK lifecycle: stored, used (get succeeds), revoked, then get fails cleanly."""
    vault = InMemoryKeyVault()
    handle = vault.put("player-1", "anthropic", SUPER_SECRET)

    # stored + used
    assert vault.get(handle).reveal() == SUPER_SECRET

    # revoked
    vault.revoke(handle)

    # unusable afterwards
    with pytest.raises(KeyNotFoundError):
        vault.get(handle)


def test_revoke_unknown_handle_raises_key_not_found() -> None:
    vault = InMemoryKeyVault()
    with pytest.raises(KeyNotFoundError):
        vault.revoke("byok_does_not_exist")


def test_revoke_is_permanent_and_double_revoke_is_a_harmless_no_op() -> None:
    vault = InMemoryKeyVault()
    handle = vault.put("player-1", "anthropic", SUPER_SECRET)
    vault.revoke(handle)

    # Revoking an already-revoked handle doesn't raise (mirrors
    # TokenStore.revoke: only an unknown handle is an error) or un-revoke it.
    vault.revoke(handle)

    with pytest.raises(KeyNotFoundError):
        vault.get(handle)


def test_describe_returns_metadata_without_the_key_material() -> None:
    vault = InMemoryKeyVault()
    handle = vault.put("player-1", "anthropic", SUPER_SECRET)

    info = vault.describe(handle)

    assert isinstance(info, KeyHandleInfo)
    assert info.handle == handle
    assert info.owner == "player-1"
    assert info.provider == "anthropic"
    assert info.revoked is False
    assert SUPER_SECRET not in repr(info)
    assert SUPER_SECRET not in str(info)


def test_describe_reflects_revocation() -> None:
    vault = InMemoryKeyVault()
    handle = vault.put("player-1", "anthropic", SUPER_SECRET)
    vault.revoke(handle)

    info = vault.describe(handle)

    assert info.revoked is True


def test_describe_unknown_handle_raises_key_not_found() -> None:
    vault = InMemoryKeyVault()
    with pytest.raises(KeyNotFoundError):
        vault.describe("byok_does_not_exist")


def test_two_different_owners_puts_produce_independent_handles() -> None:
    vault = InMemoryKeyVault()
    handle_a = vault.put("player-a", "anthropic", "key-for-a")
    handle_b = vault.put("player-b", "anthropic", "key-for-b")

    assert handle_a != handle_b
    assert vault.get(handle_a).reveal() == "key-for-a"
    assert vault.get(handle_b).reveal() == "key-for-b"

    vault.revoke(handle_a)
    # Revoking one owner's handle never touches the other's.
    assert vault.get(handle_b).reveal() == "key-for-b"
