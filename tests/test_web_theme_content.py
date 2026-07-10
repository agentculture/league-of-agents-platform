"""Tests for the rewritten landing page and first-visit onboarding content."""

from __future__ import annotations

from typing import Any

from league_site.web.app import build_app
from league_site.web.http import WSGIApp, http_app
from league_site.web.shell import FooterSlotRegistry, with_shell


def _get(app: WSGIApp, path: str) -> tuple[str, dict[str, str], bytes]:
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path}
    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


def _shelled() -> WSGIApp:
    return with_shell(http_app(), footer_slots=FooterSlotRegistry())


def test_start_human_and_start_agent_docs_are_registered() -> None:
    app = build_app()
    for slug in ("start-human", "start-agent"):
        doc = app.get_doc(slug)
        assert doc is not None, slug
        assert doc.text.strip(), slug


def test_landing_page_names_the_three_onboarding_paths() -> None:
    _, _, body = _get(_shelled(), "/index")
    text = body.decode("utf-8")
    assert "Play as a human" in text
    assert "Bring your agent" in text
    assert "Watch matches" in text


def test_landing_page_links_to_both_onboarding_pages() -> None:
    _, _, body = _get(_shelled(), "/index")
    text = body.decode("utf-8")
    assert 'href="/start-human"' in text
    assert 'href="/start-agent"' in text


def test_landing_page_raw_markdown_carries_the_same_three_paths() -> None:
    """The raw ``.md`` passthrough is untouched by the shell but is the same content."""
    app = http_app()
    _, _, body = _get(app, "/index.md")
    text = body.decode("utf-8")
    assert "Play as a human" in text
    assert "Bring your agent" in text
    assert "Watch matches" in text


def test_start_human_page_covers_the_human_entry_path() -> None:
    _, _, body = _get(_shelled(), "/start-human")
    text = body.decode("utf-8")
    assert "GitHub" in text
    assert "Google" in text
    assert "leaderboard" in text.lower()


def test_start_agent_page_covers_http_mcp_and_cli_entry_paths() -> None:
    _, _, body = _get(_shelled(), "/start-agent")
    text = body.decode("utf-8")
    for term in ("HTTP", "MCP", "CLI", "token"):
        assert term in text


def test_start_agent_page_links_back_to_the_full_agent_onboarding_doc() -> None:
    _, _, body = _get(_shelled(), "/start-agent")
    text = body.decode("utf-8")
    assert 'href="/agent-onboarding"' in text


def test_agents_token_page_is_registered_and_documents_self_serve_minting() -> None:
    """The /agents page carries the self-serve acquisition path (issue #12)."""
    app = build_app()
    doc = app.get_doc("agents")
    assert doc is not None
    assert "/auth/agents" in doc.text
    assert "curl" in doc.text


def test_agents_token_page_is_served_over_http() -> None:
    _, _, body = _get(_shelled(), "/agents")
    text = body.decode("utf-8")
    assert "/auth/agents" in text


def test_start_agent_page_points_at_self_serve_token_minting() -> None:
    """Step one no longer says 'ask the operator' — it links the mint-it-yourself page."""
    _, _, body = _get(_shelled(), "/start-agent")
    text = body.decode("utf-8")
    assert "/auth/agents" in text
    assert 'href="/agents"' in text


def test_start_agent_content_is_grounded_in_agent_onboarding_doc() -> None:
    """The onboarding teaser doesn't contradict the canonical agent-onboarding doc."""
    app = build_app()
    onboarding = app.get_doc("agent-onboarding")
    start_agent = app.get_doc("start-agent")
    assert onboarding is not None
    assert start_agent is not None
    # Both describe the same three entry paths.
    for surface in ("HTTP", "MCP", "CLI"):
        assert surface in onboarding.text
        assert surface in start_agent.text
