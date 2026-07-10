"""Cross-surface tests: a doc/tool registered once must appear on HTTP, MCP,
and CLI with no extra wiring — the whole point of a single agentfront registry.
"""

from __future__ import annotations

import argparse

from agentfront.testing import assert_surfaces_agree, run_cli

from league_site.web.app import build_app


def test_platform_registry_surfaces_agree() -> None:
    """The platform's own docs/tools never drift across HTTP/CLI/MCP/TAUI."""
    assert_surfaces_agree(build_app())


def test_probe_doc_and_tool_appear_on_all_three_surfaces() -> None:
    """Register a probe doc + tool once; confirm HTTP, MCP, and CLI all expose it."""
    app = build_app()
    app.add_doc(slug="probe-doc", title="Probe Doc", text="# Probe Doc\nprobe content.\n")

    @app.tool(name="probe_tool")
    def probe_tool() -> str:
        """A throwaway tool registered once to prove no surface needs extra wiring."""
        return "probe-result"

    # --- HTTP: doc served as raw markdown, and listed in the sitemap -------
    wsgi = app.http_app()
    captured: dict[str, object] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status

    body = b"".join(wsgi({"REQUEST_METHOD": "GET", "PATH_INFO": "/probe-doc"}, start_response))
    assert captured["status"] == "200 OK"
    assert "probe content" in body.decode("utf-8")

    sitemap = b"".join(wsgi({"REQUEST_METHOD": "GET", "PATH_INFO": "/sitemap.xml"}, start_response))
    assert b"/probe-doc" in sitemap

    # --- MCP: the single `run` tool's embedded catalog carries the probe --
    server = app.mcp_server()
    assert "probe_tool" in server.run_tool.description

    # --- CLI: the derived parser dispatches a `probe_tool` verb -----------
    cli_result = run_cli(app, ["probe_tool"])
    assert cli_result.exit_code == 0
    assert "probe-result" in cli_result.stdout

    # Introspect the parser itself too (not just its runtime behavior).
    parser = app.cli()
    sub_action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    assert "probe_tool" in sub_action.choices
