from collections.abc import Iterator
from datetime import UTC, date, datetime, time

import pytest
from ae_db.session import Database
from ae_marketdata.instruments import refresh_instruments
from ae_worker.__main__ import main, next_run
from sqlalchemy import create_engine, text

HEADER = (
    "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,"
    "instrument_type,segment,exchange"
)


def rows(*specs: tuple[str, str, float, int]) -> list[dict[str, str]]:
    keys = HEADER.split(",")
    return [
        dict(zip(keys, ["1", "1", "X", n, "0", e, str(k), "0.05", str(lot), "CE", "NFO-OPT", "NFO"], strict=True))
        for n, e, k, lot in specs
    ]


@pytest.fixture
def seeded(migrated_db: str) -> Iterator[str]:
    """The instruments table is seed data shared by all tests: put it back afterwards."""
    engine = create_engine(migrated_db)
    with engine.connect() as c:
        before = c.execute(text("SELECT code, lot_size, strike_step, weekly_expiry, source FROM instruments")).all()
    yield migrated_db
    with engine.begin() as c:
        for code, lot, step, weekly, source in before:
            c.execute(
                text(
                    "UPDATE instruments SET lot_size=:l, strike_step=:s, weekly_expiry=:w, source=:src, "
                    "refreshed_at=NULL WHERE code=:c"
                ),
                {"l": lot, "s": step, "w": weekly, "src": source, "c": code},
            )
    engine.dispose()


def test_schedule_is_daily_in_ist() -> None:
    at = time(8, 0)
    assert next_run(datetime(2026, 9, 30, 2, 0, tzinfo=UTC), at) == datetime(2026, 9, 30, 2, 30, tzinfo=UTC)
    assert next_run(datetime(2026, 9, 30, 2, 30, tzinfo=UTC), at) == datetime(2026, 10, 1, 2, 30, tzinfo=UTC)


def test_needs_a_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main(["--once"]) == 2


async def test_refresh_updates_lot_sizes_and_keeps_missing_ones(seeded: str) -> None:
    async def fetch(exchange: str) -> list[dict[str, str]]:
        if exchange == "BFO":
            return []  # SENSEX missing today: keeps its values
        return rows(
            ("NIFTY", "2026-10-06", 25000, 75),  # SEBI changed the lot size
            ("NIFTY", "2026-10-06", 25050, 75),
            ("NIFTY", "2026-10-13", 25000, 75),
            *[
                (n, "2026-10-27", k, lot)
                for n, lot, step in (("BANKNIFTY", 30, 100), ("FINNIFTY", 60, 50), ("MIDCPNIFTY", 120, 25))
                for k in (1000, 1000 + step)
            ],
        )

    db = Database(seeded, pool_size=1)
    try:
        r = await refresh_instruments(db, fetch, today=date(2026, 9, 30))
    finally:
        await db.dispose()
    assert r.changed == {"NIFTY": {"lot_size": (65, 75)}}
    assert sorted(r.unchanged) == ["BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"] and r.missing == ["SENSEX"]
    e = create_engine(seeded)
    with e.connect() as c:
        got = dict(c.execute(text("SELECT code, lot_size || ':' || source FROM instruments")).all())
    e.dispose()
    assert got["NIFTY"] == "75:kite" and got["SENSEX"] == "20:seed"


def test_backfill_reads_from_the_feed_s_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from ae_worker.__main__ import history_provider

    for k in ("MARKET_DATA_SOURCE", "BREEZE_API_KEY", "BREEZE_API_SECRET", "KITE_FEED_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert history_provider() == "breeze"  # nothing set: Breeze, the default (it then says what is missing)
    monkeypatch.setenv("KITE_FEED_API_KEY", "k")
    assert history_provider() == "kite"
    monkeypatch.setenv("BREEZE_API_KEY", "b")
    monkeypatch.setenv("BREEZE_API_SECRET", "s")
    assert history_provider() == "breeze"  # both set: Breeze stays the default
    monkeypatch.setenv("MARKET_DATA_SOURCE", "kite")
    assert history_provider() == "kite"
