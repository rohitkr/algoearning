"""Backtests API: plan gate, validation, per-user isolation, the queue limit and coverage."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from ae_api.main import create_app
from ae_api.settings import Settings
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from conftest import TEST_REDIS_URL

A = {"X-Dev-User": "alice@example.com"}
B = {"X-Dev-User": "bob@example.com"}
CFG: dict[str, Any] = {"kind": "time_based", "legs": [{"id": "L1", "action": "SELL", "option_type": "CE"}]}
BODY = {"start_date": "2026-09-01", "end_date": "2026-09-25"}


@pytest.fixture
def api(clean_db: str) -> Iterator[TestClient]:
    app = create_app(Settings(app_env="test", dev_auth=True, database_url=clean_db, redis_url=TEST_REDIS_URL))
    with TestClient(app) as c:
        yield c


def set_backtesting(url: str, on: bool) -> None:
    e = create_engine(url)
    with e.begin() as c:
        c.execute(text(f"UPDATE plans SET features = features || '{{\"backtesting\": {str(on).lower()}}}'"))
    e.dispose()


def strategy(api: TestClient) -> str:
    return str(api.post("/v1/strategies", json={"name": "S", "config": CFG}, headers=A).json()["id"])


def test_plan_gate_and_validation(api: TestClient, clean_db: str) -> None:
    sid = strategy(api)
    r = api.post("/v1/backtests", json={"strategy_id": sid, **BODY}, headers=A)
    assert r.status_code == 403 and r.json()["error"]["code"] == "plan_feature"
    set_backtesting(clean_db, True)
    try:
        assert (
            api.post(
                "/v1/backtests",
                json={"strategy_id": sid, "start_date": "2026-09-25", "end_date": "2026-09-01"},
                headers=A,
            ).status_code
            == 400
        )
        assert (
            api.post(
                "/v1/backtests",
                json={"strategy_id": sid, "start_date": "2020-01-01", "end_date": "2026-09-01"},
                headers=A,
            ).status_code
            == 400
        )
        missing = "00000000-0000-0000-0000-000000000000"
        assert api.post("/v1/backtests", json={"strategy_id": missing, **BODY}, headers=A).status_code == 404
        b = api.post("/v1/backtests", json={"strategy_id": sid, **BODY, "multiplier": 2}, headers=A)
        assert b.status_code == 201, b.text
        run = b.json()
        assert (run["status"], run["underlying"], run["multiplier"], run["net_pnl"]) == ("pending", "NIFTY", 2, None)
        assert [x["id"] for x in api.get("/v1/backtests", headers=A).json()] == [run["id"]]
        assert api.get("/v1/backtests", headers=B).json() == []
        assert api.get(f"/v1/backtests/{run['id']}", headers=B).status_code == 404
        d = api.get(f"/v1/backtests/{run['id']}", headers=A).json()
        assert d["result"] is None and d["strategy_name"] == "S"
        for _ in range(2):
            assert api.post("/v1/backtests", json={"strategy_id": sid, **BODY}, headers=A).status_code == 201
        busy = api.post("/v1/backtests", json={"strategy_id": sid, **BODY}, headers=A)
        assert busy.json()["error"]["details"]["reason"] == "busy"  # three at a time
        assert api.delete(f"/v1/backtests/{run['id']}", headers=A).status_code == 204
        assert api.delete(f"/v1/backtests/{run['id']}", headers=B).status_code == 404
    finally:
        set_backtesting(clean_db, False)


def test_coverage(api: TestClient, clean_db: str) -> None:
    assert api.get("/v1/backtests/coverage").status_code == 401
    e = create_engine(clean_db)
    with e.begin() as c:
        c.execute(text("DELETE FROM history_candles"))
        c.execute(text(
            "INSERT INTO history_candles (key, ts, open, high, low, close, volume) VALUES "
            "('NIFTY', '2026-09-01 03:45:00+00', 1,1,1,1,0), ('NIFTY', '2026-09-02 03:45:00+00', 1,1,1,1,0), "
            "('NIFTY:2026-09-08:25000:CE', '2026-09-02 03:45:00+00', 1,1,1,1,0)"
        ))  # fmt: skip
    try:
        (cov,) = api.get("/v1/backtests/coverage", headers=A).json()
        assert (cov["underlying"], cov["index_days"], cov["index_from"], cov["index_to"]) == (
            "NIFTY",
            2,
            "2026-09-01",
            "2026-09-02",
        )
        assert (cov["option_days"], cov["expiries"]) == (1, 1)
    finally:
        with e.begin() as c:
            c.execute(text("DELETE FROM history_candles"))
        e.dispose()
