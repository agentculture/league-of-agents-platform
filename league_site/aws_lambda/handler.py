"""The AWS Lambda entrypoint: API Gateway HTTP API v2 event -> platform response.

Wires :mod:`league_site.aws_lambda.wsgi`'s translator to the app
:func:`league_site.aws_lambda.wiring.build_site_app` composes:
:func:`league_site.web.http.site_app` — the full arena (viewer/profiles +
shell + auth + match API over the agentfront HTTP surface, with the footer
branding registered; see :mod:`league_site.web.shell` and
:mod:`league_site.web.branding`) — over whichever stores the environment
selects. Deployed (the ``MATCHES_TABLE_NAME``/``TOKENS_TABLE_NAME``/
``RATINGS_TABLE_NAME`` variables present), that means DynamoDB-backed
stores and persistent state; locally and in tests (variables absent) it is
byte-identical to the bare ``site_app()`` the dev server
(``league_site.web.http.serve``) runs, so Lambda and local behave
identically. No AWS SDK (``boto3``) import happens on this module's import
path unless one of those variables is set — the env-driven construction
(and that guarantee) lives in :mod:`league_site.aws_lambda.wiring`;
:mod:`league_site.aws_lambda.wsgi` remains SDK-free always.
"""

from __future__ import annotations

from typing import Any

from league_site.aws_lambda.wiring import build_site_app
from league_site.aws_lambda.wsgi import call_wsgi_app, event_to_environ

# Built once per Lambda execution environment (cold start) and reused across
# every warm invocation it serves — rebuilding the agentfront registry (and
# re-registering the footer branding, and re-reading the env/re-building the
# store adapters) on every request would needlessly redo that work each time.
_APP = build_site_app()


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Lambda entrypoint. *event* is an API Gateway HTTP API v2 payload; *context* is unused."""
    del context
    environ = event_to_environ(event)
    return call_wsgi_app(_APP, environ)
