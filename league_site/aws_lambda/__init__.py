"""Lambda deploy target for the platform's WSGI HTTP surface.

Two modules, each with one job:

* :mod:`league_site.aws_lambda.wsgi` — the API Gateway HTTP API (payload
  format version 2.0) <-> WSGI (PEP 3333) translator. No AWS SDK calls, no
  network I/O; pure dict-in/dict-out functions.
* :mod:`league_site.aws_lambda.handler` — the actual Lambda entrypoint,
  wiring the translator to :func:`league_site.web.http.http_app`.

Stdlib only. ``boto3`` (available via this repo's ``aws`` extra) is not
imported anywhere in this package — the handler serves the agentfront HTTP
surface, which has no AWS dependency of its own.

Deliberately *not* re-exported here: importing
:mod:`league_site.aws_lambda.wsgi` alone (e.g. from a unit test that only
exercises the translator against a minimal echo app) should not pay the
cost — or risk the failure modes — of building the real platform app. Import
each submodule directly: ``from league_site.aws_lambda.wsgi import ...`` or
``from league_site.aws_lambda.handler import handler``.
"""

from __future__ import annotations
