"""Unit tests for EngineClient and typed response models.

All tests use a fake IPC client (FakeIpcClient) that returns pre-configured
responses without requiring a running Rust engine.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any

import pytest
from pydantic import ValidationError
from tom.core.engine import EngineClient
from tom.ipc.errors import (
    ConnectionLostError,
    IpcTimeoutError,
    NotConnectedError,
    RemoteError,
)
from tom.ipc.protocol import (
    BatteryInfo,
    CpuInfo,
    DiskInfo,
    DiskVolume,
    GpuInfo,
    MemoryInfo,
    PingResponse,
    ProcessInfo,
    ProcessItem,
    StatusResponse,
    SystemSnapshot,
)


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fake IPC client
# ---------------------------------------------------------------------------


class FakeIpcClient:
    """Minimal fake that records calls and returns pre-configured payloads.

    Configure by calling `set_response(method, data)` before the call, or
    `set_error(method, exc)` to make the next call for that method raise.
    """

    def __init__(self) -> None:
        self._responses: dict[str, Any] = {}
        self._errors: dict[str, Exception] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []  # (method, params) history

    def set_response(self, method: str, data: dict[str, Any]) -> None:
        self._responses[method] = data
        self._errors.pop(method, None)

    def set_error(self, method: str, exc: Exception) -> None:
        self._errors[method] = exc
        self._responses.pop(method, None)

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        self.calls.append((method, params or {}))
        if method in self._errors:
            raise self._errors[method]
        if method in self._responses:
            return self._responses[method]
        raise NotConnectedError(f"FakeIpcClient: no response configured for '{method}'")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_engine() -> tuple[EngineClient, FakeIpcClient]:
    fake = FakeIpcClient()
    engine = EngineClient(ipc_client=fake)  # type: ignore[arg-type]
    return engine, fake


# ---------------------------------------------------------------------------
# Typed model validation tests
# ---------------------------------------------------------------------------


class TestTypedModelValidation:
    """Verify all response models parse correctly from representative wire data."""

    def test_ping_response_valid(self) -> None:
        r = PingResponse.model_validate({"pong": True, "version": "0.1.0"})
        assert r.pong is True
        assert r.version == "0.1.0"

    def test_ping_response_rejects_missing_pong(self) -> None:
        with pytest.raises(ValidationError):
            PingResponse.model_validate({"version": "0.1.0"})

    def test_status_response_valid(self) -> None:
        r = StatusResponse.model_validate({"engine": "tom-engine", "status": "running"})
        assert r.engine == "tom-engine"
        assert r.status == "running"

    def test_cpu_info_valid(self) -> None:
        r = CpuInfo.model_validate({"usage_percent": 42.5, "core_count": 8, "frequency_mhz": 3600})
        assert r.usage_percent == 42.5
        assert r.core_count == 8
        assert r.frequency_mhz == 3600

    def test_cpu_info_extra_fields_ignored(self) -> None:
        # extra="ignore" — unknown fields must not raise
        r = CpuInfo.model_validate(
            {"usage_percent": 1.0, "core_count": 4, "frequency_mhz": 0, "future_field": "x"}
        )
        assert r.core_count == 4

    def test_memory_info_valid(self) -> None:
        r = MemoryInfo.model_validate(
            {
                "total_bytes": 16_000_000_000,
                "used_bytes": 8_000_000_000,
                "available_bytes": 8_000_000_000,
                "swap_total_bytes": 4_000_000_000,
                "swap_used_bytes": 512_000_000,
            }
        )
        assert r.total_bytes == 16_000_000_000
        assert r.swap_used_bytes == 512_000_000

    def test_gpu_info_available(self) -> None:
        r = GpuInfo.model_validate(
            {
                "available": True,
                "vendor": "nvidia",
                "name": "NVIDIA GeForce RTX 4060 Laptop GPU",
                "utilization_percent": 35.0,
                "memory_total_bytes": 8_589_934_592,
                "memory_used_bytes": 2_147_483_648,
            }
        )
        assert r.available is True
        assert r.vendor == "nvidia"
        assert r.utilization_percent == 35.0

    def test_gpu_info_unavailable(self) -> None:
        """GpuInfo(available=False) must validate without hardware fields."""
        r = GpuInfo.model_validate({"available": False, "reason": "telemetry_unavailable"})
        assert r.available is False
        assert r.reason == "telemetry_unavailable"
        assert r.name is None
        assert r.vendor is None

    def test_gpu_info_unavailable_minimal(self) -> None:
        """available=False with no reason field also validates (reason defaults None)."""
        r = GpuInfo.model_validate({"available": False})
        assert r.available is False

    def test_battery_info_available(self) -> None:
        r = BatteryInfo.model_validate({"available": True, "percent": 74.0, "charging": True})
        assert r.available is True
        assert r.percent == 74.0
        assert r.charging is True

    def test_battery_info_unavailable(self) -> None:
        """BatteryInfo(available=False) must validate without hardware fields."""
        r = BatteryInfo.model_validate({"available": False, "reason": "battery_unavailable"})
        assert r.available is False
        assert r.reason == "battery_unavailable"
        assert r.percent is None
        assert r.charging is None

    def test_battery_info_unavailable_minimal(self) -> None:
        r = BatteryInfo.model_validate({"available": False})
        assert r.available is False

    def test_disk_info_valid(self) -> None:
        r = DiskInfo.model_validate(
            {
                "disks": [
                    {
                        "mount_point": "C:\\",
                        "total_bytes": 512_000_000_000,
                        "available_bytes": 220_000_000_000,
                    }
                ]
            }
        )
        assert len(r.disks) == 1
        assert isinstance(r.disks[0], DiskVolume)
        assert r.disks[0].mount_point == "C:\\"

    def test_disk_info_empty_disks(self) -> None:
        r = DiskInfo.model_validate({"disks": []})
        assert r.disks == []

    def test_process_info_valid(self) -> None:
        r = ProcessInfo.model_validate(
            {
                "processes": [
                    {
                        "pid": 1234,
                        "name": "example.exe",
                        "cpu_percent": 12.4,
                        "memory_bytes": 524_288_000,
                    }
                ]
            }
        )
        assert len(r.processes) == 1
        assert isinstance(r.processes[0], ProcessItem)
        assert r.processes[0].pid == 1234

    def test_system_snapshot_valid(self) -> None:
        r = SystemSnapshot.model_validate(
            {
                "cpu": {"usage_percent": 10.0, "core_count": 8, "frequency_mhz": 3600},
                "memory": {
                    "total_bytes": 16_000_000_000,
                    "used_bytes": 4_000_000_000,
                    "available_bytes": 12_000_000_000,
                    "swap_total_bytes": 4_000_000_000,
                    "swap_used_bytes": 0,
                },
                "gpu": {"available": False, "reason": "telemetry_unavailable"},
                "battery": {"available": False, "reason": "battery_unavailable"},
                "disk": {
                    "disks": [
                        {
                            "mount_point": "C:\\",
                            "total_bytes": 512_000_000_000,
                            "available_bytes": 200_000_000_000,
                        }
                    ]
                },
                "processes": [
                    {
                        "pid": 4,
                        "name": "System",
                        "cpu_percent": 0.5,
                        "memory_bytes": 100_000,
                    }
                ],
            }
        )
        assert isinstance(r.cpu, CpuInfo)
        assert isinstance(r.memory, MemoryInfo)
        assert isinstance(r.gpu, GpuInfo)
        assert isinstance(r.battery, BatteryInfo)
        assert isinstance(r.disk, DiskInfo)
        assert isinstance(r.processes[0], ProcessItem)


# ---------------------------------------------------------------------------
# EngineClient behaviour tests
# ---------------------------------------------------------------------------


class TestEngineClientBehaviour:
    """Validate that EngineClient calls the correct IPC method and returns typed models."""

    def test_ping_calls_correct_method(self) -> None:
        async def _test() -> None:
            engine, fake = make_engine()
            fake.set_response("engine.ping", {"pong": True, "version": "0.1.0"})
            result = await engine.ping()

            assert fake.calls == [("engine.ping", {})]
            assert isinstance(result, PingResponse)
            assert result.pong is True

        run_async(_test())

    def test_status_calls_correct_method(self) -> None:
        async def _test() -> None:
            engine, fake = make_engine()
            fake.set_response("engine.status", {"engine": "tom-engine", "status": "running"})
            result = await engine.status()

            assert fake.calls == [("engine.status", {})]
            assert isinstance(result, StatusResponse)
            assert result.status == "running"

        run_async(_test())

    def test_get_cpu_calls_correct_method(self) -> None:
        async def _test() -> None:
            engine, fake = make_engine()
            fake.set_response(
                "system.cpu", {"usage_percent": 5.0, "core_count": 16, "frequency_mhz": 4000}
            )
            result = await engine.get_cpu()

            assert fake.calls == [("system.cpu", {})]
            assert isinstance(result, CpuInfo)
            assert result.core_count == 16

        run_async(_test())

    def test_get_memory_calls_correct_method(self) -> None:
        async def _test() -> None:
            engine, fake = make_engine()
            fake.set_response(
                "system.memory",
                {
                    "total_bytes": 16_000_000_000,
                    "used_bytes": 8_000_000_000,
                    "available_bytes": 8_000_000_000,
                    "swap_total_bytes": 0,
                    "swap_used_bytes": 0,
                },
            )
            result = await engine.get_memory()

            assert fake.calls == [("system.memory", {})]
            assert isinstance(result, MemoryInfo)
            assert result.total_bytes == 16_000_000_000

        run_async(_test())

    def test_get_gpu_available(self) -> None:
        async def _test() -> None:
            engine, fake = make_engine()
            fake.set_response(
                "system.gpu",
                {
                    "available": True,
                    "vendor": "nvidia",
                    "name": "RTX 4060",
                    "utilization_percent": 40.0,
                    "memory_total_bytes": 8_589_934_592,
                    "memory_used_bytes": 1_073_741_824,
                },
            )
            result = await engine.get_gpu()

            assert isinstance(result, GpuInfo)
            assert result.available is True
            assert result.name == "RTX 4060"

        run_async(_test())

    def test_get_gpu_unavailable(self) -> None:
        async def _test() -> None:
            engine, fake = make_engine()
            fake.set_response("system.gpu", {"available": False, "reason": "telemetry_unavailable"})
            result = await engine.get_gpu()

            assert isinstance(result, GpuInfo)
            assert result.available is False
            assert result.name is None

        run_async(_test())

    def test_get_battery_available(self) -> None:
        async def _test() -> None:
            engine, fake = make_engine()
            fake.set_response(
                "system.battery", {"available": True, "percent": 82.0, "charging": False}
            )
            result = await engine.get_battery()

            assert isinstance(result, BatteryInfo)
            assert result.available is True
            assert result.percent == 82.0
            assert result.charging is False

        run_async(_test())

    def test_get_battery_unavailable(self) -> None:
        async def _test() -> None:
            engine, fake = make_engine()
            fake.set_response(
                "system.battery", {"available": False, "reason": "battery_unavailable"}
            )
            result = await engine.get_battery()

            assert isinstance(result, BatteryInfo)
            assert result.available is False
            assert result.percent is None

        run_async(_test())

    def test_get_disk_calls_correct_method(self) -> None:
        async def _test() -> None:
            engine, fake = make_engine()
            fake.set_response(
                "system.disk",
                {
                    "disks": [
                        {
                            "mount_point": "C:\\",
                            "total_bytes": 512_000_000_000,
                            "available_bytes": 200_000_000_000,
                        }
                    ]
                },
            )
            result = await engine.get_disk()

            assert fake.calls == [("system.disk", {})]
            assert isinstance(result, DiskInfo)
            assert len(result.disks) == 1

        run_async(_test())

    def test_get_processes_default_limit(self) -> None:
        async def _test() -> None:
            engine, fake = make_engine()
            fake.set_response("system.processes", {"processes": []})
            await engine.get_processes()

            assert fake.calls[0][0] == "system.processes"
            assert fake.calls[0][1] == {"limit": 10}

        run_async(_test())

    def test_get_processes_custom_limit(self) -> None:
        async def _test() -> None:
            engine, fake = make_engine()
            fake.set_response(
                "system.processes",
                {
                    "processes": [
                        {
                            "pid": 100,
                            "name": "python.exe",
                            "cpu_percent": 5.0,
                            "memory_bytes": 50_000_000,
                        }
                    ]
                },
            )
            result = await engine.get_processes(limit=5)

            assert fake.calls[0][1] == {"limit": 5}
            assert isinstance(result, ProcessInfo)
            assert result.processes[0].name == "python.exe"

        run_async(_test())

    def test_get_system_snapshot_calls_correct_method(self) -> None:
        async def _test() -> None:
            engine, fake = make_engine()
            fake.set_response(
                "system.all",
                {
                    "cpu": {"usage_percent": 3.0, "core_count": 8, "frequency_mhz": 3000},
                    "memory": {
                        "total_bytes": 16_000_000_000,
                        "used_bytes": 4_000_000_000,
                        "available_bytes": 12_000_000_000,
                        "swap_total_bytes": 0,
                        "swap_used_bytes": 0,
                    },
                    "gpu": {"available": False, "reason": "telemetry_unavailable"},
                    "battery": {"available": False, "reason": "battery_unavailable"},
                    "disk": {
                        "disks": [
                            {
                                "mount_point": "C:\\",
                                "total_bytes": 512_000_000_000,
                                "available_bytes": 200_000_000_000,
                            }
                        ]
                    },
                    "processes": [
                        {
                            "pid": 4,
                            "name": "System",
                            "cpu_percent": 0.1,
                            "memory_bytes": 10_000,
                        }
                    ],
                },
            )
            result = await engine.get_system_snapshot()

            assert fake.calls == [("system.all", {})]
            assert isinstance(result, SystemSnapshot)
            assert isinstance(result.cpu, CpuInfo)
            assert isinstance(result.gpu, GpuInfo)
            assert result.gpu.available is False

        run_async(_test())

    def test_result_is_typed_model_not_dict(self) -> None:
        """EngineClient must never return a raw dict to callers."""

        async def _test() -> None:
            engine, fake = make_engine()
            fake.set_response("engine.ping", {"pong": True, "version": "0.1.0"})
            result = await engine.ping()

            # Must be a PingResponse, never a dict
            assert not isinstance(result, dict)
            assert isinstance(result, PingResponse)

        run_async(_test())

    def test_multiple_independent_calls_do_not_interfere(self) -> None:
        async def _test() -> None:
            engine, fake = make_engine()
            fake.set_response("engine.ping", {"pong": True, "version": "0.1.0"})
            fake.set_response("engine.status", {"engine": "tom-engine", "status": "running"})

            ping = await engine.ping()
            status = await engine.status()

            assert isinstance(ping, PingResponse)
            assert isinstance(status, StatusResponse)
            assert len(fake.calls) == 2

        run_async(_test())


# ---------------------------------------------------------------------------
# Error propagation tests
# ---------------------------------------------------------------------------


class TestEngineClientErrorPropagation:
    """IPC exceptions must propagate unchanged through EngineClient."""

    def test_not_connected_error_propagates(self) -> None:
        async def _test() -> None:
            engine, fake = make_engine()
            fake.set_error("engine.ping", NotConnectedError("not connected"))

            with pytest.raises(NotConnectedError):
                await engine.ping()

        run_async(_test())

    def test_timeout_error_propagates(self) -> None:
        async def _test() -> None:
            engine, fake = make_engine()
            fake.set_error("system.cpu", IpcTimeoutError("system.cpu", 5000))

            with pytest.raises(IpcTimeoutError) as exc_info:
                await engine.get_cpu()

            assert exc_info.value.method == "system.cpu"

        run_async(_test())

    def test_connection_lost_error_propagates(self) -> None:
        async def _test() -> None:
            engine, fake = make_engine()
            fake.set_error("system.memory", ConnectionLostError("pipe closed"))

            with pytest.raises(ConnectionLostError):
                await engine.get_memory()

        run_async(_test())

    def test_remote_error_propagates(self) -> None:
        async def _test() -> None:
            engine, fake = make_engine()
            fake.set_error("system.gpu", RemoteError("NOT_FOUND", "handler not registered"))

            with pytest.raises(RemoteError) as exc_info:
                await engine.get_gpu()

            assert exc_info.value.code == "NOT_FOUND"

        run_async(_test())

    def test_cancellation_propagates(self) -> None:
        async def _test() -> None:
            engine, fake = make_engine()
            fake.set_error("system.all", asyncio.CancelledError())

            with pytest.raises(asyncio.CancelledError):
                await engine.get_system_snapshot()

        run_async(_test())

    def test_invalid_response_data_raises_validation_error(self) -> None:
        """A response with missing required fields must raise ValidationError."""

        async def _test() -> None:
            engine, fake = make_engine()
            # Missing required 'core_count' and 'frequency_mhz'
            fake.set_response("system.cpu", {"usage_percent": 10.0})

            with pytest.raises(ValidationError):
                await engine.get_cpu()

        run_async(_test())
