"""The v1 API against a real database: sign-in gate, per-user strategies, cross-user access, error format,
audit trail and readiness."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from ae_api.main import create_app
from ae_api.settings import Settings
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from conftest import TEST_REDIS_URL

A = {"X-Dev-User": "alice@example.com"}
B = {"X-Dev-User": "bob@example.com"}


@pytest.fixture
def api(clean_db: str) -> Iterator[TestClient]:
    app = create_app(Settings(app_env="test", dev_auth=True, database_url=clean_db, redis_url=TEST_REDIS_URL))
    with TestClient(app) as c:
        yield c


def audit_actions(url: str) -> list[str]:
    e = create_engine(url)
    with e.connect() as c:
        rows = [r[0] for r in c.execute(text("SELECT action FROM audit_log ORDER BY id"))]
    e.dispose()
    return rows


def test_sign_in_is_required(api: TestClient, clean_db: str) -> None:
    r = api.get("/v1/me")
    assert r.status_code == 401
    body = r.json()["error"]
    assert body["code"] == "unauthorized" and body["request_id"]
    off = create_app(Settings(app_env="test", dev_auth=False, database_url=clean_db))
    with TestClient(off) as c:
        assert c.get("/v1/me", headers=A).status_code == 401  # header ignored unless DEV_AUTH is on


def test_dev_auth_cannot_be_enabled_in_production() -> None:
    with pytest.raises(ValueError, match="DEV_AUTH"):
        Settings(app_env="production", dev_auth=True)


def test_me_creates_the_user_once(api: TestClient) -> None:
    r1 = api.get("/v1/me", headers={"X-Dev-User": "Alice@Example.com"})
    r2 = api.get("/v1/me", headers=A)
    assert r1.status_code == 200 and r1.json()["email"] == "alice@example.com"
    assert r1.json()["id"] == r2.json()["id"] and r1.json()["role"] == "user"


def test_a_new_login_with_a_taken_email_is_refused_not_linked(api: TestClient, clean_db: str) -> None:
    api.get("/v1/me", headers=A)
    e = create_engine(clean_db)
    with e.begin() as c:  # alice first signed up through another login (e.g. Clerk)
        c.execute(text("UPDATE users SET auth_subject = 'clerk|user_1' WHERE email = 'alice@example.com'"))
    e.dispose()
    r = api.get("/v1/me", headers=A)  # a new subject (dev|alice@...) with the same email
    assert r.status_code == 409 and r.json()["error"]["details"] == {"reason": "email_in_use"}


def test_plans_are_public(api: TestClient) -> None:
    r = api.get("/v1/plans")
    assert r.status_code == 200 and [p["code"] for p in r.json()] == ["free", "pro", "pro_plus"]
    assert r.json()[0]["features"]["live_trading"] is False


def test_strategy_crud_and_isolation(api: TestClient, clean_db: str) -> None:
    straddle = {"kind": "time_based", "legs": [{"id": "L1", "action": "SELL", "option_type": "CE"}]}
    r = api.post("/v1/strategies", json={"name": "Short straddle", "config": straddle}, headers=A)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["version"] == 1 and r.json()["status"] == "draft"

    # Bob can neither see nor touch it, and cannot tell it exists (404, not 403)
    for method, path, kw in (
        ("get", f"/v1/strategies/{sid}", {}),
        ("patch", f"/v1/strategies/{sid}", {"json": {"name": "x"}}),
        ("delete", f"/v1/strategies/{sid}", {}),
        ("post", f"/v1/strategies/{sid}/duplicate", {"json": {}}),
    ):
        resp = getattr(api, method)(path, headers=B, **kw)
        assert resp.status_code == 404 and resp.json()["error"]["code"] == "not_found", (method, resp.text)
    assert api.get("/v1/strategies", headers=B).json()["items"] == []

    straddle["legs"].append({"id": "L2", "action": "SELL", "option_type": "PE"})
    r = api.patch(f"/v1/strategies/{sid}", json={"config": straddle, "status": "ready"}, headers=A)
    assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["status"] == "ready"
    dup = api.post(f"/v1/strategies/{sid}/duplicate", json={}, headers=A)
    assert dup.status_code == 201 and dup.json()["name"] == "Short straddle (copy)"
    assert len(api.get("/v1/strategies", headers=A).json()["items"]) == 2
    assert api.delete(f"/v1/strategies/{sid}", headers=A).status_code == 204
    assert api.get(f"/v1/strategies/{sid}", headers=A).status_code == 404
    assert audit_actions(clean_db) == ["strategy.create", "strategy.update", "strategy.duplicate", "strategy.delete"]


def test_pagination_through_the_api(api: TestClient) -> None:
    for i in range(5):
        api.post("/v1/strategies", json={"name": f"s{i}"}, headers=A)
    p1 = api.get("/v1/strategies?limit=2", headers=A).json()
    p2 = api.get(f"/v1/strategies?limit=2&cursor={p1['next_cursor']}", headers=A).json()
    p3 = api.get(f"/v1/strategies?limit=2&cursor={p2['next_cursor']}", headers=A).json()
    names = [x["name"] for x in p1["items"] + p2["items"] + p3["items"]]
    assert names == ["s4", "s3", "s2", "s1", "s0"] and p3["next_cursor"] is None
    assert api.get("/v1/strategies?cursor=garbage", headers=A).json()["error"]["code"] == "bad_request"
    assert api.get("/v1/strategies?limit=500", headers=A).status_code == 422


def test_validation_errors_share_the_error_shape(api: TestClient) -> None:
    r = api.post("/v1/strategies", json={"name": "", "user_id": "someone-else"}, headers=A)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error" and err["request_id"]
    assert {tuple(d["loc"]) for d in err["details"]} >= {("body", "name"), ("body", "user_id")}  # extra field refused
    nf = api.get("/v1/nothing-here")
    assert nf.status_code == 404 and nf.json()["error"]["code"] == "not_found"


def test_readiness_checks_real_dependencies(api: TestClient, clean_db: str) -> None:
    r = api.get("/health/ready")
    assert r.status_code == 200 and {c["name"]: c["status"] for c in r.json()["checks"]} == {
        "database": "ok",
        "redis": "ok",
    }
    broken = create_app(Settings(app_env="test", database_url=clean_db, redis_url="redis://localhost:1/0"))
    with TestClient(broken) as c:
        r = c.get("/health/ready")
    assert r.status_code == 503 and {x["name"]: x["status"] for x in r.json()["checks"]}["redis"] == "down"
