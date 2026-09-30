"""Background jobs.

    python -m ae_worker                        # run: every job once at start, then on its daily schedule
    python -m ae_worker --once                 # every job once, then exit (cron, deploy hooks)
    python -m ae_worker refresh-instruments    # one job, then exit

Jobs (times IST): refresh-instruments at 08:00 (Zerodha publishes the day's list before that). A plain asyncio
schedule until the job queue lands; every job is idempotent, so running it twice is harmless."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime, time, timedelta

import structlog
from ae_db.session import Database

from . import __version__
from .instruments import IST, refresh_instruments

log = structlog.get_logger("ae_worker")

JOBS: dict[str, tuple[time, Callable[[Database], Awaitable[object]]]] = {
    "refresh-instruments": (time(8, 0), refresh_instruments),
}


def next_run(now: datetime, at: time) -> datetime:
    """The next `at` (IST) strictly after `now`."""
    local = now.astimezone(IST)
    run = datetime.combine(local.date(), at, tzinfo=IST)
    return run if run > local else run + timedelta(days=1)


async def _run_job(db: Database, name: str) -> bool:
    try:
        await JOBS[name][1](db)
        return True
    except Exception:  # a failing job must not stop the others; it runs again at its next time
        log.exception("job failed", job=name)
        return False


async def _schedule(db: Database, name: str) -> None:
    while True:
        wait = (next_run(datetime.now(IST), JOBS[name][0]) - datetime.now(IST)).total_seconds()
        await asyncio.sleep(max(wait, 1))
        await _run_job(db, name)


async def _main(args: argparse.Namespace) -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        log.error("DATABASE_URL is not set")
        return 2
    db = Database(url, pool_size=1)
    try:
        names = [args.job] if args.job else list(JOBS)
        ok = all([await _run_job(db, n) for n in names])
        if args.job or args.once:
            return 0 if ok else 1
        log.info("worker ready", version=__version__, jobs=names)
        await asyncio.gather(*(_schedule(db, n) for n in names))
        return 0
    finally:
        await db.dispose()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ae_worker")
    ap.add_argument("job", nargs="?", choices=sorted(JOBS), help="run one job and exit")
    ap.add_argument("--once", action="store_true", help="run every job once and exit")
    return asyncio.run(_main(ap.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
