"""Tests for league_site.auth.oauth: provider registry, state, code exchange, userinfo.

Every network-shaped call in this suite goes through a stub transport — no
test here ever imports or exercises :func:`league_site.auth.oauth.default_transport`.
"""

from __future__ import annotations

import inspect
import json
from urllib.parse import parse_qs, urlparse

import pytest

from league_site.auth import oauth
from league_site.auth._signing import MissingSecretError, read_secret, sign_payload
from league_site.auth.oauth import (
    HttpRequest,
    HttpResponse,
    OAuthConfigError,
    OAuthError,
    authorize_url,
    build_state,
    client_credentials,
    complete_login,
    exchange_code,
    fetch_identity,
    get_provider,
    verify_state,
)

GITHUB_USER = {"id": 42, "login": "octocat", "name": "The Octocat", "email": "octocat@example.com"}
#: A GitHub profile that hides its email (the /user endpoint returns ``email:
#: null`` when the human keeps their address private) — the trigger for the
#: /user/emails fallback :func:`fetch_identity` performs.
GITHUB_USER_NO_EMAIL = {"id": 42, "login": "octocat", "name": "The Octocat", "email": None}
GOOGLE_USER = {"sub": "9999", "email": "octocat@example.com", "name": "The Octocat"}


@pytest.fixture(autouse=True)
def _oauth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEAGUE_SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LEAGUE_OAUTH_GITHUB_CLIENT_ID", "gh-client-id")
    monkeypatch.setenv("LEAGUE_OAUTH_GITHUB_CLIENT_SECRET", "gh-client-secret")
    monkeypatch.setenv("LEAGUE_OAUTH_GOOGLE_CLIENT_ID", "gg-client-id")
    monkeypatch.setenv("LEAGUE_OAUTH_GOOGLE_CLIENT_SECRET", "gg-client-secret")


def _json_response(payload: dict[str, object], status: int = 200) -> HttpResponse:
    return HttpResponse(status=status, body=json.dumps(payload).encode("utf-8"))


def _stub_transport(token_payload: dict[str, object], userinfo_payload: dict[str, object]):
    """Build a stub transport that answers the token endpoint then the userinfo endpoint."""
    calls: list[HttpRequest] = []

    def transport(request: HttpRequest) -> HttpResponse:
        calls.append(request)
        if request.method == "POST":
            return _json_response(token_payload)
        return _json_response(userinfo_payload)

    transport.calls = calls  # type: ignore[attr-defined]
    return transport


def _stub_transport_with_emails(
    token_payload: dict[str, object],
    userinfo_payload: dict[str, object],
    emails_response: HttpResponse,
):
    """Stub routing token -> userinfo -> the GitHub ``/user/emails`` fallback.

    ``emails_response`` is returned verbatim for a GET to ``/user/emails`` so a
    test can hand back a list, a non-list object, or an outright junk body to
    exercise the fallback's tolerance.
    """
    calls: list[HttpRequest] = []

    def transport(request: HttpRequest) -> HttpResponse:
        calls.append(request)
        if request.method == "POST":
            return _json_response(token_payload)
        if request.url.endswith("/user/emails"):
            return emails_response
        return _json_response(userinfo_payload)

    transport.calls = calls  # type: ignore[attr-defined]
    return transport


# --- provider registry -----------------------------------------------------


def test_known_providers_are_github_and_google() -> None:
    assert get_provider("github").name == "github"
    assert get_provider("google").name == "google"


def test_unknown_provider_raises() -> None:
    with pytest.raises(OAuthError, match="unknown OAuth provider"):
        get_provider("facebook")


# --- config / env vars -------------------------------------------------------


def test_missing_client_id_env_var_raises_naming_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEAGUE_OAUTH_GITHUB_CLIENT_ID", raising=False)
    with pytest.raises(OAuthConfigError) as excinfo:
        client_credentials(get_provider("github"))
    assert excinfo.value.env_var == "LEAGUE_OAUTH_GITHUB_CLIENT_ID"
    assert "LEAGUE_OAUTH_GITHUB_CLIENT_ID" in str(excinfo.value)


def test_missing_client_secret_env_var_raises_naming_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEAGUE_OAUTH_GOOGLE_CLIENT_SECRET", raising=False)
    with pytest.raises(OAuthConfigError) as excinfo:
        client_credentials(get_provider("google"))
    assert excinfo.value.env_var == "LEAGUE_OAUTH_GOOGLE_CLIENT_SECRET"


# --- state (CSRF) ------------------------------------------------------------


def test_state_round_trips_for_matching_provider() -> None:
    state = build_state("github")
    assert verify_state(state, "github") is True


