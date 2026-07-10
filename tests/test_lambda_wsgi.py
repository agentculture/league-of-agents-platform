"""Tests for :mod:`league_site.aws_lambda.wsgi` — the API Gateway HTTP API

(payload format version 2.0) <-> WSGI translator.

Sample events below are shaped after the AWS-documented HTTP API v2 payload
format:
https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-develop-integrations-lambda.html

Each test either round-trips against a minimal hand-rolled "echo" WSGI app
(so the *environ* the translator builds can be asserted precisely) or
against the platform's real WSGI app from :mod:`league_site.web.http` (so
the translator is proven against production code, not just a toy).
"""

from __future__ import annotations

import base64
from typing import Any

from league_site.aws_lambda.wsgi import WSGIApp, call_wsgi_app, event_to_environ
from league_site.web.http import http_app

# --- sample API Gateway HTTP API v2 events ----------------------------------


def _base_request_context(
    method: str = "GET", path: str = "/", source_ip: str = "192.0.2.1"
) -> dict:
    return {
        "accountId": "123456789012",
        "apiId": "api-id",
        "domainName": "id.execute-api.us-east-1.amazonaws.com",
        "domainPrefix": "id",
        "http": {
            "method": method,
            "path": path,
            "protocol": "HTTP/1.1",
            "sourceIp": source_ip,
            "userAgent": "agent-testsuite/1.0",
        },
        "requestId": "request-id",
        "routeKey": "$default",
        "stage": "$default",
        "time": "12/Mar/2020:19:03:58 +0000",
        "timeEpoch": 1583348638390,
    }


def get_event_with_query_string() -> dict:
    """GET with a repeated query-string parameter — AWS's own documented example."""
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/my/path",
        "rawQueryString": "parameter1=value1&parameter1=value2&parameter2=value",
        "cookies": ["cookie1=value1", "cookie2=value2"],
        "headers": {
            "accept": "*/*",
            "content-length": "0",
            "host": "id.execute-api.us-east-1.amazonaws.com",
        },
        "queryStringParameters": {
            "parameter1": "value1,value2",
            "parameter2": "value",
        },
        "requestContext": _base_request_context(method="GET", path="/my/path"),
        "isBase64Encoded": False,
    }


def post_event_with_body() -> dict:
    """POST with a JSON text body (not base64-encoded)."""
    body = '{"greeting": "hello from the test suite"}'
    return {
        "version": "2.0",
        "routeKey": "POST /echo",
        "rawPath": "/echo",
        "rawQueryString": "",
        "headers": {
            "content-type": "application/json",
            "content-length": str(len(body)),
            "host": "id.execute-api.us-east-1.amazonaws.com",
        },
        "requestContext": _base_request_context(method="POST", path="/echo"),
        "body": body,
        "isBase64Encoded": False,
    }


def post_event_with_base64_body() -> dict:
    """POST whose body is base64-encoded binary (e.g. a small PNG-ish blob)."""
    raw = bytes(range(0, 16)) + b"\xff\xfe\x00\x01"
    return {
        "version": "2.0",
        "routeKey": "POST /upload",
        "rawPath": "/upload",
        "rawQueryString": "",
        "headers": {
            "content-type": "application/octet-stream",
            "content-length": str(len(raw)),
            "host": "id.execute-api.us-east-1.amazonaws.com",
        },
        "requestContext": _base_request_context(method="POST", path="/upload"),
        "body": base64.b64encode(raw).decode("ascii"),
        "isBase64Encoded": True,
    }, raw


def get_event(path: str, *, query_string: str = "") -> dict:
    """A minimal, realistic GET event for a given path."""
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": query_string,
        "headers": {
            "accept": "text/markdown",
            "host": "id.execute-api.us-east-1.amazonaws.com",
        },
        "requestContext": _base_request_context(method="GET", path=path),
        "isBase64Encoded": False,
    }


# --- a minimal echo WSGI app, for asserting environ precisely --------------


def _echo_app(environ: dict[str, Any], start_response: Any) -> list[bytes]:
    """A tiny WSGI app that reflects the request back as a deterministic body.

    Reports method, path, query string, a couple of headers, and the request
    body — everything the translator is responsible for carrying over.
    """
    content_length = int(environ.get("CONTENT_LENGTH") or 0)
    request_body = environ["wsgi.input"].read(content_length) if content_length else b""
    lines = [
        f"METHOD={environ.get('REQUEST_METHOD')}",
        f"PATH={environ.get('PATH_INFO')}",
        f"QUERY={environ.get('QUERY_STRING')}",
        f"CONTENT_TYPE={environ.get('CONTENT_TYPE')}",
        f"COOKIE={environ.get('HTTP_COOKIE', '')}",
        f"CUSTOM={environ.get('HTTP_X_CUSTOM', '')}",
    ]
    body = ("\n".join(lines) + "\n").encode("utf-8") + request_body
    start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8")])
    return [body]


