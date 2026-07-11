"""Tests for ``league-site tokens list|block|unblock``.

The operator kill-switch surface for agent credentials: ``block``/``unblock``
flip the persisted ``blocked`` flag through the store (effective on the next
request, no restart), ``list`` shows every token's blocked/revoked state.
Every dependency is monkeypatched via
:mod:`league_site.cli._commands._stores`'s seam — no real AWS anywhere.

Token *values* are never emitted: ``list`` shows the token id (a uuid), the
agent name, and the block/revoke flags, never the secret or its hash.
"""

from __future__ import annotations

import json

import pytest

from league_site.auth import tokens
from league_site.auth.token_store import InMemoryTokenStore
from league_site.cli import main
from league_site.cli._commands import _stores


def _store_with_token(
    *, agent_name: str = "Sonnet", owner: str = "github:1"
) -> tuple[InMemoryTokenStore, str, str]:
    store = InMemoryTokenStore()
    issued = tokens.issue(
        store, agent_name=agent_name, model="m", provider="p", owner_account_id=owner
    )
    return store, issued.identity.token_id, issued.token


# --- list --------------------------------------------------------------------


def test_tokens_list_json_shows_blocked_state(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store, token_id, secret = _store_with_token(agent_name="Sonnet")
    store.set_blocked(token_id, True)
    monkeypatch.setattr(_stores, "resolve_token_store", lambda: (store, False))

    rc = main(["tokens", "list", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["tokens"]) == 1
    row = payload["tokens"][0]
    assert row["token_id"] == token_id
    assert row["agent_name"] == "Sonnet"
    assert row["blocked"] is True
    assert row["revoked"] is False
    assert row["owner_account_id"] == "github:1"
    # The plaintext token (and its hash) never appears in the listing.
    assert secret not in json.dumps(payload)


def test_tokens_list_ephemeral_note(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(_stores, "resolve_token_store", lambda: (InMemoryTokenStore(), True))
    rc = main(["tokens", "list", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tokens"] == []
    assert "TOKENS_TABLE_NAME" in payload["note"]


def test_tokens_list_text_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store, token_id, _ = _store_with_token(agent_name="Sonnet")
    monkeypatch.setattr(_stores, "resolve_token_store", lambda: (store, False))
    rc = main(["tokens", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Sonnet" in out
    assert token_id in out


# --- block / unblock ---------------------------------------------------------


def test_tokens_block_by_token_id_flips_the_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store, token_id, _ = _store_with_token()
    monkeypatch.setattr(_stores, "resolve_token_store", lambda: (store, False))

    rc = main(["tokens", "block", token_id, "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["token_id"] == token_id
    assert payload["blocked"] is True
    assert store.list_all()[0].blocked is True


def test_tokens_block_by_agent_name_flips_the_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store, token_id, _ = _store_with_token(agent_name="Sonnet")
    monkeypatch.setattr(_stores, "resolve_token_store", lambda: (store, False))

    rc = main(["tokens", "block", "Sonnet"])
    assert rc == 0
    assert store.list_all()[0].blocked is True


def test_tokens_block_then_unblock_round_trips(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store, token_id, _ = _store_with_token()
    monkeypatch.setattr(_stores, "resolve_token_store", lambda: (store, False))

    assert main(["tokens", "block", token_id]) == 0
    assert store.list_all()[0].blocked is True
    assert main(["tokens", "unblock", token_id]) == 0
    assert store.list_all()[0].blocked is False


def test_tokens_block_unknown_selector_is_a_user_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(_stores, "resolve_token_store", lambda: (InMemoryTokenStore(), False))
    rc = main(["tokens", "block", "nope"])
    assert rc == 1
    assert "nope" in capsys.readouterr().err


def test_tokens_block_ambiguous_name_asks_for_a_token_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two live records can share an agent name (the raw operator ``issue``
    doesn't enforce the name-uniqueness the self-serve mint does); blocking by
    that name is refused in favour of an unambiguous token id."""
    store = InMemoryTokenStore()
    tokens.issue(store, agent_name="Dup", model="m", provider="p", owner_account_id="github:1")
    tokens.issue(store, agent_name="Dup", model="m", provider="p", owner_account_id="github:2")
    monkeypatch.setattr(_stores, "resolve_token_store", lambda: (store, False))

    rc = main(["tokens", "block", "Dup"])
    assert rc == 1
    assert "token id" in capsys.readouterr().err.lower()


def test_tokens_block_prefers_the_live_token_when_a_revoked_one_shares_the_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A revoked token frees the name for re-mint; resolving that name for a
    block prefers the single live record over the revoked one."""
    store = InMemoryTokenStore()
    old = tokens.issue(
        store, agent_name="Dup", model="m", provider="p", owner_account_id="github:1"
    )
    tokens.revoke(store, old.identity.token_id)
    live = tokens.issue(
        store, agent_name="Dup", model="m", provider="p", owner_account_id="github:1"
    )
    monkeypatch.setattr(_stores, "resolve_token_store", lambda: (store, False))

    rc = main(["tokens", "block", "Dup"])
    assert rc == 0
    by_id = {r.token_id: r.blocked for r in store.list_all()}
    assert by_id[live.identity.token_id] is True
    assert by_id[old.identity.token_id] is False