def test_state_rejected_for_mismatched_provider() -> None:
    state = build_state("github")
    assert verify_state(state, "google") is False


def test_state_rejected_when_tampered() -> None:
    state = build_state("github")
    assert verify_state(state + "tampered", "github") is False


def test_state_rejected_when_expired() -> None:
    state = build_state("github")
    assert verify_state(state, "github", max_age_seconds=-1) is False


def test_state_missing_session_secret_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEAGUE_SESSION_SECRET", raising=False)
    with pytest.raises(MissingSecretError):
        build_state("github")


def test_state_rejected_when_issued_at_is_not_an_int() -> None:
    forged = sign_payload(
        {"provider": "github", "nonce": "x", "issued_at": "not-a-number"},
        read_secret("LEAGUE_SESSION_SECRET"),
    )
    assert verify_state(forged, "github") is False


# --- authorize_url -------------------------------------------------------------


@pytest.mark.parametrize("provider_name", ["github", "google"])
def test_authorize_url_includes_client_id_redirect_and_state(provider_name: str) -> None:
    url, state = authorize_url(
        provider_name, "https://league-of-agents.ai/auth/callback/" + provider_name
    )
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    provider = get_provider(provider_name)
    assert url.startswith(provider.authorize_url)
    assert params["response_type"] == ["code"]
    assert params["redirect_uri"] == [f"https://league-of-agents.ai/auth/callback/{provider_name}"]
    assert params["state"] == [state]
    assert params["scope"] == [" ".join(provider.scopes)]
    assert verify_state(state, provider_name) is True


# --- exchange_code / fetch_identity / complete_login --------------------------


def test_github_flow_normalizes_identity() -> None:
    transport = _stub_transport(
        token_payload={"access_token": "gh-token-abc", "token_type": "bearer"},
        userinfo_payload=GITHUB_USER,
    )
    identity = complete_login(
        "github",
        "the-code",
        "https://league-of-agents.ai/auth/callback/github",
        transport=transport,
    )
    assert identity == {
        "provider": "github",
        "subject": "42",
        "handle": "octocat",
        "display_name": "The Octocat",
        "email": "octocat@example.com",
    }
    # A profile that already carries an email needs no /user/emails fallback:
    # token exchange, then userinfo — exactly two calls, each with the right auth.
    token_request, userinfo_request = transport.calls  # type: ignore[attr-defined]
    assert token_request.method == "POST"
    assert userinfo_request.headers["Authorization"] == "Bearer gh-token-abc"


def test_google_flow_normalizes_identity() -> None:
    transport = _stub_transport(
        token_payload={"access_token": "gg-token-xyz", "token_type": "bearer"},
        userinfo_payload=GOOGLE_USER,
    )
    identity = complete_login(
        "google",
        "the-code",
        "https://league-of-agents.ai/auth/callback/google",
        transport=transport,
    )
    assert identity == {
        "provider": "google",
        "subject": "9999",
        "handle": "octocat@example.com",
        "display_name": "The Octocat",
        "email": "octocat@example.com",
    }


def test_exchange_code_raises_when_access_token_missing() -> None:
    transport = _stub_transport(
        token_payload={"error": "bad_verification_code"}, userinfo_payload={}
    )
    with pytest.raises(OAuthError, match="did not return an access_token"):
        exchange_code("github", "bad-code", "https://x/callback", transport=transport)


def test_exchange_code_raises_on_non_json_response() -> None:
    def transport(request: HttpRequest) -> HttpResponse:
        return HttpResponse(status=200, body=b"not json")

    with pytest.raises(OAuthError, match="non-JSON response"):
        exchange_code("github", "code", "https://x/callback", transport=transport)


def test_exchange_code_raises_on_non_object_json_response() -> None:
    def transport(request: HttpRequest) -> HttpResponse:
        return HttpResponse(status=200, body=b"[1, 2, 3]")

    with pytest.raises(OAuthError, match="non-object JSON response"):
        exchange_code("github", "code", "https://x/callback", transport=transport)


def test_fetch_identity_raises_when_required_field_missing() -> None:
    def transport(request: HttpRequest) -> HttpResponse:
        return _json_response({"login": "octocat"})  # missing "id"

    with pytest.raises(OAuthError, match="userinfo response missing"):
        fetch_identity("github", "token", transport=transport)


def test_authorize_url_requests_user_email_scope_for_github() -> None:
    """The GitHub authorize redirect must ask for ``user:email`` — the scope
    that lets the callback read a private/primary email from /user/emails."""
    url, _state = authorize_url("github", "https://league-of-agents.ai/auth/callback/github")
    scope = parse_qs(urlparse(url).query)["scope"][0].split(" ")
    assert "user:email" in scope


