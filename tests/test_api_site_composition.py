"""Tests for the ``site_app()`` composition: auth + API + shell, together.

``tests/test_api_wsgi.py`` exercises :func:`~league_site.api.wsgi.with_api`
in isolation (identity injected straight into ``environ``); this module
proves the actual wiring in :func:`league_site.web.http.site_app` —
``with_shell(with_auth(with_api(http_app())))`` — end to end: an agent
bearer token and a *real*, signed human session cookie both authenticate
through the full composed app, the API's ``application/json`` responses
survive being wrapped in ``with_shell``, and every pre-existing passthrough
guarantee (``.md``, ``/llms.txt``, ``/front``, the rendered HTML shell, the
footer branding) still holds with the API mounted.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from league_site.api.engines import DEFAULT_MODE
from league_site.auth import sessions, tokens
from league_site.auth.token_store import InMemoryTokenStore
from league_site.auth.wsgi import SESSION_COOKIE_NAME
from league_site.matches import GameEngine, InMemoryMatchStore, Participant
from league_site.ratings.ledger import InMemoryRatingLedgerStore
from league_site.web.branding import FOOTER_HTML
from league_site.web.http import http_app, site_app
from league_site.web.shell import FOOTER_SLOTS
from tests._api_support import bearer, call


class _MalformedActionEngine(GameEngine):
    """Mirrors ``GridLaneEngine.apply_turn``'s contract (see
    ``league_site.game.adapter``): a turn's ``action`` must be a
    JSON-object-shaped mapping, or ``apply_turn`` raises ``TypeError`` --
    reproduces, without a real subprocess-backed game, the live crash a
    ``{"actions": [...]}`` body (missing the ``"action"`` wrapper) caused."""

    def __init__(self, *, game_id: str = "malformed-action-demo") -> None:
        self._game_id = game_id

    @property
    def game_id(self) -> str:
        return self._game_id

    def initial_state(self, participants: Sequence[Participant]) -> dict[str, Any]:
        return {"turns_taken": 0}

    def apply_turn(self, state: dict[str, Any], participant_id: str, action: Any) -> dict[str, Any]:
        if not isinstance(action, Mapping):
            raise TypeError(
                "apply_turn(action=...) must be a JSON-object-shaped mapping of league "
                "orders, e.g. {'actions': [...]}"
            )
        return {"turns_taken": state["turns_taken"] + 1}

    def is_over(self, state: dict[str, Any]) -> bool:
        return False

    def score(self, state: dict[str, Any]) -> dict[str, float]:
        return {}


@pytest.fixture(autouse=True)
def _oauth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEAGUE_SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LEAGUE_OAUTH_GITHUB_CLIENT_ID", "gh-client-id")
    monkeypatch.setenv("LEAGUE_OAUTH_GITHUB_CLIENT_SECRET", "gh-client-secret")


def _human_session_cookie(subject: str = "42", display_name: str = "Ada") -> dict[str, str]:
    token = sessions.issue({"subject": subject, "provider": "github", "display_name": display_name})
    return {"Cookie": f"{SESSION_COOKIE_NAME}={token}"}


# --- pre-existing guarantees, re-checked with the API mounted ----------------


def test_site_app_still_serves_index_md_byte_identical_to_http_app() -> None:
    unwrapped = http_app()
    composed = site_app()
    _, unwrapped_headers, unwrapped_body = call(unwrapped, "GET", "/index.md")
    _, composed_headers, composed_body = call(composed, "GET", "/index.md")
    assert composed_body == unwrapped_body
    assert composed_headers["Content-Type"] == unwrapped_headers["Content-Type"]


def test_site_app_still_leaves_llms_txt_and_front_unshelled() -> None:
    unwrapped = http_app()
    composed = site_app()
    for path in ("/llms.txt", "/front"):
        unwrapped_status, unwrapped_headers, unwrapped_body = call(unwrapped, "GET", path)
        composed_status, composed_headers, composed_body = call(composed, "GET", path)
        assert composed_status == unwrapped_status, path
        assert composed_body == unwrapped_body, path
        assert composed_headers["Content-Type"] == unwrapped_headers["Content-Type"], path


def test_site_app_still_renders_the_shelled_root_page() -> None:
    status, headers, body = call(site_app(), "GET", "/")
    assert status == "200 OK"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    assert "<!doctype html>" in text
    assert FOOTER_HTML in text


def test_site_app_still_registers_branding_without_duplicating_it() -> None:
    site_app()
    site_app()
    assert FOOTER_SLOTS.render().count(FOOTER_HTML) == 1


def test_site_app_still_serves_the_auth_login_redirect() -> None:
    status, headers, _ = call(site_app(), "GET", "/auth/login/github")
    assert status == "302 Found"
    assert "Location" in headers


# --- the API, reached through the full composition --------------------------


def test_api_response_through_site_app_is_not_shelled() -> None:
    """The API's JSON must survive ``with_shell`` untouched — it only shells
    ``text/markdown`` responses (see that module's docstring)."""
    app = site_app(
        match_store=InMemoryMatchStore(),
        token_store=InMemoryTokenStore(),
        ledger_store=InMemoryRatingLedgerStore(),
    )
    status, headers, payload = call(app, "GET", "/api/v1/leaderboard")
    assert status == "200 OK"
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert payload == {"leaderboard": []}


def test_agent_token_plays_a_full_match_through_the_composed_site_app() -> None:
    token_store = InMemoryTokenStore()
    match_store = InMemoryMatchStore()
    ledger_store = InMemoryRatingLedgerStore()
    app = site_app(match_store=match_store, token_store=token_store, ledger_store=ledger_store)

    issued = tokens.issue(
        token_store,
        agent_name="Sonnet",
        model="claude-sonnet-5",
        provider="anthropic",
        owner_account_id="github:sonnet-owner",
    )
    auth = {"headers": bearer(issued.token)}

    status, _, created = call(app, "POST", "/api/v1/matches", body={}, **auth)
    assert status == "201 Created"
    assert created["game_id"] == DEFAULT_MODE
    match_id = created["match_id"]

    status, _, payload = call(
        app,
        "POST",
        f"/api/v1/matches/{match_id}/turns",
        body={"action": {"points": 3}},
        **auth,
    )
    assert status == "200 OK"

    for _ in range(20):
        if payload["status"] == "completed":
            break
        status, _, payload = call(
            app,
            "POST",
            f"/api/v1/matches/{match_id}/turns",
            body={"action": {"points": 3}},
            **auth,
        )
        assert status == "200 OK"
    assert payload["status"] == "completed"

    status, _, score = call(app, "GET", f"/api/v1/matches/{match_id}/score")
    assert status == "200 OK"
    assert score["result"]["completed"] is True


def test_human_session_cookie_authenticates_through_the_composed_site_app() -> None:
    app = site_app(
        match_store=InMemoryMatchStore(),
        token_store=InMemoryTokenStore(),
        ledger_store=InMemoryRatingLedgerStore(),
    )
    cookie = _human_session_cookie(subject="cookie-human", display_name="Ada")

    status, _, created = call(app, "POST", "/api/v1/matches", body={}, headers=cookie)
    assert status == "201 Created"
    assert created["participants"][0]["kind"] == "human"
    assert created["participants"][0]["display_name"] == "Ada"

    match_id = created["match_id"]
    status, _, other_view = call(app, "GET", f"/api/v1/matches/{match_id}")
    assert status == "200 OK"
    assert other_view["match_id"] == match_id


def test_malformed_turn_body_through_the_composed_site_app_is_bad_request_not_a_500() -> None:
    """A live end-to-end session posting ``{"actions": [...]}`` (missing the
    ``"action"`` wrapper) crashed all the way through
    ``GridLaneEngine.apply_turn``'s ``TypeError`` to a raw, unhandled 500
    with an HTML-ish body. Through the *full* composed ``site_app`` --
    ``with_shell(with_auth(with_api(http_app())))`` -- it must instead be a
    structured 400 whose message keeps the engine's own descriptive text."""
    app = site_app(
        match_store=InMemoryMatchStore(),
        token_store=InMemoryTokenStore(),
        ledger_store=InMemoryRatingLedgerStore(),
        engine_registry={"malformed-action-demo": _MalformedActionEngine},
    )
    cookie = _human_session_cookie(subject="malformed-turn-human")
    status, _, created = call(
        app, "POST", "/api/v1/matches", body={"mode": "malformed-action-demo"}, headers=cookie
    )
    assert status == "201 Created"

    status, headers, payload = call(
        app,
        "POST",
        f"/api/v1/matches/{created['match_id']}/turns",
        body={"actions": [{"unit": "u1", "action": "move"}]},
        headers=cookie,
    )
    assert status == "400 Bad Request"
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert payload["code"] == "malformed_action"
    assert "JSON-object-shaped mapping" in payload["message"]


def test_non_participant_cannot_submit_turns_through_the_composed_site_app() -> None:
    app = site_app(
        match_store=InMemoryMatchStore(),
        token_store=InMemoryTokenStore(),
        ledger_store=InMemoryRatingLedgerStore(),
    )
    owner_cookie = _human_session_cookie(subject="owner", display_name="Ada")
    status, _, created = call(app, "POST", "/api/v1/matches", body={}, headers=owner_cookie)
    assert status == "201 Created"

    status, _, payload = call(
        app,
        "POST",
        f"/api/v1/matches/{created['match_id']}/turns",
        body={"action": {"points": 1}},
    )
    assert status == "403 Forbidden"
    assert payload["code"] == "forbidden"
