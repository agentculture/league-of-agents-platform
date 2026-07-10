"""``league_site.web`` — the platform's single agentfront registry.

Docs and tools are declared once in :func:`build_app` (an
:class:`agentfront.App`); the HTTP site, the MCP server, and the CLI are all
*derived* from that one registry, so the three surfaces cannot drift apart.

Public surface of this package:

* :func:`build_app` — construct the registry (see :mod:`league_site.web.app`).
* :func:`http_app` — the WSGI HTTP surface, plus a ``.md`` raw-markdown
  passthrough (see :mod:`league_site.web.http`).
* :func:`serve` — a local dev server entry point (``wsgiref``); see
  :mod:`league_site.web.http` for details.

No ``site serve`` CLI verb exists yet: wiring one needs a new
``league_site/cli/_commands/site.py`` *and* an import + ``register()`` call
in ``league_site/cli/__init__.py``. This module's brief scopes out touching
any existing CLI command module, so the verb is left for a follow-up
task/merge — :func:`serve` below is ready for it to call.
"""

from __future__ import annotations

from league_site.web.app import build_app
from league_site.web.http import http_app, serve

__all__ = ["build_app", "http_app", "serve"]
