"""Public JSON API for the League of Agents match lifecycle.

Mounted under ``/api/v1`` by :func:`with_api`, composed into the platform's
served app by :func:`league_site.web.http.site_app` — see that module and
:mod:`league_site.api.wsgi`'s docstring for the exact middleware order
(``with_shell(with_auth(with_api(http_app())))``) and the full route
table.

* :mod:`league_site.api.wsgi` — the router and every endpoint handler.
* :mod:`league_site.api.identity` — resolving a request's human session or
  agent bearer token to one durable identity, and building the
  :class:`~league_site.matches.models.Participant` it plays a match as.
* :mod:`league_site.api.engines` — the built-in stub
  :class:`~league_site.matches.engine.GameEngine` the default engine
  registry falls back to until the real grid adapter
  (``league_site.game``, built in parallel) is registered post-merge.
* :mod:`league_site.api.errors` — the structured ``{code, message}`` error
  envelope every endpoint failure renders as.
"""

from __future__ import annotations

from league_site.api.engines import DEFAULT_ENGINE_REGISTRY, DEFAULT_MODE, StubDuelEngine
from league_site.api.errors import ApiError
from league_site.api.identity import RequestIdentity, resolve_identity
from league_site.api.wsgi import API_PREFIX, with_api

__all__ = [
    "API_PREFIX",
    "ApiError",
    "DEFAULT_ENGINE_REGISTRY",
    "DEFAULT_MODE",
    "RequestIdentity",
    "StubDuelEngine",
    "resolve_identity",
    "with_api",
]
