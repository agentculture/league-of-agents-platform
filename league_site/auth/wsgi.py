"""WSGI wrapper adding auth routes — OAuth login and agent token minting — on top of any app.

:func:`with_auth` adds four routes — ``/auth/login/<provider>``,
``/auth/callback/<provider>``, ``/auth/logout``, ``/auth/agents`` — and
passes every other path straight through to the wrapped app *unchanged*, so
anonymous browsing/spectating keeps working with zero behavior change. On
every request (auth routes and pass-through alike) the wrapper reads the
session cookie, verifies it via :mod:`league_site.auth.sessions`, and stores
the result (a :class:`~league_site.auth.sessions.Session` or ``None``) at
``environ[SESSION_ENVIRON_KEY]`` so a wrapped app can read who's asking
without reimplementing cookie parsing or signature verification itself.

Route behavior:

* ``GET /auth/login/<provider>`` — 302 redirect to the provider's
  authorization URL (:func:`league_site.auth.oauth.authorize_url`).
* ``GET /auth/callback/<provider>?code=...&state=...`` — verifies
  ``state``, exchanges ``code`` for the provider's identity
  (:func:`league_site.auth.oauth.complete_login`), issues a session
  (:func:`league_site.auth.sessions.issue`), sets it as a cookie, and
  redirects to ``/``.
* ``GET /auth/logout`` — clears the session cookie and redirects to ``/``.
* ``POST /auth/agents`` — self-serve agent token onboarding. JSON body
  ``{"name", "model", "provider"}`` in; ``201 {"token": "loa_...",
  "identity": "agent:..."}`` out — the only moment the plaintext token is
  ever shown (see :mod:`league_site.auth.tokens`). Guarded by
  :func:`~league_site.auth.tokens.issue_self_serve`: a name that already
  has a live token is a ``409 name_taken``, and minting past the rolling
  hourly cap is a ``429 issue_cap_exceeded``. Requires a ``token_store``
  to have been injected into :func:`with_auth` — the *same* store the
  ``/api/v1`` layer verifies against (see
  :func:`league_site.web.http.site_app`) — otherwise it answers
  ``503 not_configured`` rather than minting tokens nothing would accept.
"""

from __future__ import annotations

import json
from datetime import datetime
from http.cookies import SimpleCookie
from typing import Any, Callable
from urllib.parse import parse_qs

from league_site.auth import oauth, sessions, tokens
from league_site.auth.token_store import TokenStore

WSGIApp = Callable[[dict[str, Any], Callable[..., Any]], list[bytes]]

SESSION_COOKIE_NAME = "league_session"
SESSION_ENVIRON_KEY = "league_site.auth.session"

_LOGIN_PREFIX = "/auth/login/"
_CALLBACK_PREFIX = "/auth/callback/"
_LOGOUT_PATH = "/auth/logout"
_AGENTS_PATH = "/auth/agents"

#: The JSON body fields ``POST /auth/agents`` requires, in the order they
#: are validated (and reported when missing/blank).
_AGENT_FIELDS = ("name", "model", "provider")


