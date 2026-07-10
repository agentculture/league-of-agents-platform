"""Tests for :mod:`league_site.web._markdown` — the stdlib markdown renderer."""

from __future__ import annotations

from league_site.web._markdown import extract_title, render


def test_headers_render_with_correct_levels() -> None:
    assert render("# Title\n") == "<h1>Title</h1>"
    assert render("## Sub\n") == "<h2>Sub</h2>"
    assert render("###### Deep\n") == "<h6>Deep</h6>"


def test_paragraph_wraps_plain_text() -> None:
    assert render("Hello world.\n") == "<p>Hello world.</p>"


def test_multi_line_paragraph_joins_with_a_space() -> None:
    assert render("Line one\nline two.\n") == "<p>Line one line two.</p>"


def test_blank_line_separates_paragraphs() -> None:
    assert render("First.\n\nSecond.\n") == "<p>First.</p>\n<p>Second.</p>"


def test_bold_italic_and_inline_code() -> None:
    out = render("**bold** and *italic* and `code`")
    assert "<strong>bold</strong>" in out
    assert "<em>italic</em>" in out
    assert "<code>code</code>" in out


def test_link_renders_as_anchor() -> None:
    out = render("See [docs](/docs) now.")
    assert '<a href="/docs">docs</a>' in out


def test_autolink_renders_as_anchor() -> None:
    out = render("Visit <https://league-of-agents.ai> today.")
    assert '<a href="https://league-of-agents.ai">https://league-of-agents.ai</a>' in out


def test_unordered_list_renders() -> None:
    assert render("- one\n- two\n") == "<ul><li>one</li><li>two</li></ul>"


def test_ordered_list_renders() -> None:
    assert render("1. one\n2. two\n") == "<ol><li>one</li><li>two</li></ol>"


def test_nested_list_renders_a_sub_list_inside_its_parent_item() -> None:
    md = "- claim\n  - honesty: detail\n"
    assert render(md) == "<ul><li>claim<ul><li>honesty: detail</li></ul></li></ul>"


def test_list_tolerates_a_blank_line_between_items() -> None:
    """A blank line followed by more list markup is a lazy continuation, not a new block."""
    assert render("- one\n\n- two\n") == "<ul><li>one</li><li>two</li></ul>"


def test_blank_line_followed_by_prose_ends_the_list() -> None:
    out = render("- one\n\nafter the list.\n")
    assert out == "<ul><li>one</li></ul>\n<p>after the list.</p>"


def test_fenced_code_block_escapes_html_and_sets_language_class() -> None:
    out = render('```json\n{"a": 1}\n```\n')
    assert out == '<pre><code class="language-json">{&quot;a&quot;: 1}</code></pre>'


def test_fenced_code_block_without_language() -> None:
    out = render("```\nplain\n```\n")
    assert out == "<pre><code>plain</code></pre>"


def test_blockquote_renders_nested_block_content() -> None:
    assert render("> quoted text\n") == "<blockquote><p>quoted text</p></blockquote>"


def test_horizontal_rule_renders() -> None:
    assert render("---\n") == "<hr>"
    assert render("***\n") == "<hr>"


def test_table_renders_with_a_horizontal_scroll_wrapper() -> None:
    md = "| A | B |\n|---|---|\n| 1 | 2 |\n"
    out = render(md)
    assert out.startswith('<div class="table-wrap"><table>')
    assert "<th>A</th>" in out
    assert "<th>B</th>" in out
    assert "<td>1</td>" in out
    assert "<td>2</td>" in out


def test_raw_html_special_characters_are_escaped_not_interpreted() -> None:
    out = render("<script>alert(1)</script> & stuff")
    assert "<script>" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    assert "&amp; stuff" in out


def test_unknown_construct_degrades_to_an_escaped_paragraph_without_raising() -> None:
    out = render("Just & <weird> text with no special markup.")
    assert "<p>" in out
    assert "&lt;weird&gt;" in out


def test_extract_title_strips_inline_markup() -> None:
    assert extract_title("# **League** of Agents\n\nbody") == "League of Agents"


def test_extract_title_returns_none_without_a_heading() -> None:
    assert extract_title("just text, no heading\n") is None


def test_indented_continuation_lines_stay_inside_their_list_item() -> None:
    out = render(
        "- **Play as a human** — sign in. No\n"
        "  installation required. Start at\n"
        "  [`start-human`](/start-human).\n"
        "- Second item.\n"
    )
    assert out.count("<li>") == 2
    assert "sign in. No installation required." in out
    # The continuation prose renders inside the <li>, never as a stray
    # paragraph after the list.
    assert "<p>installation" not in out
