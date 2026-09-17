"""TOM Deterministic System Telemetry Tools.

This module provides typed, deterministic inspection tools for host telemetry:
- system.cpu_info: Core count, frequency, utilization.
- system.memory_info: Total, used, available RAM and swap.
- system.gpu_info: Utilization, VRAM, temperature (graceful fallback if absent).
- system.battery_info: Battery percentage and charging status (graceful fallback for desktops).
- system.disk_info: Mounted volumes and capacity.
- system.list_processes: Top processes ranked by CPU usage.
- system.get_snapshot: Aggregated system telemetry snapshot.

All system tools are classified as SAFE. They leverage Phase 2's `EngineClient` when
connected to tom-engine, with seamless fallback to `psutil`/standard library for standalone
operation and unit testing.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import TYPE_CHECKING

try:
    import psutil
except ImportError:
    psutil = None

from pydantic import BaseModel, ConfigDict, Field

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
from tom.tools.registry import ToolDefinition, ToolRegistry, default_registry

if TYPE_CHECKING:
    from tom.core.engine import EngineClient


# ---------------------------------------------------------------------------
# Input Schemas
# ---------------------------------------------------------------------------


class EmptyInput(BaseModel):
    """Empty input schema for zero-argument system telemetry tools."""

    model_config = ConfigDict(extra="forbid")


class CpuInfoInput(EmptyInput):
    """Input parameters for system.cpu_info."""


class MemoryInfoInput(EmptyInput):
    """Input parameters for system.memory_info."""


class GpuInfoInput(EmptyInput):
    """Input parameters for system.gpu_info."""


class BatteryInfoInput(EmptyInput):
    """Input parameters for system.battery_info."""


class DiskInfoInput(EmptyInput):
    """Input parameters for system.disk_info."""


class ProcessListInput(BaseModel):
    """Input parameters for system.list_processes."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of top processes to return (ranked by CPU usage)",
    )


class SystemSnapshotInput(EmptyInput):
    """Input parameters for system.get_snapshot."""


# ---------------------------------------------------------------------------
# Fallback Telemetry Providers (Standard Library / psutil)
# ---------------------------------------------------------------------------


def _fallback_cpu() -> CpuInfo:
    if psutil is not None:
        usage = psutil.cpu_percent(interval=None)
        cores = psutil.cpu_count(logical=True) or 1
        freq = psutil.cpu_freq()
        freq_mhz = int(freq.current) if freq else 0
        return CpuInfo(usage_percent=usage, core_count=cores, frequency_mhz=freq_mhz)
    cores = os.cpu_count() or 1
    return CpuInfo(usage_percent=0.0, core_count=cores, frequency_mhz=0)


def _fallback_memory() -> MemoryInfo:
    if psutil is not None:
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return MemoryInfo(
            total_bytes=mem.total,
            used_bytes=mem.used,
            available_bytes=mem.available,
            swap_total_bytes=swap.total,
            swap_used_bytes=swap.used,
        )
    return MemoryInfo(
        total_bytes=16_000_000_000,
        used_bytes=8_000_000_000,
        available_bytes=8_000_000_000,
        swap_total_bytes=0,
        swap_used_bytes=0,
    )


def _fallback_gpu() -> GpuInfo:
    return GpuInfo(available=False)


def _fallback_battery() -> BatteryInfo:
    if psutil is not None:
        bat = psutil.sensors_battery()
        if bat is not None:
            return BatteryInfo(
                available=True,
                percent=float(bat.percent),
                charging=bat.power_plugged if bat.power_plugged is not None else False,
            )
    return BatteryInfo(available=False)


def _fallback_disk() -> DiskInfo:
    if psutil is not None:
        volumes: list[DiskVolume] = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                volumes.append(
                    DiskVolume(
                        mount_point=part.mountpoint,
                        total_bytes=usage.total,
                        used_bytes=usage.used,
                        available_bytes=usage.free,
                    )
                )
            except (PermissionError, OSError):
                continue
        if volumes:
            return DiskInfo(disks=volumes)

    try:
        root_path = "C:\\" if sys.platform == "win32" else "/"
        usage = shutil.disk_usage(root_path)
        return DiskInfo(
            disks=[
                DiskVolume(
                    mount_point=root_path,
                    total_bytes=usage.total,
                    used_bytes=usage.used,
                    available_bytes=usage.free,
                )
            ]
        )
    except OSError:
        return DiskInfo(disks=[])


