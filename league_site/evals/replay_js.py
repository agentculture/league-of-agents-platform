"""Replay script (t11 owns this file): board, story, open moment, live polling.

Stub until t11: only marks the page ready. Served at ``/evals/replay.js``.
"""

from __future__ import annotations

REPLAY_JS = """\
(function () {
  "use strict";
  document.documentElement.dataset.evals = "ready";
})();
"""
