"""Liveness / readiness. /health never touches dependencies (for the load balancer); /health/ready checks each
configured dependency with a short timeout and answers 503 while one is down."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from .. import __version__
from ..settings import SettingsDep

router = APIRouter(tags=["health"])
TIMEOUT_S = 2.0


class Health(BaseModel):
    status: Literal["ok"]
    version: str
    env: str


class Check(BaseModel):
    name: str
    status: Literal["ok", "down", "not_configured"]
    detail: str = ""


class Readiness(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: list[Check]


@router.get("/health", response_model=Health)
def health(settings: SettingsDep) -> Health:
    return Health(status="ok", version=__version__, env=settings.app_env)


async def _check(name: str, probe: object) -> Check:
    if probe is None:
        return Check(name=name, status="not_configured")
    try:
        await asyncio.wait_for(probe, TIMEOUT_S)  # type: ignore[arg-type]
        return Check(name=name, status="ok")
    except Exception as exc:
        return Check(name=name, status="down", detail=type(exc).__name__)


@router.get("/health/ready", response_model=Readiness)
async def ready(request: Request, response: Response) -> Readiness:
    db = getattr(request.app.state, "db", None)
    redis = getattr(request.app.state, "redis", None)
    checks = [
        await _check("database", db.ping() if db is not None else None),
        await _check("redis", redis.ping() if redis is not None else None),
    ]
    down = any(c.status == "down" for c in checks)
    if down:
        response.status_code = 503
    return Readiness(status="not_ready" if down else "ready", checks=checks)
