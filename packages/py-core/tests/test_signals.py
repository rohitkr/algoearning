"""Reading a tips channel (ADR 0025): message kinds, signal assembly and status, user overrides; real messages of
"Nifty Sensex VIP setups" as fixtures, and (when the reference store is on this machine) all 500 studied messages."""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from ae_core.signals import Message, VipSetups, assemble

P = VipSetups()
HEADER = "🟢 BUY NIFTY 22450 CE\n💰 Entry : ₹150 - ₹154\n📊 Intraday Trade ⭐ ⭐"
DETAILS = ("🎯 TP 1: ₹169\n🎯 TP 2: ₹184\n🎯 TP 3: ₹204\n🛑 Stop Loss: ₹135\n📝 Rationale: Reversal\n"
           "⏳ Valid for: Intraday Only\n⚠️ Terms, Conditions, Disclaimer: Read everything here")  # fmt: skip
FIXTURE = Path(__file__).parent / "fixtures" / "vip_setups_messages.json"
GOLDEN = Path(os.environ.get("AE_SIGNALS_GOLDEN_DB", "~/git/algo-trading-claude/data/telegram/messages.sqlite"))


# -- one message --------------------------------------------------------------------------------------------------
def test_signal_header() -> None:
    p = P.parse(HEADER)
    assert p.kind == "SIGNAL"
    want = {"action": "BUY", "index": "NIFTY", "strike": 22450, "option_type": "CE", "entry_low": 150.0,
            "entry_high": 154.0, "direction": "BULLISH", "intraday": True}  # fmt: skip
    assert dict(p.data) == want
    assert P.parse("🟢 BUY SENSEX 72900 PE\n💰 Entry : ₹311 - ₹315").data["direction"] == "BEARISH"
    assert P.parse("SELL NIFTY 22450 PE\nEntry : 80").data["direction"] == "BULLISH"  # selling a put
    assert P.parse("SELL NIFTY 22450 CE\nEntry : 80 to 82").data["entry_high"] == 82


def test_details_and_updates() -> None:
    d = P.parse(DETAILS)
    assert d.kind == "DETAILS" and d.data["targets"] == [169, 184, 204] and d.data["stop_loss"] == 135
    assert d.data["rationale"] == "Reversal" and d.data["valid_for"] == "Intraday Only"
    t = P.parse("🎯 Target 2 done 🔥🔥🔥🔥🔥\n💹 Ltp ₹190\n✅ Book Major quantity here")
    assert (t.kind, dict(t.data)) == ("TARGET", {"target": 2, "ltp": 190.0})
    s = P.parse("🛑 STOP LOSS HIT | NIFTY 22450 CE\n💹 Live LTP: ₹135\n❌ Position closed. Capital preserved.")
    assert (s.kind, dict(s.data)) == ("SL_HIT", {"ltp": 135.0, "symbol": "NIFTY 22450 CE"})
    assert dict(P.parse("₹163 🔥🔥🔥🔥🔥").data) == {"price": 163.0} and P.parse("145 🔥🔥🔥").kind == "TICK"
    assert P.parse("", has_media=True).kind == "MEDIA" and P.parse("   ").kind == "NOISE"


def test_advice_noise_and_unclear_are_never_signals() -> None:
    assert dict(P.parse("EXIT COMPLETELY \nBOOK ALL PROFITS").data) == {"exit": True}
    a = P.parse("book small profit near 149 and trail sl near 143")
    assert a.kind == "ADVISORY" and dict(a.data) == {"exit": False, "trail_sl": 143.0, "book_near": 149.0}
    assert P.parse("Good morning traders").kind == "NOISE"
    assert P.parse("We are live pls join now").kind == "NOISE"
    assert P.parse("keep 74400 ce and 74200 pe for jodi \nstrangle buy near 215 combinedly").kind == "UNCLEAR"


# -- a channel ----------------------------------------------------------------------------------------------------
T0 = datetime(2026, 10, 8, 4, 45, tzinfo=UTC)


def msg(i: int, secs: int, text: str, reply_to: int | None = None, media: bool = False) -> Message:
    return Message(i, T0 + timedelta(seconds=secs), text, reply_to, media)


THREAD = [
    msg(1, 0, HEADER),
    msg(2, 5, DETAILS, 1),
    msg(3, 60, "₹157 🔥🔥🔥", 1),
    msg(4, 300, "🎯 Target 1 done\n💹 Ltp ₹170", 1),
    msg(5, 900, "trail sl near 160"),  # not a reply: the latest signal
    msg(6, 1500, "🛑 STOP LOSS HIT | NIFTY 22450 CE\n💹 Live LTP: ₹160", 1),
]


