"""Unit tests for TOM Deterministic System Telemetry Tools."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from tom.ipc.protocol import (
    BatteryInfo,
    CpuInfo,
    DiskInfo,
    DiskVolume,
    GpuInfo,
    MemoryInfo,
    ProcessInfo,
    ProcessItem,
    SystemSnapshot,
)
from tom.security.permissions import PermissionLevel
from tom.tools.executor import ToolExecutor
from tom.tools.registry import ToolRegistry
from tom.tools.system import (
    register_system_tools,
)


def run_async(coro: Any) -> Any:
    """Helper to execute async coroutines in tests."""
    return asyncio.run(coro)


class MockEngineClient:
    """Mock EngineClient returning deterministic telemetry fixtures."""

    def __init__(self) -> None:
        self.get_cpu = AsyncMock(
            return_value=CpuInfo(usage_percent=25.5, core_count=8, frequency_mhz=3200)
        )
        self.get_memory = AsyncMock(
            return_value=MemoryInfo(
                total_bytes=16_000_000_000,
                used_bytes=8_000_000_000,
                available_bytes=8_000_000_000,
                swap_total_bytes=4_000_000_000,
                swap_used_bytes=1_000_000_000,
            )
        )
        self.get_gpu = AsyncMock(
            return_value=GpuInfo(
                available=True,
                name="NVIDIA GeForce RTX 4060",
                temperature_c=55.0,
                utilization_percent=30.0,
                vram_total_mb=8192,
                vram_used_mb=2048,
            )
        )
        self.get_battery = AsyncMock(
            return_value=BatteryInfo(available=True, percent=85.0, charging=False)
        )
        self.get_disk = AsyncMock(
            return_value=DiskInfo(
                disks=[
                    DiskVolume(
                        mount_point="C:\\",
                        total_bytes=500_000_000_000,
                        used_bytes=200_000_000_000,
                        available_bytes=300_000_000_000,
                    )
                ]
            )
        )
        self.get_processes = AsyncMock(
            return_value=ProcessInfo(
                processes=[
                    ProcessItem(
                        pid=1001, name="tom-engine.exe", cpu_percent=2.5, memory_bytes=50_000_000
                    ),
                    ProcessItem(
                        pid=1002, name="python.exe", cpu_percent=1.0, memory_bytes=30_000_000
                    ),
                ]
            )
        )
        self.get_system_snapshot = AsyncMock(
            return_value=SystemSnapshot(
                cpu=CpuInfo(usage_percent=25.5, core_count=8, frequency_mhz=3200),
                memory=MemoryInfo(
                    total_bytes=16_000_000_000,
                    used_bytes=8_000_000_000,
                    available_bytes=8_000_000_000,
                    swap_total_bytes=4_000_000_000,
                    swap_used_bytes=1_000_000_000,
                ),
                gpu=GpuInfo(available=True, name="NVIDIA GeForce RTX 4060"),
                battery=BatteryInfo(available=True, percent=85.0, charging=False),
                disk=DiskInfo(disks=[]),
                processes=[],
            )
        )


class TestSystemToolsWithEngineClient:
    """Test deterministic system tools backed by EngineClient."""

    @pytest.fixture
    def mock_client(self) -> MockEngineClient:
        return MockEngineClient()

    @pytest.fixture
    def registry(self, mock_client: MockEngineClient) -> ToolRegistry:
        reg = ToolRegistry()
        register_system_tools(registry=reg, client=mock_client)  # type: ignore[arg-type]
        return reg

    def test_registered_tool_metadata(self, registry: ToolRegistry) -> None:
        assert len(registry) == 7
        for name in [
            "system.cpu_info",
            "system.memory_info",
            "system.gpu_info",
            "system.battery_info",
            "system.disk_info",
            "system.list_processes",
            "system.get_snapshot",
        ]:
            tool_def = registry.get(name)
            assert tool_def.category == "system"
            assert tool_def.permission_level is PermissionLevel.SAFE
            assert tool_def.handler is not None

    def test_cpu_info_with_client(
        self, registry: ToolRegistry, mock_client: MockEngineClient
    ) -> None:
        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("system.cpu_info", {}))
        assert res.success is True
        assert isinstance(res.data, CpuInfo)
        assert res.data.usage_percent == 25.5
        assert res.data.core_count == 8
        mock_client.get_cpu.assert_awaited_once()

    def test_memory_info_with_client(
        self, registry: ToolRegistry, mock_client: MockEngineClient
    ) -> None:
        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("system.memory_info", {}))
        assert res.success is True
        assert isinstance(res.data, MemoryInfo)
        assert res.data.total_bytes == 16_000_000_000
        mock_client.get_memory.assert_awaited_once()

    def test_gpu_info_with_client(
        self, registry: ToolRegistry, mock_client: MockEngineClient
    ) -> None:
        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("system.gpu_info", {}))
        assert res.success is True
        assert isinstance(res.data, GpuInfo)
        assert res.data.name == "NVIDIA GeForce RTX 4060"
        mock_client.get_gpu.assert_awaited_once()

    def test_battery_info_with_client(
        self, registry: ToolRegistry, mock_client: MockEngineClient
    ) -> None:
        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("system.battery_info", {}))
        assert res.success is True
        assert isinstance(res.data, BatteryInfo)
        assert res.data.percent == 85.0
        mock_client.get_battery.assert_awaited_once()

    def test_disk_info_with_client(
        self, registry: ToolRegistry, mock_client: MockEngineClient
    ) -> None:
        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("system.disk_info", {}))
        assert res.success is True
        assert isinstance(res.data, DiskInfo)
        assert len(res.data.disks) == 1
        mock_client.get_disk.assert_awaited_once()

    def test_list_processes_with_client(
        self, registry: ToolRegistry, mock_client: MockEngineClient
    ) -> None:
        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("system.list_processes", {"limit": 5}))
        assert res.success is True
        assert isinstance(res.data, ProcessInfo)
        assert len(res.data.processes) == 2
        mock_client.get_processes.assert_awaited_once_with(limit=5)

    def test_get_snapshot_with_client(
        self, registry: ToolRegistry, mock_client: MockEngineClient
    ) -> None:
        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("system.get_snapshot", {}))
        assert res.success is True
        assert isinstance(res.data, SystemSnapshot)
        assert res.data.cpu.usage_percent == 25.5
        mock_client.get_system_snapshot.assert_awaited_once()


class TestSystemToolsFallback:
    """Test deterministic system tools fallback to psutil/stdlib when no engine is connected."""

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        reg = ToolRegistry()
        register_system_tools(registry=reg, client=None)
        return reg

    def test_fallback_cpu_info(self, registry: ToolRegistry) -> None:
        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("system.cpu_info", {}))
        assert res.success is True
        assert isinstance(res.data, CpuInfo)
        assert res.data.core_count >= 1

    def test_fallback_memory_info(self, registry: ToolRegistry) -> None:
        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("system.memory_info", {}))
        assert res.success is True
        assert isinstance(res.data, MemoryInfo)
        assert res.data.total_bytes > 0

    def test_fallback_gpu_info(self, registry: ToolRegistry) -> None:
        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("system.gpu_info", {}))
        assert res.success is True
        assert isinstance(res.data, GpuInfo)
        assert res.data.available is False

    def test_fallback_battery_info(self, registry: ToolRegistry) -> None:
        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("system.battery_info", {}))
        assert res.success is True
        assert isinstance(res.data, BatteryInfo)

    def test_fallback_disk_info(self, registry: ToolRegistry) -> None:
        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("system.disk_info", {}))
        assert res.success is True
        assert isinstance(res.data, DiskInfo)

    def test_fallback_list_processes(self, registry: ToolRegistry) -> None:
        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("system.list_processes", {"limit": 3}))
        assert res.success is True
        assert isinstance(res.data, ProcessInfo)
        assert len(res.data.processes) <= 3

    def test_fallback_snapshot(self, registry: ToolRegistry) -> None:
        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("system.get_snapshot", {}))
        assert res.success is True
        assert isinstance(res.data, SystemSnapshot)
        assert res.data.cpu.core_count >= 1

    def test_invalid_arguments_validation(self, registry: ToolRegistry) -> None:
        executor = ToolExecutor(registry=registry)
        # limit cannot be <= 0
        res = run_async(executor.execute("system.list_processes", {"limit": 0}))
        assert res.success is False
        assert "validation failed" in res.error.lower()
