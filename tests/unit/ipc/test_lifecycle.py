"""Unit tests for LifecycleManager (Phase 2, Iteration 7).

Verifies startup sequence, readiness checks, exception taxonomy,
idempotent shutdown, signal handling, and resource cleanup.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any
from unittest.mock import AsyncMock

import pytest
from tom.core.engine import EngineClient
from tom.core.lifecycle import LifecycleManager
from tom.ipc.errors import ConnectionError, LifecycleError, RemoteError
from tom.ipc.protocol import PingResponse, StatusResponse
from tom.schemas.config import TOMConfig


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Helper to run coroutines synchronously without relying on pytest-asyncio plugin."""
    return asyncio.run(coro)


class FakeEngine:
    """Mock EngineClient tracking method calls and returning typed responses."""

    def __init__(
        self, *, ping_error: Exception | None = None, status_error: Exception | None = None
    ) -> None:
        self.calls: list[str] = []
        self.ping_error = ping_error
        self.status_error = status_error
        self.closed: bool = False
        self.ipc = AsyncMock()
        self.ipc.is_connected = False

    async def ping(self) -> PingResponse:
        self.calls.append("ping")
        if self.ping_error:
            raise self.ping_error
        return PingResponse(pong=True, version="0.1.0")

    async def status(self) -> StatusResponse:
        self.calls.append("status")
        if self.status_error:
            raise self.status_error
        return StatusResponse(engine="tom-engine", status="running")

    async def close(self) -> None:
        self.closed = True
        self.calls.append("close")


