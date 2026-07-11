"""The browser play surface: a signed-in human starts a match and takes turns.

``/play`` is the human companion to the ``/api/v1`` match API (task t9): the
same match/game stack, driven by server-rendered pages and plain HTML forms
instead of JSON — zero new JavaScript; the viewer's 5s meta-refresh keeps a
live board current while the other side moves. Humans play *as themselves*:
the verified session's ``human:<provider>:<subject>`` identity (see
:func:`league_site.api.identity.identity_for_session`) is the match
participant — never an agent token.

Routes (see :func:`league_site.play.wsgi.with_play`):

* ``GET /play`` — signed out: an invitation to sign in with GitHub. Signed
  in: the start-a-match form plus the human's own live matches to resume.
* ``POST /play/matches`` — create a solo-vs-bot match through the same
  shared creation flow as the JSON API (:mod:`league_site.api.matchops` —
  capacity gate and mode validation included).
* ``GET /play/matches/<id>`` — the play view: the viewer's board rendering
  (:mod:`league_site.viewer.render`, reused, not forked) plus — when the
  match is live and it's the human's turn — a form of the current legal
  actions. Non-participants (and anonymous visitors) are redirected to the
  public spectate page.
* ``POST /play/matches/<id>/turns`` — submit one chosen legal action;
  POST-redirect-GET back to the play view so a refresh never re-submits.

CSRF stance
-----------
State-changing POSTs on this surface are authenticated solely by the
session cookie, which ``league_site.auth.wsgi`` sets with ``SameSite=Lax``
(+ ``HttpOnly``, and ``Secure`` over https). A cross-site form POST
therefore arrives *without* the cookie and is refused as anonymous (401) —
that attribute is the CSRF boundary for these same-origin forms, so no
separate token is minted. ``tests/test_play_wsgi.py`` pins the cookie
attributes this stance depends on. Independently, a submitted action string
is never trusted: it must match one of the *current* legal actions computed
server-side (:func:`league_site.play.actions.match_choice`) before it is
ever handed to an engine.
"""

from league_site.play.wsgi import PLAY_MODES, with_play

__all__ = ["PLAY_MODES", "with_play"]
