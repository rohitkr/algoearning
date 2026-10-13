"""Data sync jobs: the history downloads asked for from Monitor > Data sync.

The API only writes a `data_sync_jobs` row (status pending). This worker job picks it up, builds the price provider's
history client (Breeze, or the platform Kite app) from today's stored session, runs the same download the
`python -m ae_worker backfill` command runs, and keeps the progress on the row for the page to show. It touches only
`history_candles`, `data_sync_jobs` and a small platform setting (market holidays): nothing the engine reads while
it trades. A job that stops (budget used up, or cancelled) can simply be asked for again: it continues where it
stopped, because a download only fetches what is still missing."""

from __future__ import annotations

import asyncio
import os
import time as clock
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from ae_core.secrets import SecretBox, load_master_key
from ae_db.models import DataSyncJob, Instrument
from ae_db.session import Database
from ae_marketdata.backfill import BreezeHistory, HistoryClient, backfill, backfill_tail
from ae_marketdata.hub import Hub
from ae_marketdata.kite_feed import KiteHistory
from ae_marketdata.sources import choose_source
from ae_marketdata.types import IST
from redis.asyncio import Redis
from sqlalchemy import select

log = structlog.get_logger("ae_worker.data_sync")
STALE = timedelta(minutes=2)  # a running job that has not reported for this long was lost with its worker
RESERVE = 500  # Breeze calls always left for the live feed
Cleanup = Callable[[], Awaitable[None]]


class SyncError(Exception):
    """Something the admin can act on, said plainly."""


def provider_for(choice: str) -> str:
    """auto means what the price feed uses: Kite when only Kite is configured, else Breeze."""
    if choice in ("breeze", "kite"):
        return choice
    return "kite" if choose_source(os.environ) == "kite" else "breeze"


async def _kite(db: Database, code: str) -> tuple[HistoryClient, Cleanup]:
    import httpx
    from ae_marketdata.kite_feed import KiteCode, TokenBook
    from ae_marketdata.session import KITE_SESSION, load_session

    key, enc = os.environ.get("KITE_FEED_API_KEY", ""), os.environ.get("APP_ENCRYPTION_KEY", "")
    if not (key and enc):
        raise SyncError("Kite is not set up for downloads: KITE_FEED_API_KEY and APP_ENCRYPTION_KEY are needed")
    async with db.system_session() as s:
        inst = (await s.execute(select(Instrument).where(Instrument.code == code))).scalar_one()
        token, _ = await load_session(s, SecretBox({1: load_master_key(enc)}, 1), KITE_SESSION)
        if not inst.kite_symbol:
            raise SyncError(f"{code} has no Kite symbol")
        codes = {code: KiteCode(inst.spot_exchange, inst.kite_symbol, code, inst.exchange)}
    if not token:
        raise SyncError("No Kite session today: log in from Monitor > Market data first")
    book = TokenBook()
    async with httpx.AsyncClient() as c:
        await book.load(codes, c, datetime.now(IST).date())
    kite = KiteHistory(key, token, code, book)
    return kite, kite.aclose


async def _breeze(db: Database) -> tuple[HistoryClient, Cleanup]:
    from ae_marketdata.session import breeze_session
    from ae_marketdata.sources import _breeze_sdk

    key, secret, enc = (os.environ.get(k, "") for k in ("BREEZE_API_KEY", "BREEZE_API_SECRET", "APP_ENCRYPTION_KEY"))
    url = os.environ.get("REDIS_URL")
    if not (key and secret and enc and url):
        raise SyncError("Breeze is not set up for downloads: BREEZE_API_KEY, BREEZE_API_SECRET, APP_ENCRYPTION_KEY and "
                        "REDIS_URL are needed")  # fmt: skip
    async with db.system_session() as s:
        token, _ = await breeze_session(s, SecretBox({1: load_master_key(enc)}, 1))
    if not token:
        raise SyncError("No Breeze session today: log in from Monitor > Market data first")
    redis = Redis.from_url(url)
    hub = Hub(redis)
    sdk = _breeze_sdk(key)
    await hub.count_api_call(1)  # generate_session is a REST call
    try:
        await asyncio.to_thread(sdk.generate_session, api_secret=secret, session_token=token)
    except Exception as exc:  # the SDK raises plain Exception
        await redis.aclose()
        raise SyncError("Breeze rejected the stored session (expired, or replaced by a newer login): log in again "
                        f"from Monitor > Market data ({exc})") from exc  # fmt: skip
    return BreezeHistory(sdk, hub.count_api_call, hub.api_calls_today, reserve=RESERVE), redis.aclose