class FakeIpcClient:
    """Mock NamedPipeIpcClient for lifecycle startup tests."""

    def __init__(self, *, connect_error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self.connect_error = connect_error
        self.is_connected: bool = False
        self.closed: bool = False

    async def connect(self) -> None:
        self.calls.append("connect")
        if self.connect_error:
            raise self.connect_error
        self.is_connected = True

    async def close(self) -> None:
        self.closed = True
        self.is_connected = False
        self.calls.append("close")

    async def request(self, method: str, params: dict | None = None) -> dict:
        self.calls.append(f"request:{method}")
        if method == "engine.ping":
            return {"pong": True, "version": "0.1.0"}
        elif method == "engine.status":
            return {"engine": "tom-engine", "status": "running"}
        return {}


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


def test_lifecycle_initial_state() -> None:
    """Manager before start() is not running and engine property raises."""
    manager = LifecycleManager()
    assert not manager.is_running
    assert not manager.is_started
    assert not manager.is_stopped

    with pytest.raises(LifecycleError, match="not been started"):
        _ = manager.engine


def test_lifecycle_successful_startup() -> None:
    """start() calls connect, ping, and status in order and exposes engine."""

    async def _test() -> None:
        fake_engine = FakeEngine()
        manager = LifecycleManager(engine=fake_engine)

        await manager.start()

        assert manager.is_running
        assert manager.is_started
        assert not manager.is_stopped
        assert manager.engine is fake_engine
        assert fake_engine.calls == ["ping", "status"]

        await manager.stop()

    run_async(_test())


def test_lifecycle_stop_is_idempotent() -> None:
    """stop() can be called before start, once after start, and repeatedly without raising."""

    async def _test() -> None:
        fake_engine = FakeEngine()
        manager = LifecycleManager(engine=fake_engine)

        # Calling stop before start must not raise
        await manager.stop()
        assert manager.is_stopped
        assert not manager.is_running

        # Now start
        await manager.start()
        assert manager.is_running
        assert not manager.is_stopped

        # Stop once
        await manager.stop()
        assert manager.is_stopped
        assert not manager.is_running
        assert fake_engine.closed

        # Accessing engine after stop must raise LifecycleError
        with pytest.raises(LifecycleError, match="stopped"):
            _ = manager.engine

        # Stop a second time (idempotent)
        await manager.stop()
        assert manager.is_stopped

    run_async(_test())


def test_lifecycle_start_when_already_running_raises() -> None:
    """Calling start() twice while running raises LifecycleError."""

    async def _test() -> None:
        fake_engine = FakeEngine()
        manager = LifecycleManager(engine=fake_engine)

        await manager.start()
        try:
            with pytest.raises(LifecycleError, match="already running"):
                await manager.start()
        finally:
            await manager.stop()

    run_async(_test())


def test_lifecycle_partial_startup_failure_ping() -> None:
    """Failure during ping() raises LifecycleError and triggers cleanup."""

    async def _test() -> None:
        fake_engine = FakeEngine(ping_error=ConnectionError("Pipe unreachable"))
        manager = LifecycleManager(engine=fake_engine)

        with pytest.raises(LifecycleError, match="TOM startup failed"):
            await manager.start()

        assert not manager.is_running
        assert not manager.is_started
        assert fake_engine.closed

        with pytest.raises(LifecycleError):
            _ = manager.engine

    run_async(_test())


def test_lifecycle_partial_startup_failure_status() -> None:
    """Failure during status() raises LifecycleError and triggers cleanup."""

    async def _test() -> None:
        fake_engine = FakeEngine(
            status_error=RemoteError("INTERNAL", "Engine failed to initialize")
        )
        manager = LifecycleManager(engine=fake_engine)

        with pytest.raises(LifecycleError, match="TOM startup failed"):
            await manager.start()

        assert not manager.is_running
        assert not manager.is_started
        assert fake_engine.closed

    run_async(_test())


def test_lifecycle_connect_failure() -> None:
    """Failure during ipc.connect() raises LifecycleError and cleans up."""

    async def _test() -> None:
        fake_ipc = FakeIpcClient(connect_error=ConnectionError("Pipe not found"))
        manager = LifecycleManager(ipc_client=fake_ipc)

        with pytest.raises(LifecycleError, match="TOM startup failed"):
            await manager.start()

        assert not manager.is_running
        assert not manager.is_started
        assert fake_ipc.closed

    run_async(_test())


def test_lifecycle_repeated_cycles() -> None:
    """Manager supports multiple start/stop cycles cleanly."""

    async def _test() -> None:
        fake_ipc = FakeIpcClient()
        manager = LifecycleManager(ipc_client=fake_ipc)

        # Cycle 1
        await manager.start()
        assert manager.is_running
        assert isinstance(manager.engine, EngineClient)
        await manager.stop()
        assert not manager.is_running

        # Cycle 2
        fake_ipc.closed = False
        fake_ipc.is_connected = False
        await manager.start()
        assert manager.is_running
        await manager.stop()
        assert not manager.is_running

    run_async(_test())


def test_lifecycle_run_until_shutdown_via_trigger() -> None:
    """run_until_shutdown() waits until trigger_shutdown() is called, then stops."""

    async def _test() -> None:
        fake_engine = FakeEngine()
        manager = LifecycleManager(engine=fake_engine)

        await manager.start()

        async def trigger_later() -> None:
            await asyncio.sleep(0.05)
            manager.trigger_shutdown()

        trigger_task = asyncio.create_task(trigger_later())
        await manager.run_until_shutdown()
        await trigger_task

        assert manager.is_stopped
        assert not manager.is_running
        assert fake_engine.closed

    run_async(_test())


def test_lifecycle_run_until_shutdown_auto_starts() -> None:
    """run_until_shutdown() automatically starts the manager if not yet started."""

    async def _test() -> None:
        fake_engine = FakeEngine()
        manager = LifecycleManager(engine=fake_engine)

        async def trigger_later() -> None:
            await asyncio.sleep(0.05)
            manager.trigger_shutdown()

        trigger_task = asyncio.create_task(trigger_later())
        await manager.run_until_shutdown()
        await trigger_task

        assert "ping" in fake_engine.calls
        assert "status" in fake_engine.calls
        assert manager.is_stopped

    run_async(_test())


def test_lifecycle_run_until_shutdown_cancellation() -> None:
    """Cancelling run_until_shutdown() cleans up and stops the manager."""

    async def _test() -> None:
        fake_engine = FakeEngine()
        manager = LifecycleManager(engine=fake_engine)

        await manager.start()

        run_task = asyncio.create_task(manager.run_until_shutdown())
        await asyncio.sleep(0.05)
        run_task.cancel()

        await asyncio.gather(run_task, return_exceptions=True)

        assert manager.is_stopped
        assert fake_engine.closed

    run_async(_test())


def test_lifecycle_config_access() -> None:
    """Manager stores and exposes TOMConfig."""
    cfg = TOMConfig()
    manager = LifecycleManager(config=cfg)
    assert manager.config is cfg