def with_auth(
    app: WSGIApp,
    *,
    transport: oauth.Transport = oauth.default_transport,
    token_store: TokenStore | None = None,
    issue_hourly_cap: int | None = None,
    clock: Callable[[], datetime] | None = None,
) -> WSGIApp:
    """Wrap *app* with the ``/auth/*`` routes described in the module docstring.

    ``transport`` is forwarded to every OAuth call the callback route makes
    (:func:`~league_site.auth.oauth.complete_login`) — production callers
    leave it at the default (the real network); tests inject a stub so no
    request in this suite ever leaves the process.

    ``token_store`` enables ``POST /auth/agents`` and MUST be the same
    :class:`~league_site.auth.token_store.TokenStore` instance the composed
    ``/api/v1`` layer (:func:`league_site.api.wsgi.with_api`) verifies
    bearer tokens against — a token minted into any other store would never
    authenticate. Left ``None``, the route answers ``503 not_configured``.

    ``issue_hourly_cap`` overrides the self-serve issuance cap; ``None``
    (the default) reads :data:`~league_site.auth.tokens.ISSUE_HOURLY_CAP_ENV`
    once here at construction — once per process/Lambda cold start, not per
    request — falling back to
    :data:`~league_site.auth.tokens.DEFAULT_ISSUE_HOURLY_CAP` (same
    read-env-at-construction shape as ``with_api``'s capacity config).
    ``clock`` injects the guard's notion of "now" (default: real UTC time);
    tests use it to walk the rolling window deterministically.
    """
    resolved_cap = (
        issue_hourly_cap if issue_hourly_cap is not None else tokens.issue_hourly_cap_from_env()
    )

    def application(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        environ[SESSION_ENVIRON_KEY] = _read_session(environ)
        path = environ.get("PATH_INFO", "/")

        if path.startswith(_LOGIN_PREFIX):
            return _login(environ, start_response, path[len(_LOGIN_PREFIX) :])
        if path.startswith(_CALLBACK_PREFIX):
            return _callback(environ, start_response, path[len(_CALLBACK_PREFIX) :], transport)
        if path == _LOGOUT_PATH:
            return _logout(environ, start_response)
        if path == _AGENTS_PATH:
            return _issue_agent_token(environ, start_response, token_store, resolved_cap, clock)

        return app(environ, start_response)

    return application


def _read_session(environ: dict[str, Any]) -> sessions.Session | None:
    """Extract and verify the session cookie, if any, from *environ*."""
    header = environ.get("HTTP_COOKIE")
    if not header:
        return None
    cookie: SimpleCookie = SimpleCookie()
    cookie.load(header)
    morsel = cookie.get(SESSION_COOKIE_NAME)
    if morsel is None:
        return None
    return sessions.verify(morsel.value)


def _base_url(environ: dict[str, Any]) -> str:
    scheme = environ.get("wsgi.url_scheme", "https")
    host = environ.get("HTTP_HOST") or environ.get("SERVER_NAME", "localhost")
    return f"{scheme}://{host}"


def _redirect_uri(environ: dict[str, Any], provider: str) -> str:
    return f"{_base_url(environ)}{_CALLBACK_PREFIX}{provider}"


def _query_params(environ: dict[str, Any]) -> dict[str, list[str]]:
    return parse_qs(environ.get("QUERY_STRING", ""))


def _plain(start_response: Any, status: str, body: bytes) -> list[bytes]:
    start_response(status, [("Content-Type", "text/plain; charset=utf-8")])
    return [body]


def _redirect(start_response: Any, location: str, *, cookie: str | None = None) -> list[bytes]:
    headers = [("Location", location)]
    if cookie is not None:
        headers.append(("Set-Cookie", cookie))
    start_response("302 Found", headers)
    return [b""]


def _set_cookie_header(environ: dict[str, Any], token: str) -> str:
    attrs = (
        f"{SESSION_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; "
        f"Max-Age={sessions.DEFAULT_TTL_SECONDS}"
    )
    if environ.get("wsgi.url_scheme") == "https":
        attrs += "; Secure"
    return attrs


def _clear_cookie_header() -> str:
    return f"{SESSION_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"


def _is_valid_provider_segment(provider: str) -> bool:
    return bool(provider) and "/" not in provider


def _login(environ: dict[str, Any], start_response: Any, provider: str) -> list[bytes]:
    if not _is_valid_provider_segment(provider):
        return _plain(start_response, "404 Not Found", b"unknown auth provider")
    try:
        url, _state = oauth.authorize_url(provider, _redirect_uri(environ, provider))
    except oauth.OAuthError as exc:
        return _plain(start_response, "400 Bad Request", str(exc).encode("utf-8"))
    return _redirect(start_response, url)


def _callback(
    environ: dict[str, Any], start_response: Any, provider: str, transport: oauth.Transport
) -> list[bytes]:
    if not _is_valid_provider_segment(provider):
        return _plain(start_response, "404 Not Found", b"unknown auth provider")
    params = _query_params(environ)
    code = (params.get("code") or [""])[0]
    state = (params.get("state") or [""])[0]
    if not code or not state or not oauth.verify_state(state, provider):
        return _plain(start_response, "400 Bad Request", b"invalid or expired OAuth state")
    try:
        identity = oauth.complete_login(
            provider, code, _redirect_uri(environ, provider), transport=transport
        )
    except oauth.OAuthError as exc:
        return _plain(start_response, "400 Bad Request", str(exc).encode("utf-8"))
    token = sessions.issue(identity)
    return _redirect(start_response, "/", cookie=_set_cookie_header(environ, token))


def _logout(environ: dict[str, Any], start_response: Any) -> list[bytes]:
    return _redirect(start_response, "/", cookie=_clear_cookie_header())


# --- POST /auth/agents: self-serve agent token onboarding ---------------------


def _json_response(start_response: Any, status: str, payload: Any) -> list[bytes]:
    """Render *payload* as JSON — same envelope shape as ``league_site.api.wsgi``."""
    body = json.dumps(payload).encode("utf-8")
    start_response(
        status,
        [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ],
    )
    return [body]


def _json_error(start_response: Any, status: str, code: str, message: str) -> list[bytes]:
    return _json_response(start_response, status, {"code": code, "message": message})


def _read_json_object(environ: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Read the request body as a JSON object: ``(object, None)`` or ``(None, error)``.

    An empty body reads as ``{}`` (field validation then names what's
    missing), mirroring ``league_site.api.wsgi._read_json_body`` — not
    imported from there because :mod:`league_site.api` already imports this
    package; importing back would create a cycle.
    """
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        length = 0
    raw = environ["wsgi.input"].read(length) if length > 0 else b""
    if not raw:
        return {}, None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"invalid JSON body: {exc}"
    if not isinstance(data, dict):
        return None, "request body must be a JSON object"
    return data, None


def _issue_agent_token(
    environ: dict[str, Any],
    start_response: Any,
    token_store: TokenStore | None,
    hourly_cap: int,
    clock: Callable[[], datetime] | None,
) -> list[bytes]:
    """Handle ``POST /auth/agents`` — see the module docstring for the contract."""
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if method != "POST":
        return _json_error(
            start_response,
            "405 Method Not Allowed",
            "method_not_allowed",
            f"expected POST, got {method}",
        )
    if token_store is None:
        return _json_error(
            start_response,
            "503 Service Unavailable",
            "not_configured",
            "agent token issuance is not configured on this deployment",
        )
    body, error = _read_json_object(environ)
    if body is None:
        return _json_error(start_response, "400 Bad Request", "bad_request", error or "")
    fields: dict[str, str] = {}
    for field in _AGENT_FIELDS:
        value = body.get(field)
        if not isinstance(value, str) or not value.strip():
            return _json_error(
                start_response,
                "400 Bad Request",
                "bad_request",
                f"{field} must be a non-empty string",
            )
        fields[field] = value.strip()
    try:
        issued = tokens.issue_self_serve(
            token_store,
            agent_name=fields["name"],
            model=fields["model"],
            provider=fields["provider"],
            hourly_cap=hourly_cap,
            now=clock() if clock is not None else None,
        )
    except tokens.AgentNameTakenError as exc:
        return _json_error(start_response, "409 Conflict", "name_taken", str(exc))
    except tokens.IssueCapExceededError as exc:
        return _json_error(start_response, "429 Too Many Requests", "issue_cap_exceeded", str(exc))
    return _json_response(
        start_response,
        "201 Created",
        {"token": issued.token, "identity": tokens.identity_key(issued.identity)},
    )
