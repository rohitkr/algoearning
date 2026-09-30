"""Phase 8: strategy configs are validated on every save (shape and cross-field rules), the builder's catalog
and dry-run validation, list filters, and the plan's lot limit as a warning."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from ae_api.main import create_app
from ae_api.settings import Settings
from fastapi.testclient import TestClient

from conftest import TEST_REDIS_URL

A = {"X-Dev-User": "alice@example.com"}


@pytest.fixture
def api(clean_db: str) -> Iterator[TestClient]:
    app = create_app(Settings(app_env="test", dev_auth=True, database_url=clean_db, redis_url=TEST_REDIS_URL))
    with TestClient(app) as c:
        yield c


def cfg(**over: Any) -> dict[str, Any]:
    return {"kind": "time_based", "legs": [{"id": "L1", "action": "SELL", "option_type": "CE"}], **over}


def create(api: TestClient, name: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name} | ({"config": config} if config is not None else {})
    r = api.post("/v1/strategies", json=body, headers=A)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def test_saved_config_is_normalised_and_kind_follows_it(api: TestClient) -> None:
    s = create(api, "Straddle", cfg())
    assert s["kind"] == "time_based" and s["schema_version"] == 1
    leg = s["config"]["legs"][0]
    assert (
        leg["lots"] == 1
        and leg["expiry"] == "current_week"
        and leg["strike"]
        == {
            "mode": "atm",
            "offset": 0,
            "premium": None,
        }
    )
    assert s["config"]["timing"] == {"entry": "09:20", "exit": "15:15", "days": ["MON", "TUE", "WED", "THU", "FRI"]}
    assert create(api, "Blank")["config"]["legs"][0]["action"] == "BUY"  # no config: the builder's start

    r = api.patch(f"/v1/strategies/{s['id']}", json={"config": {"kind": "zero_dte"}}, headers=A)
    assert r.status_code == 200 and r.json()["kind"] == "zero_dte" and r.json()["version"] == 2
    r = api.patch(f"/v1/strategies/{s['id']}", json={"name": "Renamed"}, headers=A)
    assert r.json()["version"] == 2 and r.json()["config"]["kind"] == "zero_dte"  # untouched config: same version


def test_invalid_configs_are_refused_with_the_path(api: TestClient) -> None:
    r = api.post("/v1/strategies", json={"name": "x", "config": cfg(legs=[{"id": "L1", "action": "HOLD"}])}, headers=A)
    assert r.status_code == 422
    locs = [d["loc"] for d in r.json()["error"]["details"]]
    assert ["body", "config", "time_based", "legs", 0, "action"] in locs

    bad = cfg(timing={"entry": "15:20", "exit": "09:30"}, underlying="BANKNIFTY")
    r = api.post("/v1/strategies", json={"name": "x", "config": bad}, headers=A)
    err = r.json()["error"]
    assert r.status_code == 422 and err["code"] == "validation_error"
    assert [d["loc"] for d in err["details"]] == [
        ["body", "config", "timing", "exit"],
        ["body", "config", "legs", 0, "expiry"],
    ]
    s = create(api, "ok", cfg())
    r = api.patch(f"/v1/strategies/{s['id']}", json={"config": bad}, headers=A)
    assert r.status_code == 422 and api.get(f"/v1/strategies/{s['id']}", headers=A).json()["version"] == 1


def test_catalog(api: TestClient) -> None:
    r = api.get("/v1/strategies/catalog", headers=A)
    assert r.status_code == 200, r.text
    c = r.json()
    assert {i["code"] for i in c["instruments"]} == {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}
    assert [p["id"] for p in c["presets"]][:2] == ["blank", "short_straddle"]
    assert {p["config"]["kind"] for p in c["presets"]} == {"time_based", "range_breakout", "zero_dte"}
    assert c["limits"]["max_legs"] == 6 and c["limits"]["max_lots_per_order"] == 10  # free plan
    assert api.get("/v1/strategies/catalog").status_code == 401
    for p in c["presets"][:3]:  # presets save as-is
        create(api, p["name"], p["config"])


def test_validate_reports_errors_and_plan_warnings(api: TestClient) -> None:
    def validate(config: dict[str, Any]) -> dict[str, Any]:
        r = api.post("/v1/strategies/validate", json={"config": config}, headers=A)
        assert r.status_code == 200, r.text
        return r.json()  # type: ignore[no-any-return]

    ok = validate(cfg())
    assert ok == {"valid": True, "errors": [], "warnings": []}
    shape = validate(cfg(legs=[{"id": "L1", "action": "SELL", "option_type": "CE", "lots": 0}]))
    assert not shape["valid"] and shape["errors"][0]["loc"] == ["legs", 0, "lots"]  # union tag stripped
    rules = validate(
        cfg(legs=[{"id": "L1", "action": "SELL", "option_type": "CE", "trailing": {"trigger": 5, "step": 2}}])
    )
    assert not rules["valid"] and rules["errors"][0]["loc"] == ["legs", 0, "trailing"]
    lots = validate(cfg(legs=[{"id": "L1", "action": "SELL", "option_type": "CE", "lots": 11}]))
    assert lots["valid"] and lots["warnings"][0] == {
        "loc": ["legs", 0, "lots"],
        "msg": "your plan allows 10 lot(s) per order",
        "type": "plan_limit",
    }
    assert not validate({"kind": "nope"})["valid"]


def test_list_filters(api: TestClient) -> None:
    a = create(api, "Nifty straddle")
    create(api, "Bank 50%_off")
    create(api, "Sensex strangle")
    api.patch(f"/v1/strategies/{a['id']}", json={"status": "archived"}, headers=A)

    def names(qs: str) -> list[str]:
        r = api.get(f"/v1/strategies?{qs}", headers=A)
        assert r.status_code == 200, r.text
        return [x["name"] for x in r.json()["items"]]

    assert names("status=draft&status=ready") == ["Sensex strangle", "Bank 50%_off"]
    assert names("status=archived") == ["Nifty straddle"]
    assert names("q=STRADDLE") == ["Nifty straddle"]
    assert names("q=50%25_") == ["Bank 50%_off"]  # % and _ are literal, not wildcards
    assert names("q=%25") == ["Bank 50%_off"]
    assert api.get("/v1/strategies?status=deleted", headers=A).status_code == 422
