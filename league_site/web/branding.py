"""Footer acknowledgement — registers the platform's one footer line.

:mod:`league_site.web.shell` ships an empty :data:`~league_site.web.shell.
FOOTER_SLOTS` registry and documents the contract for filling it (see
:class:`~league_site.web.shell.FooterSlotRegistry`'s docstring). This module
is that later task: it registers one small, static footer fragment —
"Powered by AWS · Community Builders program" plus a link to the
:mod:`~league_site.web.content` About page (``/about``) — so every page
rendered through :func:`~league_site.web.shell.with_shell` carries the same
acknowledgement.

The fragment is entirely static (no user- or request-controlled text is
interpolated into it), so it is written once as a trusted HTML literal
rather than built with :func:`html.escape` — there is nothing dynamic here
to escape. A future caller that *does* need to interpolate anything
user-controlled into a footer fragment must escape it before calling
:meth:`~league_site.web.shell.FooterSlotRegistry.register`, per that
method's contract.
"""

from __future__ import annotations

from league_site.web.shell import FOOTER_SLOTS, FooterSlotRegistry

#: The one footer acknowledgement line this module owns. Kept as a module
#: constant (rather than inlined in :func:`register_branding`) so the
#: idempotency check in :func:`register_branding` and any test asserting on
#: its exact text share one definition.
FOOTER_HTML = '<p>Powered by AWS · Community Builders program. <a href="/about">About</a></p>'


def register_branding(footer_slots: FooterSlotRegistry = FOOTER_SLOTS) -> None:
    """Register the footer acknowledgement fragment on *footer_slots*.

    Idempotent: safe to call more than once (e.g. once from
    :func:`league_site.web.http.site_app` and again from a test or a second
    import) — if :data:`FOOTER_HTML` is already present in *footer_slots*'
    rendered output, this is a no-op, so the footer never shows a
    duplicated acknowledgement line.

    *footer_slots* defaults to the process-wide
    :data:`~league_site.web.shell.FOOTER_SLOTS` registry; pass an explicit
    :class:`~league_site.web.shell.FooterSlotRegistry` (e.g. in tests) to
    register into an isolated registry instead.
    """
    if FOOTER_HTML in footer_slots.render():
        return
    footer_slots.register(FOOTER_HTML)