async def build_client(db: Database, code: str, provider: str) -> tuple[HistoryClient, Cleanup]:
    return await (_kite(db, code) if provider == "kite" else _breeze(db))


async def _reap(db: Database) -> None:
    """A job still marked running whose worker went away: say so, so the page does not wait for it forever."""
    now = datetime.now(UTC)
    async with db.system_session() as s:
        rows = await s.execute(select(DataSyncJob).where(DataSyncJob.status == "running"))
        for job in rows.scalars():
            if job.heartbeat_at is None or now - job.heartbeat_at > STALE:
                job.status, job.finished_at = "failed", now
                job.error = "The worker stopped while this was running. Start it again: it continues where it stopped."


async def run_pending(db: Database) -> None:
    """Run the oldest pending job, if any (one at a time)."""
    await _reap(db)
    async with db.system_session() as s:
        busy = (await s.execute(select(DataSyncJob.id).where(DataSyncJob.status == "running").limit(1))).first()
        if busy:
            return
        job = (
            await s.execute(
                select(DataSyncJob)
                .where(DataSyncJob.status == "pending")
                .order_by(DataSyncJob.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if job is None:
            return
        now = datetime.now(UTC)
        job.status, job.started_at, job.heartbeat_at = "running", now, now
        job.progress = {"phase": "starting", "done": 0, "total": 0, "calls": 0, "rows": 0}
        job_id = job.id
    await execute(db, job_id)


async def _update(db: Database, job_id: Any, **values: Any) -> bool:
    """Write fields on the job; returns whether the admin asked it to stop."""
    async with db.system_session() as s:
        job = await s.get(DataSyncJob, job_id)
        if job is None:
            return True
        for k, v in values.items():
            setattr(job, k, v)
        job.heartbeat_at = datetime.now(UTC)
        return bool(job.cancel_requested)


async def execute(db: Database, job_id: Any) -> None:
    async with db.system_session() as s:
        job = await s.get(DataSyncJob, job_id)
        if job is None:
            return
        code, mode, choice, start, end = job.underlying, job.mode, job.provider, job.start_date, job.end_date
        inst = (await s.execute(select(Instrument).where(Instrument.code == code))).scalar_one_or_none()
        facts = None if inst is None else (inst.feed_code or code, inst.spot_exchange, inst.exchange, inst.strike_step)
    provider = provider_for(choice)
    cleanup: Cleanup | None = None
    try:
        if facts is None:
            raise SyncError(f"{code} is not an instrument")
        feed, spot_ex, deriv_ex, step = facts
        client, cleanup = await build_client(db, code, provider)
        stop = {"asked": False, "checked": 0.0}

        async def should_stop() -> bool:
            if clock.monotonic() - stop["checked"] >= 1.0:  # looked up at most once a second
                stop["checked"] = clock.monotonic()
                async with db.system_session() as s:
                    job = await s.get(DataSyncJob, job_id)
                    stop["asked"] = bool(job is None or job.cancel_requested)
            return bool(stop["asked"])

        async def progress(p: dict[str, Any]) -> None:
            await _update(db, job_id, progress=p)

        if mode == "closing":
            rep = await backfill_tail(db, code, start, end, feed_code=feed, deriv_exchange=deriv_ex, strike_step=step,
                                      client=client, progress=progress, should_stop=should_stop)  # fmt: skip
        else:
            rep = await backfill(db, code, start, end, feed_code=feed, spot_exchange=spot_ex, deriv_exchange=deriv_ex,
                                 strike_step=step, client=client, refill_partial=True, progress=progress,
                                 should_stop=should_stop)  # fmt: skip
        result = {**asdict(rep), "provider": provider}
        await _update(db, job_id, status="done" if rep.stopped is None else "stopped", result=result,
                      finished_at=datetime.now(UTC))  # fmt: skip
        log.info("data sync finished", job=str(job_id), code=code, mode=mode, provider=provider,
                 calls=rep.calls_made, rows=rep.rows, stopped=rep.stopped)  # fmt: skip
    except SyncError as exc:
        await _update(db, job_id, status="failed", error=str(exc), finished_at=datetime.now(UTC))
    except Exception as exc:  # whatever the provider or the database threw: on the page, not a crash of the worker
        log.exception("data sync failed", job=str(job_id))
        await _update(db, job_id, status="failed", error=f"{type(exc).__name__}: {exc}"[:500],
                      finished_at=datetime.now(UTC))  # fmt: skip
    finally:
        if cleanup is not None:
            await cleanup()
