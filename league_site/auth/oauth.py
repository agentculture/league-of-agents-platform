"""Server-side OAuth 2.0 authorization-code flows for GitHub and Google.

Everything a WSGI login/callback route needs, minus the WSGI plumbing
itself (that lives in :mod:`league_site.auth.wsgi`):

* :data:`PROVIDERS` — the provider registry (authorize/token/userinfo URLs
  and scopes for ``github`` and ``google``).
* :func:`authorize_url` — builds the URL to redirect a human to, paired
  with a signed, stateless ``state`` token for CSRF protection
  (:func:`build_state` / :func:`verify_state`).
* :func:`complete_login` — exchanges an authorization ``code`` for a token
  and fetches+normalizes the provider's userinfo into an :class:`Identity`
  (``{provider, subject, handle, display_name, email}``), the shape every
  downstream consumer (sessions, the account upsert, and eventually match
  participants) reads. ``email`` may be ``None``; for GitHub the
  ``user:email`` scope plus a ``/user/emails`` fallback recovers a hidden
  address when it can (:func:`fetch_identity`).

All outbound HTTP goes through an injectable ``transport`` callable
(:data:`Transport`) rather than calling :mod:`urllib.request` directly, so
tests can stub provider responses and never touch the network — see
:func:`default_transport` for the one real implementation, only ever used
outside tests.

Client ids/secrets are read from environment variables named on
:class:`ProviderConfig` (``LEAGUE_OAUTH_GITHUB_CLIENT_ID``,
``LEAGUE_OAUTH_GITHUB_CLIENT_SECRET``, ``LEAGUE_OAUTH_GOOGLE_CLIENT_ID``,
``LEAGUE_OAUTH_GOOGLE_CLIENT_SECRET``); a missing var raises
:class:`OAuthConfigError` naming exactly which one.
"""

from __future__ import annotations

import json
import os
import secrets
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, TypedDict
from urllib.parse import urlencode

from league_site.auth._signing import read_secret, sign_payload, verify_payload

# The OAuth ``state`` token is a lightweight, self-verifying CSRF nonce, not
# a login credential — it reuses the same app-level signing secret as
# session tokens (see league_site.auth.sessions.SESSION_SECRET_ENV) rather
# than requiring operators to configure a second secret for it.
STATE_SECRET_ENV = "LEAGUE_SESSION_SECRET"  # nosec B105 - an env var *name*, not a credential
_STATE_TTL_SECONDS = 600  # 10 minutes: long enough for a human OAuth round trip

#: The ``Accept`` header value every outbound provider request sends, since
#: every endpoint this module talks to (token exchange, userinfo, GitHub's
#: emails list) replies in JSON.
_CONTENT_TYPE_JSON = "application/json"


class OAuthError(Exception):
    """Base class for OAuth flow errors (unknown provider, failed exchange, ...)."""


class OAuthConfigError(OAuthError):
    """Raised when a provider's client id/secret environment variable is unset."""

    def __init__(self, env_var: str) -> None:
        self.env_var = env_var
        super().__init__(
            f"missing required environment variable {env_var!r} "
            "(OAuth client credentials for league-of-agents-platform)"
        )


@dataclass(frozen=True)
class ProviderConfig:
    """Static configuration for one OAuth provider."""

    name: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scopes: tuple[str, ...]
    client_id_env: str
    client_secret_env: str


class Identity(TypedDict):
    """Normalized identity returned by :func:`fetch_identity` / :func:`complete_login`.

    ``email`` is ``None`` when the provider reports no usable address — GitHub
    omits it from the ``/user`` profile for humans who keep their email private
    (see :func:`_fetch_github_primary_email` for the ``user:email`` fallback
    that recovers it when it can). Downstream, the OAuth callback carries this
    straight onto the account it upserts (:class:`league_site.accounts.store.
    AccountRecord`), where an absent email is a valid, explicit state — never
    an error, and never a reason to fail a sign-in.
    """

    provider: str
    subject: str
    handle: str
    display_name: str
    email: str | None


@dataclass(frozen=True)
class HttpRequest:
    """A provider-bound HTTP request, passed to a :data:`Transport`."""

    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None = None


@dataclass(frozen=True)
class HttpResponse:
    """The response side of a :data:`Transport` call."""

    status: int
    body: bytes


Transport = Callable[[HttpRequest], HttpResponse]


