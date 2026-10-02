"""Background jobs.

    python -m ae_worker                        # run: every job once at start, then on its daily schedule
    python -m ae_worker --once                 # every job once, then exit (cron, deploy hooks)
    python -m ae_worker refresh-instruments    # one job, then exit
    uv run --with duckdb python -m ae_worker import-history ~/git/algo-trading-claude/data/market_data.duckdb
    python -m ae_worker backfill NIFTY --from 2025-01-01 [--to 2026-09-30] [--dry-run] [--index-only]  # ADR 0018
    python -m ae_worker smc-report NIFTY,BANKNIFTY,SENSEX --from 2025-01-01 --out smc.json  # the 3 x 3 SMC backtests

Jobs (times IST): refresh-instruments at 08:00 (Zerodha publishes the day's list before that). A plain asyncio
schedule until the job queue lands; every job is idempotent, so running it twice is harmless."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

import structlog
from ae_core.secrets import SecretBox, load_master_key
from ae_db.models import Instrument
from ae_db.session import Database
from ae_marketdata.backfill import BreezeHistory, HistoryClient, backfill
from ae_marketdata.history import archive_today, import_duckdb
from ae_marketdata.hub import Hub
from ae_marketdata.instruments import IST, refresh_instruments
from ae_marketdata.kite_feed import KiteHistory
from ae_marketdata.sources import choose_source
from redis.asyncio import Redis
from sqlalchemy import select

from . import __version__
from .backtests import run_pending, smc_report
from .notify import Sender, check_engine, dispatch_pending, link_telegram
from .public_ip import check_public_ip

log = structlog.get_logger("ae_worker")


async def archive_history(db: Database) -> int:
    url = os.environ.get("REDIS_URL")
    if not url:
        return 0
    redis = Redis.from_url(url)
    try:
        return int(await archive_today(db, Hub(redis)))
    finally:
        await redis.aclose()


JOBS: dict[str, tuple[time, Callable[[Database], Awaitable[object]]]] = {
    "refresh-instruments": (time(8, 0), refresh_instruments),
    "archive-history": (time(16, 5), archive_history),  # after the close: keep the day's bars for backtesting
}

# every few seconds, while the worker runs (not part of --once)
_sender = Sender.from_env()
EVERY: dict[str, tuple[float, Callable[[Database], Awaitable[object]]]] = {
    "send-notifications": (5, lambda db: dispatch_pending(db, _sender)),
    "link-telegram": (3, lambda db: link_telegram(db, _sender)),
    "check-engine": (30, check_engine),
    "run-backtests": (3, run_pending),
    "check-public-ip": (300, check_public_ip),  # Zerodha only takes orders from the IP in each user's Kite app
}


def history_provider() -> str:
    """The provider the backfill fetches from: the feed's (choose_source); Breeze when the feed simulates."""
    return "kite" if choose_source(os.environ) == "kite" else "breeze"


async def _kite_history(db: Database, code: str) -> tuple[KiteHistory | None, str | None]:
    """A KiteHistory for `code` with today's platform Kite session and instrument tokens, or why not."""
    import httpx
    from ae_marketdata.kite_feed import KiteCode, TokenBook
    from ae_marketdata.session import KITE_SESSION, load_session

    key, enc = os.environ.get("KITE_FEED_API_KEY", ""), os.environ.get("APP_ENCRYPTION_KEY", "")
    if not (key and enc):
        return None, "backfill from Kite needs KITE_FEED_API_KEY and APP_ENCRYPTION_KEY"
    async with db.system_session() as s:
        inst = (await s.execute(select(Instrument).where(Instrument.code == code))).scalar_one()
        token, _ = await load_session(s, SecretBox({1: load_master_key(enc)}, 1), KITE_SESSION)
        if not inst.kite_symbol:
            return None, f"{code} has no Kite symbol"
        codes = {code: KiteCode(inst.spot_exchange, inst.kite_symbol, code, inst.exchange)}
    if not token:
        return None, "no Kite session today: log in from Monitor > Market data first"
    book = TokenBook()
    async with httpx.AsyncClient() as c:
        await book.load(codes, c, datetime.now(IST).date())
    return KiteHistory(key, token, code, book), None


