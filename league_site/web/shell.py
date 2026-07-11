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

Versioned asset URLs
---------------------
Every stylesheet/script/font URL this module emits — the ``<link
rel="stylesheet">``, the deferred ``<script src>``, and both font
``<link rel="preload">`` tags — carries a ``?v=<hash>`` query built once at
import time from the exact bytes served (see :func:`asset_url`). This is
the fix for the incident documented in
``docs/runbooks/cloudflare-league-of-agents-ai.md`` ("Cache purge
(emergency, /theme.css and /site.js)"): a deploy that changes an asset's
bytes changes its URL too, so shelled HTML can never reference stale bytes
at any caching layer (Cloudflare's edge, a visitor's browser) — there is
nothing stale left to purge, ever. ``/theme.css`` and ``/site.js`` are
served with the same long-lived immutable ``Cache-Control`` the fonts
already carry, safe now that the URL changes with the content.
:mod:`league_site.viewer.wsgi` and :mod:`league_site.profiles.wsgi` — the
two standalone page shells that render ahead of this module — call
:func:`asset_url` too, so there is exactly one hash computation for the
whole site.

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

import hashlib
import html
import re
from pathlib import Path
from typing import Any, Callable

from league_site.auth import sessions
from league_site.auth.wsgi import SESSION_ENVIRON_KEY
from league_site.web import fonts, hero, scripts, theme
from league_site.web._markdown import extract_title, render

WSGIApp = Callable[[dict[str, Any], Callable[..., Any]], list[bytes]]

_MD_SUFFIX = ".md"
_UNSHELLED_PATHS = frozenset({"/llms.txt", "/front"})
_CT_MARKDOWN = "text/markdown"
_THEME_PATH = "/theme.css"
_SITE_JS_PATH = "/site.js"
#: URL prefix the shell serves vendored fonts under. A GET/HEAD whose path
#: is ``_FONTS_PREFIX + <name>`` for a name in :data:`league_site.web.fonts.
#: FONTS` is answered directly with the font bytes; anything else under the
#: prefix falls through to the wrapped app (a 404).
_FONTS_PREFIX = "/fonts/"

#: The site's tab icon and link-preview card, served as immutable static
#: assets at ``/favicon.svg`` and ``/og.png`` — the same static pattern the
#: fonts use (GET/HEAD only, long-lived immutable ``Cache-Control``, versioned
#: reference via :func:`asset_url`). Both are package data under ``assets/``
#: next to this module, so hatchling ships them in the wheel automatically,
#: exactly like the vendored fonts (see :mod:`league_site.web.fonts`).
#:
#: The favicon is a dawn-palette SVG carrying its own ``prefers-color-scheme``
#: dark variant; the og-image is a committed 1200x630 PNG build artifact
#: (regenerated by ``scripts/generate-og-image.py`` — Pillow is not a runtime
#: dependency).
_FAVICON_PATH = "/favicon.svg"
_OG_IMAGE_PATH = "/og.png"
_FAVICON_MEDIA_TYPE = "image/svg+xml"
_OG_IMAGE_MEDIA_TYPE = "image/png"

_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
#: Read once at import from the package's ``assets/`` dir (the same
#: package-data pattern :mod:`league_site.web.fonts` uses for the woff2 files).
_FAVICON_BYTES = (_ASSETS_DIR / "favicon.svg").read_bytes()
_OG_IMAGE_BYTES = (_ASSETS_DIR / "og.png").read_bytes()

#: The site's canonical public origin. Hardcoded rather than derived from the
#: request's ``Host`` header because Open Graph / Twitter Card ``og:image``
#: and ``og:url`` MUST be absolute URLs (scrapers do not resolve relative
#: ones), and because the production deploy sits behind Cloudflare, where the
#: inbound Host can be an edge/worker hostname rather than the public domain.
#: There is exactly one public origin for this site.
_CANONICAL_ORIGIN = "https://league-of-agents.ai"

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
    ("Play", "/play"),
    ("Docs", "/docs"),
    ("Leaderboard", "/leaderboard"),
    ("About", "/about"),
)

