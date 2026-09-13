"""Live integration tests for LifecycleManager against running tom-engine."""

import asyncio
from collections.abc import Coroutine
from typing import Any

from tom.core.lifecycle import LifecycleManager
from tom.ipc.protocol import PingResponse
from tom.schemas.config import TOMConfig


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Helper to run coroutines synchronously in pytest."""
    return asyncio.run(coro)


def test_live_lifecycle_startup_and_shutdown(live_engine: str) -> None:
    """Verify LifecycleManager starts up against live engine and shuts down cleanly."""

    async def _test() -> None:
        config = TOMConfig()
        config.ipc.pipe_name = live_engine

        manager = LifecycleManager(config=config)
        assert not manager.is_running

        await manager.start()
        assert manager.is_running
        assert manager.is_started

        # Verify engine calls work through manager.engine
        ping_resp = await manager.engine.ping()
        assert isinstance(ping_resp, PingResponse)
        assert ping_resp.pong is True

        await manager.stop()
        assert not manager.is_running
        assert manager.is_stopped

    run_async(_test())


def test_live_lifecycle_repeated_cycles_no_leaks(live_engine: str) -> None:
    """Verify LifecycleManager executes two full start/stop cycles without leaks or errors."""

    async def _test() -> None:
        config = TOMConfig()
        config.ipc.pipe_name = live_engine

        manager = LifecycleManager(config=config)

        # Cycle 1
        await manager.start()
        assert manager.is_running
        res1 = await manager.engine.ping()
        assert res1.pong is True
        await manager.stop()
        assert not manager.is_running

        # Cycle 2
        await manager.start()
        assert manager.is_running
        res2 = await manager.engine.ping()
        assert res2.pong is True
        await manager.stop()
        assert not manager.is_running

    run_async(_test())
