"""Admin: the history download (data sync) page. Shows what history is stored and what is missing, and queues a
download that the worker runs (ae_worker.data_sync) while this page shows its progress. Nothing here touches the
trading engine: it reads `history_candles` and writes `data_sync_jobs`."""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from ae_db.models import DataSyncJob, Instrument
from ae_marketdata.backfill import CAS_FROM
from ae_marketdata.coverage import Coverage, coverage
from ae_marketdata.sources import choose_source
from ae_marketdata.types import IST
from fastapi import APIRouter, Query, Request
from sqlalchemy import select, text

from ..audit import audit
from ..deps import DbDep
from ..errors import Conflict, NotFound
from ..schemas import DataSyncCoverage, SyncDay, SyncJobIn, SyncJobOut, SyncPlan
from .admin import Admin

router = APIRouter(tags=["data-sync"])
CACHE_S = 30.0
_cache: dict[tuple[str, date, date], tuple[float, Coverage]] = {}
ACTIVE = ("pending", "running")


def _job_out(j: DataSyncJob) -> SyncJobOut:
    return SyncJobOut(
        id=j.id,
        underlying=j.underlying,
        mode=j.mode,
        provider=j.provider,
        start_date=j.start_date,
        end_date=j.end_date,
        status=j.status,  # type: ignore[arg-type]
        progress=dict(j.progress or {}),
        result=j.result,
        error=j.error,
        cancel_requested=j.cancel_requested,
        created_at=j.created_at,
        started_at=j.started_at,
        finished_at=j.finished_at,
        heartbeat_at=j.heartbeat_at,
    )


def default_provider() -> Literal["breeze", "kite", "none"]:
    source = choose_source(os.environ)
    return "breeze" if source == "breeze" else "kite" if source == "kite" else "none"


@router.get("/v1/admin/data-sync/coverage", response_model=DataSyncCoverage)
async def data_sync_coverage(
    _: Admin,
    db: DbDep,
    underlying: Annotated[str | None, Query(max_length=20)] = None,
    start: date | None = None,
    end: date | None = None,
    refresh: bool = False,
) -> DataSyncCoverage:
    """Per day: the index candles and the option contracts stored against the ones a download would want."""
    async with db.system_session() as s:
        rows = (
            await s.execute(
                select(Instrument.code, Instrument.strike_step)
                .where(Instrument.is_active.is_(True))
                .order_by(Instrument.code)
            )
        ).all()
        steps = {c: int(step) for c, step in rows}
        code = (underlying or ("NIFTY" if "NIFTY" in steps else next(iter(steps), ""))).upper()
        if code not in steps:
            raise NotFound(f"{code or 'that'} is not an active instrument")
        first = (
            await s.execute(
                text("SELECT min((ts AT TIME ZONE 'Asia/Kolkata')::date) FROM history_candles WHERE key = :k"),
                {"k": code},
            )
        ).scalar_one_or_none()
    today = datetime.now(IST).date()
    lo = start or first or today - timedelta(days=90)
    hi = min(end or today, today)
    if lo > hi:
        raise Conflict("the start date is after the end date")
    key = (code, lo, hi)
    hit = _cache.get(key)
    if hit is None or refresh or time.monotonic() - hit[0] > CACHE_S:
        _cache[key] = (time.monotonic(), await coverage(db, code, lo, hi, steps[code]))
    cov = _cache[key][1]
    return DataSyncCoverage(
        underlying=code,
        underlyings=sorted(steps),
        start=cov.start,
        end=cov.end,
        first_day=cov.first_day,
        last_day=cov.last_day,
        trading_days=cov.trading_days,
        days=[
            SyncDay(
                day=d.day,
                status=d.status,  # type: ignore[arg-type]
                index_bars=d.index_bars,
                options_expected=d.options_expected,
                options_complete=d.options_complete,
                options_partial=d.options_partial,
                options_missing=d.options_missing,
                closing_missing=d.closing_missing,
                closing_have=d.closing_have,
                closing_empty=d.closing_empty,
            )
            for d in cov.days
        ],
        plan=SyncPlan(**vars(cov.plan)),
        default_provider=default_provider(),
    )


@router.get("/v1/admin/data-sync/jobs", response_model=list[SyncJobOut])
async def data_sync_jobs(_: Admin, db: DbDep) -> list[SyncJobOut]:
    async with db.system_session() as s:
        rows = (await s.execute(select(DataSyncJob).order_by(DataSyncJob.created_at.desc()).limit(15))).scalars()
        return [_job_out(j) for j in rows]


@router.post("/v1/admin/data-sync/jobs", response_model=SyncJobOut, status_code=201)
async def start_data_sync(body: SyncJobIn, admin: Admin, request: Request, db: DbDep) -> SyncJobOut:
    """Queue a download. One at a time: the provider's call budget is shared with the live price feed."""
    code = body.underlying.upper()
    today = datetime.now(IST).date()
    async with db.system_session() as s:
        if (await s.execute(select(Instrument.code).where(Instrument.code == code))).first() is None:
            raise NotFound(f"{code} is not an instrument")
        busy = (await s.execute(select(DataSyncJob.id).where(DataSyncJob.status.in_(ACTIVE)).limit(1))).first()
        if busy:
            raise Conflict("a download is already running: wait for it to finish or stop it", {"reason": "busy"})
        first = (
            await s.execute(
                text("SELECT min((ts AT TIME ZONE 'Asia/Kolkata')::date) FROM history_candles WHERE key = :k"),
                {"k": code},
            )
        ).scalar_one_or_none()
        lo = body.start_date or (CAS_FROM if body.mode == "closing" else first) or today - timedelta(days=90)
        hi = min(body.end_date or today, today)
        if lo > hi:
            raise Conflict("the start date is after the end date")
        job = DataSyncJob(
            underlying=code,
            mode=body.mode,
            provider=body.provider,
            start_date=lo,
            end_date=hi,
            status="pending",
            progress={"phase": "queued", "done": 0, "total": 0, "calls": 0, "rows": 0},
            requested_by=admin.user_id,
        )
        s.add(job)
        await s.flush()
        await audit(
            s, request, "admin.data_sync.start", admin.user_id, "data_sync_job", job.id,
            actor="admin", underlying=code, mode=body.mode, provider=body.provider,
            start=lo.isoformat(), end=hi.isoformat(),
        )  # fmt: skip
        _cache.clear()
        await s.refresh(job)
        return _job_out(job)


@router.post("/v1/admin/data-sync/jobs/{job_id}/cancel", response_model=SyncJobOut)
async def cancel_data_sync(job_id: uuid.UUID, admin: Admin, request: Request, db: DbDep) -> SyncJobOut:
    async with db.system_session() as s:
        job = await s.get(DataSyncJob, job_id)
        if job is None:
            raise NotFound("job not found")
        if job.status in ACTIVE:
            job.cancel_requested = True
            if job.status == "pending":
                job.status, job.finished_at = "stopped", datetime.now(UTC)
                job.result = {"stopped": "stopped by you", "provider": job.provider}
            await audit(s, request, "admin.data_sync.cancel", admin.user_id, "data_sync_job", job.id, actor="admin")
        await s.flush()
        await s.refresh(job)
        return _job_out(job)
