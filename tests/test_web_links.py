"""Link integrity — zero broken internal links anywhere on the site (t9).

The crawl contract (spec c20/h17): starting from ``/``, every internal
``href`` reachable by following rendered pages must resolve to ``200 OK``
with a non-empty body, against :func:`league_site.web.http.site_app` — the
real composed production app (the same reason as
:mod:`tests.test_web_raw_surface`: ``/leaderboard`` never passes through
``with_shell``, so the bare shelled fixture would 404 it and make the crawl
vacuous).

External links (``http://``/``https://``) are *content*, not fetched
resources: the unit crawl collects and shape-checks them but never fetches
— the live Playwright crawl at the ship gate (plan t8) covers them against
the deployed site.

Raw agent surfaces (``*.md``, ``/llms.txt``, ``/front``) are still fetched
and asserted ``200``, but never parsed for further links: they are
machine-readable markdown, not navigation.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from league_site.web.http import WSGIApp, site_app

_HREF_RE = re.compile(r'href="([^"]+)"')
_CRAWL_CAP = 200
_NAV_TARGETS = ("/index", "/docs", "/leaderboard", "/about")
_NO_RECURSE_PATHS = frozenset({"/llms.txt", "/front"})
#: Auth *action* routes (the header's "Sign in"/"Sign out" entries link
#: ``/auth/login/github`` / ``/auth/logout``) are provider-gated redirects,
#: not navigable content pages — collected-but-not-crawled, exactly like the
#: ``mailto:``/``tel:`` links below. Fetching one hits the OAuth handoff (a
#: 302, or a 400 when the flow is disabled pre-OAuth), never a 200 HTML page.
_NON_NAVIGABLE_PREFIXES = ("/auth/",)


def _get(app: WSGIApp, path: str) -> tuple[str, dict[str, str], bytes]:
    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path}
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


def _normalize(href: str) -> str:
    """Internal href -> visitable path: drop fragment and query string."""
    parts = urlsplit(href)
    return parts.path


def _crawl(app: WSGIApp) -> tuple[dict[str, str], set[str]]:
    """Breadth-first crawl from ``/``.

    Returns ``(visited path -> status, external hrefs seen)``. Fails fast
    inside the loop so a broken link names the page that linked to it.
    """
    frontier = ["/"]
    parents: dict[str, str] = {"/": "(start)"}
    visited: dict[str, str] = {}
    externals: set[str] = set()

    while frontier:
        assert len(visited) <= _CRAWL_CAP, "crawl cap exceeded — link loop?"
        path = frontier.pop(0)
        if path in visited:
            continue
        status, headers, body = _get(app, path)
        visited[path] = status
        assert status == "200 OK", (
            f"broken internal link: {path!r} -> {status!r} " f"(linked from {parents[path]!r})"
        )
        assert body, f"empty body at {path!r} (linked from {parents[path]!r})"

        content_type = headers.get("Content-Type", "")
        recurse = (
            content_type.startswith("text/html")
            and not path.endswith(".md")
            and path not in _NO_RECURSE_PATHS
        )
        if not recurse:
            continue
        for href in _HREF_RE.findall(body.decode("utf-8")):
            if href.startswith(("http://", "https://")):
                externals.add(href)
                continue
            if href.startswith(("#", "mailto:", "tel:")):
                continue
            if not href.startswith("/") or href.startswith("//"):
                continue
            if href.startswith(_NON_NAVIGABLE_PREFIXES):
                continue
            target = _normalize(href)
            if target and target not in visited and target not in frontier:
                parents.setdefault(target, path)
                frontier.append(target)
    return visited, externals


def test_every_internal_link_reachable_from_the_landing_page_resolves() -> None:
    visited, _ = _crawl(site_app())
    assert "/" in visited
    # The crawl asserted 200 + non-empty per page; this pins that it
    # actually traversed beyond the landing page.
    assert len(visited) > 1, "crawl never left the landing page"


def test_nav_and_footer_targets_resolve_with_real_content() -> None:
    app = site_app()
    for path in _NAV_TARGETS:
        status, _, body = _get(app, path)
        assert status == "200 OK", path
        assert len(body) > 100, f"{path} rendered a suspiciously empty page"


def test_raw_agent_surfaces_resolve_but_are_not_navigation() -> None:
    app = site_app()
    for path in ("/index.md", "/llms.txt", "/front"):
        status, _, body = _get(app, path)
        assert status == "200 OK", path
        assert body, path


def test_external_links_are_well_formed_and_never_fetched_here() -> None:
    _, externals = _crawl(site_app())
    for url in externals:
        parts = urlsplit(url)
        assert parts.scheme in ("http", "https"), url
        assert parts.netloc, f"external link without a host: {url!r}"
