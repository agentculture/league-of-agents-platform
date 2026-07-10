"""The platform's agentfront ``App`` — the single registry.

Docs and tools are declared **once** here, into one :class:`agentfront.App`.
The HTTP site (markdown pages), the MCP server, and the CLI are all *derived*
from this one registry — see :mod:`agentfront.app` — so the three surfaces
cannot drift apart. Nothing is ever registered directly on a surface; every
``add_doc`` / ``@app.tool`` call in :func:`build_app` is the only place a doc
or tool is declared.

:mod:`league_site.web.http` derives the HTTP surface (plus a thin
raw-markdown passthrough) on top of the registry built here.
"""

from __future__ import annotations

from pathlib import Path

from agentfront import App

from league_site import __version__
from league_site.cli._commands.whoami import report as _whoami_report

_WEB_DIR = Path(__file__).resolve().parent
_CONTENT_DIR = _WEB_DIR / "content"

_DESCRIPTION = (
    "League of Agents — a turn-based arena for humans and agents, for fun and "
    "for benchmarks. https://league-of-agents.ai"
)


def _repo_docs_dir() -> Path | None:
    """Locate the repo's top-level ``docs/`` tree by walking up from this file.

    Mirrors :func:`league_site.cli._commands.whoami.find_culture_yaml`'s
    walk-up-from-``__file__`` approach: in an editable/source install this
    finds the repo root's ``docs/``; in a wheel install no ``docs/`` ships
    alongside the package and callers get ``None`` (the registry then just
    carries this package's own ``content/`` docs).
    """
    for parent in _WEB_DIR.parents:
        candidate = parent / "docs"
        if candidate.is_dir():
            return candidate
    return None


def build_app() -> App:
    """Construct the platform's agentfront ``App`` — the single registry.

    Registers, in order:

    * ``league_site/web/content/*.md`` — the site's own authored pages
      (``index.md`` today; more pages land in a later task).
    * the repo's ``docs/`` tree (``docs/skill-sources.md`` and anything under
      ``docs/specs/``/``docs/plans/``) — wired into the same registry so it
      appears on every surface without a second copy anywhere.
    * a ``whoami`` tool that calls the existing
      :func:`league_site.cli._commands.whoami.report` — the identity logic
      lives in exactly one place; this only exposes it on the agentfront
      surfaces too.

    Returns a fresh :class:`App` on every call. Callers that want a shared,
    long-lived instance (e.g. a dev server) should call this once and reuse
    the result rather than rebuild it per request.
    """
    app = App(
        name="league-of-agents-platform",
        version=__version__,
        description=_DESCRIPTION,
    )

    app.add_docs_dir(str(_CONTENT_DIR))

    repo_docs = _repo_docs_dir()
    if repo_docs is not None:
        app.add_docs_dir(str(repo_docs))

    @app.tool
    def whoami() -> dict[str, object]:
        """Report this agent's nick, version, backend, and served model."""
        return _whoami_report()

    return app
