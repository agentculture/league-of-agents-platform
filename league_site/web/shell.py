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
:mod:`league_site.web.theme`) and the site's one first-party script at
``/site.js`` (see :mod:`league_site.web.scripts`) directly, so a caller
only has to wrap the app once to get the layout, its styling, and its
progressive enhancements (theme toggle, reveal-on-scroll).

JavaScript in the shell — two pieces, both first-party
------------------------------------------------------
Shelled pages carry exactly two scripts, and raw passthrough responses
carry none (they are byte-identical to the unwrapped app, as ever):

* An **inline pre-paint snippet** (:data:`league_site.web.scripts.
  PRE_PAINT_JS`) placed in ``<head>`` *before* the stylesheet link, so it
  runs before first paint: it applies a stored explicit theme choice to
  ``<html data-theme>`` (no flash of the wrong theme) and stamps
  ``html[data-js]`` so JS-gated reveal styles can never hide content when
  JavaScript is off.
* A **deferred ``/site.js``** tag for everything that can wait for the
  DOM: the header theme-toggle button's behavior and the
  IntersectionObserver reveal. With JavaScript disabled the toggle button
  is inert but harmless and theming falls back to the OS preference.
"""

from __future__ import annotations

import html
import re
from typing import Any, Callable

from league_site.web import hero, scripts, theme
from league_site.web._markdown import extract_title, render

WSGIApp = Callable[[dict[str, Any], Callable[..., Any]], list[bytes]]

_MD_SUFFIX = ".md"
_UNSHELLED_PATHS = frozenset({"/llms.txt", "/front"})
_CT_MARKDOWN = "text/markdown"
_THEME_PATH = "/theme.css"
_SITE_JS_PATH = "/site.js"

#: The landing page is reachable at two URLs — the canonical ``/`` (see
#: :func:`league_site.web.http._with_root_landing`, which rewrites
#: ``PATH_INFO`` internally rather than redirecting, so the root URL stays
#: canonical in the address bar) and the legacy/stable ``/index``, which
#: keeps serving directly rather than 301-ing to ``/``. Both serve
#: byte-identical authored content (``league_site/web/content/index.md``)
#: whose own ``# `` heading already reads "League of Agents" — but both
#: paths are pinned to the plain site name here rather than trusting that
#: coincidence, the same title a page with no H1 at all would get (see
#: :func:`_render_page`), so the landing's title stays correct even if that
#: heading is ever edited to something more conversational than the site
#: name.
#:
#: agentfront's own generated doc catalog — formerly served at ``/``, where
#: its body's honest ``# Documentation`` heading was wrong as a landing
#: *title* — now lives at its own stable path, ``/docs`` (platform#14). It
#: is not a landing path, so it is deliberately absent from this set: it
#: falls through to the normal case in :func:`_render_page` below and gets
#: its own heading as its title, exactly like any other doc page.
_LANDING_PATHS = frozenset({"/", "/index"})

_SITE_TITLE = "League of Agents"
_SITE_DESCRIPTION = (
    "League of Agents — a turn-based arena where humans and AI agents play, "
    "compete, and get benchmarked, side by side."
)

_NAV_ITEMS = (
    ("Home", "/index"),
    ("Docs", "/docs"),
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

#: The header theme-toggle button, rendered in its "system" state — the
#: truthful default for markup that ships before any script runs (no
#: ``data-theme`` attribute means the OS decides). ``/site.js`` repaints
#: the glyph/title/aria-label to the *current* state at load and on every
#: click (cycling light → dark → system). With JavaScript disabled the
#: button is inert but harmless: it has a real accessible name, visible
#: text, and ``type="button"`` so it can never submit anything — pages
#: stay fully readable and theming falls back to the OS preference.
#: Styled by ``.theme-toggle`` in :mod:`league_site.web.theme`.
_THEME_TOGGLE_HTML = (
    '<button type="button" id="theme-toggle" class="theme-toggle"'
    ' title="Theme: system"'
    ' aria-label="Theme: system — activate to switch to light">◐</button>'
)


def header_html() -> str:
    """The canonical site header: wordmark + primary nav + theme toggle.

    Public so standalone page shells that render *ahead of* ``with_shell``
    (:mod:`league_site.viewer.wsgi` today) carry the same header — same
    nav, same toggle — instead of a drifting hand copy. One source, every
    page.
    """
    nav_html = "".join(f'<a href="{href}">{label}</a>' for label, href in _NAV_ITEMS)
    return f"""<header class="site-header">
<div class="wrap">
{_WORDMARK_HTML}
<nav aria-label="Primary">{nav_html}</nav>
{_THEME_TOGGLE_HTML}
</div>
</header>"""


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

        if path == _SITE_JS_PATH:
            return _serve_site_js(start_response)

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

        page = _render_page(body.decode("utf-8"), slots, path=path)
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


def _serve_site_js(start_response: Any) -> list[bytes]:
    body = scripts.SITE_JS.encode("utf-8")
    start_response(
        "200 OK",
        [
            ("Content-Type", "application/javascript; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ],
    )
    return [body]


#: The first rendered ``<h1>`` block of a landing page's markdown body —
#: stripped (rendered HTML only; the raw ``.md`` passthrough never comes
#: through :func:`_render_page` at all) because the hero's headline is the
#: landing page's one semantic ``<h1>``. See :mod:`league_site.web.hero`'s
#: docstring for the full rationale behind this choice.
_LANDING_H1_RE = re.compile(r"<h1>.*?</h1>\n?", re.S)


def _render_page(markdown_text: str, slots: FooterSlotRegistry, *, path: str) -> str:
    title = _SITE_TITLE if path in _LANDING_PATHS else extract_title(markdown_text) or _SITE_TITLE
    page_title = title if title == _SITE_TITLE else f"{title} — {_SITE_TITLE}"
    body_html = render(markdown_text)
    if path in _LANDING_PATHS:
        # The hero (league_site.web.hero) leads the landing page — first
        # child of <main>, before the markdown body. It orchestrates its
        # own entrance, so it carries no .reveal class (site.js skips it
        # when stamping the stagger). Its <h1> replaces the markdown's.
        body_html = hero.HERO_HTML + "\n" + _LANDING_H1_RE.sub("", body_html, count=1)
    footer_html = slots.render()
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{html.escape(_SITE_DESCRIPTION)}">
<title>{html.escape(page_title)}</title>
<script>{scripts.PRE_PAINT_JS}</script>
<link rel="stylesheet" href="{_THEME_PATH}">
<script defer src="{_SITE_JS_PATH}"></script>
</head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
{header_html()}
<main id="main" class="wrap">
{body_html}
</main>
<footer class="site-footer">
<div class="wrap">{footer_html}</div>
</footer>
</body>
</html>
"""
