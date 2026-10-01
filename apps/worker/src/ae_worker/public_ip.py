"""The server's public IP (ADR 0019). Zerodha only takes orders from an IP registered in each user's Kite app. At home
the broadband IP can change without notice, so the worker checks it every few minutes, records it as a platform
setting (`public_ip`) for the web app to show, and tells every user with a Zerodha account when it changes.

Both sources answer over IPv4 only: Kite's static-IP list is IPv4, and an IPv6 answer would hide the one that
matters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from ipaddress import IPv4Address

import httpx
import structlog
from ae_db.enums import Broker
from ae_db.models import BrokerAccount, Notification, PlatformSetting
from ae_db.session import Database
from sqlalchemy import select

log = structlog.get_logger("ae_worker.public_ip")
KEY = "public_ip"
SOURCES = ("https://1.1.1.1/cdn-cgi/trace", "https://api.ipify.org")

Fetch = Callable[[], Awaitable[str | None]]


def _parse(body: str) -> str | None:
    """`ip=1.2.3.4` (Cloudflare trace) or a bare address (ipify) -> the address, if it is a public IPv4."""
    for line in body.splitlines():
        value = line.removeprefix("ip=").strip()
        try:
            ip = IPv4Address(value)
        except ValueError:
            continue
        return str(ip) if ip.is_global else None
    return None


async def fetch_public_ip(transport: httpx.AsyncBaseTransport | None = None) -> str | None:
    """Ask each source in turn; None when none answers (offline)."""
    async with httpx.AsyncClient(timeout=8, transport=transport) as client:
        for url in SOURCES:
            try:
                r = await client.get(url)
                r.raise_for_status()
            except httpx.HTTPError:
                continue
            if ip := _parse(r.text):
                return ip
    return None


async def check_public_ip(db: Database, fetch: Fetch = fetch_public_ip, now: datetime | None = None) -> str | None:
    """Record the current IP; on a change, notify Zerodha users. Returns the IP (None when it could not be read)."""
    ip = await fetch()
    if ip is None:
        log.warning("public IP unknown: no source answered")
        return None
    now = now or datetime.now(UTC)
    async with db.system_session() as s:
        row = (await s.execute(select(PlatformSetting).where(PlatformSetting.key == KEY))).scalar_one_or_none()
        old = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
        value = {**old, "ip": ip, "checked_at": now.isoformat()}
        if old.get("ip") != ip:
            value |= {"previous": old.get("ip"), "changed_at": now.isoformat()}
        if row is None:
            s.add(PlatformSetting(key=KEY, value=value))
        else:
            row.value = value
        if old.get("ip") and old["ip"] != ip:
            log.warning("public IP changed", previous=old["ip"], ip=ip)
            users = (
                await s.execute(select(BrokerAccount.user_id).where(BrokerAccount.broker == Broker.ZERODHA).distinct())
            ).scalars()
            for user_id in users:
                s.add(
                    Notification(
                        user_id=user_id,
                        event="ip_changed",
                        title=f"Server IP changed to {ip}",
                        body=f"AlgoEarning now reaches Zerodha from {ip} (was {old['ip']}). Zerodha refuses orders "
                        "from an IP your Kite app does not list: open developers.kite.trade, edit your app, and "
                        f"replace {old['ip']} with {ip} before your next live trade.",
                    )
                )
    return ip
