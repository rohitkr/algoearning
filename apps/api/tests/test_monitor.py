"""Monitor (admin API): only admins get in; overrides change what a user may do at once; grants, suspension,
instruments and the audit trail."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from ae_api.main import create_app
from ae_api.settings import Settings
from ae_core.secrets import new_master_key
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from conftest import TEST_REDIS_URL

A = {"X-Dev-User": "alice@example.com"}
ADMIN = {"X-Dev-User": "admin@example.com"}


@pytest.fixture
def api(clean_db: str) -> Iterator[TestClient]:
    settings = Settings(
        app_env="test",
        dev_auth=True,
        database_url=clean_db,
        redis_url=TEST_REDIS_URL,
        app_encryption_key=new_master_key(),
    )
    app = create_app(settings)
    with TestClient(app) as c:
        c.get("/v1/me", headers=A)
        c.get("/v1/me", headers=ADMIN)
        e = create_engine(clean_db)
        with e.begin() as conn:
            conn.execute(text("UPDATE users SET role = 'admin' WHERE email = 'admin@example.com'"))
        e.dispose()
        yield c


def alice(api: TestClient) -> dict[str, Any]:
    items = api.get("/v1/admin/users?q=ALICE", headers=ADMIN).json()["items"]
    assert [u["email"] for u in items] == ["alice@example.com"]
    return items[0]  # type: ignore[no-any-return]


def test_only_admins(api: TestClient) -> None:
    for path in ("/v1/admin/overview", "/v1/admin/users", "/v1/admin/instruments", "/v1/admin/audit"):
        assert api.get(path, headers=A).status_code == 403, path
        assert api.get(path, headers=ADMIN).status_code == 200, path


def test_overrides_lift_a_limit_for_one_user(api: TestClient) -> None:
    uid = alice(api)["id"]
    body = {"broker": "zerodha", "api_key": "k" * 16, "api_secret": "s" * 32}
    assert api.post("/v1/broker-accounts", json={**body, "client_id": "AB0001"}, headers=A).status_code == 201
    r = api.post("/v1/broker-accounts", json={**body, "client_id": "AB0002"}, headers=A)
    assert r.json()["error"]["code"] == "plan_limit"  # free plan: one broker account

    bad = api.put(f"/v1/admin/users/{uid}/overrides", json={"features": {"max_brokers": 3}}, headers=ADMIN)
    assert bad.status_code == 400
    r = api.put(
        f"/v1/admin/users/{uid}/overrides",
        json={"features": {"max_broker_accounts": 3}, "note": "tester"},
        headers=ADMIN,
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["entitlements"]["overrides"] == {"max_broker_accounts": 3} and d["override_note"] == "tester"
    assert d["user"]["has_overrides"] and d["entitlements"]["usage"]["max_broker_accounts"]["limit"] == 3
    assert api.post("/v1/broker-accounts", json={**body, "client_id": "AB0002"}, headers=A).status_code == 201
    me = api.get("/v1/me/entitlements", headers=A).json()
    assert me["plan_code"] == "free" and me["features"]["max_broker_accounts"] == 3

    r = api.put(f"/v1/admin/users/{uid}/overrides", json={"features": {}}, headers=ADMIN)
    assert r.json()["entitlements"]["features"]["max_broker_accounts"] == 1 and not r.json()["user"]["has_overrides"]


def test_grant_and_end_a_plan(api: TestClient) -> None:
    uid = alice(api)["id"]
    r = api.post(f"/v1/admin/users/{uid}/grants", json={"plan_code": "pro_plus", "days": 30}, headers=ADMIN)
    assert r.status_code == 201 and r.json()["user"]["plan_code"] == "pro_plus"
    sub = r.json()["subscriptions"][0]
    assert sub["provider"] == "manual"
    assert api.get("/v1/admin/overview", headers=ADMIN).json()["paying_users"] == 1
    assert api.delete(f"/v1/admin/users/{uid}/grants/{sub['id']}", headers=ADMIN).status_code == 204
    assert api.get(f"/v1/admin/users/{uid}", headers=ADMIN).json()["user"]["plan_code"] == "free"
    assert (
        api.post(f"/v1/admin/users/{uid}/grants", json={"plan_code": "gold", "days": 3}, headers=ADMIN).status_code
        == 404
    )


def test_suspend_and_roles(api: TestClient) -> None:
    uid = alice(api)["id"]
    me = api.get("/v1/me", headers=ADMIN).json()["id"]
    assert api.patch(f"/v1/admin/users/{me}", json={"role": "user"}, headers=ADMIN).status_code == 400  # not yourself
    r = api.patch(f"/v1/admin/users/{uid}", json={"status": "suspended"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["user"]["status"] == "suspended"
    assert api.get("/v1/me", headers=A).status_code == 403
    assert [u["email"] for u in api.get("/v1/admin/users?status=suspended", headers=ADMIN).json()["items"]] == [
        "alice@example.com"
    ]
    api.patch(f"/v1/admin/users/{uid}", json={"status": "active", "role": "admin"}, headers=ADMIN)
    assert api.get("/v1/admin/overview", headers=A).status_code == 200  # now an admin herself

    log = api.get("/v1/admin/audit?action=admin.user", headers=ADMIN).json()["items"]
    assert [x["action"] for x in log] == ["admin.user.update", "admin.user.update"]
    assert log[0]["actor"] == "admin" and log[0]["detail"]["after"] == {"status": "active", "role": "admin"}
    assert log[0]["user_email"] == "admin@example.com"


def test_instrument_hours(api: TestClient) -> None:
    try:
        r = api.patch("/v1/admin/instruments/NIFTY", json={"session_close": "15:30"}, headers=ADMIN)
        assert r.status_code == 200 and r.json()["session_close"] == "15:30"
        cfg = {
            "kind": "time_based",
            "timing": {"exit": "15:40"},
            "legs": [{"id": "L1", "action": "BUY", "option_type": "CE"}],
        }
        assert not api.post("/v1/strategies/validate", json={"config": cfg}, headers=A).json()["valid"]
        assert (
            api.patch("/v1/admin/instruments/NIFTY", json={"session_open": "16:00"}, headers=ADMIN).status_code == 400
        )
        assert api.patch("/v1/admin/instruments/NIFTY", json={"session_close": "3pm"}, headers=ADMIN).status_code == 422
        assert api.patch("/v1/admin/instruments/DOW", json={"is_active": False}, headers=ADMIN).status_code == 404
    finally:
        api.patch("/v1/admin/instruments/NIFTY", json={"session_close": "15:40"}, headers=ADMIN)


def test_overview_counts(api: TestClient) -> None:
    api.post("/v1/strategies", json={"name": "s"}, headers=A)
    o = api.get("/v1/admin/overview", headers=ADMIN).json()
    assert (o["users"], o["new_users_7d"], o["strategies"], o["paying_users"]) == (2, 2, 1, 0)
    assert {u["email"] for u in o["recent_users"]} == {"alice@example.com", "admin@example.com"}
