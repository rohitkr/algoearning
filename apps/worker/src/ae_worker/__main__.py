"""python -m ae_worker --once: smoke test until the Celery app lands."""

from __future__ import annotations

import sys

import structlog

from . import __version__


def main(argv: list[str] | None = None) -> int:
    structlog.get_logger("ae_worker").info("worker ready", version=__version__, jobs=0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