def _fallback_processes(limit: int) -> ProcessInfo:
    if psutil is not None:
        procs: list[ProcessItem] = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
            try:
                info = p.info
                mem_bytes = info["memory_info"].rss if info.get("memory_info") else 0
                procs.append(
                    ProcessItem(
                        pid=info["pid"],
                        name=info["name"] or "unknown",
                        cpu_percent=float(info["cpu_percent"] or 0.0),
                        memory_bytes=mem_bytes,
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        procs.sort(key=lambda x: x.cpu_percent, reverse=True)
        return ProcessInfo(processes=procs[:limit])

    return ProcessInfo(
        processes=[
            ProcessItem(
                pid=os.getpid(),
                name="python",
                cpu_percent=0.0,
                memory_bytes=0,
            )
        ]
    )


def _fallback_snapshot() -> SystemSnapshot:
    return SystemSnapshot(
        cpu=_fallback_cpu(),
        memory=_fallback_memory(),
        gpu=_fallback_gpu(),
        battery=_fallback_battery(),
        disk=_fallback_disk(),
        processes=_fallback_processes(10).processes,
    )


# ---------------------------------------------------------------------------
# Tool Factory & Registration
# ---------------------------------------------------------------------------


def create_system_tools(
    client: EngineClient | None = None,
) -> list[ToolDefinition]:
    """Create the suite of deterministic system inspection tool definitions.

    Args:
        client: Optional EngineClient. If None or disconnected, tools fall back
                to standard library and psutil telemetry.

    Returns:
        List of ToolDefinition instances.
    """

    async def cpu_info(_params: CpuInfoInput | None = None) -> CpuInfo:
        if client is not None:
            return await client.get_cpu()
        return _fallback_cpu()

    async def memory_info(_params: MemoryInfoInput | None = None) -> MemoryInfo:
        if client is not None:
            return await client.get_memory()
        return _fallback_memory()

    async def gpu_info(_params: GpuInfoInput | None = None) -> GpuInfo:
        if client is not None:
            return await client.get_gpu()
        return _fallback_gpu()

    async def battery_info(_params: BatteryInfoInput | None = None) -> BatteryInfo:
        if client is not None:
            return await client.get_battery()
        return _fallback_battery()

    async def disk_info(_params: DiskInfoInput | None = None) -> DiskInfo:
        if client is not None:
            return await client.get_disk()
        return _fallback_disk()

    async def list_processes(params: ProcessListInput | None = None) -> ProcessInfo:
        limit = params.limit if params is not None else 10
        if client is not None:
            return await client.get_processes(limit=limit)
        return _fallback_processes(limit=limit)

    async def get_snapshot(_params: SystemSnapshotInput | None = None) -> SystemSnapshot:
        if client is not None:
            return await client.get_system_snapshot()
        return _fallback_snapshot()

    return [
        ToolDefinition(
            name="system.cpu_info",
            description="Query host CPU core count, frequency, and current utilization percentage.",
            category="system",
            input_schema=CpuInfoInput,
            output_schema=CpuInfo,
            permission_level=PermissionLevel.SAFE,
            handler=cpu_info,
        ),
        ToolDefinition(
            name="system.memory_info",
            description="Query host physical RAM and swap memory capacity and utilization.",
            category="system",
            input_schema=MemoryInfoInput,
            output_schema=MemoryInfo,
            permission_level=PermissionLevel.SAFE,
            handler=memory_info,
        ),
        ToolDefinition(
            name="system.gpu_info",
            description="Query GPU utilization, VRAM usage, and temperature (gracefully degrades if absent).",
            category="system",
            input_schema=GpuInfoInput,
            output_schema=GpuInfo,
            permission_level=PermissionLevel.SAFE,
            handler=gpu_info,
        ),
        ToolDefinition(
            name="system.battery_info",
            description="Query system battery level and charging status (gracefully degrades on desktops).",
            category="system",
            input_schema=BatteryInfoInput,
            output_schema=BatteryInfo,
            permission_level=PermissionLevel.SAFE,
            handler=battery_info,
        ),
        ToolDefinition(
            name="system.disk_info",
            description="Query disk capacity and free space across all mounted filesystem volumes.",
            category="system",
            input_schema=DiskInfoInput,
            output_schema=DiskInfo,
            permission_level=PermissionLevel.SAFE,
            handler=disk_info,
        ),
        ToolDefinition(
            name="system.list_processes",
            description="List top running processes ranked by CPU consumption.",
            category="system",
            input_schema=ProcessListInput,
            output_schema=ProcessInfo,
            permission_level=PermissionLevel.SAFE,
            handler=list_processes,
        ),
        ToolDefinition(
            name="system.get_snapshot",
            description="Retrieve an aggregated telemetry snapshot covering CPU, memory, GPU, battery, disk, and processes.",
            category="system",
            input_schema=SystemSnapshotInput,
            output_schema=SystemSnapshot,
            permission_level=PermissionLevel.SAFE,
            handler=get_snapshot,
        ),
    ]


def register_system_tools(
    registry: ToolRegistry | None = None,
    client: EngineClient | None = None,
    replace: bool = True,
) -> list[ToolDefinition]:
    """Register all deterministic system inspection tools into a ToolRegistry.

    Args:
        registry: Target ToolRegistry. Defaults to the global default_registry.
        client: Optional EngineClient.
        replace: Whether to replace existing registrations.

    Returns:
        List of registered ToolDefinitions.
    """
    target_registry = registry if registry is not None else default_registry
    tools = create_system_tools(client=client)
    for t in tools:
        target_registry.register(t, replace=replace)
    return tools
