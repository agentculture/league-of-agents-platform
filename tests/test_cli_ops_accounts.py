"""Tests for ``league-site accounts list|block|unblock``.

Blocking a human account is the account-level kill-switch: it denies *every*
agent token that account minted (enforced at ``verify`` time, see
``tests/test_api_blocking.py``) and refuses new mints. These verbs flip the
persisted ``blocked`` flag through the store; ``list`` shows blocked state.
Every dependency is monkeypatched via the ``_stores`` seam — no real AWS.
"""

from __future__ import annotations

import json

import pytest

from league_site.accounts.store import AccountRecord, InMemoryAccountStore
from league_site.cli import main
from league_site.cli._commands import _stores


def _store_with_account(account_id: str = "github:42") -> InMemoryAccountStore:
    store = InMemoryAccountStore()
    provider, _, subject = account_id.partition(":")
    store.upsert(
        AccountRecord(
            account_id=account_id,
            provider=provider,
            provider_user_id=subject,
            display_name="Ada",
            email=None,
        )
    )
    return store


# --- list --------------------------------------------------------------------


def test_accounts_list_json_shows_blocked_state(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _store_with_account("github:42")
    store.set_blocked("github:42", True)
    monkeypatch.setattr(_stores, "resolve_account_store", lambda: (store, False))

    rc = main(["accounts", "list", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["accounts"]) == 1
    row = payload["accounts"][0]
    assert row["account_id"] == "github:42"
    assert row["display_name"] == "Ada"
    assert row["blocked"] is True


def test_accounts_list_ephemeral_note(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(_stores, "resolve_account_store", lambda: (InMemoryAccountStore(), True))
    rc = main(["accounts", "list", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["accounts"] == []
    assert "TOKENS_TABLE_NAME" in payload["note"]


def test_accounts_list_text_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _store_with_account("github:42")
    monkeypatch.setattr(_stores, "resolve_account_store", lambda: (store, False))
    rc = main(["accounts", "list"])
    assert rc == 0
    assert "github:42" in capsys.readouterr().out


# --- block / unblock ---------------------------------------------------------


def test_accounts_block_flips_the_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _store_with_account("github:42")
    monkeypatch.setattr(_stores, "resolve_account_store", lambda: (store, False))

    rc = main(["accounts", "block", "github:42", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["account_id"] == "github:42"
    assert payload["blocked"] is True
    assert store.get("github:42").blocked is True


def test_accounts_block_then_unblock_round_trips(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _store_with_account("github:42")
    monkeypatch.setattr(_stores, "resolve_account_store", lambda: (store, False))

    assert main(["accounts", "block", "github:42"]) == 0
    assert store.get("github:42").blocked is True
    assert main(["accounts", "unblock", "github:42"]) == 0
    assert store.get("github:42").blocked is False


def test_accounts_block_unknown_id_is_a_user_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(_stores, "resolve_account_store", lambda: (InMemoryAccountStore(), False))
    rc = main(["accounts", "block", "github:nope"])
    assert rc == 1
    assert "github:nope" in capsys.readouterr().err
