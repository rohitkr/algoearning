"""Razorpay REST client (Orders + Payments) and signature checks. Test and live keys work the same way; only the
key pair differs. https://razorpay.com/docs/api/"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import httpx

API = "https://api.razorpay.com/v1"


class RazorpayError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Razorpay {status}: {message}")
        self.status = status


def _hmac_hex(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def checkout_signature_ok(key_secret: str, order_id: str, payment_id: str, signature: str) -> bool:
    """Checkout handler signature: HMAC-SHA256(order_id + "|" + payment_id, key_secret)."""
    return hmac.compare_digest(_hmac_hex(key_secret, f"{order_id}|{payment_id}".encode()), signature or "")


def webhook_signature_ok(webhook_secret: str, body: bytes, signature: str) -> bool:
    """X-Razorpay-Signature: HMAC-SHA256(raw body, webhook secret)."""
    return hmac.compare_digest(_hmac_hex(webhook_secret, body), signature or "")


class RazorpayClient:
    name = "razorpay"

    def __init__(
        self,
        key_id: str,
        key_secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = API,
        timeout: float = 15.0,
    ) -> None:
        self.key_id, self.key_secret = key_id, key_secret
        self._client_args: dict[str, Any] = {
            "base_url": base_url,
            "auth": (key_id, key_secret),
            "timeout": timeout,
            "transport": transport,
        }

    async def _call(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        async with httpx.AsyncClient(**self._client_args) as c:
            r = await c.request(method, path, **kw)
        body: dict[str, Any] = r.json() if r.content else {}
        if r.status_code >= 400:
            err = body.get("error") or {}
            raise RazorpayError(r.status_code, str(err.get("description") or err or r.text[:200]))
        return body

    async def create_order(
        self, amount_paise: int, currency: str, receipt: str, notes: dict[str, str]
    ) -> dict[str, Any]:
        return await self._call(
            "POST",
            "/orders",
            json={"amount": amount_paise, "currency": currency, "receipt": receipt[:40], "notes": notes},
        )

    async def fetch_payment(self, payment_id: str) -> dict[str, Any]:
        return await self._call("GET", f"/payments/{payment_id}")

    async def capture(self, payment_id: str, amount_paise: int, currency: str) -> dict[str, Any]:
        return await self._call(
            "POST", f"/payments/{payment_id}/capture", json={"amount": amount_paise, "currency": currency}
        )

    async def order_payments(self, order_id: str) -> list[dict[str, Any]]:
        return list((await self._call("GET", f"/orders/{order_id}/payments")).get("items") or [])

    def checkout_signature_ok(self, order_id: str, payment_id: str, signature: str) -> bool:
        return checkout_signature_ok(self.key_secret, order_id, payment_id, signature)
