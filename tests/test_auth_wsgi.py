"""Tests for league_site.auth.wsgi.with_auth: the /auth/* routes and pass-through.

These are the WSGI-level acceptance tests: a test user completes the full
GitHub flow and the full Google flow (stub transport, never the network)
and ends up with a session cookie that verifies and carries their
identity; an anonymous request to any non-/auth path is passed through to
the wrapped app completely unchanged.
"""

from __future__ import annotations

import json
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from league_site.accounts.store import InMemoryAccountStore, account_id_for
from league_site.auth import oauth, sessions
from league_site.auth.oauth import HttpRequest, HttpResponse
from league_site.auth.wsgi import SESSION_COOKIE_NAME, SESSION_ENVIRON_KEY, with_auth

GITHUB_USER = {"id": 42, "login": "octocat", "name": "The Octocat", "email": "octocat@example.com"}
GITHUB_USER_NO_EMAIL = {"id": 42, "login": "octocat", "name": "The Octocat", "email": None}
GOOGLE_USER = {"sub": "9999", "email": "octocat@example.com", "name": "The Octocat"}


@pytest.fixture(autouse=True)
def _oauth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEAGUE_SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LEAGUE_OAUTH_GITHUB_CLIENT_ID", "gh-client-id")
    monkeypatch.setenv("LEAGUE_OAUTH_GITHUB_CLIENT_SECRET", "gh-client-secret")
    monkeypatch.setenv("LEAGUE_OAUTH_GOOGLE_CLIENT_ID", "gg-client-id")
    monkeypatch.setenv("LEAGUE_OAUTH_GOOGLE_CLIENT_SECRET", "gg-client-secret")


def _inner_app(environ: dict[str, Any], start_response: Any) -> list[bytes]:
    """A trivial wrapped app: echoes the path and whatever session it sees."""
    session = environ.get(SESSION_ENVIRON_KEY)
    body = json.dumps(
        {"path": environ.get("PATH_INFO"), "session": session.display if session else None}
    ).encode("utf-8")
    start_response("200 OK", [("Content-Type", "application/json")])
    return [body]


def _call(
    app: Any, path: str, *, query: str = "", cookie: str | None = None
) -> tuple[str, dict[str, str], bytes]:
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ: dict[str, Any] = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "wsgi.url_scheme": "https",
        "HTTP_HOST": "league-of-agents.ai",
    }
    if cookie is not None:
        environ["HTTP_COOKIE"] = cookie
    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


def _stub_transport(token_payload: dict[str, object], userinfo_payload: dict[str, object]):
    def transport(request: HttpRequest) -> HttpResponse:
        if request.method == "POST":
            return HttpResponse(status=200, body=json.dumps(token_payload).encode("utf-8"))
        return HttpResponse(status=200, body=json.dumps(userinfo_payload).encode("utf-8"))

    return transport


def _stub_transport_with_emails(
    token_payload: dict[str, object],
    userinfo_payload: dict[str, object],
    emails_payload: object,
):
    """Like :func:`_stub_transport` but also answers GitHub's /user/emails."""

    def transport(request: HttpRequest) -> HttpResponse:
        if request.method == "POST":
            return HttpResponse(status=200, body=json.dumps(token_payload).encode("utf-8"))
        if request.url.endswith("/user/emails"):
            return HttpResponse(status=200, body=json.dumps(emails_payload).encode("utf-8"))
        return HttpResponse(status=200, body=json.dumps(userinfo_payload).encode("utf-8"))

    return transport


def _extract_cookie_value(set_cookie_header: str, name: str) -> str:
    cookie: SimpleCookie = SimpleCookie()
    cookie.load(set_cookie_header)
    return cookie[name].value


def _login_then_callback(
    app: Any, provider: str, transport, *, tamper_state: bool = False
) -> tuple[str, dict[str, str], bytes]:
    """Drive /auth/login/<provider> to mint a real state, then hit the callback with it."""
    status, headers, _ = _call(app, f"/auth/login/{provider}")
    assert status == "302 Found"
    location = headers["Location"]
    state = parse_qs(urlparse(location).query)["state"][0]
    if tamper_state:
        state = state + "tampered"
    return _call(app, f"/auth/callback/{provider}", query=f"code=the-code&state={state}")


# --- full provider flows (the acceptance-critical hook) -----------------------


def test_github_flow_completes_and_session_verifies() -> None:
    transport = _stub_transport({"access_token": "gh-token", "token_type": "bearer"}, GITHUB_USER)
    app = with_auth(_inner_app, transport=transport)

    status, headers, _ = _login_then_callback(app, "github", transport)

    assert status == "302 Found"
    assert headers["Location"] == "/"
    token = _extract_cookie_value(headers["Set-Cookie"], SESSION_COOKIE_NAME)

    session = sessions.verify(token)
    assert session is not None
    assert session.provider == "github"
    assert session.subject == "42"
    assert session.display == "The Octocat"

    # And the cookie actually authenticates a follow-up request through the app.
    _, _, body = _call(app, "/", cookie=f"{SESSION_COOKIE_NAME}={token}")
    assert json.loads(body)["session"] == "The Octocat"


