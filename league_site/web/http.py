"""HTTP surface for the platform's agentfront ``App``.

``agentfront``'s own :meth:`agentfront.App.http_app` already serves every
registered doc as raw markdown (``Content-Type: text/markdown``) at
``/<slug>`` — see :mod:`agentfront.http_surface`. This module adds one thin
routing behavior on top: a ``.md`` suffix passthrough, so a consumer that
expects an explicit ``/<slug>.md`` "raw" URL (the common docs-site
convention, distinct from the page URL) resolves to the exact same registry
entry as ``/<slug>``. The registry built by
:func:`league_site.web.app.build_app` stays the single source of truth for
both URLs — this wrapper only rewrites ``PATH_INFO`` before delegating to
agentfront's own WSGI app.

:func:`site_app` composes this surface with the match API
(:mod:`league_site.api.wsgi`), human/agent auth
(:mod:`league_site.auth.wsgi`), the HTML shell (:mod:`league_site.web.
shell`), and the footer branding (:mod:`league_site.web.branding`) — it is
what actually serves the site (see :mod:`league_site.aws_lambda.handler`);
:func:`http_app` alone stays the raw, unshelled markdown surface that
:func:`site_app` wraps and that existing tests (and the
``.md``/``llms.txt``/``front`` passthroughs) keep exercising directly.
"""

from __future__ import annotations

from typing import Any, Callable
from wsgiref.simple_server import WSGIServer, make_server

from league_site.api.wsgi import EngineRegistry, with_api
from league_site.auth import oauth
from league_site.auth.token_store import TokenStore
from league_site.auth.wsgi import with_auth
from league_site.matches import MatchStore
from league_site.ratings.ledger import RatingLedgerStore
from league_site.web.app import build_app
from league_site.web.branding import register_branding
from league_site.web.shell import with_shell

WSGIApp = Callable[[dict[str, Any], Callable[..., Any]], list[bytes]]

_MD_SUFFIX = ".md"


def _md_passthrough(inner: WSGIApp) -> WSGIApp:
    """Wrap *inner* so ``GET /<slug>.md`` resolves to ``GET /<slug>``.

    Only ``PATH_INFO`` is rewritten; the request is otherwise forwarded
    verbatim, so both URLs hit the same doc lookup inside *inner* and return
    identical bytes from the one registry entry.
    """

    def application(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        path = environ.get("PATH_INFO", "/")
        if path != "/" and path.endswith(_MD_SUFFIX):
            environ = dict(environ)
            environ["PATH_INFO"] = path[: -len(_MD_SUFFIX)]
        return inner(environ, start_response)

    return application


def http_app() -> WSGIApp:
    """Return the platform's WSGI HTTP surface.

    Builds a fresh registry via :func:`~league_site.web.app.build_app` and
    derives its HTTP surface via :meth:`agentfront.App.http_app`, wrapped
    with the ``.md`` suffix passthrough (see :func:`_md_passthrough`).
    """
    return _md_passthrough(build_app().http_app())


def site_app(
    *,
    transport: oauth.Transport = oauth.default_transport,
    match_store: MatchStore | None = None,
    token_store: TokenStore | None = None,
    ledger_store: RatingLedgerStore | None = None,
    engine_registry: EngineRegistry | None = None,
) -> WSGIApp:
    """Return the platform's composed site app: auth + API + :func:`http_app` + the HTML shell.

    This is the app callers actually want to *serve* — the local dev server
    (:func:`serve`) and the Lambda entrypoint
    (:mod:`league_site.aws_lambda.handler`) both use it. Composition order,
    outermost first::

        with_shell(with_auth(with_api(http_app())))

    * :func:`~league_site.web.branding.register_branding` runs first,
      registering the footer acknowledgement fragment into the
      process-wide :data:`~league_site.web.shell.FOOTER_SLOTS` registry
      (idempotent, so calling :func:`site_app` more than once — e.g. once
      per Lambda cold start plus once in a test — never duplicates the
      footer line).
    * :func:`~league_site.api.wsgi.with_api` is mounted directly on
      :func:`http_app`, claiming every ``/api/v1/*`` path and passing
      everything else through unchanged.
    * :func:`~league_site.auth.wsgi.with_auth` wraps *that* — adding the
      ``/auth/*`` routes and, on every request (API or page alike),
      resolving the session cookie into
      ``environ[league_site.auth.wsgi.SESSION_ENVIRON_KEY]`` before the
      request reaches ``with_api``. This is why ``with_api`` must sit
      *inside* ``with_auth`` rather than the other way around — see
      :mod:`league_site.api.wsgi`'s docstring.
    * :func:`~league_site.web.shell.with_shell` stays outermost, wrapping
      every rendered markdown page (``/``, ``/<slug>``) in the shared HTML
      layout — header, main content, and the footer above. It leaves the
      API's ``application/json`` responses (and the ``/auth/*`` redirects)
      alone, since it only ever shells a ``text/markdown`` response.

    All of :func:`http_app`'s byte-identical guarantees survive the wrap
    unchanged: raw ``.md`` passthrough, ``/llms.txt``, and ``/front`` are
    exempted by :func:`~league_site.web.shell.with_shell` itself (see that
    module's docstring), so they still return exactly what :func:`http_app`
    would return on its own.

    Every keyword defaults to a fresh in-memory reference implementation
    (see :func:`~league_site.api.wsgi.with_api`), so a bare ``site_app()``
    — what the Lambda entrypoint and local dev server both call — is a
    complete, self-contained app; tests inject their own stores/registry
    (and OAuth ``transport``, forwarded to :func:`~league_site.auth.wsgi.
    with_auth` unchanged) to control identity, persistence, and available
    games.
    """
    register_branding()
    api = with_api(
        http_app(),
        match_store=match_store,
        token_store=token_store,
        ledger_store=ledger_store,
        engine_registry=engine_registry,
    )
    return with_shell(with_auth(api, transport=transport))


def serve(host: str = "127.0.0.1", port: int = 8000) -> WSGIServer:
    """Start a local dev HTTP server for the platform site.

    The ``site serve``-shaped entry point: builds :func:`site_app` (the
    composed, shelled site — the same app :mod:`league_site.aws_lambda.
    handler` serves, so Lambda and local behave identically) and binds it
    with :mod:`wsgiref`. Returns the server without blocking — callers
    invoke ``.serve_forever()`` on it, or use it as a context manager in
    tests/scripts, then ``.server_close()`` when done.

    Not wired to a CLI verb yet: that needs a new
    ``league_site/cli/_commands/site.py`` *and* an import + ``register()``
    call in ``league_site/cli/__init__.py``, which is out of scope for this
    module (see :mod:`league_site.web`'s docstring) — left for a follow-up
    task/merge to wire in.
    """
    return make_server(host, port, site_app())
