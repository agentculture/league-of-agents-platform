"""Tests for :mod:`league_site.aws_lambda.handler` — the Lambda entrypoint.

Direct invocation only (``handler(event, context)``): no HTTP server, no
API Gateway, no AWS credentials. Confirms the entrypoint wires the
translator to the *real* :func:`league_site.web.http.http_app`, built once
at import (cold-start) time.
"""

from __future__ import annotations


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
    """``GET /`` returns the platform's landing content (200, markdown body).

    The platform serves markdown everywhere by design (agentfront's HTTP
    surface never emits raw HTML — see ``tests/test_web_http.py``), so "the
    landing page" here means the markdown index page linking to ``/index``,
    not literal ``<html>`` markup.
    """
    from league_site.aws_lambda.handler import handler

    response = handler(_apigw_event("/"), context=None)
    assert response["statusCode"] == 200
    assert "/index" in response["body"]
    assert response["isBase64Encoded"] is False


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
