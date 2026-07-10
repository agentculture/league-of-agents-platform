"""Tests for the motion system in :mod:`league_site.web.theme` (t4).

Design contract (docs/design/dazzle-direction.md + the t4 task brief):

* Reveal primitives (``.reveal`` / ``.revealed``) only take effect when BOTH
  ``html[data-js]`` is present (t3's pre-paint snippet sets it before first
  paint) AND the visitor has not asked for reduced motion. Content must
  never be hidden with JS off or with reduced motion on — the hidden
  ``opacity: 0`` state may only exist inside that double guard.
* Every motion rule (transitions, keyframes/animations, view-transition
  fades) lives inside a single ``@media (prefers-reduced-motion:
  no-preference)`` block. Static hover changes (color, border-color) may
  live outside it.
* Only ``transform``/``opacity`` are animated continuously; ``box-shadow``
  is allowed on hover-triggered transitions only. No layout properties
  (width/height/margin/top/left/right/bottom) are ever animated.
* ``--accent-glow`` is defined in all three token blocks (:root, the
  explicit dark attribute block, and the prefers-color-scheme dark block).
"""

from __future__ import annotations

import re

from league_site.web import theme

_FORBIDDEN_LAYOUT_PROPS = (
    "width:",
    "height:",
    "margin:",
    "top:",
    "left:",
    "right:",
    "bottom:",
    "padding:",
)


def _reduced_motion_guard_spans(css: str) -> list[tuple[int, int]]:
    """Return [(start, end), ...] character spans of every top-level
    ``@media (prefers-reduced-motion: no-preference) { ... }`` block's body
    (the body only, between its outermost braces), found by brace-depth
    counting from each media query's opening brace."""
    spans = []
    marker = "@media (prefers-reduced-motion: no-preference)"
    search_from = 0
    while True:
        idx = css.find(marker, search_from)
        if idx == -1:
            break
        open_brace = css.index("{", idx)
        depth = 0
        pos = open_brace
        body_start = open_brace + 1
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
        assert end is not None, "unbalanced braces in prefers-reduced-motion block"
        spans.append((body_start, end))
        search_from = end + 1
    return spans


def _inside_any_span(index: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= index < end for start, end in spans)


def _block_span_from(css: str, from_idx: int) -> tuple[int, int]:
    """Return the (body_start, body_end) span of the braced block whose
    opening ``{`` is the first one at or after *from_idx*, via brace-depth
    counting (so nested braces, e.g. keyframe percentage selectors, don't
    confuse it)."""
    open_brace = css.index("{", from_idx)
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
    assert end is not None, f"unbalanced braces from offset {from_idx}"
    return open_brace + 1, end


def _first_block_span(css: str, marker: str) -> tuple[int, int]:
    """Find the first occurrence of *marker*, then return the span of the
    braced block that immediately follows it."""
    return _block_span_from(css, css.index(marker))


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


def test_reduced_motion_guard_exists_at_least_once() -> None:
    spans = _reduced_motion_guard_spans(theme.STYLESHEET)
    assert spans, "expected at least one @media (prefers-reduced-motion: no-preference) block"


def test_reveal_hidden_state_is_scoped_under_both_data_js_and_reduced_motion() -> None:
    spans = _reduced_motion_guard_spans(theme.STYLESHEET)
    # The rule that sets the hidden initial state must exist...
    match = re.search(r"html\[data-js\]\s+\.reveal\s*\{[^}]*opacity:\s*0", theme.STYLESHEET)
    assert match is not None, "expected html[data-js] .reveal { opacity: 0; ... }"
    # ...and it must live inside a reduced-motion no-preference guard.
    assert _inside_any_span(match.start(), spans), (
        "the .reveal hidden state must be nested inside "
        "@media (prefers-reduced-motion: no-preference)"
    )


