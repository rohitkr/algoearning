"""Plans and entitlements through the API: default free plan, subscription states, plan limits enforced on
strategy creation, and admin-only plan management."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from ae_api.main import create_app
from ae_api.settings import Settings
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

A = {"X-Dev-User": "alice@example.com"}
ADMIN = {"X-Dev-User": "admin@example.com"}


@pytest.fixture
def api(clean_db: str) -> Iterator[TestClient]:
    app = create_app(Settings(app_env="test", dev_auth=True, database_url=clean_db))
    with TestClient(app) as c:
        yield c


def sql(url: str, stmt: str, **params: object) -> None:
    e = create_engine(url)
    with e.begin() as conn:
        conn.execute(text(stmt), params)
    e.dispose()


def subscribe(url: str, email: str, plan: str, status: str, end: datetime | None) -> None:
    sql(
        url,
        "INSERT INTO subscriptions (user_id, plan_id, status, provider, current_period_end, cancel_at_period_end) "
        "SELECT u.id, p.id, :status, 'razorpay', :end, false FROM users u, plans p "
        "WHERE u.email = :email AND p.code = :plan",
        status=status,
        end=end,
        email=email,
        plan=plan,
    )


def ent(api: TestClient, headers: dict[str, str] = A) -> dict:
    r = api.get("/v1/me/entitlements", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_everyone_starts_on_free(api: TestClient) -> None:
    e = ent(api)
    assert e["plan_code"] == "free" and e["subscription_status"] is None
    assert e["features"]["live_trading"] is False and e["features"]["paper_trading"] is True
    assert e["usage"]["max_strategies"] == {"used": 0, "limit": 5}
    assert {f["key"] for f in e["catalog"]} >= {"live_trading", "max_strategies"}


@pytest.mark.parametrize(
    ("status", "days", "plan"),
    [
        ("active", 20, "pro"),
        ("cancelled", 2, "pro"),
        ("cancelled", -1, "free"),
        ("past_due", -2, "pro"),
        ("past_due", -5, "free"),
        ("pending", 30, "free"),
        ("expired", 30, "free"),
    ],
)
def test_subscription_states(api: TestClient, clean_db: str, status: str, days: int, plan: str) -> None:
    ent(api)  # creates the user
    subscribe(clean_db, "alice@example.com", "pro", status, datetime.now(UTC) + timedelta(days=days))
    e = ent(api)
    assert e["plan_code"] == plan
    assert e["features"]["live_trading"] is (plan == "pro")


def test_strategy_limit_is_enforced_and_explained(api: TestClient, clean_db: str) -> None:
    ids = [api.post("/v1/strategies", json={"name": f"s{i}"}, headers=A).json()["id"] for i in range(5)]
    r = api.post("/v1/strategies", json={"name": "one too many"}, headers=A)
    assert r.status_code == 403
    err = r.json()["error"]
    assert err["code"] == "plan_limit"
    assert err["details"] == {"feature": "max_strategies", "limit": 5, "used": 5, "plan": "free"}
    assert api.post(f"/v1/strategies/{ids[0]}/duplicate", json={}, headers=A).json()["error"]["code"] == "plan_limit"
    api.delete(f"/v1/strategies/{ids[0]}", headers=A)  # deleted ones do not count
    assert api.post("/v1/strategies", json={"name": "fits again"}, headers=A).status_code == 201
    subscribe(clean_db, "alice@example.com", "pro", "active", datetime.now(UTC) + timedelta(days=30))
    assert api.post("/v1/strategies", json={"name": "pro room"}, headers=A).status_code == 201
    assert ent(api)["usage"]["max_strategies"] == {"used": 6, "limit": 25}


def make_admin(api: TestClient, url: str) -> None:
    api.get("/v1/me", headers=ADMIN)
    sql(url, "UPDATE users SET role = 'admin' WHERE email = 'admin@example.com'")


def test_admin_plan_management(api: TestClient, clean_db: str) -> None:
    body = {
        "code": "pro_year",
        "name": "Pro (yearly)",
        "price_paise": 999_000,
        "interval": "year",
        "features": {"live_trading": True, "max_strategies": 25},
        "sort_order": 11,
    }
    assert api.post("/v1/admin/plans", json=body, headers=A).status_code == 403  # not an admin
    make_admin(api, clean_db)
    r = api.post("/v1/admin/plans", json=body, headers=ADMIN)
    assert r.status_code == 201 and r.json()["features"]["max_broker_accounts"] == 1  # filled with the default
    assert api.post("/v1/admin/plans", json=body, headers=ADMIN).status_code == 409
    bad = api.patch("/v1/admin/plans/pro_year", json={"features": {"live_trade": True}}, headers=ADMIN)
    assert bad.status_code == 400 and "unknown features" in bad.json()["error"]["message"]
    assert api.patch("/v1/admin/plans/free", json={"is_active": False}, headers=ADMIN).status_code == 400
    assert api.patch("/v1/admin/plans/pro_year", json={"is_active": False}, headers=ADMIN).status_code == 200
    assert "pro_year" not in [p["code"] for p in api.get("/v1/plans").json()]  # hidden from pricing
    assert "pro_year" in [p["code"] for p in api.get("/v1/admin/plans", headers=ADMIN).json()]
