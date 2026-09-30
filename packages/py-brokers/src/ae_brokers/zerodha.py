"""Zerodha Kite Connect (v3). Login: https://kite.zerodha.com/connect/login?v=3&api_key=...&redirect_params=...
Kite redirects to the app's registered Redirect URL with request_token, status and our redirect_params; the token is
exchanged with checksum = sha256(api_key + request_token + api_secret). Access tokens expire at 06:00 IST the next
morning (a new login every trading day). Adapted from the local app's zerodha/auth.py."""

from __future__ import annotations

import hashlib
from datetime import datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from .base import BrokerError, BrokerInfo, BrokerSession, Credentials

IST = timezone(timedelta(hours=5, minutes=30))
TOKEN_RESET = time(6, 0)
LOGIN = "https://kite.zerodha.com/connect/login"
API = "https://api.kite.trade"


def next_token_expiry(issued: datetime) -> datetime:
    """Kite invalidates access tokens around 06:00 IST: a token issued before 06:00 dies at 06:00 the same day."""
    local = issued.astimezone(IST)
    reset = datetime.combine(local.date(), TOKEN_RESET, tzinfo=IST)
    return reset if local < reset else reset + timedelta(days=1)


class ZerodhaAdapter:
    info = BrokerInfo(
        code="zerodha",
        name="Zerodha",
        available=True,
        developer_console="https://developers.kite.trade/apps",
        notes="Create a Kite Connect app and set its Redirect URL to the callback shown below. "
        "Kite allows one Redirect URL per app.",
    )

    def __init__(
        self, *, transport: httpx.AsyncBaseTransport | None = None, api: str = API, clock: type[datetime] = datetime
    ) -> None:
        self._client_args: dict[str, Any] = {
            "base_url": api,
            "timeout": 15.0,
            "transport": transport,
            "headers": {"X-Kite-Version": "3"},
        }
        self._clock = clock

    def login_url(self, creds: Credentials, state: str) -> str:
        return f"{LOGIN}?" + urlencode(
            {"v": "3", "api_key": creds.api_key, "redirect_params": urlencode({"state": state})}
        )

    @staticmethod
    def checksum(creds: Credentials, request_token: str) -> str:
        return hashlib.sha256(f"{creds.api_key}{request_token}{creds.api_secret}".encode()).hexdigest()

    async def _call(self, method: str, path: str, *, auth: str | None = None, **kw: Any) -> dict[str, Any]:
        headers = {"Authorization": f"token {auth}"} if auth else {}
        try:
            async with httpx.AsyncClient(**self._client_args) as c:
                r = await c.request(method, path, headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise BrokerError("unreachable", f"Zerodha is unreachable ({type(exc).__name__})") from exc
        body: dict[str, Any] = r.json() if r.content else {}
        if r.status_code >= 400 or body.get("status") == "error":
            kind = body.get("error_type") or "error"
            code = "session_expired" if kind == "TokenException" else "rejected"
            raise BrokerError(code, str(body.get("message") or f"Zerodha error {r.status_code}"), r.status_code)
        return body

    async def exchange(self, creds: Credentials, callback_params: dict[str, str]) -> BrokerSession:
        if callback_params.get("status") != "success" or not callback_params.get("request_token"):
            raise BrokerError("login_cancelled", "Zerodha login was not completed")
        token = callback_params["request_token"]
        body = await self._call(
            "POST",
            "/session/token",
            data={"api_key": creds.api_key, "request_token": token, "checksum": self.checksum(creds, token)},
        )
        data = body.get("data") or {}
        if not data.get("access_token") or not data.get("user_id"):
            raise BrokerError("rejected", "Zerodha returned no session")
        issued = self._clock.now(IST)
        profile = {k: data.get(k) for k in ("user_id", "user_name", "email", "broker", "exchanges", "products")}
        return BrokerSession(
            access_token=str(data["access_token"]),
            client_id=str(data["user_id"]).upper(),
            expires_at=next_token_expiry(issued),
            profile=profile,
        )

    async def profile(self, creds: Credentials, access_token: str) -> dict[str, Any]:
        data = (await self._call("GET", "/user/profile", auth=f"{creds.api_key}:{access_token}")).get("data") or {}
        return {k: data.get(k) for k in ("user_id", "user_name", "email", "broker", "exchanges", "products")}

    async def logout(self, creds: Credentials, access_token: str) -> None:
        await self._call("DELETE", "/session/token", params={"api_key": creds.api_key, "access_token": access_token})
