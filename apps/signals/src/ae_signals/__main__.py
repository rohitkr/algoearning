"""python -m ae_signals: the signal reader process (ADR 0025).

Reads every connected signal source's Telegram chat (read-only) into signal_messages and signals. Needs DATABASE_URL,
REDIS_URL and APP_ENCRYPTION_KEY (the sources' Telegram sessions are encrypted with it); TELEGRAM_API_ID /
TELEGRAM_API_HASH for sources that use the platform's Telegram app. One copy runs at a time (a Redis lease): a second
one started by mistake stands by instead of opening a second connection per source."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import uuid

import structlog
from ae_core.secrets import SecretBox, load_master_key
from ae_db.session import Database
from redis.asyncio import Redis

from . import __version__
from .service import SYNC_S, SignalsService

log = structlog.get_logger("ae_signals")
LEASE, LEASE_S = "ae:signals:lease", 20


async def _run() -> int:
    db_url, redis_url, enc = (
        os.environ.get("DATABASE_URL"),
        os.environ.get("REDIS_URL"),
        os.environ.get("APP_ENCRYPTION_KEY"),
    )
    if not db_url or not redis_url or not enc:
        log.error("DATABASE_URL, REDIS_URL and APP_ENCRYPTION_KEY are required")
        return 2
    api_id, api_hash = os.environ.get("TELEGRAM_API_ID"), os.environ.get("TELEGRAM_API_HASH")
    platform_app = (int(api_id), api_hash) if api_id and api_hash else None
    db, redis = Database(db_url, pool_size=4), Redis.from_url(redis_url)
    service = SignalsService(db, SecretBox({1: load_master_key(enc)}, 1), platform_app, publisher=redis)
    me, stop, leading = uuid.uuid4().hex, asyncio.Event(), False
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    log.info("signal reader ready", version=__version__, platform_app=platform_app is not None)
    try:
        while not stop.is_set():
            ok = await redis.set(LEASE, me, nx=True, ex=LEASE_S) or (await redis.get(LEASE)) == me.encode()
            if ok:
                await redis.expire(LEASE, LEASE_S)
                if not leading:
                    log.info("signal reader leading")
                leading = True
                try:
                    await service.sync()
                except Exception:
                    log.exception("signal sources sync failed")
            elif leading:
                log.warning("another signal reader holds the lease: standing by")
                await service.stop()
                leading = False
            try:
                await asyncio.wait_for(stop.wait(), timeout=SYNC_S)
            except TimeoutError:
                pass
    finally:
        await service.stop()
        if leading and (await redis.get(LEASE)) == me.encode():
            await redis.delete(LEASE)
        await redis.aclose()
        await db.dispose()
    log.info("signal reader stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ae_signals")
    ap.add_argument("--once", action="store_true", help="start, report ready, exit (smoke test)")
    args = ap.parse_args(argv)
    if args.once:
        log.info("signal reader ready", version=__version__)
        return 0
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
