"""WSGI wrapper adding OAuth login/callback/logout routes on top of any app.

:func:`with_auth` adds three routes — ``/auth/login/<provider>``,
``/auth/callback/<provider>``, ``/auth/logout`` — and passes every other
path straight through to the wrapped app *unchanged*, so anonymous
browsing/spectating keeps working with zero behavior change. On every
request (auth routes and pass-through alike) the wrapper reads the session
cookie, verifies it via :mod:`league_site.auth.sessions`, and stores the
result (a :class:`~league_site.auth.sessions.Session` or ``None``) at
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
"""

from __future__ import annotations

from http.cookies import SimpleCookie
from typing import Any, Callable
from urllib.parse import parse_qs

from league_site.auth import oauth, sessions

WSGIApp = Callable[[dict[str, Any], Callable[..., Any]], list[bytes]]

SESSION_COOKIE_NAME = "league_session"
SESSION_ENVIRON_KEY = "league_site.auth.session"

_LOGIN_PREFIX = "/auth/login/"
_CALLBACK_PREFIX = "/auth/callback/"
_LOGOUT_PATH = "/auth/logout"


def with_auth(app: WSGIApp, *, transport: oauth.Transport = oauth.default_transport) -> WSGIApp:
    """Wrap *app* with the ``/auth/*`` routes described in the module docstring.

    ``transport`` is forwarded to every OAuth call the callback route makes
    (:func:`~league_site.auth.oauth.complete_login`) — production callers
    leave it at the default (the real network); tests inject a stub so no
    request in this suite ever leaves the process.
    """

    def application(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        environ[SESSION_ENVIRON_KEY] = _read_session(environ)
        path = environ.get("PATH_INFO", "/")

        if path.startswith(_LOGIN_PREFIX):
            return _login(environ, start_response, path[len(_LOGIN_PREFIX) :])
        if path.startswith(_CALLBACK_PREFIX):
            return _callback(environ, start_response, path[len(_CALLBACK_PREFIX) :], transport)
        if path == _LOGOUT_PATH:
            return _logout(environ, start_response)

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
