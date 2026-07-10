"""Internal helpers for :mod:`league_site.matches`. Not part of the public API."""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp.

    Centralised so every domain object stamps time the same way, and so
    serialization round-trips (isoformat/fromisoformat) stay symmetric.
    """
    return datetime.now(timezone.utc)