async def run_backfill(db: Database, args: argparse.Namespace) -> int:
    """History for one underlying (index, then options) from the price provider: Breeze, or the platform Kite app
    (which only has contracts still listed: expired ones are skipped). Needs the day's session for real fetches."""
    code = str(args.arg or "").upper()
    async with db.system_session() as s:
        inst = (await s.execute(select(Instrument).where(Instrument.code == code))).scalar_one_or_none()
        if inst is None:
            log.error("unknown instrument", code=code)
            return 2
        feed, spot_ex, deriv_ex, step = inst.feed_code or code, inst.spot_exchange, inst.exchange, inst.strike_step
    start = date.fromisoformat(args.from_)
    end = date.fromisoformat(args.to) if args.to else datetime.now(IST).date() - timedelta(days=1)
    client: HistoryClient | None = None
    kite: KiteHistory | None = None
    redis = None
    if not args.dry_run and history_provider() == "kite":
        kite, why = await _kite_history(db, code)
        if kite is None:
            log.error(why)
            return 2
        client = kite
    elif not args.dry_run:
        from ae_marketdata.session import breeze_session
        from ae_marketdata.sources import _breeze_sdk

        key, secret, enc = (
            os.environ.get(k, "") for k in ("BREEZE_API_KEY", "BREEZE_API_SECRET", "APP_ENCRYPTION_KEY")
        )
        url = os.environ.get("REDIS_URL")
        if not (key and secret and enc and url):
            log.error("backfill needs BREEZE_API_KEY, BREEZE_API_SECRET, APP_ENCRYPTION_KEY and REDIS_URL")
            return 2
        async with db.system_session() as s:
            token, _ = await breeze_session(s, SecretBox({1: load_master_key(enc)}, 1))
        if not token:
            log.error("no Breeze session today: log in from Monitor > Market data first")
            return 2
        redis = Redis.from_url(url)
        hub = Hub(redis)
        sdk = _breeze_sdk(key)
        await hub.count_api_call(1)  # generate_session is a REST call
        await asyncio.to_thread(sdk.generate_session, api_secret=secret, session_token=token)
        client = BreezeHistory(sdk, hub.count_api_call, hub.api_calls_today, reserve=args.reserve)
    try:
        rep = await backfill(db, code, start, end, feed_code=feed, spot_exchange=spot_ex, deriv_exchange=deriv_ex,
                             strike_step=step, client=client, buffer=args.buffer, dry_run=args.dry_run,
                             index_only=args.index_only)  # fmt: skip
    finally:
        if redis is not None:
            await redis.aclose()
        if kite is not None:
            await kite.aclose()
    print(json.dumps(asdict(rep), indent=2, default=str))
    return 0 if rep.stopped is None else 3


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


async def _every(db: Database, name: str) -> None:
    seconds, fn = EVERY[name]
    while True:
        try:
            await fn(db)
        except Exception:
            log.exception("job failed", job=name)
        await asyncio.sleep(seconds)


async def _main(args: argparse.Namespace) -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        log.error("DATABASE_URL is not set")
        return 2
    db = Database(url, pool_size=2)
    try:
        if args.job == "import-history":
            args.file = args.arg
            if not args.file:
                log.error("usage: python -m ae_worker import-history FILE.duckdb")
                return 2
            log.info("history imported", **await import_duckdb(db, args.file))
            return 0
        if args.job == "backfill":
            return await run_backfill(db, args)
        if args.job == "smc-report":
            end = date.fromisoformat(args.to) if args.to else datetime.now(IST).date() - timedelta(days=1)
            unds = [u.strip().upper() for u in (args.arg or "NIFTY,BANKNIFTY,SENSEX").split(",")]
            report = await smc_report(db, unds, [2, 3, 4], date.fromisoformat(args.from_), end)
            text = json.dumps(report, indent=1, default=str)
            if args.out:
                await asyncio.to_thread(Path(args.out).write_text, text)
            else:
                print(text)
            return 0
        names = [args.job] if args.job else list(JOBS)
        ok = all([await _run_job(db, n) for n in names])
        if args.job or args.once:
            return 0 if ok else 1
        log.info("worker ready", version=__version__, jobs=names)
        await asyncio.gather(*(_schedule(db, n) for n in names), *(_every(db, n) for n in EVERY))
        return 0
    finally:
        await db.dispose()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ae_worker")
    ap.add_argument(
        "job",
        nargs="?",
        choices=[*sorted(JOBS), "import-history", "backfill", "smc-report"],
        help="run one job and exit",
    )
    ap.add_argument("arg", nargs="?", help="import-history: the DuckDB file; backfill: the underlying (NIFTY, ...)")
    ap.add_argument("--once", action="store_true", help="run every job once and exit")
    ap.add_argument("--from", dest="from_", default="2025-01-01", help="backfill: first day (YYYY-MM-DD)")
    ap.add_argument("--to", help="backfill: last day (default: yesterday)")
    ap.add_argument("--dry-run", action="store_true", help="backfill: plan and count calls, fetch nothing")
    ap.add_argument("--reserve", type=int, default=500, help="backfill: Breeze calls left for the live feed")
    ap.add_argument("--buffer", type=int, default=4, help="backfill: strikes beyond each day's index range")
    ap.add_argument("--index-only", action="store_true", help="backfill: the index's candles only, no options")
    ap.add_argument("--out", help="smc-report: write the JSON report to this file")
    return asyncio.run(_main(ap.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
