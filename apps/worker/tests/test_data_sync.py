"""Data sync jobs: the worker runs a queued download, keeps its progress on the row, and records every way it can end
(done, stopped, failed) instead of raising."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from ae_db.models import DataSyncJob, HistoryCandle
from ae_db.session import Database
from ae_marketdata.types import IST
from ae_worker import data_sync
from sqlalchemy import func, select, text

DAY = date(2026, 8, 4)  # a Tuesday, after the closing session began


def ts(hm: str, day: date = DAY) -> datetime:
    return datetime.combine(day, time.fromisoformat(hm), tzinfo=IST)


class FakeClient:
    """A provider that has every minute asked for, shaped like Breeze's rows."""

    def __init__(self) -> None:
        self.calls = 0
        self.asked: list[dict[str, Any]] = []

    async def candles(self, params: dict[str, Any], start: datetime, end: datetime) -> list[dict[str, Any]]:
        self.calls += 1
        self.asked.append(params)
        out, t = [], start
        while t <= end:
            out.append({"datetime": t.isoformat(), "open": 10, "high": 11, "low": 9, "close": 10, "volume": 5})
            t += timedelta(minutes=1)
        return out


async def seed_index(db: Database) -> None:
    async with db.system_session() as s:
        await s.execute(text("DELETE FROM history_candles"))
        for i in range(375):
            s.add(
                HistoryCandle(
                    key="NIFTY",
                    ts=ts("09:15") + timedelta(minutes=i),
                    open=25000,
                    high=25040,
                    low=24960,
                    close=25000,
                    volume=0,
                )
            )


async def queue(db: Database, **kw: Any) -> Any:
    async with db.system_session() as s:
        job = DataSyncJob(underlying="NIFTY", mode="closing", provider="breeze", start_date=DAY, end_date=DAY,
                          progress={}, **kw)  # fmt: skip
        s.add(job)
        await s.flush()
        return job.id


async def job(db: Database, job_id: Any) -> DataSyncJob:
    async with db.system_session() as s:
        return (await s.execute(select(DataSyncJob).where(DataSyncJob.id == job_id))).scalar_one()


def use(monkeypatch: pytest.MonkeyPatch, client: FakeClient | Exception) -> None:
    async def build(db: Database, code: str, provider: str) -> Any:
        if isinstance(client, Exception):
            raise client

        async def cleanup() -> None:
            return None

        return client, cleanup

    monkeypatch.setattr(data_sync, "build_client", build)


async def test_a_queued_job_runs_reports_progress_and_stores_the_candles(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_index(db)
    client = FakeClient()
    use(monkeypatch, client)
    job_id = await queue(db)
    await data_sync.run_pending(db)
    j = await job(db, job_id)
    assert j.status == "done" and j.error is None and j.finished_at is not None
    assert j.progress["phase"] == "closing" and j.progress["done"] == j.progress["total"] > 0
    assert j.result is not None and j.result["rows"] > 0 and j.result["provider"] == "breeze"
    assert client.calls == j.progress["total"]  # one small call per contract-day
    async with db.system_session() as s:
        late = (
            await s.execute(select(func.count()).select_from(HistoryCandle).where(HistoryCandle.key.like("NIFTY:%")))
        ).scalar_one()
    assert late == j.result["rows"]  # everything fetched was stored
    await data_sync.run_pending(db)  # nothing else is waiting
    assert (await job(db, job_id)).status == "done"


async def test_a_job_asked_to_stop_ends_stopped_and_can_be_run_again(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_index(db)
    client = FakeClient()
    use(monkeypatch, client)
    job_id = await queue(db, cancel_requested=True)
    await data_sync.run_pending(db)
    j = await job(db, job_id)
    assert j.status == "stopped" and j.result is not None and j.result["stopped"] == "stopped by you"
    assert client.calls == 0  # it never started fetching


async def test_a_problem_the_admin_can_fix_is_said_plainly(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, data_sync.SyncError("No Breeze session today: log in from Monitor > Market data first"))
    job_id = await queue(db)
    await data_sync.run_pending(db)
    j = await job(db, job_id)
    assert j.status == "failed" and j.error == "No Breeze session today: log in from Monitor > Market data first"


async def test_an_unexpected_error_fails_the_job_not_the_worker(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, RuntimeError("boom"))
    job_id = await queue(db)
    await data_sync.run_pending(db)
    j = await job(db, job_id)
    assert j.status == "failed" and j.error == "RuntimeError: boom"


async def test_only_one_job_runs_at_a_time_and_a_lost_one_is_marked_failed(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, data_sync.SyncError("not reached"))
    now = datetime.now(UTC)
    async with db.system_session() as s:
        s.add(DataSyncJob(underlying="NIFTY", mode="fill", provider="auto", start_date=DAY, end_date=DAY,
                          status="running", heartbeat_at=now, progress={}))  # fmt: skip
    waiting = await queue(db)
    await data_sync.run_pending(db)  # another job is running and alive: this one waits
    assert (await job(db, waiting)).status == "pending"
    async with db.system_session() as s:  # that worker died 10 minutes ago
        await s.execute(text("UPDATE data_sync_jobs SET heartbeat_at = :t WHERE status = 'running'"),
                        {"t": now - timedelta(minutes=10)})  # fmt: skip
    await data_sync.run_pending(db)
    async with db.system_session() as s:
        lost = (await s.execute(select(DataSyncJob).where(DataSyncJob.mode == "fill"))).scalar_one()
    assert lost.status == "failed" and "worker stopped" in (lost.error or "")
    assert (await job(db, waiting)).status in ("pending", "failed")  # the next pass picks it up


def test_auto_means_the_feeds_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("BREEZE_API_KEY", "BREEZE_API_SECRET", "KITE_FEED_API_KEY", "MARKET_DATA_SOURCE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KITE_FEED_API_KEY", "k")
    assert data_sync.provider_for("auto") == "kite" and data_sync.provider_for("breeze") == "breeze"
    monkeypatch.setenv("BREEZE_API_KEY", "b")
    monkeypatch.setenv("BREEZE_API_SECRET", "s")
    assert data_sync.provider_for("auto") == "breeze" and data_sync.provider_for("kite") == "kite"
