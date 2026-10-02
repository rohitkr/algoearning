"""Live SMC charts (ae_api.charts): which indices can be charted, a one-off snapshot, and the live stream.

The stream is server-sent events over a plain GET, so the browser sends its Clerk token as a Bearer header like
every other call (an EventSource cannot; the page reads the stream with fetch). It ends itself after a while and the
page reconnects with a fresh token, so a signed-out or blocked user stops receiving prices within minutes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from ae_db.models import Instrument
from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ..charts import TIMEFRAMES, ChartInstrument, ChartService
from ..deps import CurrentUser, DbDep
from ..errors import AppError, Unavailable
from ..schemas import ERROR_RESPONSES, ChartInstrumentOut, ChartOptions, ChartSnapshot

router = APIRouter(prefix="/v1/charts", tags=["charts"], responses=ERROR_RESPONSES)

# Indices offered on the charts page, in this order. Add an instrument's code to offer it (it must be active).
CHART_CODES = ("NIFTY", "SENSEX", "BANKNIFTY")
HEARTBEAT_S = 15.0
STREAM_MAX_S = 15 * 60

KeyQ = Annotated[str, Query(min_length=1, max_length=20, description="Index code, e.g. NIFTY")]
TimeframeQ = Annotated[int, Query(description="Candle length in minutes")]


def _service(request: Request) -> ChartService:
    svc: ChartService | None = getattr(request.app.state, "charts", None)
    if svc is None:
        raise Unavailable("charts need REDIS_URL and DATABASE_URL")
    return svc


async def _instruments(db: DbDep) -> list[ChartInstrument]:
    async with db.system_session() as s:
        rows = {
            r.code: r
            for r in (
                await s.execute(select(Instrument).where(Instrument.code.in_(CHART_CODES), Instrument.is_active))
            ).scalars()
        }
    return [
        ChartInstrument(r.code, r.name, r.session_open, r.session_close)
        for code in CHART_CODES
        if (r := rows.get(code)) is not None
    ]


async def _resolve(db: DbDep, key: str, timeframe: int) -> ChartInstrument:
    if timeframe not in TIMEFRAMES:
        raise AppError(f"timeframe must be one of {', '.join(map(str, TIMEFRAMES))} minutes")
    inst = next((i for i in await _instruments(db) if i.code == key.upper()), None)
    if inst is None:
        raise AppError(f"{key} cannot be charted", {"reason": "unknown_instrument"})
    return inst


@router.get("/options", response_model=ChartOptions)
async def chart_options(_: CurrentUser, db: DbDep) -> ChartOptions:
    return ChartOptions(
        instruments=[ChartInstrumentOut(code=i.code, name=i.name) for i in await _instruments(db)],
        timeframes=list(TIMEFRAMES),
    )


@router.get("/snapshot", response_model=ChartSnapshot)
async def chart_snapshot(
    _: CurrentUser, request: Request, db: DbDep, key: KeyQ, timeframe: TimeframeQ = 5
) -> dict[str, Any]:
    svc = _service(request)
    inst = await _resolve(db, key, timeframe)
    feed, sub = await svc.subscribe(inst, timeframe)
    try:
        return feed.snapshot()
    finally:
        svc.unsubscribe(feed, sub)


def _event(msg: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(msg, separators=(',', ':'))}\n\n".encode()


@router.get(
    "/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "Server-sent events: ChartSnapshot first"}},
)
async def chart_stream(
    _: CurrentUser, request: Request, db: DbDep, key: KeyQ, timeframe: TimeframeQ = 5
) -> StreamingResponse:
    svc = _service(request)
    inst = await _resolve(db, key, timeframe)

    async def events() -> AsyncIterator[bytes]:
        # subscribed only once the response is being sent, so a client gone before that leaves nothing behind
        feed, sub = await svc.subscribe(inst, timeframe)
        loop = asyncio.get_running_loop()
        until = loop.time() + STREAM_MAX_S
        try:
            yield _event(feed.snapshot())
            while loop.time() < until:
                try:
                    msg = await asyncio.wait_for(sub.queue.get(), timeout=HEARTBEAT_S)
                except TimeoutError:
                    yield b": ping\n\n"  # keeps proxies from closing a quiet stream (market closed)
                    continue
                if msg["type"] == "resync":
                    sub.resync = False
                    msg = feed.snapshot()
                yield _event(msg)
            yield _event({"type": "reconnect"})
        finally:
            svc.unsubscribe(feed, sub)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
