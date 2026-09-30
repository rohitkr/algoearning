"""Verification of Svix-signed webhooks (Clerk uses Svix): HMAC-SHA256 over "{id}.{timestamp}.{body}" with
the base64 secret after "whsec_", any of the space-separated "v1,<sig>" values may match, and the timestamp
must be within 5 minutes (replay protection on top of our stored event ids)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

TOLERANCE_S = 5 * 60


class InvalidSignature(Exception):
    pass


def verify_svix(secret: str, headers: dict[str, str], body: bytes, now: float | None = None) -> str:
    """Returns the event id; raises InvalidSignature."""
    msg_id, ts, sigs = headers.get("svix-id"), headers.get("svix-timestamp"), headers.get("svix-signature")
    if not (msg_id and ts and sigs):
        raise InvalidSignature("missing svix headers")
    try:
        sent = int(ts)
    except ValueError as exc:
        raise InvalidSignature("bad timestamp") from exc
    if abs((now or time.time()) - sent) > TOLERANCE_S:
        raise InvalidSignature("timestamp outside tolerance")
    key = base64.b64decode(secret.removeprefix("whsec_"))
    expected = base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    for part in sigs.split():
        version, _, sig = part.partition(",")
        if version == "v1" and hmac.compare_digest(sig, expected):
            return msg_id
    raise InvalidSignature("signature mismatch")


def sign_svix(secret: str, msg_id: str, ts: int, body: bytes) -> str:
    """For tests and local tooling: the header value Clerk would send."""
    key = base64.b64decode(secret.removeprefix("whsec_"))
    return "v1," + base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
