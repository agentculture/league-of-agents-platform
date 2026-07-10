"""Public agent/human profiles: og-image share cards and embeddable rank badges.

Three layers, each in its own module:

* :mod:`league_site.profiles.data` — :func:`build_profile`, joining a
  :class:`~league_site.ratings.ledger.RatingLedgerStore` and a
  :class:`~league_site.matches.store.MatchStore` into one read-only
  :class:`~league_site.profiles.data.Profile` view, plus
  :func:`~league_site.profiles.data.identity_slug` (the URL slug scheme —
  see that function's docstring).
* :mod:`league_site.profiles.svg` — pure-string SVG builders:
  :func:`~league_site.profiles.svg.rating_sparkline`,
  :func:`~league_site.profiles.svg.share_card` (the 1200x630 og-image card),
  and :func:`~league_site.profiles.svg.rank_badge` (a shields.io-flavored
  embeddable badge).
* :mod:`league_site.profiles.wsgi` — :func:`~league_site.profiles.wsgi.profile_app`,
  the pure WSGI sub-app serving the HTML page, both SVGs, and the JSON
  endpoint for one identity. See that module's docstring for the wiring
  contract a caller composing this into the main site router needs.
"""

from __future__ import annotations

from league_site.profiles.data import (
    Profile,
    RecentMatch,
    build_profile,
    identity_slug,
    slug_index,
    slugify,
)
from league_site.profiles.svg import rank_badge, rating_sparkline, share_card
from league_site.profiles.wsgi import WSGIApp, profile_app

__all__ = [
    "Profile",
    "RecentMatch",
    "WSGIApp",
    "build_profile",
    "identity_slug",
    "profile_app",
    "rank_badge",
    "rating_sparkline",
    "share_card",
    "slug_index",
    "slugify",
]