_WORDMARK_HTML = (
    '<a class="wordmark" href="/index" aria-label="League of Agents — home">'
    '<span class="wordmark-glyph" aria-hidden="true">⚔</span>'
    "<span>League</span>"
    '<span class="wordmark-accent">of Agents</span>'
    "</a>"
)

#: The header theme-toggle button. The server-rendered accessible name is
#: deliberately STATE-NEUTRAL ("Switch theme"): markup ships before any
#: script runs, and a visitor with a stored explicit choice would be lied
#: to by a hardcoded "Theme: system …" label until (or unless — JS may be
#: disabled) ``/site.js`` repaints it. A neutral name is always true;
#: ``/site.js`` upgrades it to the specific current/next state at load and
#: on every click (cycling light → dark → system). With JavaScript
#: disabled the button is inert but harmless: real accessible name,
#: visible text, ``type="button"`` so it can never submit — pages stay
#: fully readable and theming falls back to the OS preference.
#: Styled by ``.theme-toggle`` in :mod:`league_site.web.theme`.
_THEME_TOGGLE_HTML = (
    '<button type="button" id="theme-toggle" class="theme-toggle"'
    ' title="Theme"'
    ' aria-label="Switch theme">◐</button>'
)


def header_html(session: sessions.Session | None = None) -> str:
    """The canonical site header: wordmark + primary nav + account entry + theme toggle.

    The account entry reflects auth state per request. Anonymous callers get a
    "Sign in" entry linking ``/auth/login/github`` — GitHub is the *only*
    provider the UI ever links (the OAuth code in
    :mod:`league_site.auth.oauth` still supports others; no page anywhere
    links ``/auth/login/google``). A caller with a verified *session* gets the
    human's display name and a "Sign out" entry linking ``/auth/logout``, and
    no sign-in link at all.

    *session* is the verified :class:`~league_site.auth.sessions.Session` that
    :func:`league_site.auth.wsgi.with_auth` resolves into ``environ`` (under
    :data:`~league_site.auth.wsgi.SESSION_ENVIRON_KEY`) on every request;
    :func:`with_shell` reads it back after delegating and passes it here.
    ``None`` — the default, and the value the standalone shells that render
    *ahead of* ``with_auth`` (:mod:`league_site.viewer.wsgi`,
    :mod:`league_site.profiles.wsgi`) always pass, since no session is
    resolved into their requests — renders the anonymous header.

    Public so those standalone shells carry the same header — same nav, same
    toggle, same sign-in entry — instead of a drifting hand copy. One source,
    every page.
    """
    if session is None:
        return _HEADER_HTML
    return _header_with_account(_signed_in_account_html(session))


_NAV_HTML = "".join(f'<a href="{href}">{label}</a>' for label, href in _NAV_ITEMS)

#: The anonymous account entry — a single GitHub sign-in link. GitHub is the
#: ONLY provider the UI links anywhere; the disabled/enabled behavior of the
#: flow itself lives in :func:`league_site.auth.wsgi._login` (unchanged here).
_SIGN_IN_HTML = '<a class="header-auth" href="/auth/login/github">Sign in</a>'


def _signed_in_account_html(session: sessions.Session) -> str:
    """The signed-in account entry: the human's display name + a sign-out link.

    ``session.display`` originates from an OAuth provider's profile
    (user-controlled), so it is HTML-escaped — never trusted as raw markup —
    exactly like every other user-derived string the shell renders.
    """
    return (
        '<span class="header-account">'
        f'<span class="account-name">{html.escape(session.display)}</span>'
        '<a class="header-auth" href="/auth/logout">Sign out</a>'
        "</span>"
    )


