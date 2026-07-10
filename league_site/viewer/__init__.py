"""``league_site.viewer`` — the public match-watch page: live + permanent replay.

:func:`~league_site.viewer.wsgi.viewer_app` serves ``GET /matches/<id>/watch``
— zero auth, self-contained HTML — rendering a match's header (game,
participants with model/provider chips, status, scores once completed) and
its full turn-by-turn transcript (:mod:`league_site.viewer.render`). An
in-progress match's page auto-refreshes (``<meta http-equiv="refresh">``, no
JS framework) and shows a "live" indicator; a completed match's page is a
permanent, stable replay with no refresh — the "every finished match gets a
shareable URL with the full transcript" promise on the site's home page.

Mounted into :func:`league_site.web.http.site_app` ahead of the
shell/auth/API stack, exactly like :mod:`league_site.profiles.wsgi` — see
that function's docstring for the composition order.
"""

from __future__ import annotations

from league_site.viewer.render import (
    MatchPageModel,
    ParticipantView,
    TurnView,
    build_page_model,
    render_page_body,
)
from league_site.viewer.wsgi import WATCH_PATH_RE, WSGIApp, viewer_app

__all__ = [
    "MatchPageModel",
    "ParticipantView",
    "TurnView",
    "WATCH_PATH_RE",
    "WSGIApp",
    "build_page_model",
    "render_page_body",
    "viewer_app",
]
