"""API Gateway HTTP API (payload format 2.0) <-> WSGI (PEP 3333) translator.

Hand-rolled, stdlib-only. No third-party adapter (e.g. ``apig-wsgi`` /
``serverless-wsgi``) is added — this repo's ``pyproject.toml`` is frozen for
this task, and the translation surface is small enough that owning it here
beats a new dependency.

Two functions carry the whole contract:

* :func:`event_to_environ` — API Gateway v2 event -> WSGI ``environ`` dict.
* :func:`call_wsgi_app` — invoke a WSGI app with that environ, fold its
  ``start_response`` call and body iterable back into an API Gateway v2
  response dict (status, headers, cookies, base64-for-binary body).

Event/response shape reference (payload format version 2.0):
https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-develop-integrations-lambda.html
"""

from __future__ import annotations

import base64
import io
import sys
from typing import Any, Callable, Iterable

#: A PEP 3333 WSGI application callable.
WSGIApp = Callable[[dict[str, Any], Callable[..., Any]], Iterable[bytes]]

_DEFAULT_PROTOCOL = "HTTP/1.1"
_DEFAULT_STATUS = "500 Internal Server Error"


def event_to_environ(event: dict[str, Any]) -> dict[str, Any]:
    """Build a WSGI ``environ`` dict from an API Gateway HTTP API v2 *event*.

    Uses ``rawPath``/``rawQueryString`` verbatim (not the pre-parsed
    ``queryStringParameters`` dict, which collapses repeated keys) so
    repeated query parameters survive the round trip. Request headers are
    HTTP API v2's already-lower-cased, comma-joined dict; the separate
    ``cookies`` list is re-joined into a single ``Cookie`` header, matching
    what a real HTTP client would have sent.
    """
    request_context = event.get("requestContext", {})
    http_ctx = request_context.get("http", {})

    method = http_ctx.get("method", "GET")
    path = event.get("rawPath") or http_ctx.get("path") or "/"
    query_string = event.get("rawQueryString", "") or ""

    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}

    cookies = event.get("cookies")
    if cookies:
        headers.setdefault("cookie", "; ".join(cookies))

    body_bytes = _decode_body(event)

    content_type = headers.pop("content-type", "")
    content_length = headers.pop("content-length", str(len(body_bytes)))
    host = headers.pop("host", "localhost")

    environ: dict[str, Any] = {
        "REQUEST_METHOD": method,
        "SCRIPT_NAME": "",
        "PATH_INFO": path,
        "QUERY_STRING": query_string,
        "CONTENT_TYPE": content_type,
        "CONTENT_LENGTH": content_length,
        "SERVER_NAME": host.split(":")[0],
        "SERVER_PORT": host.split(":")[1] if ":" in host else "443",
        "SERVER_PROTOCOL": http_ctx.get("protocol", _DEFAULT_PROTOCOL),
        "REMOTE_ADDR": http_ctx.get("sourceIp", ""),
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "https",
        "wsgi.input": io.BytesIO(body_bytes),
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": False,
        "wsgi.multiprocess": True,
        "wsgi.run_once": False,
    }

    for name, value in headers.items():
        env_key = "HTTP_" + name.upper().replace("-", "_")
        environ[env_key] = value

    return environ


def _decode_body(event: dict[str, Any]) -> bytes:
    body = event.get("body")
    if body is None:
        return b""
    if event.get("isBase64Encoded"):
        return base64.b64decode(body)
    return body.encode("utf-8")


def call_wsgi_app(app: WSGIApp, environ: dict[str, Any]) -> dict[str, Any]:
    """Invoke *app* with *environ* and return an API Gateway v2 response dict.

    Repeated response headers are comma-joined per HTTP semantics, except
    ``Set-Cookie`` — API Gateway v2 requires each cookie to be its own entry
    in the response's ``cookies`` array rather than a folded header, so
    those are pulled out separately. The body is returned as text
    (``isBase64Encoded: False``) when it decodes as UTF-8, otherwise
    base64-encoded with ``isBase64Encoded: True``.
    """
    captured: dict[str, Any] = {}

    def start_response(
        status: str, response_headers: list[tuple[str, str]], exc_info: Any = None
    ) -> Callable[[bytes], None]:
        captured["status"] = status
        captured["headers"] = response_headers
        return lambda data: None  # legacy WSGI write() callable; unused by this adapter

    body_iter = app(environ, start_response)
    try:
        body = b"".join(body_iter)
    finally:
        close = getattr(body_iter, "close", None)
        if close is not None:
            close()

    status_line = captured.get("status", _DEFAULT_STATUS)
    status_code = int(status_line.split(" ", 1)[0])

    out_headers: dict[str, str] = {}
    cookies: list[str] = []
    for name, value in captured.get("headers", []):
        if name.lower() == "set-cookie":
            cookies.append(value)
        elif name in out_headers:
            out_headers[name] = f"{out_headers[name]}, {value}"
        else:
            out_headers[name] = value

    response: dict[str, Any] = {"statusCode": status_code, "headers": out_headers}
    if cookies:
        response["cookies"] = cookies

    try:
        response["body"] = body.decode("utf-8")
        response["isBase64Encoded"] = False
    except UnicodeDecodeError:
        response["body"] = base64.b64encode(body).decode("ascii")
        response["isBase64Encoded"] = True

    return response
