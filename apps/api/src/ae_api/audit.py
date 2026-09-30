"""Record security-relevant actions with who / what / from where."""

from __future__ import annotations

import ipaddress
import uuid
from typing import Any

import structlog
from ae_db.repositories import AuditRepo
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


def client_ip(request: Request) -> str | None:
    """The peer address if it is a real IP (a unix socket or test client name must not break a request).
    Behind a proxy, uvicorn's --proxy-headers already puts the forwarded client address here."""
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


async def audit(
    session: AsyncSession,
    request: Request,
    action: str,
    user_id: uuid.UUID | None,
    target_type: str | None = None,
    target_id: object = None,
    **detail: Any,
) -> None:
    await AuditRepo(session).record(
        action,
        user_id=user_id,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        request_id=structlog.contextvars.get_contextvars().get("request_id"),
        detail=detail,
    )
