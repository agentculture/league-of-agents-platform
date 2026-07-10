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
"""

from __future__ import annotations

from typing import Any, Callable
from wsgiref.simple_server import WSGIServer, make_server

from league_site.web.app import build_app

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


def serve(host: str = "127.0.0.1", port: int = 8000) -> WSGIServer:
    """Start a local dev HTTP server for the platform site.

    The ``site serve``-shaped entry point: builds :func:`http_app` and binds
    it with :mod:`wsgiref`. Returns the server without blocking — callers
    invoke ``.serve_forever()`` on it, or use it as a context manager in
    tests/scripts, then ``.server_close()`` when done.

    Not wired to a CLI verb yet: that needs a new
    ``league_site/cli/_commands/site.py`` *and* an import + ``register()``
    call in ``league_site/cli/__init__.py``, which is out of scope for this
    module (see :mod:`league_site.web`'s docstring) — left for a follow-up
    task/merge to wire in.
    """
    return make_server(host, port, http_app())
