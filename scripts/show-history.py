"""Look at the stored 1-minute history (table history_candles) used by backtests.

    uv run --env-file .env python scripts/show-history.py                      # what is stored, per underlying
    uv run --env-file .env python scripts/show-history.py NIFTY 2025-03-12      # the index's candles that day
    uv run --env-file .env python scripts/show-history.py NIFTY 2025-03-12 --options        # contracts stored that day
    uv run --env-file .env python scripts/show-history.py NIFTY:2025-03-13:22500:CE 2025-03-12   # one contract

Keys: an index is its code ("NIFTY"); an option is UNDERLYING:EXPIRY:STRIKE:RIGHT. Times are IST."""

from __future__ import annotations

import argparse
import os

import psycopg


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("key", nargs="?", help="NIFTY, or a contract key such as NIFTY:2025-03-13:22500:CE")
    ap.add_argument("day", nargs="?", help="YYYY-MM-DD")
    ap.add_argument("--options", action="store_true", help="list the option contracts stored that day")
    ap.add_argument("--limit", type=int, default=400, help="candles to print (default 400)")
    a = ap.parse_args()
    url = os.environ.get("DATABASE_URL", "postgresql://algoearning:algoearning@localhost:5432/algoearning")
    with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://")) as c:
        if not a.key:
            print(f"{'underlying':<11}{'kind':<8}{'from':<12}{'to':<12}{'days':>6}{'contracts':>11}{'candles':>12}")
            for u, kind, lo, hi, days, keys, n in c.execute(
                """SELECT split_part(key, ':', 1), CASE WHEN key LIKE '%:%' THEN 'options' ELSE 'index' END,
                    min((ts AT TIME ZONE 'Asia/Kolkata')::date), max((ts AT TIME ZONE 'Asia/Kolkata')::date),
                    count(DISTINCT (ts AT TIME ZONE 'Asia/Kolkata')::date), count(DISTINCT key), count(*)
                    FROM history_candles GROUP BY 1, 2 ORDER BY 1, 2"""
            ):
                print(f"{u:<11}{kind:<8}{lo!s:<12}{hi!s:<12}{days:>6}{keys:>11}{n:>12,}")
            return
        if not a.day:
            ap.error("give a day (YYYY-MM-DD)")
        if a.options:
            rows = c.execute(
                """SELECT key, count(*), sum(volume), max(oi) FROM history_candles
                    WHERE key LIKE %s AND (ts AT TIME ZONE 'Asia/Kolkata')::date = %s GROUP BY key ORDER BY key""",
                (f"{a.key}:%", a.day),
            ).fetchall()
            print(f"{'contract':<32}{'candles':>8}{'volume':>14}{'OI':>12}")
            for key, n, vol, oi in rows:
                print(f"{key:<32}{n:>8}{vol or 0:>14,}{oi if oi is not None else '-':>12}")
            print(f"{len(rows)} contracts")
            return
        rows = c.execute(
            """SELECT to_char(ts AT TIME ZONE 'Asia/Kolkata', 'HH24:MI'), open, high, low, close, volume, oi
                FROM history_candles WHERE key = %s AND (ts AT TIME ZONE 'Asia/Kolkata')::date = %s
                ORDER BY ts LIMIT %s""",
            (a.key, a.day, a.limit),
        ).fetchall()
        print(f"{'time':<7}{'open':>11}{'high':>11}{'low':>11}{'close':>11}{'volume':>12}{'OI':>12}")
        for t, o, h, lo, cl, v, oi in rows:
            print(f"{t:<7}{o:>11.2f}{h:>11.2f}{lo:>11.2f}{cl:>11.2f}{v or 0:>12,}{oi if oi is not None else '-':>12}")
        print(f"{len(rows)} candles")


if __name__ == "__main__":
    main()
