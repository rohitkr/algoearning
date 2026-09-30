from datetime import date

from ae_brokers.instruments import derive_facts, parse_dump

HEADER = (
    "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,"
    "instrument_type,segment,exchange\n"
)


def row(name: str, expiry: str, strike: float, kind: str = "CE", lot: int = 65) -> str:
    return f"1,1,{name}X,{name},0.0,{expiry},{strike},0.05,{lot},{kind},NFO-OPT,NFO\n"


def dump(*rows: str) -> list[dict[str, str]]:
    return parse_dump(HEADER + "".join(rows))


TODAY = date(2026, 9, 30)


def test_weekly_index_uses_the_nearest_live_expiry() -> None:
    rows = dump(
        row("NIFTY", "2026-09-29", 25000, lot=75),  # expired yesterday: ignored
        row("NIFTY", "2026-10-06", 25000),
        row("NIFTY", "2026-10-06", 25050),
        row("NIFTY", "2026-10-06", 25200),
        row("NIFTY", "2026-10-06", 25000, "PE"),
        row("NIFTY", "2026-10-13", 25000, lot=75),  # a new lot size from a later contract does not apply yet
        row("NIFTY", "2026-10-06", 0, "FUT"),
        row("BANKNIFTY", "2026-10-27", 55000, lot=30),
    )
    f = derive_facts(rows, "NIFTY", TODAY)
    assert f is not None
    assert (f.lot_size, f.strike_step, f.weekly_expiry, f.nearest_expiry) == (65, 50, True, date(2026, 10, 6))
    assert f.expiries == (date(2026, 10, 6), date(2026, 10, 13))


def test_monthly_only_index() -> None:
    rows = dump(
        row("BANKNIFTY", "2026-10-27", 55000, lot=30),
        row("BANKNIFTY", "2026-10-27", 55100, lot=30),
        row("BANKNIFTY", "2026-11-24", 55000, lot=30),
    )
    f = derive_facts(rows, "BANKNIFTY", TODAY)
    assert f is not None and (f.lot_size, f.strike_step, f.weekly_expiry) == (30, 100, False)


def test_missing_or_unusable_data_returns_none() -> None:
    assert derive_facts(dump(row("NIFTY", "2026-10-06", 25000)), "SENSEX", TODAY) is None
    assert derive_facts(dump(row("NIFTY", "2026-10-06", 25000)), "NIFTY", TODAY) is None  # one strike: no step
