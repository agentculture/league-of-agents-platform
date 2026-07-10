"""Accessibility hardening tests (t7) — the consumer-side gaps.

This file deliberately avoids re-asserting what other test files already
cover:

* ``tests/test_web_theme_motion.py`` already parses
  :data:`league_site.web.theme.STYLESHEET` (the Python string) with
  brace-depth guard-span parsing to prove every motion rule lives inside
  ``@media (prefers-reduced-motion: no-preference)``. This file instead
  checks the *served* ``/theme.css`` bytes (the artifact a browser actually
  gets) and any inline ``<style>`` blocks the shelled landing page happens
  to carry — a different angle (consumer side, HTTP-served) that also stays
  resilient to a parallel task adding a hero section with its own scoped
  styles.
* ``tests/test_web_theme_tokens.py`` (t2) already proves every
  ``color-scheme`` value stays in sync with every palette token, straight
  from the module. This file's color-scheme test is narrower and operates
  on the served stylesheet instead, as a consumer-side complement.
* ``tests/test_web_scripts.py`` already runs a node harness that executes
  ``SITE_JS`` end to end (clicking the toggle through light -> dark ->
  system and asserting the resulting DOM/localStorage state). This file's
  ``SITE_JS`` test stays at the string level, per the task brief, and only
  asserts the accessible-name/state contract (aria-label reflects the
  *current* state, for every state) rather than re-running the harness.

Every test here uses targeted parsing (``html.parser`` / regex on stable
attributes, not full-page string matches) so it stays resilient to a
parallel task's landing-page changes (a hero section, extra markup, etc.).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from league_site.web import scripts
from league_site.web.http import WSGIApp, http_app
from league_site.web.shell import FooterSlotRegistry, with_shell

# ---------------------------------------------------------------------------
# Shared test-client plumbing (same idiom as test_web_theme_shell.py /
# test_web_scripts.py: a WSGI app with its own footer registry, isolated
# from the process-wide default).
# ---------------------------------------------------------------------------


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


def _landing_html() -> str:
    _, _, body = _get(_shelled(), "/")
    return body.decode("utf-8")


def _served_theme_css() -> str:
    _, _, body = _get(_shelled(), "/theme.css")
    return body.decode("utf-8")


# ---------------------------------------------------------------------------
# Generic CSS-text helpers (the guard-span technique from
# test_web_theme_motion.py, parameterized so it can run over *any* CSS
# text — served /theme.css bytes or an inline <style> block's own text —
# not just the imported theme.STYLESHEET module string).
# ---------------------------------------------------------------------------


def _block_span_from(css: str, open_brace: int) -> tuple[int, int]:
    """Return the (body_start, body_end) span of the braced block whose
    opening ``{`` is at *open_brace*, via brace-depth counting."""
    depth = 0
    end = None
    for pos in range(open_brace, len(css)):
        ch = css[pos]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = pos
                break
    assert end is not None, "unbalanced braces"
    return open_brace + 1, end


def _reduced_motion_guard_spans(css: str) -> list[tuple[int, int]]:
    """[(start, end), ...] character spans of every top-level
    ``@media (prefers-reduced-motion: no-preference) { ... }`` block body
    in *css*."""
    spans = []
    marker = "@media (prefers-reduced-motion: no-preference)"
    search_from = 0
    while True:
        idx = css.find(marker, search_from)
        if idx == -1:
            break
        open_brace = css.index("{", idx)
        body_start, body_end = _block_span_from(css, open_brace)
        spans.append((body_start, body_end))
        search_from = body_end + 1
    return spans


def _inside_any_span(index: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= index < end for start, end in spans)


def _all_occurrences(css: str, token: str) -> list[int]:
    indices = []
    start = 0
    while True:
        i = css.find(token, start)
        if i == -1:
            break
        indices.append(i)
        start = i + 1
    return indices


def _assert_all_motion_tokens_are_guarded(css: str, *, context: str) -> None:
    """Every ``transition:``/``animation:``/``@keyframes``/
    ``::view-transition`` occurrence in *css* must fall inside a
    ``@media (prefers-reduced-motion: no-preference)`` block body. If *css*
    has none of those tokens, this passes vacuously — exactly the "no
    inline style blocks" case the task brief calls out."""
    spans = _reduced_motion_guard_spans(css)
    for token in ("transition:", "animation:", "@keyframes", "::view-transition"):
        for idx in _all_occurrences(css, token):
            assert _inside_any_span(idx, spans), (
                f"{context}: found {token!r} at offset {idx} outside the "
                "prefers-reduced-motion: no-preference guard"
            )


_STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)


