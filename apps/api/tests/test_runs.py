"""Deploying strategies, following and stopping runs, risk settings, and the admin engine switch."""

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
ADMIN = {"X-Dev-User": "admin@example.com"}
CFG: dict[str, Any] = {"kind": "time_based", "legs": [{"id": "L1", "action": "SELL", "option_type": "CE", "lots": 2}]}


@pytest.fixture
def api(clean_db: str) -> Iterator[TestClient]:
    app = create_app(Settings(app_env="test", dev_auth=True, database_url=clean_db, redis_url=TEST_REDIS_URL))
    with TestClient(app) as c:
        for h in (A, B, ADMIN):
            c.get("/v1/me", headers=h)
        e = create_engine(clean_db)
        with e.begin() as conn:
            conn.execute(text("UPDATE users SET role = 'admin' WHERE email = 'admin@example.com'"))
        e.dispose()
        yield c


def ready_strategy(api: TestClient, config: dict[str, Any] = CFG, name: str = "S") -> str:
    sid = api.post("/v1/strategies", json={"name": name, "config": config}, headers=A).json()["id"]
    api.patch(f"/v1/strategies/{sid}", json={"status": "ready"}, headers=A)
    return str(sid)


def test_deploy_checks_and_run_lifecycle(api: TestClient) -> None:
    draft = api.post("/v1/strategies", json={"name": "d", "config": CFG}, headers=A).json()["id"]
    r = api.post(f"/v1/strategies/{draft}/deploy", json={}, headers=A)
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "not_ready"

    sid = ready_strategy(api)
    live = api.post(f"/v1/strategies/{sid}/deploy", json={"mode": "live"}, headers=A)
    assert live.status_code == 403 and live.json()["error"]["code"] == "plan_feature"  # free plan: no live trading
    too_big = api.post(f"/v1/strategies/{sid}/deploy", json={"multiplier": 6}, headers=A)  # 12 lots > 10
    assert too_big.json()["error"]["code"] == "plan_limit"

    r = api.post(f"/v1/strategies/{sid}/deploy", json={"multiplier": 2}, headers=A)
    assert r.status_code == 201, r.text
    run = r.json()
    assert (run["status"], run["mode"], run["multiplier"], run["underlying"]) == ("pending", "paper", 2, "NIFTY")
    again = api.post(f"/v1/strategies/{sid}/deploy", json={}, headers=A)
    assert again.json()["error"]["details"]["reason"] == "already_running"
    other = ready_strategy(api, name="T")
    limit = api.post(f"/v1/strategies/{other}/deploy", json={}, headers=A)
    assert limit.json()["error"]["code"] == "plan_limit"  # free plan: one running strategy

    assert [x["id"] for x in api.get("/v1/runs", headers=A).json()] == [run["id"]]
    assert api.get("/v1/runs", headers=B).json() == []
    assert api.get(f"/v1/runs/{run['id']}", headers=B).status_code == 404  # another user's run does not exist
    d = api.get(f"/v1/runs/{run['id']}", headers=A).json()
    assert d["run"]["strategy_name"] == "S" and d["positions"] == [] and d["events"] == []

    stopped = api.post(f"/v1/runs/{run['id']}/stop", headers=A).json()
    assert stopped["status"] == "stopped"  # never started: stops at once
    assert api.post(f"/v1/runs/{run['id']}/stop", headers=A).status_code == 409
    assert api.get("/v1/runs", headers=A).json() == []
    assert [x["status"] for x in api.get("/v1/runs?active=false", headers=A).json()] == ["stopped"]