def default_transport(request: HttpRequest) -> HttpResponse:
    """Perform *request* over the real network via :mod:`urllib.request`.

    Only used when a caller passes no explicit ``transport`` — production
    code's default. Every test in this project's suite injects a stub
    transport instead, so this function is never exercised in tests and
    never touches the network from CI. URLs only ever come from
    :data:`PROVIDERS` (fixed https endpoints baked into this module, never
    user input), so the request is safe despite bandit's blanket warning on
    ``urlopen``.
    """
    prepared = urllib.request.Request(
        request.url, data=request.body, headers=request.headers, method=request.method
    )
    with urllib.request.urlopen(prepared, timeout=10) as raw:  # nosec B310 - fixed provider URLs
        return HttpResponse(status=raw.status, body=raw.read())


def _clean_email(value: Any) -> str | None:
    """Return *value* as an email string, or ``None`` if it is not a usable one.

    Providers report a hidden/absent email as ``null`` (GitHub) or simply omit
    the key; both normalize to ``None`` here so every :class:`Identity` agrees
    that "no email" is exactly ``None``, never ``""`` or a stray non-string.
    """
    return value if isinstance(value, str) and value else None


def _normalize_github(data: dict[str, Any]) -> Identity:
    handle = str(data.get("login") or data["id"])
    return Identity(
        provider="github",
        subject=str(data["id"]),
        handle=handle,
        display_name=str(data.get("name") or handle),
        email=_clean_email(data.get("email")),
    )


def _normalize_google(data: dict[str, Any]) -> Identity:
    handle = str(data.get("email") or data["sub"])
    return Identity(
        provider="google",
        subject=str(data["sub"]),
        handle=handle,
        display_name=str(data.get("name") or handle),
        email=_clean_email(data.get("email")),
    )


#: GitHub's "list the authenticated user's email addresses" endpoint. Read
#: only when the ``/user`` profile hides the email (see
#: :func:`_fetch_github_primary_email`); reachable because the authorize
#: redirect requests the ``user:email`` scope (see the ``github`` provider's
#: ``scopes`` below).
_GITHUB_EMAILS_URL = "https://api.github.com/user/emails"

PROVIDERS: dict[str, ProviderConfig] = {
    "github": ProviderConfig(
        name="github",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",  # nosec B106 - a URL, not a secret
        userinfo_url="https://api.github.com/user",
        scopes=("read:user", "user:email"),
        client_id_env="LEAGUE_OAUTH_GITHUB_CLIENT_ID",
        client_secret_env="LEAGUE_OAUTH_GITHUB_CLIENT_SECRET",
    ),
    "google": ProviderConfig(
        name="google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",  # nosec B106 - a URL, not a secret
        userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
        scopes=("openid", "email", "profile"),
        client_id_env="LEAGUE_OAUTH_GOOGLE_CLIENT_ID",
        client_secret_env="LEAGUE_OAUTH_GOOGLE_CLIENT_SECRET",
    ),
}

_NORMALIZERS: dict[str, Callable[[dict[str, Any]], Identity]] = {
    "github": _normalize_github,
    "google": _normalize_google,
}


def get_provider(name: str) -> ProviderConfig:
    """Look up *name* in :data:`PROVIDERS`, raising :class:`OAuthError` if unknown."""
    try:
        return PROVIDERS[name]
    except KeyError as exc:
        known = sorted(PROVIDERS)
        raise OAuthError(f"unknown OAuth provider {name!r}; expected one of {known}") from exc


def _read_env(env_var: str) -> str:
    value = os.environ.get(env_var)
    if not value:
        raise OAuthConfigError(env_var)
    return value


def client_credentials(provider: ProviderConfig) -> tuple[str, str]:
    """Return ``(client_id, client_secret)`` for *provider* from the environment."""
    return _read_env(provider.client_id_env), _read_env(provider.client_secret_env)


def build_state(provider_name: str) -> str:
    """Build a signed, self-verifying CSRF ``state`` token for *provider_name*.

    Carries the provider name (so a state minted for one provider cannot be
    replayed against another's callback), a random nonce, and an
    issued-at timestamp checked by :func:`verify_state` against
    ``max_age_seconds``. No server-side storage is needed — the signature
    alone proves this app minted it.
    """
    get_provider(provider_name)  # raise early on an unknown provider
    payload = {
        "provider": provider_name,
        "nonce": secrets.token_urlsafe(16),
        "issued_at": int(time.time()),
    }
    return sign_payload(payload, read_secret(STATE_SECRET_ENV))


def verify_state(
    state: str, provider_name: str, *, max_age_seconds: int = _STATE_TTL_SECONDS
) -> bool:
    """Return ``True`` iff *state* is a signature-valid, fresh, matching-provider token.

    Rejects (returns ``False`` for): a bad/missing signature, a state
    minted for a different provider, and a state older than
    *max_age_seconds*.
    """
    payload = verify_payload(state, read_secret(STATE_SECRET_ENV))
    if payload is None:
        return False
    if payload.get("provider") != provider_name:
        return False
    issued_at = payload.get("issued_at")
    if not isinstance(issued_at, int):
        return False
    return 0 <= (int(time.time()) - issued_at) <= max_age_seconds


