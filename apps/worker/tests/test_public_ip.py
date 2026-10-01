"""The public-IP check: parse each source, record the IP, and tell Zerodha users (only them, only on a change)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from ae_db.models import BrokerAccount, Notification, PlatformSetting, User
from ae_db.session import Database
from ae_worker.public_ip import KEY, _parse, check_public_ip, fetch_public_ip
from sqlalchemy import select


def test_parse() -> None:
    assert _parse("fl=1\nh=1.1.1.1\nip=115.99.89.241\nts=1") == "115.99.89.241"
    assert _parse("115.99.89.241\n") == "115.99.89.241"
    assert _parse("ip=192.168.1.5") is None  # private: not what Zerodha sees
    assert _parse("ip=2001:db8::1") is None and _parse("") is None


async def test_fetch_falls_back_to_the_next_source() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "1.1.1.1":
            return httpx.Response(503)
        return httpx.Response(200, text="49.36.10.20")

    assert await fetch_public_ip(httpx.MockTransport(handler)) == "49.36.10.20"
    assert await fetch_public_ip(httpx.MockTransport(lambda r: httpx.Response(500))) is None


async def test_change_notifies_zerodha_users_once(db: Database) -> None:
    async with db.system_session() as s:
        kite, other = User(auth_subject="a", email="a@example.com"), User(auth_subject="b", email="b@example.com")
        s.add_all([kite, other])
        await s.flush()
        s.add(BrokerAccount(user_id=kite.id, broker="zerodha", client_id="AB1234"))

    ip = "49.36.10.20"

    async def fetch() -> str | None:
        return ip

    t0 = datetime(2026, 10, 1, 4, 0, tzinfo=UTC)
    assert await check_public_ip(db, fetch, t0) == ip  # first sighting: recorded, nobody told
    assert await check_public_ip(db, fetch, t0 + timedelta(minutes=5)) == ip
    ip = "49.36.10.99"
    await check_public_ip(db, fetch, t0 + timedelta(minutes=10))
    await check_public_ip(db, fetch, t0 + timedelta(minutes=15))

    async with db.system_session() as s:
        value = (await s.execute(select(PlatformSetting.value).where(PlatformSetting.key == KEY))).scalar_one()
        sent = list((await s.execute(select(Notification))).scalars())
    assert value["ip"] == "49.36.10.99" and value["previous"] == "49.36.10.20"
    assert value["changed_at"] == (t0 + timedelta(minutes=10)).isoformat()
    assert value["checked_at"] == (t0 + timedelta(minutes=15)).isoformat()
    assert [(n.user_id, n.event) for n in sent] == [(kite.id, "ip_changed")]
    assert "49.36.10.20" in sent[0].body and "49.36.10.99" in sent[0].title


async def test_offline_keeps_the_last_ip(db: Database) -> None:
    async def none() -> str | None:
        return None

    assert await check_public_ip(db, none) is None
    async with db.system_session() as s:
        assert (await s.execute(select(PlatformSetting))).first() is None