def test_reveal_revealed_state_restores_full_visibility() -> None:
    assert "html[data-js] .reveal.revealed" in theme.STYLESHEET
    _, block = theme.STYLESHEET.split("html[data-js] .reveal.revealed", 1)
    block = block.split("}", 1)[0]
    assert "opacity: 1" in block


def test_reveal_is_never_hidden_outside_the_data_js_and_motion_guard() -> None:
    """No rule for a bare ``.reveal`` (without the html[data-js] ancestor)
    may set opacity: 0 anywhere outside the reduced-motion guard — that
    would hide content when JS is off, which the brief forbids."""
    spans = _reduced_motion_guard_spans(theme.STYLESHEET)
    for match in re.finditer(r"\.reveal[^{]*\{[^}]*\}", theme.STYLESHEET):
        selector = theme.STYLESHEET[: match.start()]
        selector_line = selector.splitlines()[-1] if selector else ""
        rule_text = match.group(0)
        if "opacity: 0" in rule_text:
            assert "data-js" in match.group(0) or "data-js" in selector_line, (
                "a .reveal rule hides content (opacity: 0) without requiring " "html[data-js]"
            )
            assert _inside_any_span(match.start(), spans)


def test_reveal_is_staggerable_via_a_custom_delay_property() -> None:
    assert "--reveal-delay" in theme.STYLESHEET
    assert "transition-delay: var(--reveal-delay, 0s)" in theme.STYLESHEET


def test_reveal_transition_duration_and_easing() -> None:
    _, block = theme.STYLESHEET.split("html[data-js] .reveal {", 1)
    block = block.split("html[data-js] .reveal.revealed", 1)[0]
    assert "500ms" in block
    assert "cubic-bezier(0.2, 0.6, 0.2, 1)" in block
    assert "translateY(8px)" in block


def test_accent_glow_token_defined_in_all_three_token_blocks() -> None:
    css = theme.STYLESHEET

    root_block = css.split(":root {", 1)[1].split("}", 1)[0]
    assert "--accent-glow:" in root_block
    assert "rgba(194, 65, 12, .18)" in root_block or "rgba(194,65,12,.18)" in root_block

    _, dark_attr = css.split(':root[data-theme="dark"]', 1)
    dark_attr_block = dark_attr.split("}", 1)[0]
    assert "--accent-glow:" in dark_attr_block
    assert "rgba(255, 138, 61, .22)" in dark_attr_block or "rgba(255,138,61,.22)" in dark_attr_block

    _, media_block = css.split("@media (prefers-color-scheme: dark)", 1)
    scoped_block = media_block.split(':root:not([data-theme="light"])', 1)[1]
    scoped_block = scoped_block.split("}", 1)[0]
    assert "--accent-glow:" in scoped_block
    assert "rgba(255, 138, 61, .22)" in scoped_block or "rgba(255,138,61,.22)" in scoped_block


def test_button_hover_gets_translate_and_accent_glow_box_shadow_inside_guard() -> None:
    spans = _reduced_motion_guard_spans(theme.STYLESHEET)
    match = re.search(
        r"\.button:hover,\s*\.button:focus-visible\s*\{[^}]*transform:\s*translateY\(-1px\)[^}]*\}",
        theme.STYLESHEET,
    )
    assert match is not None
    assert _inside_any_span(match.start(), spans)
    assert "var(--accent-glow)" in match.group(0)
    assert "box-shadow" in match.group(0)


def test_card_hover_gets_translate_inside_guard_and_border_color_outside() -> None:
    spans = _reduced_motion_guard_spans(theme.STYLESHEET)

    transform_match = re.search(
        r"\.card:hover\s*\{[^}]*transform:\s*translateY\(-2px\)[^}]*\}", theme.STYLESHEET
    )
    assert transform_match is not None
    assert _inside_any_span(transform_match.start(), spans)

    border_match = re.search(
        r"\.card:hover\s*\{[^}]*border-color:\s*var\(--border-strong\)[^}]*\}",
        theme.STYLESHEET,
    )
    assert border_match is not None
    # The static color-only hover cue is allowed to live outside the guard.