def test_github_hidden_email_is_fetched_from_user_emails() -> None:
    """A profile with ``email: null`` triggers the /user/emails fallback, which
    returns the primary verified address — bearer-authed, at the emails URL."""
    emails = [
        {"email": "secondary@example.com", "primary": False, "verified": True},
        {"email": "primary@example.com", "primary": True, "verified": True},
    ]
    transport = _stub_transport_with_emails(
        token_payload={"access_token": "gh-token-abc", "token_type": "bearer"},
        userinfo_payload=GITHUB_USER_NO_EMAIL,
        emails_response=_json_response(emails),  # type: ignore[arg-type]
    )
    identity = complete_login(
        "github", "code", "https://league-of-agents.ai/auth/callback/github", transport=transport
    )
    assert identity["email"] == "primary@example.com"
    # token exchange, userinfo, then the emails fallback — three calls in order.
    token_request, userinfo_request, emails_request = transport.calls  # type: ignore[attr-defined]
    assert token_request.method == "POST"
    assert userinfo_request.url.endswith("/user")
    assert emails_request.url.endswith("/user/emails")
    assert emails_request.headers["Authorization"] == "Bearer gh-token-abc"


def test_github_hidden_email_absent_when_no_primary_verified_address() -> None:
    """No *primary verified* address is retrievable -> email stays absent
    (None), and the sign-in still succeeds — a hidden email never fails it."""
    emails = [
        {"email": "unverified@example.com", "primary": True, "verified": False},
        {"email": "verified-not-primary@example.com", "primary": False, "verified": True},
    ]
    transport = _stub_transport_with_emails(
        token_payload={"access_token": "t", "token_type": "bearer"},
        userinfo_payload=GITHUB_USER_NO_EMAIL,
        emails_response=_json_response(emails),  # type: ignore[arg-type]
    )
    identity = fetch_identity("github", "t", transport=transport)
    assert identity["email"] is None
    assert identity["subject"] == "42"


def test_github_hidden_email_absent_when_emails_endpoint_returns_non_list() -> None:
    """A junk/error /user/emails body (not a JSON list) is tolerated: email
    absent, no exception — the sign-in must not fail over the fallback."""
    transport = _stub_transport_with_emails(
        token_payload={"access_token": "t", "token_type": "bearer"},
        userinfo_payload=GITHUB_USER_NO_EMAIL,
        emails_response=HttpResponse(status=403, body=b'{"message": "Requires authentication"}'),
    )
    identity = fetch_identity("github", "t", transport=transport)
    assert identity["email"] is None


def test_github_hidden_email_absent_when_emails_endpoint_body_is_not_json() -> None:
    transport = _stub_transport_with_emails(
        token_payload={"access_token": "t", "token_type": "bearer"},
        userinfo_payload=GITHUB_USER_NO_EMAIL,
        emails_response=HttpResponse(status=200, body=b"not json at all"),
    )
    identity = fetch_identity("github", "t", transport=transport)
    assert identity["email"] is None


def test_github_hidden_email_survives_a_raising_transport() -> None:
    """If the emails call itself raises (a network blip), sign-in still
    completes with email absent rather than 500-ing the callback."""

    def transport(request: HttpRequest) -> HttpResponse:
        if request.url.endswith("/user/emails"):
            raise OSError("connection reset")
        return _json_response(GITHUB_USER_NO_EMAIL)

    identity = fetch_identity("github", "t", transport=transport)
    assert identity["email"] is None


def test_github_profile_with_email_never_calls_user_emails() -> None:
    """When the profile already carries an email, the fallback is skipped
    entirely — no second GET to /user/emails."""
    transport = _stub_transport(
        token_payload={"access_token": "t", "token_type": "bearer"},
        userinfo_payload=GITHUB_USER,
    )
    identity = complete_login("github", "code", "https://x/callback", transport=transport)
    calls = transport.calls  # type: ignore[attr-defined]
    assert identity["email"] == "octocat@example.com"
    assert len(calls) == 2
    assert all(not c.url.endswith("/user/emails") for c in calls)


def test_default_transport_is_the_default_for_exchange_and_fetch() -> None:
    # Not called (no test hits the network) - just documents the wiring.
    assert (
        inspect.signature(exchange_code).parameters["transport"].default is oauth.default_transport
    )
    assert (
        inspect.signature(fetch_identity).parameters["transport"].default is oauth.default_transport
    )