def _binary_echo_app(environ: dict[str, Any], start_response: Any) -> list[bytes]:
    """A WSGI app that always answers with non-UTF-8 binary bytes."""
    del environ
    start_response("200 OK", [("Content-Type", "application/octet-stream")])
    return [bytes(range(0, 16)) + b"\xff\xfe\x00\x01"]


def _cookie_setting_app(environ: dict[str, Any], start_response: Any) -> list[bytes]:
    """A WSGI app that sets two cookies via repeated ``Set-Cookie`` headers."""
    del environ
    start_response(
        "200 OK",
        [
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Set-Cookie", "session=abc123; Path=/; HttpOnly"),
            ("Set-Cookie", "theme=dark; Path=/"),
        ],
    )
    return [b"cookies set"]


# --- event_to_environ --------------------------------------------------------


def test_get_query_string_uses_raw_query_string_verbatim() -> None:
    """``rawQueryString`` (not the pre-parsed dict) must land in QUERY_STRING,

    since it is the only field that preserves repeated keys faithfully.
    """
    environ = event_to_environ(get_event_with_query_string())
    assert environ["REQUEST_METHOD"] == "GET"
    assert environ["PATH_INFO"] == "/my/path"
    assert environ["QUERY_STRING"] == "parameter1=value1&parameter1=value2&parameter2=value"


def test_get_query_string_round_trips_through_echo_app() -> None:
    environ = event_to_environ(get_event_with_query_string())
    response: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        response["status"] = status

    body = b"".join(_echo_app(environ, start_response)).decode("utf-8")
    assert response["status"] == "200 OK"
    assert "METHOD=GET" in body
    assert "PATH=/my/path" in body
    assert "QUERY=parameter1=value1&parameter1=value2&parameter2=value" in body


def test_post_body_and_content_type_and_length_carried_over() -> None:
    event = post_event_with_body()
    environ = event_to_environ(event)
    assert environ["REQUEST_METHOD"] == "POST"
    assert environ["CONTENT_TYPE"] == "application/json"
    assert environ["CONTENT_LENGTH"] == str(len(event["body"]))
    assert environ["wsgi.input"].read() == event["body"].encode("utf-8")


def test_post_body_round_trips_through_echo_app() -> None:
    event = post_event_with_body()
    environ = event_to_environ(event)
    result = call_wsgi_app(_echo_app, environ)
    assert result["statusCode"] == 200
    assert result["isBase64Encoded"] is False
    assert '{"greeting": "hello from the test suite"}' in result["body"]


def test_base64_encoded_request_body_is_decoded_before_reaching_the_app() -> None:
    event, raw = post_event_with_base64_body()
    environ = event_to_environ(event)
    assert environ["wsgi.input"].read() == raw


def test_cookies_list_joined_into_single_cookie_header() -> None:
    environ = event_to_environ(get_event_with_query_string())
    assert environ["HTTP_COOKIE"] == "cookie1=value1; cookie2=value2"


def test_custom_header_is_translated_to_http_env_var() -> None:
    event = get_event("/probe")
    event["headers"]["x-custom"] = "probe-value"
    environ = event_to_environ(event)
    assert environ["HTTP_X_CUSTOM"] == "probe-value"


def test_missing_body_produces_empty_wsgi_input() -> None:
    environ = event_to_environ(get_event("/nobody"))
    assert environ["wsgi.input"].read() == b""
    assert environ["CONTENT_LENGTH"] in ("0", "")


# --- call_wsgi_app: response mapping ----------------------------------------


def test_text_response_is_not_base64_encoded() -> None:
    environ = event_to_environ(get_event("/text"))
    result = call_wsgi_app(_echo_app, environ)
    assert result["isBase64Encoded"] is False
    assert isinstance(result["body"], str)


def test_binary_response_is_base64_encoded() -> None:
    environ = event_to_environ(get_event("/binary"))
    result = call_wsgi_app(_binary_echo_app, environ)
    assert result["isBase64Encoded"] is True
    decoded = base64.b64decode(result["body"])
    assert decoded == bytes(range(0, 16)) + b"\xff\xfe\x00\x01"
    assert result["headers"]["Content-Type"] == "application/octet-stream"


