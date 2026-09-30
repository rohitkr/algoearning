from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from ae_brokers.base import BrokerError, Credentials
from ae_brokers.zerodha import IST, ZerodhaAdapter, next_token_expiry

CREDS = Credentials("kiteapikey", "kitesecret")


def test_login_url_carries_state_back_through_redirect_params() -> None:
    url = ZerodhaAdapter().login_url(CREDS, "signed.state.token")
    q = parse_qs(urlparse(url).query)
    assert url.startswith("https://kite.zerodha.com/connect/login?")
    assert q["v"] == ["3"] and q["api_key"] == ["kiteapikey"]
    assert parse_qs(q["redirect_params"][0]) == {"state": ["signed.state.token"]}


@pytest.mark.parametrize(
    ("issued", "expires"),
    [
        (datetime(2026, 10, 1, 9, 30, tzinfo=IST), datetime(2026, 10, 2, 6, 0, tzinfo=IST)),
        (datetime(2026, 10, 1, 5, 59, tzinfo=IST), datetime(2026, 10, 1, 6, 0, tzinfo=IST)),
        (datetime(2026, 10, 1, 0, 30, tzinfo=UTC), datetime(2026, 10, 2, 6, 0, tzinfo=IST)),  # 06:00 IST
    ],
)
def test_tokens_expire_at_six_ist(issued: datetime, expires: datetime) -> None:
    assert next_token_expiry(issued) == expires


def fake_kite(responses: dict[tuple[str, str], httpx.Response], seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return responses[(req.method, req.url.path)]

    return httpx.MockTransport(handler)


async def test_exchange_sends_the_checksum_and_returns_a_session() -> None:
    seen: list[httpx.Request] = []
    ok = httpx.Response(
        200,
        json={
            "status": "success",
            "data": {"access_token": "at-123", "user_id": "ab1234", "user_name": "Rohit", "email": "r@x.com"},
        },
    )
    z = ZerodhaAdapter(transport=fake_kite({("POST", "/session/token"): ok}, seen))
    s = await z.exchange(CREDS, {"status": "success", "request_token": "rt-1"})
    assert s.access_token == "at-123" and s.client_id == "AB1234" and s.profile["user_name"] == "Rohit"
    form = parse_qs(seen[0].content.decode())
    assert form["checksum"] == [ZerodhaAdapter.checksum(CREDS, "rt-1")] and seen[0].headers["X-Kite-Version"] == "3"
    assert s.expires_at > datetime.now(timezone(timedelta(hours=5, minutes=30)))


async def test_errors_are_classified() -> None:
    seen: list[httpx.Request] = []
    bad = httpx.Response(403, json={"status": "error", "message": "Invalid `checksum`", "error_type": "TokenException"})
    z = ZerodhaAdapter(transport=fake_kite({("POST", "/session/token"): bad}, seen))
    with pytest.raises(BrokerError) as e:
        await z.exchange(CREDS, {"status": "success", "request_token": "rt"})
    assert e.value.code == "session_expired" and "checksum" in e.value.message
    with pytest.raises(BrokerError) as e:
        await z.exchange(CREDS, {"status": "cancelled"})
    assert e.value.code == "login_cancelled"


async def test_profile_uses_token_auth_header() -> None:
    seen: list[httpx.Request] = []
    ok = httpx.Response(200, json={"status": "success", "data": {"user_id": "AB1234", "user_name": "Rohit"}})
    z = ZerodhaAdapter(transport=fake_kite({("GET", "/user/profile"): ok}, seen))
    assert (await z.profile(CREDS, "at-9"))["user_id"] == "AB1234"
    assert seen[0].headers["Authorization"] == "token kiteapikey:at-9"
