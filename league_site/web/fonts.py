"""First-party variable fonts — vendored bytes, served by the shell.

The sibling-of-agentculture.org pass (spec h1) adopted agentculture.org's
type voice wholesale (a USER DECISION): the two variable ``woff2`` files that
site itself serves are vendored into this package and served first-party at
``/fonts/<name>.woff2`` by :mod:`league_site.web.shell` — no third-party
webfont CDN, no external request, so the site's zero-external-fetch guarantee
(see :mod:`league_site.web.theme`'s performance-budget section) survives the
move off system stacks. t2 pinned the 320KB :data:`~league_site.web.theme.
FONT_BUDGET_BYTES` ceiling ahead of this; the two files here fit inside it
(~153KB combined).

The two files (kept byte-identical to what agentculture.org serves,
OFL-licensed — each ships its ``LICENSE`` text beside it under
``assets/fonts/``):

* **Fraunces Variable** (display) — the FULL variable file, which carries the
  SOFT and WONK axes the display type needs; the wght-only Fraunces file is
  smaller but lacks them, so the full file is the deliberate choice.
* **Albert Sans Variable** (body).

Loading, and why by filesystem path
------------------------------------
The bytes are read once at import from ``assets/fonts/`` *next to this
module*, resolved via ``Path(__file__)`` — the same package-data pattern
:mod:`league_site.web.app` uses for ``content/*.md``. Because the files live
inside the ``league_site`` package, hatchling ships them in the wheel
automatically (verified by ``tests/test_web_fonts.py``), so they resolve at
``/var/task/league_site/web/assets/fonts/`` inside the Lambda artifact with
no Makefile ``cp`` — unlike the top-level ``docs/`` tree, which lives outside
the package and needed an explicit copy (platform#20).

The ``@font-face`` rules that *reference* these URLs are not here: they live
in :mod:`league_site.web.theme`'s stylesheet (a later task, t5). This module
only owns the bytes and the metadata the shell needs to serve and preload
them.
"""

from __future__ import annotations

from pathlib import Path

#: The media type every ``/fonts/*.woff2`` response carries, and the
#: ``type=`` on each ``<head>`` preload link. woff2 is binary — no charset.
MEDIA_TYPE = "font/woff2"

#: Long-lived immutable caching: font bytes are content-stable, so a browser
#: may cache them for a year and never revalidate. Versioned/fingerprinted
#: URLs (which make this bulletproof against edits) are a later task (t4);
#: today the files are simply never edited in place.
CACHE_CONTROL = "public, max-age=31536000, immutable"

_FONTS_DIR = Path(__file__).resolve().parent / "assets" / "fonts"

#: Served filenames, in preload order — display (Fraunces) before body
#: (Albert Sans). :mod:`league_site.web.shell` iterates this mapping to build
#: both the ``/fonts/*`` routes and the ``<head>`` preload links, so route,
#: served bytes, and preload can never disagree about which files exist.
FONTS: dict[str, bytes] = {
    "fraunces-var.woff2": (_FONTS_DIR / "fraunces-var.woff2").read_bytes(),
    "albert-sans-var.woff2": (_FONTS_DIR / "albert-sans-var.woff2").read_bytes(),
}