def test_google_flow_completes_and_session_verifies() -> None:
    transport = _stub_transport({"access_token": "gg-token", "token_type": "bearer"}, GOOGLE_USER)
    app = with_auth(_inner_app, transport=transport)

    status, headers, _ = _login_then_callback(app, "google", transport)

    assert status == "302 Found"
    token = _extract_cookie_value(headers["Set-Cookie"], SESSION_COOKIE_NAME)

    session = sessions.verify(token)
    assert session is not None
    assert session.provider == "google"
    assert session.subject == "9999"
    assert session.display == "The Octocat"


# --- anonymous browsing preserved ----------------------------------------------


def test_anonymous_request_to_non_auth_path_passes_through_unchanged() -> None:
    bare_status, bare_headers, bare_body = _call(_inner_app, "/index")
    wrapped_status, wrapped_headers, wrapped_body = _call(with_auth(_inner_app), "/index")

    assert wrapped_status == bare_status
    assert wrapped_headers == bare_headers
    assert wrapped_body == bare_body
    assert json.loads(wrapped_body)["session"] is None


@pytest.mark.parametrize("path", ["/", "/index", "/matches/abc123", "/leaderboard"])
def test_various_public_paths_pass_through(path: str) -> None:
    status, _, body = _call(with_auth(_inner_app), path)
    assert status == "200 OK"
    assert json.loads(body)["path"] == path


# --- login route ------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["github", "google"])
def test_login_redirects_to_provider_authorize_url(provider: str) -> None:
    app = with_auth(_inner_app)
    status, headers, _ = _call(app, f"/auth/login/{provider}")
    assert status == "302 Found"
    assert headers["Location"].startswith(oauth.get_provider(provider).authorize_url)


def test_login_unknown_provider_returns_400() -> None:
    app = with_auth(_inner_app)
    status, _, _ = _call(app, "/auth/login/facebook")
    assert status == "400 Bad Request"  # known route shape, but provider config is unknown


def test_login_empty_provider_segment_404s() -> None:
    app = with_auth(_inner_app)
    status, _, _ = _call(app, "/auth/login/")
    assert status == "404 Not Found"


# --- callback / state validation -----------------------------------------------


def test_mismatched_state_is_rejected_on_callback() -> None:
    transport = _stub_transport({"access_token": "t", "token_type": "bearer"}, GITHUB_USER)
    app = with_auth(_inner_app, transport=transport)

    # A state minted for google must not be accepted on the github callback.
    _, headers, _ = _call(app, "/auth/login/google")
    google_state = parse_qs(urlparse(headers["Location"]).query)["state"][0]

    status, _, body = _call(
        app, "/auth/callback/github", query=f"code=the-code&state={google_state}"
    )
    assert status == "400 Bad Request"
    assert b"invalid or expired OAuth state" in body


def test_tampered_state_is_rejected_on_callback() -> None:
    transport = _stub_transport({"access_token": "t", "token_type": "bearer"}, GITHUB_USER)
    app = with_auth(_inner_app, transport=transport)

    status, _, body = _login_then_callback(app, "github", transport, tamper_state=True)
    assert status == "400 Bad Request"
    assert b"invalid or expired OAuth state" in body


def test_callback_missing_code_is_rejected() -> None:
    app = with_auth(_inner_app)
    _, headers, _ = _call(app, "/auth/login/github")
    state = parse_qs(urlparse(headers["Location"]).query)["state"][0]
    status, _, _ = _call(app, "/auth/callback/github", query=f"state={state}")
    assert status == "400 Bad Request"


def test_callback_empty_provider_segment_404s() -> None:
    app = with_auth(_inner_app)
    status, _, _ = _call(app, "/auth/callback/")
    assert status == "404 Not Found"


def test_callback_surfaces_oauth_error_from_failed_exchange() -> None:
    """A valid state but a provider that refuses the code (no access_token) is a 400, not a 500."""

    def failing_transport(request: HttpRequest) -> HttpResponse:
        return HttpResponse(
            status=200, body=json.dumps({"error": "bad_verification_code"}).encode()
        )

    app = with_auth(_inner_app, transport=failing_transport)
    status, _, body = _login_then_callback(app, "github", failing_transport)
    assert status == "400 Bad Request"
    assert b"did not return an access_token" in body


# --- logout ------------------------------------------------------------------------


def test_logout_clears_session_cookie() -> None:
    app = with_auth(_inner_app)
    status, headers, _ = _call(app, "/auth/logout")
    assert status == "302 Found"
    assert headers["Location"] == "/"
    cookie: SimpleCookie = SimpleCookie()
    cookie.load(headers["Set-Cookie"])
    morsel = cookie[SESSION_COOKIE_NAME]
    assert morsel.value == ""
    assert morsel["max-age"] == "0"


def test_tampered_cookie_is_treated_as_anonymous() -> None:
    app = with_auth(_inner_app)
    _, _, body = _call(app, "/", cookie=f"{SESSION_COOKIE_NAME}=not-a-real-token")
    assert json.loads(body)["session"] is None