def test_set_cookie_headers_move_to_the_cookies_array_not_headers() -> None:
    environ = event_to_environ(get_event("/set-cookie"))
    result = call_wsgi_app(_cookie_setting_app, environ)
    assert "Set-Cookie" not in result["headers"]
    assert set(result["cookies"]) == {
        "session=abc123; Path=/; HttpOnly",
        "theme=dark; Path=/",
    }


def test_repeated_non_cookie_headers_are_comma_joined() -> None:
    def vary_app(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        del environ
        start_response(
            "200 OK",
            [
                ("Content-Type", "text/plain; charset=utf-8"),
                ("Vary", "Accept-Encoding"),
                ("Vary", "Accept-Language"),
            ],
        )
        return [b"ok"]

    environ = event_to_environ(get_event("/vary"))
    result = call_wsgi_app(vary_app, environ)
    assert result["headers"]["Vary"] == "Accept-Encoding, Accept-Language"
    assert "cookies" not in result


class _ClosableBody:
    """A WSGI response body iterable that tracks whether `.close()` was called.

    PEP 3333 requires a server/gateway to call `close()` on the returned
    iterable if it defines one (used by real frameworks to release
    resources, e.g. a file handle behind ``wsgi.file_wrapper``).
    """

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.closed = False

    def __iter__(self) -> Any:
        return iter(self._chunks)

    def close(self) -> None:
        self.closed = True


def test_closable_body_iterable_is_closed_after_reading() -> None:
    closable = _ClosableBody([b"chunk-one", b"chunk-two"])

    def closable_app(environ: dict[str, Any], start_response: Any) -> Any:
        del environ
        start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8")])
        return closable

    environ = event_to_environ(get_event("/closable"))
    result = call_wsgi_app(closable_app, environ)
    assert result["body"] == "chunk-onechunk-two"
    assert closable.closed is True


def test_status_code_parsed_from_wsgi_status_line() -> None:
    def not_found_app(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        del environ
        start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
        return [b"nope"]

    environ = event_to_environ(get_event("/missing"))
    result = call_wsgi_app(not_found_app, environ)
    assert result["statusCode"] == 404
    assert result["body"] == "nope"


# --- round-trip against the real platform WSGI app --------------------------


def test_translator_serves_the_real_platform_app_for_index() -> None:
    app: WSGIApp = http_app()
    environ = event_to_environ(get_event("/index"))
    result = call_wsgi_app(app, environ)
    assert result["statusCode"] == 200
    assert result["headers"]["Content-Type"] == "text/markdown; charset=utf-8"
    assert "League of Agents" in result["body"]
    assert result["isBase64Encoded"] is False


def test_translator_serves_the_real_platform_app_raw_markdown_suffix() -> None:
    app: WSGIApp = http_app()
    environ = event_to_environ(get_event("/index.md"))
    result = call_wsgi_app(app, environ)
    assert result["statusCode"] == 200
    page = call_wsgi_app(app, event_to_environ(get_event("/index")))
    assert result["body"] == page["body"]


def test_translator_404s_through_the_real_platform_app() -> None:
    app: WSGIApp = http_app()
    environ = event_to_environ(get_event("/not-a-real-page"))
    result = call_wsgi_app(app, environ)
    assert result["statusCode"] == 404


# --- named-stage prefix stripping -------------------------------------------
#
# With a named API Gateway stage (StageName=prod), execute-api invocations
# carry the stage as a rawPath prefix ("/prod/about") while custom-domain
# invocations do not. The translator must strip exactly that prefix so the
# WSGI app always sees the app-relative path.


def _staged_event(raw_path: str, stage: str) -> dict:
    event = get_event(raw_path)
    event["requestContext"]["stage"] = stage
    return event


def test_named_stage_prefix_is_stripped_from_path_info() -> None:
    environ = event_to_environ(_staged_event("/prod/about", "prod"))
    assert environ["PATH_INFO"] == "/about"


def test_named_stage_bare_prefix_maps_to_root() -> None:
    environ = event_to_environ(_staged_event("/prod", "prod"))
    assert environ["PATH_INFO"] == "/"


def test_custom_domain_path_without_stage_prefix_is_untouched() -> None:
    environ = event_to_environ(_staged_event("/about", "prod"))
    assert environ["PATH_INFO"] == "/about"


def test_default_stage_never_strips() -> None:
    environ = event_to_environ(_staged_event("/prod/about", "$default"))
    assert environ["PATH_INFO"] == "/prod/about"


def test_lookalike_path_segment_is_not_stripped() -> None:
    environ = event_to_environ(_staged_event("/production/about", "prod"))
    assert environ["PATH_INFO"] == "/production/about"