def _inline_style_blocks(html_text: str) -> list[str]:
    return [match.group(1) for match in _STYLE_BLOCK_RE.finditer(html_text)]


# ---------------------------------------------------------------------------
# A small structural index of a rendered page — landmark counts and a few
# stable attributes — via html.parser rather than brittle full-string
# matches, so it survives a parallel task changing the landing page's body.
# ---------------------------------------------------------------------------


class _PageIndex(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tag_counts: dict[str, int] = {}
        self.html_attrs: dict[str, str | None] = {}
        self.nav_attrs: list[dict[str, str | None]] = []
        self.meta_tags: list[dict[str, str | None]] = []
        self.title_text = ""
        self.body_focusable_order: list[dict[str, str | None]] = []
        self._in_title = False
        self._in_body = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_d = dict(attrs)
        self.tag_counts[tag] = self.tag_counts.get(tag, 0) + 1
        if tag == "html":
            self.html_attrs = attrs_d
        elif tag == "title":
            self._in_title = True
        elif tag == "body":
            self._in_body = True
        elif tag == "meta":
            self.meta_tags.append(attrs_d)
        elif tag == "nav":
            self.nav_attrs.append(attrs_d)

        if self._in_body and tag in {"a", "button", "input", "select", "textarea"}:
            if attrs_d.get("tabindex") != "-1":
                self.body_focusable_order.append({"tag": tag, **attrs_d})

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_text += data


def _index_page(html_text: str) -> _PageIndex:
    idx = _PageIndex()
    idx.feed(html_text)
    return idx


# ---------------------------------------------------------------------------
# 1. Theme toggle: type=button, non-empty aria-label, visible text, and
#    lives inside the header landmark.
# ---------------------------------------------------------------------------


def test_theme_toggle_is_a_real_button_with_a_name_and_visible_text_inside_the_header() -> None:
    text = _landing_html()
    header = text.split("<header", 1)[1].split("</header>")[0]

    match = re.search(r'<button[^>]*id="theme-toggle"[^>]*>(.*?)</button>', header, re.S)
    assert match is not None, "theme toggle button not found inside <header>...</header>"

    open_tag = re.search(r'<button[^>]*id="theme-toggle"[^>]*>', header).group(0)
    assert 'type="button"' in open_tag

    label_match = re.search(r'aria-label="([^"]*)"', open_tag)
    assert label_match is not None
    assert label_match.group(1).strip(), "aria-label must be non-empty"

    visible_text = match.group(1).strip()
    assert visible_text, "toggle button must carry visible text content (the glyph)"


# ---------------------------------------------------------------------------
# 2. Focus visibility: :focus-visible keeps a visible outline.
# ---------------------------------------------------------------------------


def test_served_stylesheet_keeps_a_visible_focus_visible_outline() -> None:
    """Looks for the *bare* ``:focus-visible { ... }`` rule specifically —
    not a compound selector like ``a:focus-visible`` or
    ``.button:focus-visible`` (those exist too, for other declarations, and
    a naive search would grab one of those instead)."""
    css = _served_theme_css()
    match = re.search(r"(?<![\w.#\]-]):focus-visible\s*\{([^}]*)\}", css)
    assert match is not None, "no bare :focus-visible rule in the served stylesheet"
    body = match.group(1)
    outline_match = re.search(r"outline\s*:\s*([^;]+);", body)
    assert outline_match is not None, ":focus-visible must set outline"
    outline_value = outline_match.group(1).strip().lower()
    assert outline_value != "none", ":focus-visible outline must not be 'none'"
    assert outline_value != "0", ":focus-visible outline must not be zero-width"


# ---------------------------------------------------------------------------
# 3. Skip link: present, first focusable element in <body>, targets #main,
#    and #main exists.
# ---------------------------------------------------------------------------


def test_skip_link_is_first_focusable_and_targets_an_existing_main() -> None:
    text = _landing_html()
    idx = _index_page(text)

    assert idx.body_focusable_order, "no focusable elements found in <body>"
    first = idx.body_focusable_order[0]
    assert first["tag"] == "a"
    assert (first.get("class") or "") == "skip-link" or "skip-link" in (first.get("class") or "")
    assert first.get("href") == "#main"

    assert idx.tag_counts.get("main", 0) >= 1
    assert 'id="main"' in text, "#main target must exist in the rendered page"


# ---------------------------------------------------------------------------
# 4. Landmarks/semantics on a shelled page.
# ---------------------------------------------------------------------------


def test_shelled_page_has_exactly_one_main_header_and_footer() -> None:
    text = _landing_html()
    idx = _index_page(text)
    assert idx.tag_counts.get("main", 0) == 1, "expected exactly one <main>"
    assert idx.tag_counts.get("header", 0) == 1, "expected exactly one <header>"
    assert idx.tag_counts.get("footer", 0) == 1, "expected exactly one <footer>"


def test_shelled_page_nav_is_labelled_primary() -> None:
    text = _landing_html()
    idx = _index_page(text)
    assert idx.nav_attrs, "expected a <nav> landmark"
    assert any(attrs.get("aria-label") == "Primary" for attrs in idx.nav_attrs)


def test_shelled_page_declares_english_language() -> None:
    text = _landing_html()
    idx = _index_page(text)
    assert idx.html_attrs.get("lang") == "en"


def test_shelled_page_has_a_non_empty_title_and_meta_description() -> None:
    text = _landing_html()
    idx = _index_page(text)
    assert idx.title_text.strip(), "expected a non-empty <title>"

    description = next(
        (m.get("content") for m in idx.meta_tags if m.get("name") == "description"), None
    )
    assert description is not None, 'expected <meta name="description">'
    assert description.strip(), "meta description must be non-empty"


# ---------------------------------------------------------------------------
# 5. Reduced-motion completeness from the consumer side: the served
#    /theme.css AND any inline <style> blocks on the shelled landing page.
# ---------------------------------------------------------------------------


def test_served_theme_css_keeps_every_motion_rule_inside_the_reduced_motion_guard() -> None:
    css = _served_theme_css()
    _assert_all_motion_tokens_are_guarded(css, context="served /theme.css")


def test_landing_page_inline_style_blocks_keep_motion_rules_inside_their_own_guard() -> None:
    """Defensive against a parallel task (t5) adding a hero with scoped
    inline styles: for every <style> block found on the shelled landing
    page, any animation/transition/keyframes occurrences inside that block
    must themselves be nested in a prefers-reduced-motion: no-preference
    media query within that same style text. Vacuously true today (the
    landing page currently authors no inline <style> blocks)."""
    blocks = _inline_style_blocks(_landing_html())
    for block in blocks:
        _assert_all_motion_tokens_are_guarded(block, context="landing page inline <style>")


# ---------------------------------------------------------------------------
# 6. No-JS readability: nothing on the shelled landing page hides content
#    by default — the .reveal hidden state must require [data-js].
# ---------------------------------------------------------------------------

_REVEAL_RULE_RE = re.compile(r"([^{}]*\.reveal[^{]*)\{([^}]*)\}")


def _assert_reveal_hidden_state_requires_data_js(css: str, *, context: str) -> None:
    for match in _REVEAL_RULE_RE.finditer(css):
        selector, body = match.group(1), match.group(2)
        if "opacity: 0" in body or "opacity:0" in body:
            assert "data-js" in selector, (
                f"{context}: found a .reveal rule hiding content (opacity: 0) "
                f"whose selector does not require [data-js]: {selector!r}"
            )


def test_served_theme_css_never_hides_reveal_content_without_data_js() -> None:
    _assert_reveal_hidden_state_requires_data_js(_served_theme_css(), context="served /theme.css")


def test_landing_page_inline_styles_never_hide_reveal_content_without_data_js() -> None:
    for block in _inline_style_blocks(_landing_html()):
        _assert_reveal_hidden_state_requires_data_js(block, context="landing inline <style>")


# ---------------------------------------------------------------------------
# 7. Toggle accessible-state contract in SITE_JS: the aria-label reflects
#    the *current* state, for every state the toggle can be in — string
#    level, since test_web_scripts.py's node harness already executes it.
# ---------------------------------------------------------------------------


def test_site_js_paints_aria_label_and_visible_text_on_every_toggle_state() -> None:
    js = scripts.SITE_JS

    # Every cycle state gets both a glyph (visible text) and is referenced
    # by the dynamic aria-label build -- not a single hardcoded string.
    for state in ("light", "dark", "system"):
        assert f"{state}:" in js or f'"{state}"' in js or f"'{state}'" in js, state

    assert "textContent" in js, "toggle button must get real visible text content"
    assert "setAttribute" in js and "aria-label" in js
    # The label is built from the *current* state variable, not a fixed
    # string per call site -- i.e. it is computed, not hardcoded thrice.
    assert re.search(
        r'"aria-label"\s*,\s*\n?\s*"Theme:\s*"\s*\+\s*state', js
    ), "expected the aria-label to be composed from the current state variable"


def test_site_js_repaints_the_toggle_both_at_load_and_on_every_click() -> None:
    """The accessible name/state must be correct immediately on load (no
    stale "system" label if a theme was already stored) and again after
    every click -- not just once at init."""
    js = scripts.SITE_JS
    init_fn = js.split("function initToggle", 1)[1].split("function init", 1)[0]
    assert "paint(button, current())" in init_fn, "must repaint to the current state at load"
    assert 'addEventListener("click"' in init_fn or "addEventListener('click'" in init_fn
    assert re.search(r"paint\(button,\s*next\)", init_fn), "must repaint again after each click"


# ---------------------------------------------------------------------------
# 8. Color-scheme sync on the served stylesheet -- complements (does not
#    duplicate) test_web_theme_tokens.py's
#    test_color_scheme_is_kept_in_sync_across_every_theming_path, which
#    already proves this from the imported theme.STYLESHEET module string;
#    this checks the same two paths from the HTTP-served bytes.
# ---------------------------------------------------------------------------


def test_served_stylesheet_pins_color_scheme_dark_on_both_dark_paths() -> None:
    css = _served_theme_css()

    _, dark_attr_block = css.split(':root[data-theme="dark"]', 1)
    dark_attr_block = dark_attr_block.split("}", 1)[0]
    assert "color-scheme: dark" in dark_attr_block

    _, media_block = css.split("@media (prefers-color-scheme: dark)", 1)
    scoped_block = media_block.split(':root:not([data-theme="light"])', 1)[1]
    scoped_block = scoped_block.split("}", 1)[0]
    assert "color-scheme: dark" in scoped_block


def test_served_stylesheet_pins_color_scheme_light_under_the_explicit_light_attribute() -> None:
    css = _served_theme_css()
    _, light_attr_block = css.split(':root[data-theme="light"]', 1)
    light_attr_block = light_attr_block.split("}", 1)[0]
    assert "color-scheme: light" in light_attr_block