def _header_with_account(account_html: str) -> str:
    """Assemble the header around an already-rendered *account_html* fragment.

    Every piece but the account fragment is a fixed module constant, so the
    anonymous header (:data:`_HEADER_HTML`) is built once at import and the
    common per-request path stays a plain attribute read. The account entry
    sits between the nav and the theme toggle: at desktop widths t7's
    ``nav[aria-label="Primary"] { margin-left: auto }`` sends the nav and
    everything after it (this entry, then the toggle) to the shell's right
    edge as one group.
    """
    return f"""<header class="site-header">
<div class="wrap">
{_WORDMARK_HTML}
<nav aria-label="Primary">{_NAV_HTML}</nav>
{account_html}
{_THEME_TOGGLE_HTML}
</div>
</header>"""


#: The anonymous header, built once at import (see :func:`_header_with_account`).
_HEADER_HTML = _header_with_account(_SIGN_IN_HTML)


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

        # Static assets answer GET/HEAD only; any other method falls
        # through to the wrapped app so the surface's method handling
        # (405s) stays uniform instead of these two paths accepting POST.
        method = environ.get("REQUEST_METHOD", "GET")
        if method in ("GET", "HEAD"):
            if path == _THEME_PATH:
                return _serve_static(
                    start_response,
                    _STYLESHEET_BYTES,
                    "text/css; charset=utf-8",
                    method,
                    extra_headers=(("Cache-Control", fonts.CACHE_CONTROL),),
                )
            if path == _SITE_JS_PATH:
                return _serve_static(
                    start_response,
                    _SITE_JS_BYTES,
                    "application/javascript; charset=utf-8",
                    method,
                    extra_headers=(("Cache-Control", fonts.CACHE_CONTROL),),
                )
            if path == _FAVICON_PATH:
                return _serve_static(
                    start_response,
                    _FAVICON_BYTES,
                    _FAVICON_MEDIA_TYPE,
                    method,
                    extra_headers=(("Cache-Control", fonts.CACHE_CONTROL),),
                )
            if path == _OG_IMAGE_PATH:
                return _serve_static(
                    start_response,
                    _OG_IMAGE_BYTES,
                    _OG_IMAGE_MEDIA_TYPE,
                    method,
                    extra_headers=(("Cache-Control", fonts.CACHE_CONTROL),),
                )
            if path.startswith(_FONTS_PREFIX):
                font_bytes = fonts.FONTS.get(path[len(_FONTS_PREFIX) :])
                if font_bytes is not None:
                    return _serve_static(
                        start_response,
                        font_bytes,
                        fonts.MEDIA_TYPE,
                        method,
                        extra_headers=(("Cache-Control", fonts.CACHE_CONTROL),),
                    )
                # An unknown /fonts/* name falls through to the wrapped app,
                # which 404s it — the same as any other unregistered path.

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

        # ``with_auth`` — mounted *inside* this middleware (see
        # league_site.web.http.site_app's composition order) — resolves the
        # session cookie into the shared ``environ`` on every request before
        # control reaches the wrapped app, so the verified Session (or None)
        # is present by the time it returns here. Absent that layer (a bare
        # ``with_shell`` in a test, or the standalone viewer/profiles
        # dispatch that never passes through ``with_auth``) the key is simply
        # missing and the header renders anonymous.
        session = environ.get(SESSION_ENVIRON_KEY)
        page = _render_page(body.decode("utf-8"), slots, path=path, session=session)
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


#: Both static assets are immutable module constants — encode once at
#: import, not per request.
_STYLESHEET_BYTES = theme.STYLESHEET.encode("utf-8")
_SITE_JS_BYTES = scripts.SITE_JS.encode("utf-8")

#: Hex digits of a content hash kept in a ``?v=`` query — long enough that
#: an accidental collision between two different builds is not a practical
#: concern, short enough to stay a cheap, readable cache-buster rather than
#: a full 64-char sha256 hex digest.
_ASSET_HASH_LEN = 8


def _content_hash(data: bytes) -> str:
    """The ``?v=`` query value for *data*: the first :data:`_ASSET_HASH_LEN`
    hex digits of its sha256.

    Not a security hash — a short, deterministic fingerprint of content. Two
    different byte strings (almost certainly) hash to two different values,
    which is what makes "bumping the version changes every reference" true
    by construction: every URL below is literally ``path + "?v=" +
    _content_hash(served_bytes)``, so a build that changes an asset's bytes
    can only ever be reached at a new URL.
    """
    return hashlib.sha256(data).hexdigest()[:_ASSET_HASH_LEN]


