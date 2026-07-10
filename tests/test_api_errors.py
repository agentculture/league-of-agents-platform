"""Tests for :mod:`league_site.api.errors` — the ``{code, message}`` error envelope."""

from __future__ import annotations

from league_site.api import errors


def test_api_error_is_an_exception() -> None:
    assert isinstance(errors.bad_request("x"), Exception)


def test_bad_request_default_code_and_message() -> None:
    err = errors.bad_request("nope")
    assert err.status == "400 Bad Request"
    assert err.code == "bad_request"
    assert str(err) == "nope"


def test_bad_request_accepts_a_custom_code() -> None:
    err = errors.bad_request("unknown", code="unknown_mode")
    assert err.status == "400 Bad Request"
    assert err.code == "unknown_mode"


def test_unauthorized_default_message() -> None:
    err = errors.unauthorized()
    assert err.status == "401 Unauthorized"
    assert err.code == "unauthorized"
    assert str(err) == "authentication required"


def test_unauthorized_custom_message() -> None:
    err = errors.unauthorized("no token")
    assert str(err) == "no token"


def test_forbidden_default_message() -> None:
    err = errors.forbidden()
    assert err.status == "403 Forbidden"
    assert err.code == "forbidden"


def test_not_found_requires_a_message() -> None:
    err = errors.not_found("no match found with id 'm1'")
    assert err.status == "404 Not Found"
    assert err.code == "not_found"
    assert str(err) == "no match found with id 'm1'"


def test_conflict_default_code() -> None:
    err = errors.conflict("cannot pause")
    assert err.status == "409 Conflict"
    assert err.code == "conflict"


def test_conflict_accepts_a_custom_code() -> None:
    err = errors.conflict("not completed", code="not_completed")
    assert err.code == "not_completed"


def test_method_not_allowed_default_message() -> None:
    err = errors.method_not_allowed()
    assert err.status == "405 Method Not Allowed"
    assert err.code == "method_not_allowed"
