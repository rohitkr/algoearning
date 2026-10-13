"""One price feed at a time, newest wins: a starting feed takes over and stops a leftover older one on this machine;
a running feed stops itself when a newer one takes over."""

from __future__ import annotations

import asyncio
import os
import signal
import socket
from typing import Any

import pytest
from ae_marketdata import single
from redis.asyncio import Redis

from conftest import TEST_REDIS_URL


@pytest.fixture
async def redis() -> Any:
    r = Redis.from_url(TEST_REDIS_URL)
    await r.delete(single.KEY)
    yield r
    await r.delete(single.KEY)
    await r.aclose()


async def test_the_newest_feed_stops_the_old_one(redis: Any) -> None:
    host = socket.gethostname()
    await redis.set(single.KEY, f"{host}:424242:old")
    killed: list[tuple[int, int]] = []
    state = {"alive": True}

    def kill(pid: int, sig: int) -> None:
        killed.append((pid, sig))
        state["alive"] = False  # it obeys SIGTERM

    pid = await single.take_over(redis, f"{host}:{os.getpid()}:new", kill=kill, alive=lambda p: state["alive"])
    assert pid == 424242 and killed == [(424242, signal.SIGTERM)]
    assert (await redis.get(single.KEY)).decode().endswith(":new")


async def test_a_stuck_old_feed_is_killed(redis: Any) -> None:
    host = socket.gethostname()
    await redis.set(single.KEY, f"{host}:424243:old")
    sent: list[int] = []
    await single.take_over(redis, f"{host}:{os.getpid()}:new", kill=lambda p, s: sent.append(s),
                           alive=lambda p: signal.SIGKILL not in sent, grace_s=0.2)  # fmt: skip
    assert sent == [signal.SIGTERM, signal.SIGKILL]


async def test_other_machines_and_dead_pids_are_left_alone(redis: Any) -> None:
    sent: list[int] = []
    await redis.set(single.KEY, "another-host:1:x")
    assert await single.take_over(redis, "me", kill=lambda p, s: sent.append(p)) is None
    await redis.set(single.KEY, f"{socket.gethostname()}:999999:x")
    assert await single.take_over(redis, "me", kill=lambda p, s: sent.append(p), alive=lambda p: False) is None
    assert sent == []


async def test_a_running_feed_stops_when_a_newer_one_takes_over(redis: Any) -> None:
    stop = asyncio.Event()
    await redis.set(single.KEY, "h:1:me")
    task = asyncio.create_task(single.keep(redis, "h:1:me", stop))
    await asyncio.sleep(0.05)
    assert not stop.is_set()
    await redis.set(single.KEY, "h:2:newer")
    single.CHECK_S, old = 0.05, single.CHECK_S
    try:
        await asyncio.wait_for(task, timeout=6)
    finally:
        single.CHECK_S = old
    assert stop.is_set()
    await single.release(redis, "h:1:me")
    assert (await redis.get(single.KEY)) == b"h:2:newer"  # not ours: not removed