def test_assembly_links_updates_and_tracks_status() -> None:
    (s,), reads = assemble(reversed(THREAD))  # any order
    assert (s.id, s.tip, s.direction, s.complete) == (1, "BUY NIFTY 22450 CE", "BULLISH", True)
    assert (s.stop_loss, s.targets, s.rationale) == (135, [169, 184, 204], "Reversal")
    assert (s.status, s.targets_done, s.last_price) == ("SL_HIT", [1], 160)
    assert [r.signal_id for r in reads] == [1] * 6 and s.message_ids == [1, 2, 3, 4, 5, 6]
    assert [r.kind for r in reads] == ["SIGNAL", "DETAILS", "TICK", "TARGET", "ADVISORY", "SL_HIT"]


def test_a_header_without_details_is_not_tradable_and_a_later_signal_takes_advice() -> None:
    sigs, reads = assemble([msg(1, 0, HEADER), msg(7, 60, "🟢 BUY SENSEX 75000 CE\n💰 Entry : ₹300 - ₹305"),
                            msg(8, 90, "EXIT COMPLETELY")])  # fmt: skip
    assert [s.complete for s in sigs] == [False, False]
    assert reads[-1].signal_id == 7


def test_overrides_win_but_cannot_invent_a_signal() -> None:
    (s,), reads = assemble(THREAD, overrides={6: "NOISE", 3: "UNCLEAR"})
    assert s.status == "T1" and s.last_price == 170  # the SL hit and the tick were demoted
    assert [r.overridden for r in reads] == [False, False, True, False, False, True]
    sigs, reads = assemble([msg(9, 0, "keep 74400 ce and 74200 pe for jodi")], overrides={9: "SIGNAL"})
    assert sigs == [] and reads[0].kind == "UNCLEAR"
    (s,), _ = assemble([*THREAD[:3], msg(4, 300, "nice move", 1)], overrides={4: "TARGET"})
    assert s.status == "OPEN"  # a forced target without its number changes nothing


def test_real_messages() -> None:
    raw = json.loads(FIXTURE.read_text())["messages"]
    msgs = [Message(r["msg_id"], datetime.fromisoformat(r["date"]), r["text"], r["reply_to"], r["has_media"])
            for r in raw]  # fmt: skip
    sigs, reads = assemble(msgs)
    got = {s.id: (s.tip, s.direction, s.status, s.complete) for s in sigs}
    assert got == {
        983: ("BUY NIFTY 23700 CE", "BULLISH", "OPEN", True),
        996: ("BUY NIFTY 23900 PE", "BEARISH", "SL_HIT", True),
        1015: ("BUY SENSEX 75000 CE", "BULLISH", "T3", True),
        1032: ("BUY SENSEX 74500 CE", "BULLISH", "T1", True),
    }
    kinds = Counter(r.kind for r in reads)
    assert kinds["SIGNAL"] == 4 and kinds["DETAILS"] == 4 and kinds["ADVISORY"] == 3 and kinds["MEDIA"] == 1
    assert all(r.signal_id is None for r in reads if r.kind in ("NOISE", "UNCLEAR"))


@pytest.mark.skipif(not GOLDEN.expanduser().exists(), reason="the reference message store is not on this machine")
def test_all_500_studied_messages() -> None:
    with sqlite3.connect(GOLDEN.expanduser()) as c:
        rows = c.execute("SELECT msg_id, date, text, reply_to, has_media FROM messages").fetchall()
    msgs = [Message(i, datetime.fromisoformat(d), t, r, bool(h)) for i, d, t, r, h in rows]
    sigs, reads = assemble(msgs)
    assert len(msgs) == 500 and len(sigs) == 43 and all(s.complete for s in sigs)
    assert Counter(s.index for s in sigs) == {"NIFTY": 27, "SENSEX": 16}
    assert Counter(s.direction for s in sigs) == {"BULLISH": 33, "BEARISH": 10}
    assert all(s.action == "BUY" and s.intraday for s in sigs)
    status = Counter(s.status for s in sigs)
    assert status["SL_HIT"] == 20 and status["T1"] + status["T2"] + status["T3"] == 21  # 47% / 49%
    assert sum(r.kind == "TICK" for r in reads) / len(reads) > 0.6  # ticks are most of the traffic
    assert all(r.signal_id is not None for r in reads if r.kind in ("DETAILS", "TARGET", "SL_HIT"))
