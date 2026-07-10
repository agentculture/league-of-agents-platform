"""The AWS Lambda entrypoint: API Gateway HTTP API v2 event -> platform response.

Wires :mod:`league_site.aws_lambda.wsgi`'s translator to
:func:`league_site.web.http.http_app` — the same WSGI app the local dev
server (``league_site.web.http.serve``) runs, so Lambda and local behave
identically. No AWS SDK (``boto3``) import here or in
:mod:`league_site.aws_lambda.wsgi`: this handler only serves the agentfront
HTTP surface, which has no AWS dependency of its own.
"""

from __future__ import annotations

from typing import Any

from league_site.aws_lambda.wsgi import call_wsgi_app, event_to_environ
from league_site.web.http import http_app

# Built once per Lambda execution environment (cold start) and reused across
# every warm invocation it serves — rebuilding the agentfront registry on
# every request would needlessly re-read every doc file each time.
_APP = http_app()


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Lambda entrypoint. *event* is an API Gateway HTTP API v2 payload; *context* is unused."""
    del context
    environ = event_to_environ(event)
    return call_wsgi_app(_APP, environ)