def authorize_url(provider_name: str, redirect_uri: str) -> tuple[str, str]:
    """Build the provider authorization URL and its paired ``state`` token.

    Returns ``(url, state)`` — the caller (the ``/auth/login/<provider>``
    route) redirects the browser to ``url``; the provider echoes ``state``
    back on the callback, verified there with :func:`verify_state`.
    """
    provider = get_provider(provider_name)
    client_id, _ = client_credentials(provider)
    state = build_state(provider_name)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(provider.scopes),
        "state": state,
    }
    return f"{provider.authorize_url}?{urlencode(params)}", state


def _parse_json_body(response: HttpResponse, *, provider_name: str, what: str) -> dict[str, Any]:
    try:
        data = json.loads(response.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise OAuthError(f"{provider_name}: {what} endpoint returned a non-JSON response") from exc
    if not isinstance(data, dict):
        raise OAuthError(f"{provider_name}: {what} endpoint returned a non-object JSON response")
    return data


def exchange_code(
    provider_name: str,
    code: str,
    redirect_uri: str,
    *,
    transport: Transport = default_transport,
) -> dict[str, Any]:
    """Exchange an authorization *code* for a token response via *transport*.

    Raises :class:`OAuthError` if the response is not JSON or carries no
    ``access_token``.
    """
    provider = get_provider(provider_name)
    client_id, client_secret = client_credentials(provider)
    body = urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode("utf-8")
    request = HttpRequest(
        method="POST",
        url=provider.token_url,
        headers={
            "Accept": _CONTENT_TYPE_JSON,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        body=body,
    )
    data = _parse_json_body(transport(request), provider_name=provider_name, what="token")
    if not data.get("access_token"):
        raise OAuthError(f"{provider_name}: token exchange did not return an access_token")
    return data


def _fetch_github_primary_email(access_token: str, *, transport: Transport) -> str | None:
    """Return the account's primary verified email from GitHub's /user/emails, or ``None``.

    GitHub omits ``email`` from the ``/user`` profile when the human keeps
    their address private in their profile settings; with the ``user:email``
    scope granted this endpoint still lists it. This is strictly best-effort
    and **never raises**: a hidden email must never fail a sign-in (the account
    simply carries ``email=None``), so a transport error, a non-JSON body, a
    body that is not a JSON list, or the absence of a primary *verified*
    address all resolve to ``None``.
    """
    request = HttpRequest(
        method="GET",
        url=_GITHUB_EMAILS_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": _CONTENT_TYPE_JSON},
    )
    try:
        # OSError covers urllib's URLError/HTTPError/timeouts from the real
        # transport; ValueError covers a non-JSON body (UnicodeDecodeError is a
        # ValueError). Any of them means "email not retrievable" -> None.
        data = json.loads(transport(request).body.decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    for entry in data:
        if (
            isinstance(entry, dict)
            and entry.get("primary")
            and entry.get("verified")
            and _clean_email(entry.get("email"))
        ):
            return str(entry["email"])
    return None


def fetch_identity(
    provider_name: str,
    access_token: str,
    *,
    transport: Transport = default_transport,
) -> Identity:
    """Fetch the provider's userinfo for *access_token* and normalize it to an :class:`Identity`.

    For GitHub, when the ``/user`` profile hides the email
    (``email is None``), fall back to
    :func:`_fetch_github_primary_email` (the ``/user/emails`` endpoint the
    ``user:email`` scope unlocks) to recover the primary verified address —
    still leaving ``email`` as ``None`` if none is retrievable, so the sign-in
    proceeds either way.
    """
    provider = get_provider(provider_name)
    request = HttpRequest(
        method="GET",
        url=provider.userinfo_url,
        headers={"Authorization": f"Bearer {access_token}", "Accept": _CONTENT_TYPE_JSON},
    )
    data = _parse_json_body(transport(request), provider_name=provider_name, what="userinfo")
    try:
        identity = _NORMALIZERS[provider_name](data)
    except KeyError as exc:
        raise OAuthError(f"{provider_name}: userinfo response missing {exc}") from exc
    if provider_name == "github" and identity["email"] is None:
        identity["email"] = _fetch_github_primary_email(access_token, transport=transport)
    return identity


def complete_login(
    provider_name: str,
    code: str,
    redirect_uri: str,
    *,
    transport: Transport = default_transport,
) -> Identity:
    """Run the full exchange: :func:`exchange_code` for a token, then :func:`fetch_identity`."""
    token_data = exchange_code(provider_name, code, redirect_uri, transport=transport)
    return fetch_identity(provider_name, token_data["access_token"], transport=transport)
