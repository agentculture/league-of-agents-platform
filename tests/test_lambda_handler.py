"""Tests for :mod:`league_site.aws_lambda.handler` — the Lambda entrypoint.

Direct invocation only (``handler(event, context)``): no HTTP server, no
API Gateway, no AWS credentials. Confirms the entrypoint wires the
translator to the app :func:`league_site.aws_lambda.wiring.build_site_app`
composes — the *real* :func:`league_site.web.http.site_app` over
env-selected stores — built once at import (cold-start) time.
"""

from __future__ import annotations

import importlib
import os
import subprocess  # nosec B404 - spawns this same interpreter to probe import behavior
import sys

import pytest

_STORE_ENV_VARS = (
    "MATCHES_TABLE_NAME",
    "ARCHIVE_BUCKET_NAME",
    "TOKENS_TABLE_NAME",
    "RATINGS_TABLE_NAME",
)


def _request_context(method: str, path: str) -> dict:
    return {
        "accountId": "123456789012",
        "apiId": "api-id",
        "domainName": "id.execute-api.us-east-1.amazonaws.com",
        "domainPrefix": "id",
        "http": {
            "method": method,
            "path": path,
            "protocol": "HTTP/1.1",
            "sourceIp": "192.0.2.1",
            "userAgent": "agent-testsuite/1.0",
        },
        "requestId": "request-id",
        "routeKey": "$default",
        "stage": "$default",
        "time": "12/Mar/2020:19:03:58 +0000",
        "timeEpoch": 1583348638390,
    }


def _apigw_event(path: str, *, method: str = "GET") -> dict:
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {
            "accept": "*/*",
            "host": "id.execute-api.us-east-1.amazonaws.com",
        },
        "requestContext": _request_context(method, path),
        "isBase64Encoded": False,
    }


def test_handler_serves_the_landing_page_for_root() -> None:
    """``GET /`` returns the platform's landing page through the composed shell.

    The Lambda entrypoint now serves ``league_site.web.http.site_app()``
    (agentfront's markdown HTTP surface wrapped in the shared HTML shell,
    with the footer acknowledgement registered — see
    ``league_site.web.shell`` and ``league_site.web.branding``), so "the
    landing page" is a real HTML page (nav linking to ``/index``, a footer)
    rather than raw markdown. Raw markdown is still available byte-identical
    at ``/index.md`` — see ``test_handler_serves_raw_markdown_for_index_md``
    below.
    """
    from league_site.aws_lambda.handler import handler

    response = handler(_apigw_event("/"), context=None)
    assert response["statusCode"] == 200
    assert "/index" in response["body"]
    assert response["isBase64Encoded"] is False
    assert response["headers"]["Content-Type"] == "text/html; charset=utf-8"
    assert "<!doctype html>" in response["body"].lower()
    assert '<a href="/about">About</a>' in response["body"]


def test_handler_serves_the_about_page_through_the_shell() -> None:
    """``GET /about`` reaches the About page via the composed Lambda app."""
    from league_site.aws_lambda.handler import handler

    response = handler(_apigw_event("/about"), context=None)
    assert response["statusCode"] == 200
    assert response["headers"]["Content-Type"] == "text/html; charset=utf-8"
    assert "Ori Nachum" in response["body"]
    assert "Claude Code" in response["body"]


def test_handler_serves_raw_markdown_for_index_md() -> None:
    from league_site.aws_lambda.handler import handler

    response = handler(_apigw_event("/index.md"), context=None)
    assert response["statusCode"] == 200
    assert response["headers"]["Content-Type"] == "text/markdown; charset=utf-8"
    assert "# League of Agents" in response["body"]
    # Raw markdown, not rendered HTML.
    assert "<html" not in response["body"].lower()


def test_handler_404s_for_an_unknown_path() -> None:
    from league_site.aws_lambda.handler import handler

    response = handler(_apigw_event("/this-page-does-not-exist"), context=None)
    assert response["statusCode"] == 404


def test_handler_app_is_built_once_at_module_import_cold_start() -> None:
    """The registry/app is a module-level singleton, not rebuilt per invocation."""
    import league_site.aws_lambda.handler as handler_module

    first_call_app = handler_module._APP
    handler_module.handler(_apigw_event("/index"), context=None)
    handler_module.handler(_apigw_event("/index"), context=None)
    assert handler_module._APP is first_call_app


def test_handler_builds_its_app_through_the_env_wiring() -> None:
    """The cold-start app comes from ``wiring.build_site_app`` — the one
    place env-driven store selection happens — not a hardcoded bare
    ``site_app()``."""
    import league_site.aws_lambda.handler as handler_module
    import league_site.aws_lambda.wiring as wiring_module

    assert handler_module.build_site_app is wiring_module.build_site_app


def test_handler_import_path_does_not_import_boto3_when_env_is_unset() -> None:
    """Cold-start cost contract: with no ``*_TABLE_NAME`` in the environment
    (local dev, tests), importing the handler must not drag in ``boto3``."""
    code = (
        "import sys\n"
        "import league_site.aws_lambda.handler\n"
        "assert 'boto3' not in sys.modules, 'boto3 imported at cold start without AWS env'\n"
    )
    env = {key: value for key, value in os.environ.items() if key not in _STORE_ENV_VARS}
    result = subprocess.run(  # nosec B603 - fixed argv, this same interpreter
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_handler_cold_start_with_table_env_serves_the_site_over_aws_stores() -> None:
    """Reloading the handler with the deploy env present must build the
    DynamoDB-backed app (boto3 resource construction only — no network
    happens until a store operation runs) and still serve the site."""
    import league_site.aws_lambda.handler as handler_module
    import league_site.aws_lambda.wiring as wiring_module

    captured: dict[str, object] = {}
    real_build = wiring_module.build_site_app

    def recording_build() -> object:
        app = real_build()
        captured["app"] = app
        return app

    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(wiring_module, "build_site_app", recording_build)
            mp.setenv("MATCHES_TABLE_NAME", "league-matches")
            mp.setenv("ARCHIVE_BUCKET_NAME", "league-archive")
            mp.setenv("TOKENS_TABLE_NAME", "league-tokens")
            mp.setenv("RATINGS_TABLE_NAME", "league-ratings")
            mp.delenv("LEAGUE_SESSION_SECRET", raising=False)
            # enough AWS config for boto3.resource("dynamodb") to construct
            # lazily; nothing in this test performs a store operation.
            mp.setenv("AWS_DEFAULT_REGION", "us-east-1")
            mp.setenv("AWS_ACCESS_KEY_ID", "testing")
            mp.setenv("AWS_SECRET_ACCESS_KEY", "testing")  # nosec B105 - fake test credential

            importlib.reload(handler_module)

            assert handler_module._APP is captured["app"]
            response = handler_module.handler(_apigw_event("/"), context=None)
            assert response["statusCode"] == 200
            assert "<!doctype html>" in response["body"].lower()
    finally:
        # rebuild the module-level singleton under the restored (clean) env
        # so later tests see the same in-memory-backed app as a fresh import
        importlib.reload(handler_module)
