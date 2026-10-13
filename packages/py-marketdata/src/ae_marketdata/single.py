"""One price feed at a time, newest wins.

Two feeds on one machine fight over the provider's single streaming session (Breeze allows one), and both end up
without prices. A restart can leave the old feed behind: the service stops the `uv run` wrapper, not always its Python
child. So a starting feed takes over: it writes itself into a Redis key, and the copy it replaces (if it still runs on
this machine) is asked to stop, then forced to. A running feed checks the key every few seconds and stops itself
as soon as a newer one owns it."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import uuid
from collections.abc import Callable
from typing import Any

import structlog

log = structlog.get_logger("ae_marketdata.single")
KEY = "ae:feed:owner"
TTL_S = 30
CHECK_S = 5


def owner_value() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def take_over(
    redis: Any,
    me: str,
    *,
    kill: Callable[[int, int], None] = os.kill,
    alive: Callable[[int], bool] = _alive,
    grace_s: float = 5.0,
) -> int | None:
    """Become the feed; stop the previous one if it runs on this machine. Returns the pid that was stopped."""
    old = await redis.getset(KEY, me)
    await redis.expire(KEY, TTL_S)
    if not old:
        return None
    host, pid_s, _ = (old.decode() if isinstance(old, bytes) else old).split(":", 2)
    pid = int(pid_s)
    if host != socket.gethostname() or pid == os.getpid() or not alive(pid):
        return None
    log.warning("another feed was still running: stopping it", pid=pid)
    with contextlib.suppress(ProcessLookupError):
        kill(pid, signal.SIGTERM)
    for _ in range(int(grace_s * 10)):
        if not alive(pid):
            return pid
        await asyncio.sleep(0.1)
    with contextlib.suppress(ProcessLookupError):
        kill(pid, signal.SIGKILL)
    log.warning("the old feed did not stop: killed it", pid=pid)
    return pid


async def keep(redis: Any, me: str, stop: asyncio.Event) -> None:
    """While running: refresh our claim; if a newer feed took over, stop."""
    while not stop.is_set():
        current = await redis.get(KEY)
        current = current.decode() if isinstance(current, bytes) else current
        if current is not None and current != me:
            log.warning("a newer feed took over: stopping")
            stop.set()
            return
        await redis.set(KEY, me, ex=TTL_S)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=CHECK_S)


async def release(redis: Any, me: str) -> None:
    current = await redis.get(KEY)
    if (current.decode() if isinstance(current, bytes) else current) == me:
        await redis.delete(KEY)
