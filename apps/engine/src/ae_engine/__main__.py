"""python -m ae_engine: the long-running engine process (ADR 0014).

    python -m ae_engine           run: one step for every active run every second
    python -m ae_engine --once    start, report ready, exit (smoke test)

Needs DATABASE_URL and REDIS_URL. Only one engine steps runs at a time (a Redis lease), so a second copy started by
mistake waits instead of placing duplicate orders."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import uuid

import structlog
from ae_db.session import Database
from ae_marketdata.hub import Hub
from redis.asyncio import Redis

from . import __version__
from .engine import Engine

log = structlog.get_logger("ae_engine")
LEASE, LEASE_S = "engine:leader", 15


async def _run() -> int:
    db_url, redis_url = os.environ.get("DATABASE_URL"), os.environ.get("REDIS_URL")
    if not db_url or not redis_url:
        log.error("DATABASE_URL and REDIS_URL are required")
        return 2
    db, redis = Database(db_url, pool_size=4), Redis.from_url(redis_url)
    engine, me = Engine(db, Hub(redis)), uuid.uuid4().hex
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    log.info("engine ready", version=__version__)
    leading = False
    try:
        while not stop.is_set():
            ok = await redis.set(LEASE, me, nx=True, ex=LEASE_S) or (await redis.get(LEASE)) == me.encode()
            if ok:
                await redis.expire(LEASE, LEASE_S)
                if not leading:
                    log.info("engine leading")
                leading = True
                try:
                    await engine.tick()
                except Exception:
                    log.exception("engine tick failed")
            elif leading:
                log.warning("another engine holds the lease: standing by")
                leading = False
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass
    finally:
        if leading and (await redis.get(LEASE)) == me.encode():
            await redis.delete(LEASE)
        await redis.aclose()
        await db.dispose()
    log.info("engine stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ae_engine")
    ap.add_argument("--once", action="store_true", help="start, report ready, exit (smoke test)")
    args = ap.parse_args(argv)
    if args.once:
        log.info("engine ready", version=__version__)
        return 0
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