def test_live_gates(api: TestClient, clean_db: str) -> None:
    def sql(stmt: str) -> None:
        e = create_engine(clean_db)
        with e.begin() as conn:
            conn.execute(text(stmt))
        e.dispose()

    sql("UPDATE plans SET features = features || '{\"live_trading\": true}' WHERE code = 'free'")
    try:
        sid = ready_strategy(api)
        status = api.get("/v1/me/live", headers=A).json()
        assert status["plan_allows"] and not status["unlocked"] and not status["can_go_live"]
        assert status["can_dry_run"] and any("unlocked" in r for r in status["reasons"])

        def deploy(**body: Any) -> dict[str, Any]:
            return api.post(f"/v1/strategies/{sid}/deploy", json={"mode": "live", **body}, headers=A).json()  # type: ignore[no-any-return]

        assert deploy()["error"]["details"]["reason"] == "live_locked"
        me = api.get("/v1/me", headers=A).json()["id"]
        assert api.put(f"/v1/admin/users/{me}/live", json={"unlocked": True}, headers=A).status_code == 403
        r = api.put(f"/v1/admin/users/{me}/live", json={"unlocked": True}, headers=ADMIN)
        assert r.status_code == 200 and r.json()["live_unlocked"] is True
        assert deploy()["error"]["details"]["reason"] == "no_broker"
        sql(
            "INSERT INTO broker_accounts (user_id, broker, client_id, status, engine_enabled, terminal_enabled, "
            "key_version) SELECT id, 'zerodha', 'AB1234', 'connected', false, true, 1 FROM users "
            "WHERE email = 'alice@example.com'"
        )
        (b,) = api.get("/v1/me/live", headers=A).json()["brokers"]
        sql(
            "INSERT INTO broker_sessions (user_id, broker_account_id, access_token_enc, expires_at, key_version) "
            f"SELECT user_id, id, 'x', now() + interval '1 day', 1 FROM broker_accounts WHERE id = '{b['id']}'"
        )
        assert deploy(broker_account_id=b["id"])["error"]["details"]["reason"] == "engine_off"
        sql("UPDATE broker_accounts SET engine_enabled = true")
        assert api.get("/v1/me/live", headers=A).json()["can_go_live"] is True
        assert deploy(broker_account_id=b["id"], confirm="wrong")["error"]["details"]["reason"] == "confirm"
        run = deploy(broker_account_id=b["id"], confirm="S")
        assert (run["mode"], run["dry_run"], run["status"]) == ("live", False, "pending")
        api.post(f"/v1/runs/{run['id']}/stop", headers=A)
        dry = deploy(dry_run=True)  # a dry run needs no unlock, broker or confirmation
        assert dry["dry_run"] is True and dry["mode"] == "live"
    finally:
        sql("UPDATE plans SET features = features || '{\"live_trading\": false}' WHERE code = 'free'")


def test_stop_all_and_risk_settings(api: TestClient) -> None:
    sid = ready_strategy(api)
    api.post(f"/v1/strategies/{sid}/deploy", json={}, headers=A)
    assert len(api.post("/v1/runs/stop-all", headers=A).json()) == 1
    assert api.get("/v1/me/risk", headers=A).json() == {
        "max_daily_loss": None,
        "max_daily_profit": None,
        "max_open_positions": None,
        "max_trades_per_day": None,
        "kill_switch": False,
    }
    body = {"max_daily_loss": 5000, "max_open_positions": 4, "kill_switch": True}
    assert api.put("/v1/me/risk", json=body, headers=A).status_code == 200
    got = api.get("/v1/me/risk", headers=A).json()
    assert got["max_daily_loss"] == 5000 and got["kill_switch"] is True
    assert api.get("/v1/me/risk", headers=B).json()["kill_switch"] is False  # per user
    assert api.put("/v1/me/risk", json={"max_daily_loss": -1}, headers=A).status_code == 422


def test_admin_engine_halt_and_all_runs(api: TestClient) -> None:
    sid = ready_strategy(api)
    api.post(f"/v1/strategies/{sid}/deploy", json={}, headers=A)
    assert api.get("/v1/admin/engine", headers=A).status_code == 403
    s = api.get("/v1/admin/engine", headers=ADMIN).json()
    assert s["trading_halted"] is False and s["active_runs"] == 1
    runs = api.get("/v1/admin/runs", headers=ADMIN).json()
    assert [r["user_email"] for r in runs] == ["alice@example.com"]
    r = api.put("/v1/admin/engine/halt", json={"halted": True, "reason": "exchange issue"}, headers=ADMIN)
    assert r.json()["trading_halted"] is True and r.json()["halt_reason"] == "exchange issue"
    r = api.put("/v1/admin/engine/halt", json={"halted": False}, headers=ADMIN)
    assert r.json()["trading_halted"] is False


def test_deleting_a_strategy_stops_its_run(api: TestClient) -> None:
    sid = ready_strategy(api)
    rid = api.post(f"/v1/strategies/{sid}/deploy", json={}, headers=A).json()["id"]
    assert api.delete(f"/v1/strategies/{sid}", headers=A).status_code == 204
    run = api.get(f"/v1/runs/{rid}", headers=A).json()["run"]
    assert run["status"] == "stopped" and run["stop_reason"] == "strategy deleted"
