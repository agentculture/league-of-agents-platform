"""The HTML shell — the platform's one page layout.

``agentfront``'s HTTP surface (see :mod:`agentfront.http_surface`, wrapped by
:mod:`league_site.web.http`) serves every doc as raw ``text/markdown``; it
never emits HTML. :func:`with_shell` is WSGI middleware that sits in front
of that surface and, for anything that *is* a rendered page, wraps the
markdown body in one shared layout: header (wordmark + nav), a main content
region holding the markdown rendered to HTML by :mod:`league_site.web.
_markdown`, and a footer whose content comes from a small registry
(:data:`FOOTER_SLOTS`) — empty by default; see that class's docstring for
the contract a later task uses to add content there.

What gets shelled, and what stays raw
--------------------------------------
Two kinds of response must survive :func:`with_shell` byte-identical to the
unwrapped app, because agents depend on them as *raw* markdown/text, not a
page:

* Any request whose path ends in ``.md`` — the raw-markdown passthrough
  :mod:`league_site.web.http` adds on top of agentfront (``/index.md``,
  etc.). An agent fetching the ``.md`` URL must get exactly the bytes
  agentfront produced, never HTML.
* ``/llms.txt`` and ``/front`` — agentfront's own agent-discovery and
  TAUI-markdown-tier endpoints. These are machine-readable contracts, not
  human pages, even though agentfront happens to serve them as
  ``text/markdown`` too.

Everything else whose response ``Content-Type`` is ``text/markdown``
(``/``, ``/<slug>`` doc pages) is a page: it gets rendered into the shell as
HTML. Anything with another content type (``/sitemap.xml``, 404s, etc.) is
left alone — :func:`with_shell` only ever *adds* a layer around markdown
pages, it never invents behavior agentfront doesn't already have.

Usage
-----
``with_shell`` wraps whatever WSGI app you already have — typically
:func:`league_site.web.http.http_app`::

    from league_site.web.http import http_app
    from league_site.web.shell import with_shell

    application = with_shell(http_app())

It also serves the design system's stylesheet at ``/theme.css`` (see
:mod:`league_site.web.theme`) directly, so a caller only has to wrap the app
once to get both the layout and its styling.
"""

from __future__ import annotations

import html
from typing import Any, Callable

from league_site.web import theme
from league_site.web._markdown import extract_title, render

WSGIApp = Callable[[dict[str, Any], Callable[..., Any]], list[bytes]]

_MD_SUFFIX = ".md"
_UNSHELLED_PATHS = frozenset({"/llms.txt", "/front"})
_CT_MARKDOWN = "text/markdown"
_THEME_PATH = "/theme.css"

_SITE_TITLE = "League of Agents"
_SITE_DESCRIPTION = (
    "League of Agents — a turn-based arena where humans and AI agents play, "
    "compete, and get benchmarked, side by side."
)

_NAV_ITEMS = (
    ("Home", "/index"),
    ("Docs", "/"),
    ("Leaderboard", "/leaderboard"),
    ("About", "/about"),
)

_WORDMARK_HTML = (
    '<a class="wordmark" href="/index" aria-label="League of Agents — home">'
    '<span class="wordmark-glyph" aria-hidden="true">⚔</span>'
    "<span>LEAGUE</span>"
    '<span class="wordmark-accent">OF AGENTS</span>'
    "</a>"
)


class FooterSlotRegistry:
    """An ordered registry of footer HTML fragments — the footer-slot contract.

    :func:`with_shell` renders a ``<footer>`` on every shelled page whose
    content is exactly ``"".join(fragment for fragment in registry)`` — empty
    by default, so today's footer is present in the markup (for consistent
    layout / CSS) but visually empty (``.site-footer:empty`` is hidden by
    :mod:`league_site.web.theme`).

    A later task (t14 — footer acknowledgement + About page) adds content by
    calling :meth:`register` with a small, pre-rendered HTML fragment, e.g.::

        from league_site.web.shell import FOOTER_SLOTS

        FOOTER_SLOTS.register(
            '<p>Powered by AWS — proud member of the '
            '<a href="https://aws.amazon.com/developer/community/community-builders/">'
            'AWS Community Builders</a> program. <a href="/about">About</a></p>'
        )

    Call that once at import/wiring time (e.g. in the new module that owns
    the About page) — every page rendered through :data:`FOOTER_SLOTS`
    afterward carries it, with no change needed here. Fragments are trusted
    HTML (not escaped): callers are responsible for escaping any
    user-controlled text they interpolate before calling :meth:`register`.

    Tests and other callers that don't want to touch the process-wide
    default can construct their own ``FooterSlotRegistry()`` and pass it to
    :func:`with_shell` via the ``footer_slots`` keyword instead.
    """

    def __init__(self) -> None:
        self._fragments: list[str] = []

    def register(self, html_fragment: str) -> None:
        """Append *html_fragment* to the footer, after anything already registered."""
        self._fragments.append(html_fragment)

    def render(self) -> str:
        """Return the concatenated footer HTML (empty string if nothing registered)."""
        return "".join(self._fragments)


