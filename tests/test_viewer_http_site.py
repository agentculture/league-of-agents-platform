"""Tests for the ``/matches/<id>/watch`` mount inside :func:`league_site.web.http.site_app`.

``league_site.viewer.wsgi.viewer_app`` is a standalone WSGI app (see its own
test suite, ``tests/test_viewer_wsgi.py``); this module proves the
*composition* seam: ``site_app()`` dispatches ``/matches/<id>/watch`` to it
ahead of the shell/auth/API stack, sharing the exact same
``match_store``/``ledger_store`` instances the match API writes through — so
a match created and played through ``POST /api/v1/matches/*`` is immediately
watchable at its own ``/matches/<id>/watch`` page on the same app, and
``/api/v1/matches/...`` keeps routing to the JSON API unaffected.
"""

from __future__ import annotations

from league_site.api.engines import DEFAULT_MODE
from league_site.auth import tokens
from league_site.auth.token_store import InMemoryTokenStore
from league_site.matches import InMemoryMatchStore
from league_site.ratings.ledger import InMemoryRatingLedgerStore
from league_site.web.http import site_app
from tests._api_support import bearer, call


def test_watch_route_is_dispatched_ahead_of_the_shell_and_api_layers() -> None:
    """An unknown match id under ``/matches/*/watch`` 404s straight from
    ``viewer_app`` (plain HTML, not the doc shell and not an API JSON error
    envelope) -- proof the dispatch happens before
    ``with_shell``/``with_auth``/``with_api``."""
    app = site_app(
        match_store=InMemoryMatchStore(),
        token_store=InMemoryTokenStore(),
        ledger_store=InMemoryRatingLedgerStore(),
    )
    status, headers, body = call(app, "GET", "/matches/does-not-exist/watch")
    assert status == "404 Not Found"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    assert "<!doctype html>" in text


def test_api_match_routes_are_unaffected_by_the_watch_mount() -> None:
    """``/api/v1/matches/...`` is a distinct prefix from ``/matches/.../watch``
    and must keep returning the JSON API's own 404 envelope."""
    app = site_app(
        match_store=InMemoryMatchStore(),
        token_store=InMemoryTokenStore(),
        ledger_store=InMemoryRatingLedgerStore(),
    )
    status, headers, payload = call(app, "GET", "/api/v1/matches/does-not-exist")
    assert status == "404 Not Found"
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert payload["code"] == "not_found"


def test_match_created_and_played_through_the_api_is_watchable_on_the_same_app() -> None:
    token_store = InMemoryTokenStore()
    match_store = InMemoryMatchStore()
    ledger_store = InMemoryRatingLedgerStore()
    app = site_app(match_store=match_store, token_store=token_store, ledger_store=ledger_store)

    issued_sonnet = tokens.issue(
        token_store, agent_name="Sonnet", model="claude-sonnet-5", provider="anthropic"
    )
    issued_rival = tokens.issue(token_store, agent_name="Rival", model="gpt-4", provider="openai")
    auth_sonnet = {"headers": bearer(issued_sonnet.token)}
    auth_rival = {"headers": bearer(issued_rival.token)}

    status, _, created = call(
        app,
        "POST",
        "/api/v1/matches",
        body={
            "mode": DEFAULT_MODE,
            "opponent": {
                "kind": "agent",
                "display_name": "Rival",
                "agent_name": "Rival",
                "model": "gpt-4",
                "provider": "openai",
            },
        },
        **auth_sonnet,
    )
    assert status == "201 Created", created
    match_id = created["match_id"]

    status, _, after_turn = call(
        app,
        "POST",
        f"/api/v1/matches/{match_id}/turns",
        body={"action": {"points": 2}},
        **auth_sonnet,
    )
    assert status == "200 OK", after_turn

    status, headers, body = call(app, "GET", f"/matches/{match_id}/watch")
    assert status == "200 OK"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    assert "Sonnet" in text
    assert "Rival" in text
    assert "turn 1" in text
    # The match hasn't reached target/max_turns yet -- still live.
    assert '<meta http-equiv="refresh" content="5">' in text

    # Second agent's turn -- proves each subsequent GET reflects the store's
    # current state, same as the standalone viewer_app test.
    status, _, second_turn = call(
        app,
        "POST",
        f"/api/v1/matches/{match_id}/turns",
        body={"action": {"points": 3}},
        **auth_rival,
    )
    assert status == "200 OK", second_turn

    _, _, body_after = call(app, "GET", f"/matches/{match_id}/watch")
    text_after = body_after.decode("utf-8") if isinstance(body_after, bytes) else body_after
    assert "turn 2" in text_after


def test_bare_site_app_shares_one_match_store_between_api_and_viewer_by_default() -> None:
    """A bare ``site_app()`` (no stores injected) must still wire the default
    in-memory match store it builds internally into *both* the API and the
    viewer mount -- not one each -- or a match created against the default
    app would never be watchable on its own page."""
    token_store = InMemoryTokenStore()
    app = site_app(token_store=token_store)

    issued = tokens.issue(
        token_store, agent_name="Solo", model="claude-sonnet-5", provider="anthropic"
    )
    auth = {"headers": bearer(issued.token)}

    status, _, created = call(app, "POST", "/api/v1/matches", body={"mode": DEFAULT_MODE}, **auth)
    assert status == "201 Created", created
    match_id = created["match_id"]

    status, _, _ = call(app, "GET", f"/matches/{match_id}/watch")
    assert status == "200 OK"