#: Every shell-served static asset's versioned URL, keyed by served name
#: (``"theme.css"``, ``"site.js"``, or a font filename matching a key of
#: :data:`league_site.web.fonts.FONTS`) — the single source :func:`asset_url`
#: reads from. Built once at import from the exact bytes :func:`with_shell`
#: serves at each path, so this dict, the served bytes, and every emitted
#: reference can never disagree about which build they name.
_ASSET_URLS: dict[str, str] = {
    "theme.css": f"{_THEME_PATH}?v={_content_hash(_STYLESHEET_BYTES)}",
    "site.js": f"{_SITE_JS_PATH}?v={_content_hash(_SITE_JS_BYTES)}",
    "favicon.svg": f"{_FAVICON_PATH}?v={_content_hash(_FAVICON_BYTES)}",
    "og.png": f"{_OG_IMAGE_PATH}?v={_content_hash(_OG_IMAGE_BYTES)}",
    **{
        name: f"{_FONTS_PREFIX}{name}?v={_content_hash(data)}" for name, data in fonts.FONTS.items()
    },
}


def asset_url(name: str) -> str:
    """The versioned URL for a shell-served static asset.

    *name* is ``"theme.css"``, ``"site.js"``, ``"favicon.svg"``, ``"og.png"``,
    or a font filename (a key of :data:`league_site.web.fonts.FONTS`, e.g.
    ``"fraunces-var.woff2"``).
    Every URL this returns carries ``?v=<hash>`` (see :func:`_content_hash`)
    — the content hash of the bytes actually served at that path — so
    bumping the build changes every reference to it, and the referenced URL
    always returns exactly the bytes that hash names, on the very first
    fetch. Route matching only ever looks at ``PATH_INFO``, which never
    includes the query string (confirmed for the real deploy path by
    :mod:`league_site.aws_lambda.wsgi`, which builds ``PATH_INFO`` /
    ``QUERY_STRING`` from ``rawPath`` / ``rawQueryString`` separately), so
    the query is decorative to the server but load-bearing for every cache
    in front of it.

    This is the ONE place the hash is computed — :mod:`league_site.web.shell`
    calls it below for the stylesheet link, the script tag, and both font
    preloads; :mod:`league_site.viewer.wsgi` and
    :mod:`league_site.profiles.wsgi`, the two standalone page shells that
    render ahead of :func:`with_shell`, call it too for their own
    ``/site.js`` reference rather than hardcoding the path.

    Raises :class:`ValueError` for any other *name* — always a programming
    error (a typo'd asset name), never user input.
    """
    try:
        return _ASSET_URLS[name]
    except KeyError:
        raise ValueError(f"unknown asset: {name!r}") from None


#: ``<head>`` preload links for both self-hosted fonts — one per file in
#: :data:`league_site.web.fonts.FONTS` (display before body), built once at
#: import. ``crossorigin`` is REQUIRED on a font preload even for a
#: same-origin font: fonts are always fetched in CORS ("anonymous") mode, so
#: a preload without it opens a second connection the actual fetch can't
#: reuse. Emitted in ``<head>`` *before* the stylesheet ``<link>`` (see
#: :func:`_render_page`), mirroring agentculture.org's Layout, so the browser
#: starts fetching the fonts as early as possible. The ``@font-face`` rules
#: that consume these URLs live in :mod:`league_site.web.theme` (a later
#: task); a preload with no matching ``@font-face`` is a harmless no-op until
#: then. Each ``href`` is versioned via :func:`asset_url`.
_FONT_PRELOAD_HTML = "\n".join(
    f'<link rel="preload" as="font" type="{fonts.MEDIA_TYPE}"'
    f' href="{asset_url(name)}" crossorigin>'
    for name in fonts.FONTS
)


