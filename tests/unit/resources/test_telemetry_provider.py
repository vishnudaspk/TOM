"""Unit tests for EngineTelemetryProvider.

Verifies:
- Rust Named Pipe IPC telemetry byte-to-MB conversions
- Caching TTL behavior (250 ms)
- Single-flight refresh suppression
- Graceful fallback on IPC connection error or missing hardware
- Synchronous get_telemetry() compatibility
"""

from __future__ import annotations

import asyncio

import pytest
from tom.ipc.protocol import GpuInfo, MemoryInfo
from tom.resources.manager import (
    EngineTelemetryProvider,
    MockTelemetryProvider,
)


class MockEngineClient:
    """Mock for EngineClient providing get_gpu and get_memory IPC methods."""

    def __init__(
        self,
        gpu: GpuInfo | None = None,
        memory: MemoryInfo | None = None,
        should_fail: bool = False,
    ) -> None:
        self.gpu = gpu or GpuInfo(
            available=True,
            vendor="NVIDIA",
            name="NVIDIA GeForce RTX 4060 Laptop GPU",
            utilization_percent=15.0,
            memory_total_bytes=8192 * 1024 * 1024,
            memory_used_bytes=1000 * 1024 * 1024,
        )
        self.memory = memory or MemoryInfo(
            total_bytes=16384 * 1024 * 1024,
            used_bytes=6000 * 1024 * 1024,
            available_bytes=10384 * 1024 * 1024,
            swap_total_bytes=0,
            swap_used_bytes=0,
        )
        self.should_fail = should_fail
        self.gpu_call_count = 0
        self.memory_call_count = 0

    async def get_gpu(self) -> GpuInfo:
        self.gpu_call_count += 1
        if self.should_fail:
            raise ConnectionError("Rust IPC engine unreachable")
        return self.gpu

    async def get_memory(self) -> MemoryInfo:
        self.memory_call_count += 1
        if self.should_fail:
            raise ConnectionError("Rust IPC engine unreachable")
        return self.memory


class TestEngineTelemetryProvider:
    @pytest.mark.anyio
    async def test_byte_to_mb_conversion(self) -> None:
        engine = MockEngineClient()
        fallback = MockTelemetryProvider(gpu_available=False)
        provider = EngineTelemetryProvider(
            engine_client=engine,
            fallback_provider=fallback,
            cache_ttl_seconds=0.250,
        )

        telem = await provider.refresh_snapshot()
        assert telem.gpu_available is True
        assert telem.vram_total_mb == 8192.0
        assert telem.vram_used_mb == 1000.0
        assert telem.vram_free_mb == 7192.0
        assert telem.ram_total_mb == 16384.0
        assert telem.ram_used_mb == 6000.0
        assert telem.ram_free_mb == 10384.0

    @pytest.mark.anyio
    async def test_cache_ttl_and_single_flight(self) -> None:
        engine = MockEngineClient()
        provider = EngineTelemetryProvider(
            engine_client=engine,
            cache_ttl_seconds=0.100,
        )

        # First refresh calls engine
        t1 = await provider.refresh_snapshot()
        assert engine.gpu_call_count == 1
        assert engine.memory_call_count == 1

        # Immediate second refresh returns cached snapshot without IPC call
        t2 = await provider.refresh_snapshot()
        assert engine.gpu_call_count == 1
        assert t2.vram_total_mb == t1.vram_total_mb

        # Synchronous getter returns cached snapshot
        sync_telem = provider.get_telemetry()
        assert sync_telem.vram_total_mb == 8192.0

        # Wait for TTL to expire
        await asyncio.sleep(0.110)
        _ = await provider.refresh_snapshot()
        assert engine.gpu_call_count == 2
        assert engine.memory_call_count == 2

    @pytest.mark.anyio
    async def test_fallback_on_ipc_error(self) -> None:
        engine = MockEngineClient(should_fail=True)
        fallback = MockTelemetryProvider(
            vram_total_mb=4096.0,
            vram_free_mb=2048.0,
            gpu_available=True,
        )
        provider = EngineTelemetryProvider(
            engine_client=engine,
            fallback_provider=fallback,
        )

        # Refresh catches IPC error and falls back gracefully
        telem = await provider.refresh_snapshot()
        assert telem.vram_total_mb == 4096.0
        assert telem.vram_free_mb == 2048.0
        assert provider.get_telemetry().vram_total_mb == 4096.0

    @pytest.mark.anyio
    async def test_gpu_unavailable_wire_shape(self) -> None:
        engine = MockEngineClient(
            gpu=GpuInfo(available=False, reason="No compatible NVML device found")
        )
        provider = EngineTelemetryProvider(engine_client=engine)

        telem = await provider.refresh_snapshot()
        assert telem.gpu_available is False
        assert telem.vram_total_mb == 0.0
        assert telem.vram_free_mb == 0.0
        assert telem.ram_total_mb == 16384.0

    def test_uninitialized_sync_get_telemetry_falls_back(self) -> None:
        engine = MockEngineClient()
        fallback = MockTelemetryProvider(
            vram_total_mb=7200.0,
            vram_free_mb=5000.0,
            gpu_available=True,
        )
        provider = EngineTelemetryProvider(
            engine_client=engine,
            fallback_provider=fallback,
        )

        # Without prior refresh, get_telemetry returns fallback
        telem = provider.get_telemetry()
        assert telem.vram_total_mb == 7200.0
        assert telem.vram_free_mb == 5000.0
