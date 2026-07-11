"""API-boundary tests for task t4 blocking enforcement.

Bearer resolution turns a blocked token — and every token owned by a blocked
account — into a clean ``403 blocked``, while another account's tokens keep
working. The flip is read live: mutating the store the way the operator CLI
would (``set_blocked``) is visible on the very next request, with no process
restart in between (the app object is reused across both calls).
"""

from __future__ import annotations

from typing import Any

from league_site.accounts.store import (
    AccountRecord,
    InMemoryAccountStore,
    account_id_for,
)
from league_site.api.wsgi import with_api
from league_site.auth import tokens
from league_site.auth.token_store import InMemoryTokenStore
from league_site.matches import InMemoryMatchStore
from league_site.ratings.ledger import InMemoryRatingLedgerStore
from tests._api_support import bearer, call


def _passthrough(environ: dict[str, Any], start_response: Any) -> list[bytes]:
    start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
    return [b"passthrough"]


def _build(
    *,
    token_store: InMemoryTokenStore | None = None,
    account_store: InMemoryAccountStore | None = None,
) -> tuple[Any, InMemoryTokenStore, InMemoryAccountStore]:
    token_store = token_store if token_store is not None else InMemoryTokenStore()
    account_store = account_store if account_store is not None else InMemoryAccountStore()
    app = with_api(
        _passthrough,
        match_store=InMemoryMatchStore(),
        token_store=token_store,
        ledger_store=InMemoryRatingLedgerStore(),
        account_store=account_store,
    )
    return app, token_store, account_store


def _account(store: InMemoryAccountStore, account_id: str) -> str:
    provider, _, subject = account_id.partition(":")
    store.upsert(
        AccountRecord(
            account_id=account_id,
            provider=provider,
            provider_user_id=subject,
            display_name="Owner",
            email=None,
        )
    )
    return account_id


# --- token-level blocking ----------------------------------------------------


def test_blocked_token_is_a_403_at_the_api_boundary() -> None:
    app, token_store, _ = _build()
    issued = tokens.issue(
        token_store, agent_name="Sonnet", model="m", provider="p", owner_account_id="github:1"
    )
    token_store.set_blocked(issued.identity.token_id, True)

    status, _, payload = call(app, "POST", "/api/v1/matches", body={}, headers=bearer(issued.token))
    assert status == "403 Forbidden"
    assert payload["code"] == "blocked"
    assert "blocked" in payload["message"]


def test_token_block_flip_takes_effect_on_the_next_request_without_restart() -> None:
    app, token_store, _ = _build()
    issued = tokens.issue(
        token_store, agent_name="Sonnet", model="m", provider="p", owner_account_id="github:1"
    )
    # First request: works.
    status, _, _ = call(app, "POST", "/api/v1/matches", body={}, headers=bearer(issued.token))
    assert status == "201 Created"

    # Operator flips the flag through the store — no redeploy, no new app.
    token_store.set_blocked(issued.identity.token_id, True)

    status, _, payload = call(app, "POST", "/api/v1/matches", body={}, headers=bearer(issued.token))
    assert status == "403 Forbidden"
    assert payload["code"] == "blocked"

    # And unblocking is just as immediate.
    token_store.set_blocked(issued.identity.token_id, False)
    status, _, _ = call(app, "POST", "/api/v1/matches", body={}, headers=bearer(issued.token))
    assert status == "201 Created"


# --- account-level blocking --------------------------------------------------


def test_blocked_account_denies_all_its_tokens_while_another_account_still_works() -> None:
    app, token_store, account_store = _build()
    blocked_owner = _account(account_store, account_id_for("github", "42"))
    ok_owner = _account(account_store, account_id_for("github", "99"))
    tok_a = tokens.issue(
        token_store, agent_name="A", model="m", provider="p", owner_account_id=blocked_owner
    )
    tok_b = tokens.issue(
        token_store, agent_name="B", model="m", provider="p", owner_account_id=blocked_owner
    )
    tok_ok = tokens.issue(
        token_store, agent_name="C", model="m", provider="p", owner_account_id=ok_owner
    )

    account_store.set_blocked(blocked_owner, True)

    for tok in (tok_a, tok_b):
        status, _, payload = call(
            app, "POST", "/api/v1/matches", body={}, headers=bearer(tok.token)
        )
        assert status == "403 Forbidden"
        assert payload["code"] == "blocked"

    status, _, _ = call(app, "POST", "/api/v1/matches", body={}, headers=bearer(tok_ok.token))
    assert status == "201 Created"


def test_account_block_flip_is_visible_on_the_next_request() -> None:
    app, token_store, account_store = _build()
    owner = _account(account_store, account_id_for("github", "42"))
    issued = tokens.issue(
        token_store, agent_name="Sonnet", model="m", provider="p", owner_account_id=owner
    )
    status, _, _ = call(app, "POST", "/api/v1/matches", body={}, headers=bearer(issued.token))
    assert status == "201 Created"

    account_store.set_blocked(owner, True)

    status, _, payload = call(app, "POST", "/api/v1/matches", body={}, headers=bearer(issued.token))
    assert status == "403 Forbidden"
    assert payload["code"] == "blocked"


def test_without_an_account_store_token_level_blocking_still_works() -> None:
    """``with_api`` built without an account store still enforces token-level
    blocking; it just cannot enforce account-level blocking."""
    app = with_api(
        _passthrough,
        match_store=InMemoryMatchStore(),
        token_store=(store := InMemoryTokenStore()),
        ledger_store=InMemoryRatingLedgerStore(),
    )
    issued = tokens.issue(
        store, agent_name="Sonnet", model="m", provider="p", owner_account_id="github:1"
    )
    store.set_blocked(issued.identity.token_id, True)

    status, _, payload = call(app, "POST", "/api/v1/matches", body={}, headers=bearer(issued.token))
    assert status == "403 Forbidden"
    assert payload["code"] == "blocked"


# --- full site composition ---------------------------------------------------


def test_site_app_threads_the_account_store_into_the_api() -> None:
    """The composed site app hands the *same* account store the OAuth callback
    writes to down to ``with_api``, so an operator account-block is enforced on
    the ``/api/v1`` surface — proving the wiring in ``league_site.web.http``."""
    from league_site.web.http import site_app

    token_store = InMemoryTokenStore()
    account_store = InMemoryAccountStore()
    owner = _account(account_store, account_id_for("github", "42"))
    issued = tokens.issue(
        token_store, agent_name="Sonnet", model="m", provider="p", owner_account_id=owner
    )
    app = site_app(token_store=token_store, account_store=account_store)

    status, _, _ = call(app, "POST", "/api/v1/matches", body={}, headers=bearer(issued.token))
    assert status == "201 Created"

    account_store.set_blocked(owner, True)

    status, _, payload = call(app, "POST", "/api/v1/matches", body={}, headers=bearer(issued.token))
    assert status == "403 Forbidden"
    assert payload["code"] == "blocked"
