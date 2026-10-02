"""python -m ae_marketdata: the platform's one market-data feed process.

Environment:
    DATABASE_URL, REDIS_URL        required
    MARKET_DATA_SOURCE             breeze | kite | simulated | auto (default). auto: breeze when the Breeze keys are
                                   set, else kite when the platform Kite keys are set, else simulated
    BREEZE_API_KEY, BREEZE_API_SECRET, APP_ENCRYPTION_KEY   for breeze (the daily session comes from Monitor)
    KITE_FEED_API_KEY, APP_ENCRYPTION_KEY                   for kite (the platform's own Kite Connect app, ADR 0021;
                                   the API holds its secret and the daily session comes from Monitor)
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys

import structlog
from ae_core.secrets import SecretBox, load_master_key
from ae_db.models import Instrument
from ae_db.session import Database
from redis.asyncio import Redis
from sqlalchemy import select

from .hub import Hub
from .kite_feed import KiteCode, KiteSource
from .service import Feed
from .session import KITE_SESSION, breeze_session, load_session
from .sources import BreezeSource, FeedCode, SimulatedSource, Source, choose_source

log = structlog.get_logger("ae_marketdata")


async def _main() -> int:
    db_url, redis_url = os.environ.get("DATABASE_URL"), os.environ.get("REDIS_URL")
    if not db_url or not redis_url:
        log.error("DATABASE_URL and REDIS_URL are required")
        return 2
    key, secret = os.environ.get("BREEZE_API_KEY", ""), os.environ.get("BREEZE_API_SECRET", "")
    kite_key = os.environ.get("KITE_FEED_API_KEY", "")
    choice = choose_source(os.environ)
    db, redis = Database(db_url, pool_size=2), Redis.from_url(redis_url)
    hub = Hub(redis)
    codes: dict[str, FeedCode] = {}
    kite_codes: dict[str, KiteCode] = {}
    active: set[str] = set()
    token: list[str | None] = [None]

    async def load() -> None:
        async with db.system_session() as s:
            rows = (await s.execute(select(Instrument).where(Instrument.is_active.is_(True)))).scalars().all()
            codes.clear()
            codes.update({r.code: FeedCode(r.spot_exchange, r.feed_code or r.code, r.exchange) for r in rows})
            kite_codes.clear()
            kite_codes.update(
                {r.code: KiteCode(r.spot_exchange, r.kite_symbol, r.code, r.exchange) for r in rows if r.kite_symbol}
            )
            active.clear()
            active.update(r.code for r in rows)
            if choice == "breeze" and box is not None:
                token[0] = (await breeze_session(s, box))[0]
            elif choice == "kite" and box is not None:
                token[0] = (await load_session(s, box, KITE_SESSION))[0]

    async def always() -> set[str]:
        return set(active)

    box = None
    source: Source
    if choice == "breeze":
        enc = os.environ.get("APP_ENCRYPTION_KEY")
        if not (key and secret and enc):
            log.error("breeze needs BREEZE_API_KEY, BREEZE_API_SECRET and APP_ENCRYPTION_KEY")
            return 2
        box = SecretBox({1: load_master_key(enc)}, 1)
        source = BreezeSource(key, secret, lambda: token[0], lambda: codes, count_call=hub.count_api_call)
    elif choice == "kite":
        enc = os.environ.get("APP_ENCRYPTION_KEY")
        if not (kite_key and enc):
            log.error("kite needs KITE_FEED_API_KEY and APP_ENCRYPTION_KEY")
            return 2
        box = SecretBox({1: load_master_key(enc)}, 1)
        source = KiteSource(kite_key, lambda: token[0], lambda: kite_codes)
    elif choice == "simulated":
        source = SimulatedSource()
        log.warning("SIMULATED market data: prices are random, for development only")
    else:
        log.error("MARKET_DATA_SOURCE must be breeze, kite, simulated or auto")
        return 2

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await Feed(hub, source, always, before_sync=load).run(stop)
    finally:
        await redis.aclose()
        await db.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
