"""Tests for :func:`league_site.web.app.build_app` — the single registry."""

from __future__ import annotations

from league_site import __version__
from league_site.cli._commands.whoami import report
from league_site.web.app import build_app


def test_build_app_identity() -> None:
    app = build_app()
    assert app.name == "league-of-agents-platform"
    assert app.version == __version__


def test_index_doc_registered_from_content_dir() -> None:
    app = build_app()
    doc = app.get_doc("index")
    assert doc is not None
    assert "League of Agents" in doc.text


def test_repo_docs_tree_wired_in() -> None:
    """``docs/skill-sources.md`` is registered alongside this package's own content."""
    app = build_app()
    doc = app.get_doc("skill-sources")
    assert doc is not None
    assert "Skill upstream sources" in doc.title


def test_whoami_tool_reuses_existing_identity_logic() -> None:
    """The tool must call, not duplicate, ``league_site.cli._commands.whoami.report``."""
    app = build_app()
    tool = app.get_tool("whoami")
    assert tool is not None
    assert tool.func() == report()


def test_build_app_returns_fresh_instance_each_call() -> None:
    first = build_app()
    second = build_app()
    assert first is not second
    # Mutating one registry must not leak into the other.
    first.add_doc(slug="scratch-probe", title="Scratch", text="# Scratch\n")
    assert second.get_doc("scratch-probe") is None
