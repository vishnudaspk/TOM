"""Live integration tests for Python IPC Client and EngineClient against tom-engine.

Verifies the complete end-to-end communication stack across the Windows Named Pipe:
- Typed responses from Rust engine
- System telemetry domains (CPU, memory, GPU, battery, disk, processes, snapshot)
- Error handling (not found, version mismatch)
- High concurrency and request correlation
- Task leak audit
"""

import asyncio
from collections.abc import Coroutine
from typing import Any

import pytest
from tom.core.engine import EngineClient
from tom.ipc.client import NamedPipeIpcClient
from tom.ipc.errors import RemoteError
from tom.ipc.protocol import (
    BatteryInfo,
    CpuInfo,
    DiskInfo,
    GpuInfo,
    MemoryInfo,
    PingResponse,
    ProcessInfo,
    StatusResponse,
    SystemSnapshot,
)
from tom.schemas.config import IpcConfig


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Helper to run coroutines synchronously in pytest."""
    return asyncio.run(coro)


def test_live_ping(live_engine: str) -> None:
    """Verify live ping returns pong=True and valid version string."""

    async def _test() -> None:
        client = NamedPipeIpcClient(config=IpcConfig(pipe_name=live_engine))
        engine = EngineClient(client)
        await client.connect()
        try:
            res = await engine.ping()
            assert isinstance(res, PingResponse)
            assert res.pong is True
            assert len(res.version) > 0
        finally:
            await client.close()

    run_async(_test())


def test_live_status(live_engine: str) -> None:
    """Verify live status returns engine identity and running state."""

    async def _test() -> None:
        client = NamedPipeIpcClient(config=IpcConfig(pipe_name=live_engine))
        engine = EngineClient(client)
        await client.connect()
        try:
            res = await engine.status()
            assert isinstance(res, StatusResponse)
            assert res.engine == "tom-engine"
            assert res.status == "running"
        finally:
            await client.close()

    run_async(_test())


def test_live_get_cpu(live_engine: str) -> None:
    """Verify live CPU telemetry contains valid core count and usage metrics."""

    async def _test() -> None:
        client = NamedPipeIpcClient(config=IpcConfig(pipe_name=live_engine))
        engine = EngineClient(client)
        await client.connect()
        try:
            cpu = await engine.get_cpu()
            assert isinstance(cpu, CpuInfo)
            assert cpu.core_count > 0
            assert 0.0 <= cpu.usage_percent <= 100.0 * cpu.core_count + 100.0
        finally:
            await client.close()

    run_async(_test())


def test_live_get_memory(live_engine: str) -> None:
    """Verify live memory telemetry contains valid byte capacities."""

    async def _test() -> None:
        client = NamedPipeIpcClient(config=IpcConfig(pipe_name=live_engine))
        engine = EngineClient(client)
        await client.connect()
        try:
            mem = await engine.get_memory()
            assert isinstance(mem, MemoryInfo)
            assert mem.total_bytes > 0
            assert mem.used_bytes > 0
            assert mem.available_bytes > 0
            assert mem.used_bytes <= mem.total_bytes
        finally:
            await client.close()

    run_async(_test())


def test_live_get_gpu(live_engine: str) -> None:
    """Verify live GPU telemetry returns typed model (graceful fallback if absent)."""

    async def _test() -> None:
        client = NamedPipeIpcClient(config=IpcConfig(pipe_name=live_engine))
        engine = EngineClient(client)
        await client.connect()
        try:
            gpu = await engine.get_gpu()
            assert isinstance(gpu, GpuInfo)
            assert isinstance(gpu.available, bool)
        finally:
            await client.close()

    run_async(_test())


def test_live_get_battery(live_engine: str) -> None:
    """Verify live battery telemetry returns typed model (graceful fallback on desktop)."""

    async def _test() -> None:
        client = NamedPipeIpcClient(config=IpcConfig(pipe_name=live_engine))
        engine = EngineClient(client)
        await client.connect()
        try:
            bat = await engine.get_battery()
            assert isinstance(bat, BatteryInfo)
            assert isinstance(bat.available, bool)
        finally:
            await client.close()

    run_async(_test())


def test_live_get_disk(live_engine: str) -> None:
    """Verify live disk telemetry returns at least one mounted volume."""

    async def _test() -> None:
        client = NamedPipeIpcClient(config=IpcConfig(pipe_name=live_engine))
        engine = EngineClient(client)
        await client.connect()
        try:
            disk = await engine.get_disk()
            assert isinstance(disk, DiskInfo)
            assert len(disk.disks) >= 1
            assert disk.disks[0].total_bytes > 0
        finally:
            await client.close()

    run_async(_test())


def test_live_get_processes(live_engine: str) -> None:
    """Verify live process telemetry respects the specified limit."""

    async def _test() -> None:
        client = NamedPipeIpcClient(config=IpcConfig(pipe_name=live_engine))
        engine = EngineClient(client)
        await client.connect()
        try:
            limit = 5
            proc = await engine.get_processes(limit=limit)
            assert isinstance(proc, ProcessInfo)
            assert len(proc.processes) <= limit
            if proc.processes:
                assert proc.processes[0].pid > 0
        finally:
            await client.close()

    run_async(_test())


def test_live_get_system_snapshot(live_engine: str) -> None:
    """Verify live snapshot aggregates all 6 telemetry domains."""

    async def _test() -> None:
        client = NamedPipeIpcClient(config=IpcConfig(pipe_name=live_engine))
        engine = EngineClient(client)
        await client.connect()
        try:
            snapshot = await engine.get_system_snapshot()
            assert isinstance(snapshot, SystemSnapshot)
            assert snapshot.cpu.core_count > 0
            assert snapshot.memory.total_bytes > 0
            assert isinstance(snapshot.gpu.available, bool)
            assert isinstance(snapshot.battery.available, bool)
            assert len(snapshot.disk.disks) >= 1
            assert isinstance(snapshot.processes, list)
        finally:
            await client.close()

    run_async(_test())


def test_live_concurrent_pings(live_engine: str) -> None:
    """Verify 10 concurrent pings complete successfully without corruption or crosstalk."""

    async def _test() -> None:
        client = NamedPipeIpcClient(config=IpcConfig(pipe_name=live_engine))
        engine = EngineClient(client)
        await client.connect()
        try:
            tasks = [engine.ping() for _ in range(10)]
            results = await asyncio.gather(*tasks)
            assert len(results) == 10
            for r in results:
                assert isinstance(r, PingResponse)
                assert r.pong is True
        finally:
            await client.close()

    run_async(_test())


def test_live_nonexistent_method_returns_remote_error(live_engine: str) -> None:
    """Verify request to unknown method returns RemoteError with NOT_FOUND."""

    async def _test() -> None:
        client = NamedPipeIpcClient(config=IpcConfig(pipe_name=live_engine))
        await client.connect()
        try:
            with pytest.raises(RemoteError) as exc_info:
                await client.request("nonexistent.method", {})
            assert exc_info.value.code == "NOT_FOUND"
            assert exc_info.value.is_not_found
        finally:
            await client.close()

    run_async(_test())


def test_live_version_mismatch_returns_remote_error(live_engine: str) -> None:
    """Verify request with unsupported protocol version returns VERSION_MISMATCH."""

    async def _test() -> None:
        client = NamedPipeIpcClient(config=IpcConfig(pipe_name=live_engine))
        await client.connect()
        try:
            raw_req = {
                "id": "test_mismatch_id",
                "version": 999,
                "method": "engine.ping",
                "params": {},
            }
            import json

            frame = json.dumps(raw_req).encode("utf-8")
            await client.transport.write_frame(frame)
            resp_bytes = await client.transport.read_frame()
            resp_data = json.loads(resp_bytes.decode("utf-8"))

            assert resp_data["success"] is False
            assert resp_data["error"]["code"] == "VERSION_MISMATCH"
        finally:
            await client.close()

    run_async(_test())


def test_live_zero_leaked_tasks_after_100_requests(live_engine: str) -> None:
    """Verify that after 100 sequential requests, no pending state or tasks leak."""

    async def _test() -> None:
        client = NamedPipeIpcClient(config=IpcConfig(pipe_name=live_engine))
        engine = EngineClient(client)
        await client.connect()
        try:
            # Baseline active tasks (test task + receive loop task)
            await engine.ping()
            await asyncio.sleep(0)
            initial_active_tasks = [t for t in asyncio.all_tasks() if not t.done()]

            for _ in range(100):
                res = await engine.ping()
                assert res.pong is True

            await asyncio.sleep(0)
            final_active_tasks = [t for t in asyncio.all_tasks() if not t.done()]

            assert client.pending_count == 0
            # Zero leaked background tasks
            assert len(final_active_tasks) == len(initial_active_tasks)
        finally:
            await client.close()

    run_async(_test())
