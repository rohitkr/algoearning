"""Reports and open positions over recorded trades: per user, by day closed (IST), filters, paging and CSV."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from datetime import date, datetime, time
from decimal import Decimal

import pytest
from ae_api.main import create_app
from ae_api.settings import Settings
from ae_db.models import Strategy, StrategyRun, Trade, User
from ae_db.session import Database
from ae_marketdata.types import IST
from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest import TEST_REDIS_URL

A = {"X-Dev-User": "alice@example.com"}
B = {"X-Dev-User": "bob@example.com"}
D1, D2, D3 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5)


def seed(url: str) -> dict[str, uuid.UUID]:
    async def go() -> dict[str, uuid.UUID]:
        db = Database(url, pool_size=1)
        ids: dict[str, uuid.UUID] = {}
        async with db.system_session() as s:
            users = {u.email: u for u in (await s.execute(select(User))).scalars()}
            for email, key in (("alice@example.com", "a"), ("bob@example.com", "b")):
                u = users[email]
                st = Strategy(user_id=u.id, name=f"Straddle {key}", config={}, kind="time_based")
                s.add(st)
                await s.flush()
                run = StrategyRun(
                    user_id=u.id, strategy_id=st.id, mode="paper", config_snapshot={}, strategy_name=st.name
                )
                s.add(run)
                await s.flush()
                ids[key], ids[f"{key}_strategy"] = run.id, st.id

            def trade(user_id: uuid.UUID, run_id: uuid.UUID, day: date, pnl: float, status: str = "closed",
                      hm: str = "15:15", symbol: str = "NIFTY26OCT0625000CE") -> Trade:  # fmt: skip
                at = datetime.combine(day, time.fromisoformat(hm), tzinfo=IST)
                return Trade(
                    user_id=user_id, run_id=run_id, mode="paper", status=status, trade_date=day, underlying="NIFTY",
                    exchange="NFO", tradingsymbol=symbol, expiry=D3, strike=Decimal(25000), option_type="CE",
                    side="SELL", product="MIS", lots=1, lot_size=65, quantity=65, entry_price=Decimal(100),
                    entry_time=at.replace(hour=9, minute=20), exit_time=at if status == "closed" else None,
                    exit_avg_price=Decimal(90) if status == "closed" else None,
                    exit_reason="exit time" if status == "closed" else None,
                    realized_pnl=Decimal(str(pnl)) if status == "closed" else Decimal(0),
                    unrealized_pnl=Decimal(str(pnl)) if status == "open" else Decimal(0), last_ltp=Decimal(95),
                )  # fmt: skip

            a, b = users["alice@example.com"].id, users["bob@example.com"].id
            s.add_all([
                trade(a, ids["a"], D1, 1000), trade(a, ids["a"], D1, -250), trade(a, ids["a"], D2, 500),
                trade(a, ids["a"], D3, -250, symbol="=HYPERLINK(1)"),
                trade(a, ids["a"], D3, 42, status="open"),
                trade(a, ids["a"], date(2026, 9, 1), 99999),  # outside the range
                trade(a, ids["a"], D1, 7, hm="23:59"),  # 23:59 IST is still D1 (18:29 UTC)
                trade(b, ids["b"], D1, -5000),
            ])  # fmt: skip
        await db.dispose()
        return ids

    return asyncio.run(go())


@pytest.fixture
def api(clean_db: str) -> Iterator[tuple[TestClient, dict[str, uuid.UUID]]]:
    app = create_app(Settings(app_env="test", dev_auth=True, database_url=clean_db, redis_url=TEST_REDIS_URL))
    with TestClient(app) as c:
        c.get("/v1/me", headers=A)
        c.get("/v1/me", headers=B)
        yield c, seed(clean_db)


R = "from=2026-10-01&to=2026-10-05"


def test_summary_daily_and_strategies(api: tuple[TestClient, dict[str, uuid.UUID]]) -> None:
    c, ids = api
    s = c.get(f"/v1/reports/summary?{R}", headers=A).json()
    assert (s["total_pnl"], s["trades"], s["wins"], s["losses"]) == (1007, 5, 3, 2)
    assert s["best_day"]["day"] == "2026-10-01" and s["worst_day"]["day"] == "2026-10-05"
    days = c.get(f"/v1/reports/daily?{R}", headers=A).json()
    assert [(d["day"], d["pnl"], d["cumulative"]) for d in days] == [
        ("2026-10-01", 757, 757), ("2026-10-02", 500, 1257), ("2026-10-05", -250, 1007),
    ]  # fmt: skip
    (perf,) = c.get(f"/v1/reports/strategies?{R}", headers=A).json()
    assert (perf["strategy_name"], perf["trades"], perf["runs"], perf["pnl"]) == ("Straddle a", 5, 1, 1007)
    assert c.get(f"/v1/reports/summary?{R}&mode=live", headers=A).json()["trades"] == 0
    other = str(ids["b_strategy"])
    assert c.get(f"/v1/reports/summary?{R}&strategy_id={other}", headers=A).json()["trades"] == 0  # not hers
    assert c.get(f"/v1/reports/summary?{R}", headers=B).json()["total_pnl"] == -5000
    assert c.get("/v1/reports/summary?from=2026-10-05&to=2026-10-01", headers=A).status_code == 400


def test_trades_paging_csv_and_open_positions(api: tuple[TestClient, dict[str, uuid.UUID]]) -> None:
    c, _ = api
    p1 = c.get(f"/v1/reports/trades?{R}&limit=3", headers=A).json()
    p2 = c.get(f"/v1/reports/trades?{R}&limit=3&cursor={p1['next_cursor']}", headers=A).json()
    pnls = [t["pnl"] for t in p1["items"] + p2["items"]]
    assert len(pnls) == 5 and p2["next_cursor"] is None and p1["items"][0]["exit_time"] > p1["items"][1]["exit_time"]
    r = c.get(f"/v1/reports/trades.csv?{R}", headers=A)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("exit_time,strategy_name") and len(lines) == 6
    assert "'=HYPERLINK(1)" in r.text  # spreadsheet formulas are neutralised
    (pos,) = c.get("/v1/positions/open", headers=A).json()
    assert pos["pnl"] == 42 and pos["last_ltp"] == 95 and pos["strategy_name"] == "Straddle a"
    assert c.get("/v1/positions/open", headers=B).json() == []
