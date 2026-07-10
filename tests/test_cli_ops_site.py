"""Tests for ``league-site site serve``.

Read-only wrt platform state (no ``--apply``/dry-run split — see the
module's own docstring). ``league_site.web.http.serve`` is monkeypatched so
the test suite never actually binds a socket or blocks on
``serve_forever()``.
"""

from __future__ import annotations

import json

import pytest

from league_site.cli import main
from league_site.cli._commands import site


class _FakeServer:
    """Stand-in for the ``wsgiref.simple_server.WSGIServer`` :func:`serve` returns."""

    def __init__(self, port: int) -> None:
        self.server_port = port
        self.served = False
        self.closed = False

    def serve_forever(self) -> None:
        self.served = True
        # Simulate an operator hitting Ctrl+C immediately so the test never blocks.
        raise KeyboardInterrupt

    def server_close(self) -> None:
        self.closed = True


def test_site_serve_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _FakeServer(8099)
    captured: dict[str, object] = {}

    def fake_serve(*, host: str, port: int) -> _FakeServer:
        captured["host"] = host
        captured["port"] = port
        return fake

    monkeypatch.setattr(site, "_serve", fake_serve)
    rc = main(["site", "serve", "--port", "8099", "--json"])
    assert rc == 0
    assert captured == {"host": "127.0.0.1", "port": 8099}
    assert fake.served is True
    assert fake.closed is True
    out, err = capsys.readouterr()
    assert err == ""
    payload = json.loads(out)
    assert payload == {"host": "127.0.0.1", "port": 8099, "url": "http://127.0.0.1:8099"}


def test_site_serve_text_mode_diagnostic_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _FakeServer(8000)
    monkeypatch.setattr(site, "_serve", lambda *, host, port: fake)
    rc = main(["site", "serve"])
    assert rc == 0
    out, err = capsys.readouterr()
    assert "serving http://127.0.0.1:8000" in err
    assert out == ""


def test_site_serve_default_port(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeServer(8000)
    captured: dict[str, object] = {}

    def fake_serve(*, host: str, port: int) -> _FakeServer:
        captured["port"] = port
        return fake

    monkeypatch.setattr(site, "_serve", fake_serve)
    rc = main(["site", "serve"])
    assert rc == 0
    assert captured["port"] == 8000


def test_site_bare_noun_is_non_empty(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["site"])
    assert rc == 0
    assert capsys.readouterr().out.strip()


def test_site_serve_has_no_apply_flag() -> None:
    """h9 scope is *state-mutating* actions; ``site serve`` isn't one, so no ``--apply``."""
    from league_site.cli import _build_parser

    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["site", "serve", "--apply"])
