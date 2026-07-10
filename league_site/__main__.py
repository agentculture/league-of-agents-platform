"""Entry point for ``python -m league_site``."""

from __future__ import annotations

import sys

from league_site.cli import main

if __name__ == "__main__":
    sys.exit(main())