def test_unrelated_cookie_is_treated_as_anonymous() -> None:
    app = with_auth(_inner_app)
    _, _, body = _call(app, "/", cookie="some_other_cookie=hello")
    assert json.loads(body)["session"] is None


# --- account upsert at callback + session<->account linkage --------------------


def _session_from_headers(headers: dict[str, str]) -> sessions.Session:
    token = _extract_cookie_value(headers["Set-Cookie"], SESSION_COOKIE_NAME)
    session = sessions.verify(token)
    assert session is not None
    return session


def test_github_callback_upserts_account_and_session_resolves_it() -> None:
    """After a GitHub sign-in the account exists in the store, and the key the
    session resolves to (``session.account_id``) is exactly the one that
    ``get()`` returns the freshly-upserted record under."""
    store = InMemoryAccountStore()
    transport = _stub_transport({"access_token": "gh-token", "token_type": "bearer"}, GITHUB_USER)
    app = with_auth(_inner_app, transport=transport, account_store=store)

    status, headers, _ = _login_then_callback(app, "github", transport)
    assert status == "302 Found"

    session = _session_from_headers(headers)
    assert session.account_id == account_id_for("github", "42")

    account = store.get(session.account_id)
    assert account is not None
    assert account.account_id == "github:42"
    assert account.provider == "github"
    assert account.provider_user_id == "42"
    assert account.display_name == "The Octocat"
    assert account.email == "octocat@example.com"
    assert account.blocked is False


def test_github_callback_fetches_hidden_email_into_the_account() -> None:
    """A profile that hides its email still lands an account carrying the
    primary verified address pulled from /user/emails."""
    store = InMemoryAccountStore()
    emails = [
        {"email": "hidden-primary@example.com", "primary": True, "verified": True},
        {"email": "other@example.com", "primary": False, "verified": True},
    ]
    transport = _stub_transport_with_emails(
        {"access_token": "gh-token", "token_type": "bearer"}, GITHUB_USER_NO_EMAIL, emails
    )
    app = with_auth(_inner_app, transport=transport, account_store=store)

    status, headers, _ = _login_then_callback(app, "github", transport)
    assert status == "302 Found"

    account = store.get(_session_from_headers(headers).account_id)
    assert account is not None
    assert account.email == "hidden-primary@example.com"


def test_github_callback_account_email_none_when_unretrievable_and_sign_in_succeeds() -> None:
    """No retrievable email -> the account is created with email=None and the
    sign-in still completes (302 + a verifying session)."""
    store = InMemoryAccountStore()
    transport = _stub_transport_with_emails(
        {"access_token": "gh-token", "token_type": "bearer"},
        GITHUB_USER_NO_EMAIL,
        [],  # /user/emails returns an empty list: nothing to store
    )
    app = with_auth(_inner_app, transport=transport, account_store=store)

    status, headers, _ = _login_then_callback(app, "github", transport)
    assert status == "302 Found"

    account = store.get(_session_from_headers(headers).account_id)
    assert account is not None
    assert account.email is None


def test_second_sign_in_preserves_created_at_and_a_pre_set_block() -> None:
    """Re-signing in is an idempotent upsert: display/email refresh, but the
    original ``created_at`` and an operator-set ``blocked`` are preserved."""
    store = InMemoryAccountStore()

    first = _stub_transport(
        {"access_token": "gh-token", "token_type": "bearer"},
        {"id": 42, "login": "octocat", "name": "The Octocat", "email": "old@example.com"},
    )
    app = with_auth(_inner_app, transport=first, account_store=store)
    _, headers, _ = _login_then_callback(app, "github", first)
    account_id = _session_from_headers(headers).account_id
    created_at = store.get(account_id).created_at

    # An operator blocks the account between sign-ins.
    store.set_blocked(account_id, True)

    # A second sign-in with a changed display name and email.
    second = _stub_transport(
        {"access_token": "gh-token-2", "token_type": "bearer"},
        {"id": 42, "login": "octocat", "name": "Octocat Prime", "email": "new@example.com"},
    )
    app2 = with_auth(_inner_app, transport=second, account_store=store)
    status, _, _ = _login_then_callback(app2, "github", second)
    assert status == "302 Found"

    account = store.get(account_id)
    assert account is not None
    assert account.display_name == "Octocat Prime"  # refreshed
    assert account.email == "new@example.com"  # refreshed
    assert account.created_at == created_at  # preserved
    assert account.blocked is True  # a re-sign-in never silently unblocks


def test_callback_without_account_store_still_signs_in() -> None:
    """Backward-compatible: with no account store wired, the callback still
    issues a session (the account upsert is simply skipped)."""
    transport = _stub_transport({"access_token": "gh-token", "token_type": "bearer"}, GITHUB_USER)
    app = with_auth(_inner_app, transport=transport)  # account_store defaults to None

    status, headers, _ = _login_then_callback(app, "github", transport)
    assert status == "302 Found"
    assert _session_from_headers(headers).provider == "github"
