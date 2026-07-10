"""A small, dependency-free Markdown → HTML renderer (stdlib only).

``agentfront`` (see :mod:`agentfront.http_surface`) serves every registered
doc as *raw markdown* — it has no HTML rendering at all. :mod:`league_site.
web.shell` uses this module to turn that markdown into the HTML body of a
shelled page, so the honesty condition "any page authored as a .md file
renders on the site without hand-written HTML" holds: nobody hand-writes
per-page HTML, this one generic renderer produces it.

This is **not** a general CommonMark implementation — there is no
third-party markdown library allowed here (stdlib only), and a full spec
implementation is out of scope for a design-system task. Instead it covers
the practical subset actually used across this repo's docs (``docs/*.md``
and ``league_site/web/content/*.md``): ATX headings, paragraphs, emphasis /
strong / inline code, links, autolinks (``<https://...>``), fenced code
blocks, block quotes, unordered/ordered lists (including one level of
indented nesting, e.g. spec "honesty:" sub-bullets), GFM-style pipe tables,
and horizontal rules. Unknown constructs degrade gracefully to an escaped
paragraph rather than raising.
"""

from __future__ import annotations

import html
import re

__all__ = ["render", "extract_title"]

_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_HR_RE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
_UL_RE = re.compile(r"^( *)[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^( *)\d+[.)]\s+(.*)$")
_FENCE_RE = re.compile(r"^```\s*([\w+-]*)\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_STRIP_INLINE_MARKUP_RE = re.compile(r"[*_`]")

_INLINE_RE = re.compile(
    r"(?P<code>`[^`]+`)"
    r"|(?P<autolink><https?://[^>\s]+>)"
    r"|(?P<link>\[[^\]]+\]\([^)]+\))"
    r"|(?P<bold>\*\*[^*]+\*\*|__[^_]+__)"
    r"|(?P<italic>\*[^*]+\*|_[^_]+_)"
)
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def extract_title(markdown_text: str) -> str | None:
    """Return the plain-text content of the first ``# `` heading, if any.

    Used for the shelled page's ``<title>`` — inline markup (``**``, `` ` ``,
    ``_``) is stripped since a ``<title>`` is plain text.
    """
    match = _H1_RE.search(markdown_text)
    if match is None:
        return None
    return _STRIP_INLINE_MARKUP_RE.sub("", match.group(1).strip())


def render(markdown_text: str) -> str:
    """Render *markdown_text* to an HTML fragment (no ``<html>``/``<body>``)."""
    lines = markdown_text.splitlines()
    blocks: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith("```"):
            i, block = _consume_fence(lines, i)
        elif _HEADER_RE.match(line):
            i, block = _consume_header(lines, i)
        elif _HR_RE.match(line):
            block = "<hr>"
            i += 1
        elif line.lstrip().startswith(">"):
            i, block = _consume_blockquote(lines, i)
        elif _is_table_start(lines, i):
            i, block = _consume_table(lines, i)
        elif _UL_RE.match(line) or _OL_RE.match(line):
            i, block = _consume_list(lines, i)
        else:
            i, block = _consume_paragraph(lines, i)
        blocks.append(block)
    return "\n".join(blocks)


def _inline(text: str) -> str:
    """Render inline markup (bold/italic/code/links/autolinks) within *text*."""
    out: list[str] = []
    pos = 0
    for match in _INLINE_RE.finditer(text):
        out.append(html.escape(text[pos : match.start()]))
        out.append(_render_inline_match(match))
        pos = match.end()
    out.append(html.escape(text[pos:]))
    return "".join(out)


def _render_inline_match(match: re.Match[str]) -> str:
    if match.group("code"):
        return f"<code>{html.escape(match.group('code')[1:-1])}</code>"
    if match.group("autolink"):
        url = match.group("autolink")[1:-1]
        return f'<a href="{html.escape(url, quote=True)}">{html.escape(url)}</a>'
    if match.group("link"):
        link_match = _LINK_RE.match(match.group("link"))
        assert link_match is not None  # _INLINE_RE's "link" branch always matches _LINK_RE too
        text_part, url = link_match.group(1), link_match.group(2)
        return f'<a href="{html.escape(url, quote=True)}">{_inline(text_part)}</a>'
    if match.group("bold"):
        return f"<strong>{_inline(match.group('bold')[2:-2])}</strong>"
    if match.group("italic"):
        return f"<em>{_inline(match.group('italic')[1:-1])}</em>"
    return ""  # pragma: no cover — every alternative in _INLINE_RE is handled above


def _consume_header(lines: list[str], i: int) -> tuple[int, str]:
    match = _HEADER_RE.match(lines[i])
    assert match is not None  # caller (render()) already matched _HEADER_RE on this line
    level = len(match.group(1))
    text = _inline(match.group(2).strip())
    return i + 1, f"<h{level}>{text}</h{level}>"


def _consume_fence(lines: list[str], i: int) -> tuple[int, str]:
    match = _FENCE_RE.match(lines[i])
    lang = match.group(1) if match else ""
    i += 1
    code_lines: list[str] = []
    n = len(lines)
    while i < n and not lines[i].startswith("```"):
        code_lines.append(lines[i])
        i += 1
    if i < n:
        i += 1  # skip the closing fence
    code = html.escape("\n".join(code_lines))
    cls = f' class="language-{html.escape(lang)}"' if lang else ""
    return i, f"<pre><code{cls}>{code}</code></pre>"


def _consume_blockquote(lines: list[str], i: int) -> tuple[int, str]:
    quoted: list[str] = []
    n = len(lines)
    while i < n and lines[i].lstrip().startswith(">"):
        content = lines[i].lstrip()[1:]
        if content.startswith(" "):
            content = content[1:]
        quoted.append(content)
        i += 1
    inner = render("\n".join(quoted))
    return i, f"<blockquote>{inner}</blockquote>"


def _is_table_start(lines: list[str], i: int) -> bool:
    if i + 1 >= len(lines):
        return False
    return "|" in lines[i] and bool(_TABLE_SEP_RE.match(lines[i + 1]))


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def _consume_table(lines: list[str], i: int) -> tuple[int, str]:
    header_cells = _split_row(lines[i])
    i += 2  # header row + separator row
    body_rows: list[list[str]] = []
    n = len(lines)
    while i < n and lines[i].strip() and "|" in lines[i]:
        body_rows.append(_split_row(lines[i]))
        i += 1
    parts = ['<div class="table-wrap"><table><thead><tr>']
    parts += [f"<th>{_inline(c)}</th>" for c in header_cells]
    parts.append("</tr></thead><tbody>")
    for row in body_rows:
        parts.append("<tr>")
        parts += [f"<td>{_inline(c)}</td>" for c in row]
        parts.append("</tr>")
    parts.append("</tbody></table></div>")
    return i, "".join(parts)


def _consume_list(lines: list[str], i: int) -> tuple[int, str]:
    items: list[tuple[int, bool, str]] = []
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            if j < n and (_UL_RE.match(lines[j]) or _OL_RE.match(lines[j])):
                i = j
                continue
            break
        ul_match = _UL_RE.match(line)
        ol_match = _OL_RE.match(line)
        if ul_match:
            items.append((len(ul_match.group(1)), False, ul_match.group(2)))
            i += 1
        elif ol_match:
            items.append((len(ol_match.group(1)), True, ol_match.group(2)))
            i += 1
        elif (
            items
            and line[:1] in (" ", "\t")
            and not line.lstrip().startswith((">", "```", "#", "|", "---"))
        ):
            # A continuation line: indented PROSE under the previous item
            # (CommonMark folds it into that item's paragraph). Without
            # this, wrapped list-item text fell out of the list and
            # rendered as a stray paragraph after it. Indented lines that
            # open a block construct (blockquote, fence, heading, table,
            # rule) are NOT folded — they end the list and render as their
            # own block, as they always did; agent-authored transcripts
            # (viewer/render.py) depend on that.
            indent, ordered, text = items[-1]
            items[-1] = (indent, ordered, f"{text} {line.strip()}")
            i += 1
        else:
            break
    html_block, _ = _build_list(items, 0, items[0][0])
    return i, html_block


def _build_list(items: list[tuple[int, bool, str]], pos: int, indent: int) -> tuple[str, int]:
    """Render ``items[pos:]`` at *indent* as one (possibly nested) list.

    Deeper-indented runs following an item are rendered as a nested list
    inside that item's ``<li>`` — this is what lets a spec's
    ``- claim`` / ``  - honesty: ...`` pairs render as a real sub-list.
    """
    ordered = items[pos][1]
    tag = "ol" if ordered else "ul"
    parts = [f"<{tag}>"]
    while pos < len(items) and items[pos][0] == indent:
        _, _, text = items[pos]
        pos += 1
        li = f"<li>{_inline(text)}"
        if pos < len(items) and items[pos][0] > indent:
            nested_html, pos = _build_list(items, pos, items[pos][0])
            li += nested_html
        parts.append(li + "</li>")
    parts.append(f"</{tag}>")
    return "".join(parts), pos


def _is_block_start(line: str) -> bool:
    if not line.strip():
        return True
    stripped = line.lstrip()
    return bool(
        line.startswith("```")
        or _HEADER_RE.match(line)
        or _HR_RE.match(line)
        or stripped.startswith(">")
        or _UL_RE.match(line)
        or _OL_RE.match(line)
    )


def _consume_paragraph(lines: list[str], i: int) -> tuple[int, str]:
    para_lines: list[str] = [lines[i].strip()]
    i += 1
    n = len(lines)
    while i < n and lines[i].strip() and not _is_block_start(lines[i]):
        para_lines.append(lines[i].strip())
        i += 1
    return i, f"<p>{_inline(' '.join(para_lines))}</p>"
