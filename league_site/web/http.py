"""HTTP surface for the platform's agentfront ``App``.

``agentfront``'s own :meth:`agentfront.App.http_app` already serves every
registered doc as raw markdown (``Content-Type: text/markdown``) at
``/<slug>`` — see :mod:`agentfront.http_surface`. This module adds two thin
routing behaviors on top, neither of which forks the registry — both only
ever rewrite ``PATH_INFO`` before delegating to agentfront's own WSGI app:

* A ``.md`` suffix passthrough, so a consumer that expects an explicit
  ``/<slug>.md`` "raw" URL (the common docs-site convention, distinct from
  the page URL) resolves to the exact same registry entry as ``/<slug>``.
* A root-landing swap (platform#14): agentfront's own ``/`` is its
  *generated* doc catalog (see :func:`agentfront.http_surface._index`), not
  the authored landing page a visitor to the site's root URL should see.
  :func:`_with_root_landing` rewrites ``GET /`` onto the authored ``index``
  doc instead, and gives the generated catalog a stable home of its own at
  ``GET /docs``. See that function's docstring for the full rationale,
  including why ``/index`` keeps serving directly rather than redirecting.

The registry built by :func:`league_site.web.app.build_app` stays the
single source of truth for every URL these two wrappers touch.

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

import re
from typing import Any, Callable
from wsgiref.simple_server import WSGIServer, make_server

from league_site.api.wsgi import EngineRegistry, with_api
from league_site.auth import oauth
from league_site.auth.token_store import TokenStore
from league_site.auth.wsgi import with_auth
from league_site.matches import InMemoryMatchStore, MatchStore
from league_site.ratings.ledger import InMemoryRatingLedgerStore, RatingLedgerStore
from league_site.web.app import build_app
from league_site.web.branding import register_branding
from league_site.web.shell import with_shell

WSGIApp = Callable[[dict[str, Any], Callable[..., Any]], list[bytes]]

_MD_SUFFIX = ".md"
_ROOT_PATH = "/"
_LANDING_SLUG_PATH = "/index"
_DOCS_CATALOG_PATH = "/docs"
#: agentfront's own generated doc catalog is only reachable, inside
#: agentfront's own ``http_surface``, at the exact path ``"/"`` (see
#: :func:`agentfront.http_surface.make_http_app`'s ``path == "/"`` branch,
#: which calls ``_index(app)``). ``PATH_INFO`` must equal this exact string
#: by the time it reaches ``build_app().http_app()`` to hit that branch --
#: named separately from :data:`_ROOT_PATH` even though the value is
#: identical, since the two constants mean different things: one is the
#: *outward-facing* URL this module remaps away from the catalog, the other
#: is the catalog's fixed *internal* address inside agentfront.
_CATALOG_SOURCE_PATH = "/"
_PROFILES_PREFIX = "/profiles/"
#: Matches ``league_site.viewer.wsgi.WATCH_PATH_RE`` exactly — duplicated here
#: (rather than imported at module scope) for the same reason
#: ``_PROFILES_PREFIX`` above duplicates ``league_site.profiles.wsgi``'s own
#: prefix instead of importing it: see :func:`site_app`'s docstring on the
#: ``league_site.web.*`` import cycle a module-level import of
#: ``league_site.viewer`` (which imports ``league_site.web._markdown``)
#: would deadlock.
_WATCH_PATH_RE = re.compile(r"^/matches/[^/]+/watch$")


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


def _with_root_landing(inner: WSGIApp) -> WSGIApp:
    """Wrap *inner* so ``GET /`` serves the authored landing doc and ``GET
    /docs`` serves agentfront's generated doc catalog — the page that
    otherwise lives at ``/`` (see :func:`agentfront.http_surface._index`).

    Same rewrite-``PATH_INFO``-and-delegate technique as
    :func:`_md_passthrough`: neither the doc registry nor agentfront's own
    routing is forked, only the ``PATH_INFO`` agentfront sees is remapped —
    so both URLs resolve to the exact same registry entries agentfront
    already serves, byte-identical to what ``/index`` and (formerly) ``/``
    always returned.

    ``/index`` keeps serving the identical landing content directly too —
    it is *not* redirected, by design: redirecting would mean an agent (or
    a human's browser history) following the old ``/index`` URL bounces
    through a 301 instead of getting the page directly, and the raw
    ``/index.md`` passthrough some consumers already depend on must stay
    byte-identical regardless. ``/`` becomes the *additional*, canonical
    URL for the same content — PATH_INFO is rewritten internally rather
    than the caller being redirected, so the root URL stays canonical in
    the address bar (see :mod:`league_site.web.shell`'s landing-title
    handling, which treats ``/`` and ``/index`` as the same page for the
    purpose of the ``<title>``).
    """

    def application(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        path = environ.get("PATH_INFO", "/")
        if path == _ROOT_PATH:
            environ = dict(environ)
            environ["PATH_INFO"] = _LANDING_SLUG_PATH
        elif path == _DOCS_CATALOG_PATH:
            environ = dict(environ)
            environ["PATH_INFO"] = _CATALOG_SOURCE_PATH
        return inner(environ, start_response)

    return application


def _with_profiles(inner: WSGIApp, profiles: WSGIApp) -> WSGIApp:
    """Wrap *inner* so any ``PATH_INFO`` starting with ``/profiles/`` goes to *profiles* instead.

    Checked ahead of everything *inner* does — :func:`site_app` uses this to
    dispatch to :func:`~league_site.profiles.wsgi.profile_app` before a
    request ever reaches ``with_shell``/``with_auth``/``with_api``, per that
    function's docstring.
    """

    def application(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        path = environ.get("PATH_INFO", "/")
        if path.startswith(_PROFILES_PREFIX):
            return profiles(environ, start_response)
        return inner(environ, start_response)

    return application


def _with_viewer(inner: WSGIApp, viewer: WSGIApp) -> WSGIApp:
    """Wrap *inner* so any ``PATH_INFO`` matching ``/matches/<id>/watch`` goes to *viewer* instead.

    Mirrors :func:`_with_profiles`: checked ahead of everything *inner*
    does — :func:`site_app` uses this to dispatch to
    :func:`~league_site.viewer.wsgi.viewer_app` before a request ever
    reaches ``with_shell``/``with_auth``/``with_api``, per that function's
    docstring. ``/api/v1/matches/...`` is a distinct prefix
    (:data:`~league_site.api.wsgi.API_PREFIX`) and is never matched by
    :data:`_WATCH_PATH_RE` — only the page path ``/matches/<id>/watch`` is,
    so the match API keeps routing to ``with_api`` unchanged.
    """

    def application(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        path = environ.get("PATH_INFO", "/")
        if _WATCH_PATH_RE.match(path) or path == "/leaderboard":
            return viewer(environ, start_response)
        return inner(environ, start_response)

    return application


def http_app() -> WSGIApp:
    """Return the platform's WSGI HTTP surface.

    Builds a fresh registry via :func:`~league_site.web.app.build_app` and
    derives its HTTP surface via :meth:`agentfront.App.http_app`, wrapped
    with the root-landing swap (see :func:`_with_root_landing` — ``/`` gets
    the authored landing, ``/docs`` gets the generated catalog) and, around
    that, the ``.md`` suffix passthrough (see :func:`_md_passthrough`) —
    applied outermost so it also covers the two new URLs (``/docs.md``
    resolves the same way ``/index.md`` always has).
    """
    return _md_passthrough(_with_root_landing(build_app().http_app()))


def site_app(
    *,
    transport: oauth.Transport = oauth.default_transport,
    match_store: MatchStore | None = None,
    token_store: TokenStore | None = None,
    ledger_store: RatingLedgerStore | None = None,
    engine_registry: EngineRegistry | None = None,
) -> WSGIApp:
    """Return the platform's composed site app: viewer + profiles + auth + API + :func:`http_app`.

    This is the app callers actually want to *serve* — the local dev server
    (:func:`serve`) and the Lambda entrypoint
    (:mod:`league_site.aws_lambda.handler`) both use it. Composition order,
    outermost first::

        viewer-or-profiles-or(with_shell(with_auth(with_api(http_app()))))

    * :func:`~league_site.web.branding.register_branding` runs first,
      registering the footer acknowledgement fragment into the
      process-wide :data:`~league_site.web.shell.FOOTER_SLOTS` registry
      (idempotent, so calling :func:`site_app` more than once — e.g. once
      per Lambda cold start plus once in a test — never duplicates the
      footer line).
    * The ``match_store``/``ledger_store`` keywords are resolved to concrete
      instances (defaulting to a fresh :class:`~league_site.matches.store.
      InMemoryMatchStore`/:class:`~league_site.ratings.ledger.
      InMemoryRatingLedgerStore` each, same as :func:`~league_site.api.wsgi.
      with_api`'s own defaults) *before* any consumer is built, so
      ``with_api``, :func:`~league_site.profiles.wsgi.profile_app`, and
      :func:`~league_site.viewer.wsgi.viewer_app` are all handed the exact
      same two objects — critical: a match recorded through the API must be
      immediately visible on its player's ``/profiles/*`` page and on its
      own ``/matches/<id>/watch`` page, which only holds if all three
      read/write the same store instances rather than each defaulting its
      own.
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
    * :func:`~league_site.web.shell.with_shell` wraps that, rendering every
      markdown page (``/``, ``/<slug>``) in the shared HTML layout — header,
      main content, and the footer above. It leaves the API's
      ``application/json`` responses (and the ``/auth/*`` redirects) alone,
      since it only ever shells a ``text/markdown`` response.
    * :func:`_with_profiles` wraps that: any ``PATH_INFO`` starting with
      ``/profiles/`` is dispatched straight to
      :func:`~league_site.profiles.wsgi.profile_app` and never reaches
      ``with_shell``/``with_auth``/``with_api`` at all — ``profile_app``
      renders its own self-contained HTML/SVG/JSON, so it has no need for
      (and must not pick up) the doc shell or the ``/api/v1`` router.
    * :func:`_with_viewer` sits outermost of all: any ``PATH_INFO`` matching
      ``/matches/<id>/watch`` is dispatched straight to
      :func:`~league_site.viewer.wsgi.viewer_app`, for the same reason and
      in the same style as ``_with_profiles`` above — it too renders its own
      self-contained HTML and must not pick up the doc shell or the
      ``/api/v1`` router. ``/api/v1/matches/...`` (a distinct prefix) is
      unaffected and keeps routing to ``with_api`` as before.

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
    # Imported lazily (not at module scope) to break an import cycle:
    # league_site.profiles.wsgi imports league_site.web.theme, and
    # league_site.viewer.render imports league_site.web._markdown — importing
    # any league_site.web.* submodule first runs league_site/web/__init__.py,
    # which imports this module's http_app/serve — a module-level import
    # here would deadlock that cycle. By the time site_app() is actually
    # *called*, every module involved has finished initializing, so a local
    # import resolves cleanly.
    from league_site.profiles.wsgi import profile_app
    from league_site.viewer.wsgi import viewer_app

    register_branding()
    resolved_match_store = match_store if match_store is not None else InMemoryMatchStore()
    resolved_ledger_store = (
        ledger_store if ledger_store is not None else InMemoryRatingLedgerStore()
    )
    api = with_api(
        http_app(),
        match_store=resolved_match_store,
        token_store=token_store,
        ledger_store=resolved_ledger_store,
        engine_registry=engine_registry,
    )
    composed = with_shell(with_auth(api, transport=transport))
    profiles = profile_app(resolved_ledger_store, resolved_match_store)
    viewer = viewer_app(resolved_match_store, resolved_ledger_store)
    return _with_viewer(_with_profiles(composed, profiles), viewer)


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
