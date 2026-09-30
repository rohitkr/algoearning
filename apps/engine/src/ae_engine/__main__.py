"""python -m ae_engine: the long-running engine process. Until phase 9 it only proves the image starts."""

from __future__ import annotations

import argparse
import signal
import threading

import structlog

from . import __version__

log = structlog.get_logger("ae_engine")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ae_engine")
    ap.add_argument("--once", action="store_true", help="start, report ready, exit (smoke test)")
    args = ap.parse_args(argv)
    log.info("engine ready", version=__version__, sessions=0)
    if args.once:
        return 0
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.wait(30):
        log.info("engine heartbeat", sessions=0)
    log.info("engine stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
