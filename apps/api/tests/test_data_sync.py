"""Monitor > Data sync: the coverage picture and the download queue (the worker's side is tested in apps/worker)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, time, timedelta

import pytest
from ae_api.main import create_app
from ae_api.routers import data_sync as router
from ae_api.settings import Settings
from ae_core.secrets import new_master_key
from ae_marketdata.types import IST
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from conftest import TEST_REDIS_URL

A = {"X-Dev-User": "alice@example.com"}
ADMIN = {"X-Dev-User": "admin@example.com"}
DAY = date(2026, 10, 6)


@pytest.fixture
def api(clean_db: str) -> Iterator[TestClient]:
    settings = Settings(
        app_env="test", dev_auth=True, database_url=clean_db, redis_url=TEST_REDIS_URL,
        app_encryption_key=new_master_key(),
    )  # fmt: skip
    router._cache.clear()
    with TestClient(create_app(settings)) as c:
        c.get("/v1/me", headers=A)
        c.get("/v1/me", headers=ADMIN)
        e = create_engine(clean_db)
        with e.begin() as conn:
            conn.execute(text("UPDATE users SET role = 'admin' WHERE email = 'admin@example.com'"))
            conn.execute(text("DELETE FROM history_candles"))
            for i in range(375):
                t = datetime.combine(DAY, time(9, 15), tzinfo=IST) + timedelta(minutes=i)
                conn.execute(
                    text("INSERT INTO history_candles (key, ts, open, high, low, close, volume) "
                         "VALUES ('NIFTY', :t, 25000, 25040, 24960, 25000, 0)"),
                    {"t": t},
                )  # fmt: skip
        e.dispose()
        yield c


def test_only_an_admin_may_see_or_start_downloads(api: TestClient) -> None:
    assert api.get("/v1/admin/data-sync/coverage").status_code == 401
    assert api.get("/v1/admin/data-sync/coverage", headers=A).status_code == 403
    assert api.get("/v1/admin/data-sync/jobs", headers=A).status_code == 403
    body = {"underlying": "NIFTY", "mode": "fill"}
    assert api.post("/v1/admin/data-sync/jobs", json=body, headers=A).status_code == 403


def test_coverage_lists_each_day_and_what_a_download_would_fetch(api: TestClient) -> None:
    r = api.get("/v1/admin/data-sync/coverage?underlying=NIFTY&start=2026-10-05&end=2026-10-07", headers=ADMIN)
    assert r.status_code == 200
    c = r.json()
    assert c["underlying"] == "NIFTY" and "NIFTY" in c["underlyings"] and c["trading_days"] == 1
    days = {d["day"]: d for d in c["days"]}
    assert days["2026-10-06"]["status"] == "missing" and days["2026-10-06"]["options_expected"] > 0
    assert days["2026-10-06"]["index_bars"] == 375
    assert c["plan"]["option_fetches"] > 0 and c["plan"]["index_days_missing"] == 2
    assert c["default_provider"] in ("breeze", "kite", "none")
    assert api.get("/v1/admin/data-sync/coverage?underlying=NOPE", headers=ADMIN).status_code == 404
    bad = api.get("/v1/admin/data-sync/coverage?start=2026-10-09&end=2026-10-01", headers=ADMIN)
    assert bad.status_code == 409


def test_a_download_is_queued_once_and_can_be_stopped(api: TestClient) -> None:
    r = api.post("/v1/admin/data-sync/jobs", json={"underlying": "nifty", "mode": "fill"}, headers=ADMIN)
    assert r.status_code == 201
    job = r.json()
    assert job["status"] == "pending" and job["underlying"] == "NIFTY" and job["provider"] == "auto"
    assert job["start_date"] == "2026-10-06"  # the first day with data
    again = api.post("/v1/admin/data-sync/jobs", json={"underlying": "NIFTY", "mode": "closing"}, headers=ADMIN)
    assert again.status_code == 409 and again.json()["error"]["details"]["reason"] == "busy"
    assert [j["id"] for j in api.get("/v1/admin/data-sync/jobs", headers=ADMIN).json()] == [job["id"]]
    stopped = api.post(f"/v1/admin/data-sync/jobs/{job['id']}/cancel", headers=ADMIN).json()
    assert stopped["status"] == "stopped" and stopped["cancel_requested"] and stopped["result"]["stopped"]
    # the next one may start now
    ok = api.post("/v1/admin/data-sync/jobs", json={"underlying": "NIFTY", "mode": "closing"}, headers=ADMIN)
    assert ok.status_code == 201 and ok.json()["start_date"] == "2026-08-03"  # the closing session began then
    assert api.post(
        "/v1/admin/data-sync/jobs", json={"underlying": "XYZ", "mode": "fill"}, headers=ADMIN
    ).status_code in (
        404,
        409,
    )
    assert api.post(f"/v1/admin/data-sync/jobs/{job['id'][:-1]}0/cancel", headers=ADMIN).status_code == 404