#: Process-wide default footer-slot registry. Empty until a later task
#: (t14) registers content into it — see :class:`FooterSlotRegistry`.
FOOTER_SLOTS = FooterSlotRegistry()


def with_shell(app: WSGIApp, *, footer_slots: FooterSlotRegistry | None = None) -> WSGIApp:
    """Wrap *app* so every rendered page carries the shared HTML shell.

    *footer_slots* defaults to the process-wide :data:`FOOTER_SLOTS`
    registry; pass an explicit :class:`FooterSlotRegistry` (e.g. in tests)
    to avoid sharing that global state.
    """
    slots = FOOTER_SLOTS if footer_slots is None else footer_slots

    def application(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        path = environ.get("PATH_INFO", "/")

        if path == _THEME_PATH:
            return _serve_theme_css(start_response)

        captured: dict[str, Any] = {}

        def capture_start_response(
            status: str, headers: list[tuple[str, str]], exc_info: Any = None
        ) -> Any:
            captured["status"] = status
            captured["headers"] = headers
            return lambda _data: None

        result = app(environ, capture_start_response)
        try:
            body = b"".join(result)
        finally:
            close = getattr(result, "close", None)
            if close is not None:
                close()

        content_type = _header(captured["headers"], "Content-Type")
        if _is_raw_passthrough(path, content_type):
            start_response(captured["status"], captured["headers"])
            return [body]

        page = _render_page(body.decode("utf-8"), slots)
        page_bytes = page.encode("utf-8")
        start_response(
            captured["status"],
            [
                ("Content-Type", "text/html; charset=utf-8"),
                ("Content-Length", str(len(page_bytes))),
            ],
        )
        return [page_bytes]

    return application


def _is_raw_passthrough(path: str, content_type: str) -> bool:
    """True when *path* must return byte-identical, un-shelled content."""
    if path.endswith(_MD_SUFFIX):
        return True
    if path in _UNSHELLED_PATHS:
        return True
    return not content_type.startswith(_CT_MARKDOWN)


def _header(headers: list[tuple[str, str]], name: str) -> str:
    lowered = name.lower()
    for key, value in headers:
        if key.lower() == lowered:
            return value
    return ""


def _serve_theme_css(start_response: Any) -> list[bytes]:
    body = theme.STYLESHEET.encode("utf-8")
    start_response(
        "200 OK",
        [
            ("Content-Type", "text/css; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ],
    )
    return [body]


def _render_page(markdown_text: str, slots: FooterSlotRegistry) -> str:
    title = extract_title(markdown_text) or _SITE_TITLE
    page_title = title if title == _SITE_TITLE else f"{title} — {_SITE_TITLE}"
    body_html = render(markdown_text)
    nav_html = "".join(f'<a href="{href}">{label}</a>' for label, href in _NAV_ITEMS)
    footer_html = slots.render()
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{html.escape(_SITE_DESCRIPTION)}">
<title>{html.escape(page_title)}</title>
<link rel="stylesheet" href="{_THEME_PATH}">
</head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
<header class="site-header">
<div class="wrap">
{_WORDMARK_HTML}
<nav aria-label="Primary">{nav_html}</nav>
</div>
</header>
<main id="main" class="wrap">
{body_html}
</main>
<footer class="site-footer">
<div class="wrap">{footer_html}</div>
</footer>
</body>
</html>
"""
