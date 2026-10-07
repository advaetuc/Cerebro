"""Run the catalog retrieval spike from the promoted catalog service."""

from __future__ import annotations

import sys

from app.services.catalog.clients import ConfigError
from app.services.catalog.retrieval import main as _main


def main() -> int:
    """Run the probe and display configuration errors without a traceback."""
    try:
        return _main()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
