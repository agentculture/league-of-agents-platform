"""Tests for the ``/profiles/*`` mount inside :func:`league_site.web.http.site_app`.

``league_site.profiles.wsgi.profile_app`` is a standalone WSGI app (see its
own test suite, ``tests/test_profiles_wsgi.py``); this module proves the
*composition* seam: ``site_app()`` dispatches ``/profiles/*`` to it ahead of
the shell/API layers, sharing the exact same ``match_store``/``ledger_store``
instances the match API writes through — so a match completed through
``POST /api/v1/matches/*`` is immediately visible on the corresponding
``/profiles/<slug>`` page and badge, with no separate wiring/sync step.
"""

from __future__ import annotations

from league_site.api.engines import DEFAULT_MODE
from league_site.auth import tokens
from league_site.auth.token_store import InMemoryTokenStore
from league_site.matches import InMemoryMatchStore, ParticipantKind
from league_site.profiles.data import identity_slug
from league_site.ratings.ledger import InMemoryRatingLedgerStore
from league_site.ratings.system import RatingIdentity
from league_site.web.http import site_app
from tests._api_support import bearer, call

SONNET_IDENTITY = RatingIdentity(
    kind=ParticipantKind.AGENT,
    display_name="Sonnet",
    model="claude-sonnet-5",
    provider="anthropic",
)


def _play_to_completion(app, match_id: str, auth_a: dict, auth_b: dict) -> dict:
    """Alternate {"points": 3} turns between the two agents until the match completes.

    ``StubDuelEngine``'s default ``target=10``: two full a/b rounds of 3
    points each (12 total for the second mover) clears it.
    """
    turn_auth = [auth_a, auth_b]
    payload: dict = {}
    for i in range(10):
        auth = turn_auth[i % 2]
        status, _, payload = call(
            app,
            "POST",
            f"/api/v1/matches/{match_id}/turns",
            body={"action": {"points": 3}},
            **auth,
        )
        assert status == "200 OK", payload
        if payload["status"] == "completed":
            break
    assert payload["status"] == "completed"
    return payload


def test_completed_match_rating_is_visible_on_the_profile_page_and_badge_through_site_app() -> None:
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

    _play_to_completion(app, match_id, auth_sonnet, auth_rival)

    # The rating write happened through the API against `ledger_store` — the
    # very instance `site_app()` was constructed with — so it's directly
    # queryable here, and (the point of this test) also reachable through
    # `/profiles/*` on the very same composed `app`.
    standing = ledger_store.get(SONNET_IDENTITY)
    assert standing.match_count == 1

    slug = identity_slug(SONNET_IDENTITY)

    status, headers, body = call(app, "GET", f"/profiles/{slug}")
    assert status == "200 OK"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    assert f"Rating:</strong> {standing.rating}" in text

    status, headers, badge_body = call(app, "GET", f"/profiles/{slug}/badge.svg")
    assert status == "200 OK"
    assert headers["Content-Type"] == "image/svg+xml; charset=utf-8"
    badge_text = badge_body.decode("utf-8") if isinstance(badge_body, bytes) else badge_body
    assert str(standing.rating) in badge_text


def test_profiles_route_is_dispatched_ahead_of_the_shell_and_api_layers() -> None:
    """An unknown slug under ``/profiles/`` 404s straight from ``profile_app``
    (plain text, not the HTML shell and not an API JSON error envelope) —
    proof the dispatch happens before ``with_shell``/``with_api`` see it."""
    app = site_app(
        match_store=InMemoryMatchStore(),
        token_store=InMemoryTokenStore(),
        ledger_store=InMemoryRatingLedgerStore(),
    )
    status, headers, body = call(app, "GET", "/profiles/does-not-exist")
    assert status == "404 Not Found"
    assert headers["Content-Type"] == "text/plain; charset=utf-8"
    assert body == b"not found"


def test_site_app_shares_one_match_store_and_ledger_store_between_api_and_profiles_by_default() -> (
    None
):
    """A bare ``site_app()`` (no stores injected) must still wire the default
    in-memory stores it builds internally into *both* the API and the
    profiles mount — not one set each — or a match played against the
    default app would never show up on its own profile page."""
    token_store = InMemoryTokenStore()
    app = site_app(token_store=token_store)

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
            "opponent": {
                "kind": "agent",
                "display_name": "Rival",
                "agent_name": "Rival",
                "model": "gpt-4",
                "provider": "openai",
            }
        },
        **auth_sonnet,
    )
    assert status == "201 Created", created
    match_id = created["match_id"]

    _play_to_completion(app, match_id, auth_sonnet, auth_rival)

    slug = identity_slug(SONNET_IDENTITY)
    status, _, _ = call(app, "GET", f"/profiles/{slug}")
    assert status == "200 OK"