def test_wordmark_glyph_pulse_keyframes_animate_only_opacity() -> None:
    spans = _reduced_motion_guard_spans(theme.STYLESHEET)
    keyframes_idx = theme.STYLESHEET.index("@keyframes")
    assert _inside_any_span(keyframes_idx, spans)
    body_start, body_end = _first_block_span(theme.STYLESHEET, "@keyframes")
    body = theme.STYLESHEET[body_start:body_end]
    # Only opacity values appear as animated properties.
    declared_props = set(re.findall(r"([\w-]+):\s*[^;]+;", body))
    assert declared_props == {"opacity"}, declared_props
    assert "1" in body and "0.75" in body

    # There is a pre-existing, unrelated `.wordmark-glyph { color: ...; }`
    # static rule earlier in the stylesheet (outside the guard) -- find the
    # one that actually carries the animation, not just the first match.
    anim_match = re.search(r"\.wordmark-glyph\s*\{[^}]*animation:[^}]*\}", theme.STYLESHEET)
    assert anim_match is not None, "expected a .wordmark-glyph rule with an animation"
    wg_block = anim_match.group(0)
    assert "4s" in wg_block
    assert "infinite" in wg_block
    assert "ease-in-out" in wg_block
    assert _inside_any_span(anim_match.start(), spans)


def test_view_transition_rule_and_crossfade_are_inside_the_guard() -> None:
    spans = _reduced_motion_guard_spans(theme.STYLESHEET)

    vt_idx = theme.STYLESHEET.find("@view-transition")
    assert vt_idx != -1
    assert _inside_any_span(vt_idx, spans)

    old_idx = theme.STYLESHEET.find("::view-transition-old(root)")
    new_idx = theme.STYLESHEET.find("::view-transition-new(root)")
    assert old_idx != -1
    assert new_idx != -1
    assert _inside_any_span(old_idx, spans)
    assert _inside_any_span(new_idx, spans)
    assert "180ms" in theme.STYLESHEET


def test_every_transition_animation_keyframes_and_view_transition_token_is_inside_the_guard() -> (
    None
):
    """Blanket sweep: every occurrence of these motion tokens anywhere in
    the stylesheet must fall inside a @media (prefers-reduced-motion:
    no-preference) block body."""
    spans = _reduced_motion_guard_spans(theme.STYLESHEET)
    assert spans

    for token in ("transition:", "animation:", "@keyframes", "::view-transition"):
        for idx in _all_occurrences(theme.STYLESHEET, token):
            assert _inside_any_span(idx, spans), (
                f"found {token!r} at offset {idx} outside the "
                "prefers-reduced-motion: no-preference guard"
            )


def test_no_layout_properties_are_animated() -> None:
    """Neither transition declarations nor keyframes may touch layout
    properties (width/height/margin/padding/top/left/right/bottom) — only
    transform/opacity continuously, box-shadow on hover transitions."""
    css = theme.STYLESHEET
    for match in re.finditer(r"transition:\s*[^;]+;", css):
        value = match.group(0)
        for prop in _FORBIDDEN_LAYOUT_PROPS:
            assert prop not in value, f"{prop!r} found in transition: {value!r}"

    for idx in _all_occurrences(css, "@keyframes"):
        body_start, body_end = _block_span_from(css, idx)
        body = css[body_start:body_end]
        for prop in _FORBIDDEN_LAYOUT_PROPS:
            assert prop not in body, f"{prop!r} found in keyframes: {body!r}"


def test_stylesheet_still_within_css_budget_after_motion_additions() -> None:
    payload = theme.STYLESHEET.encode("utf-8")
    assert len(payload) <= theme.CSS_BUDGET_BYTES


def test_docstring_documents_the_motion_system() -> None:
    doc = theme.__doc__ or ""
    assert "reveal" in doc.lower()
    assert "reduced motion" in doc.lower() or "reduced-motion" in doc.lower()
    assert "--accent-glow" in doc