def _serve_static(
    start_response: Any,
    body: bytes,
    content_type: str,
    method: str,
    *,
    extra_headers: tuple[tuple[str, str], ...] = (),
) -> list[bytes]:
    """Serve one immutable asset for GET or HEAD.

    *content_type* is sent verbatim (text assets pass their full
    ``…; charset=utf-8``; binary assets like fonts pass a bare
    ``font/woff2`` — a charset on a binary type is meaningless). Any
    *extra_headers* are appended after ``Content-Type``/``Content-Length``
    (the fonts use this for their long-lived ``Cache-Control``).

    HEAD gets the exact same headers — including the ``Content-Length`` of
    the would-be body — but an empty body, per HTTP semantics; many WSGI
    servers do not strip the body for you.
    """
    headers = [
        ("Content-Type", content_type),
        ("Content-Length", str(len(body))),
    ]
    headers.extend(extra_headers)
    start_response("200 OK", headers)
    return [] if method == "HEAD" else [body]


#: The LEADING rendered ``<h1>`` block of a landing page's markdown body —
#: stripped (rendered HTML only; the raw ``.md`` passthrough never comes
#: through :func:`_render_page` at all) because the hero's headline is the
#: landing page's one semantic ``<h1>``. Anchored to the very start of the
#: body so a mid-page ``<h1>`` (or one nested inside an earlier block) can
#: never be deleted by mistake: if the authored content stops opening with
#: an ``# `` title, nothing is stripped at all. See
#: :mod:`league_site.web.hero`'s docstring for the full rationale.
_LANDING_H1_RE = re.compile(r"\A<h1>.*?</h1>\n?", re.S)


def _render_page(
    markdown_text: str,
    slots: FooterSlotRegistry,
    *,
    path: str,
    session: sessions.Session | None = None,
) -> str:
    is_landing = path in _LANDING_PATHS
    title = _SITE_TITLE if is_landing else extract_title(markdown_text) or _SITE_TITLE
    page_title = title if title == _SITE_TITLE else f"{title} — {_SITE_TITLE}"
    body_html = render(markdown_text)
    if is_landing:
        # The hero (league_site.web.hero) leads the landing page — first
        # child of <main>, before the markdown body. It orchestrates its
        # own entrance, so it carries no .reveal class (site.js skips it
        # when stamping the stagger). Its <h1> replaces the markdown's.
        body_html = hero.HERO_HTML + "\n" + _LANDING_H1_RE.sub("", body_html, count=1)
    footer_html = slots.render()
    # Link-preview / tab identity. og:image and og:url are ABSOLUTE (built on
    # the canonical origin) because scrapers do not resolve relative URLs; the
    # favicon and every asset ref stay same-origin and versioned. These are
    # emitted only here, on shelled pages — the raw agent surfaces
    # (``.md``/``/front``/``/llms.txt``) never reach _render_page, so they stay
    # byte-identical to the unwrapped app (tests/test_web_raw_surface.py).
    canonical_url = f"{_CANONICAL_ORIGIN}{path}"
    og_image_url = f"{_CANONICAL_ORIGIN}{asset_url('og.png')}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{html.escape(_SITE_DESCRIPTION)}">
<title>{html.escape(page_title)}</title>
<link rel="icon" type="image/svg+xml" href="{asset_url('favicon.svg')}">
<meta property="og:type" content="website">
<meta property="og:title" content="{html.escape(page_title)}">
<meta property="og:description" content="{html.escape(_SITE_DESCRIPTION)}">
<meta property="og:url" content="{html.escape(canonical_url)}">
<meta property="og:image" content="{html.escape(og_image_url)}">
<meta name="twitter:card" content="summary_large_image">
<script>{scripts.PRE_PAINT_JS}</script>
{_FONT_PRELOAD_HTML}
<link rel="stylesheet" href="{asset_url('theme.css')}">
<script defer src="{asset_url('site.js')}"></script>
</head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
{header_html(session)}
<main id="main" class="wrap">
{body_html}
</main>
<footer class="site-footer">
<div class="wrap">{footer_html}</div>
</footer>
</body>
</html>
"""
