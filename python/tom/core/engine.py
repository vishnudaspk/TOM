"""High-level Engine Client for TOM.

Provides `EngineClient` — a thin, typed domain wrapper around `NamedPipeIpcClient`.

Callers receive strongly typed Pydantic models rather than raw IPC dictionaries.
All IPC mechanics (timeouts, correlation, reconnection, cancellation, logging)
remain entirely in the layers below; `EngineClient` only:

  1. Chooses the correct IPC method name and parameters.
  2. Calls `NamedPipeIpcClient.request()`.
  3. Validates the response dict with the appropriate Pydantic model.
  4. Returns the typed model.

Architecture:
    EngineClient
        │
        ▼
    NamedPipeIpcClient
        │
        ▼
    NamedPipeTransport
        │
        ▼
    Windows Named Pipe \\\\.\\pipe\\tom-engine
"""

from tom.ipc.client import NamedPipeIpcClient
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

# Default process limit mirrors Rust DEFAULT_PROCESS_LIMIT
_DEFAULT_PROCESS_LIMIT: int = 10


class EngineClient:
    """Typed high-level API over the `NamedPipeIpcClient`.

    All methods delegate directly to the underlying IPC client and return
    validated Pydantic models.  IPC exceptions (NotConnectedError,
    IpcTimeoutError, ConnectionLostError, RemoteError, CancelledError, …)
    propagate unchanged to the caller.

    Args:
        ipc_client: A `NamedPipeIpcClient` instance.  The caller is
            responsible for connecting and closing it.
    """

    def __init__(self, ipc_client: NamedPipeIpcClient) -> None:
        self._ipc = ipc_client

    @property
    def ipc(self) -> NamedPipeIpcClient:
        """Return the underlying IPC client."""
        return self._ipc

    async def close(self) -> None:
        """Close the underlying IPC client connection."""
        await self._ipc.close()

    # ------------------------------------------------------------------
    # Engine endpoints
    # ------------------------------------------------------------------

    async def ping(self) -> PingResponse:
        """Confirm engine liveness and retrieve its version string.

        IPC method: engine.ping
        Returns: PingResponse(pong=True, version=str)
        """
        data = await self._ipc.request("engine.ping")
        return PingResponse.model_validate(data)

    async def status(self) -> StatusResponse:
        """Retrieve high-level engine identity and health status.

        IPC method: engine.status
        Returns: StatusResponse(engine=str, status="running")
        """
        data = await self._ipc.request("engine.status")
        return StatusResponse.model_validate(data)

    # ------------------------------------------------------------------
    # System telemetry endpoints
    # ------------------------------------------------------------------

    async def get_cpu(self) -> CpuInfo:
        """Retrieve structured CPU telemetry.

        IPC method: system.cpu
        Returns: CpuInfo(usage_percent, core_count, frequency_mhz)
        """
        data = await self._ipc.request("system.cpu")
        return CpuInfo.model_validate(data)

    async def get_memory(self) -> MemoryInfo:
        """Retrieve structured memory telemetry.

        IPC method: system.memory
        Returns: MemoryInfo(total_bytes, used_bytes, available_bytes,
                             swap_total_bytes, swap_used_bytes)
        """
        data = await self._ipc.request("system.memory")
        return MemoryInfo.model_validate(data)

    async def get_gpu(self) -> GpuInfo:
        """Retrieve GPU telemetry with graceful fallback for absent hardware.

        IPC method: system.gpu
        Returns: GpuInfo — available=False when no GPU/NVML is present.
        """
        data = await self._ipc.request("system.gpu")
        return GpuInfo.model_validate(data)

    async def get_battery(self) -> BatteryInfo:
        """Retrieve battery telemetry with graceful fallback for desktop systems.

        IPC method: system.battery
        Returns: BatteryInfo — available=False when no battery is detected.
        """
        data = await self._ipc.request("system.battery")
        return BatteryInfo.model_validate(data)

    async def get_disk(self) -> DiskInfo:
        """Retrieve disk capacity metrics for all mounted volumes.

        IPC method: system.disk
        Returns: DiskInfo(disks=[DiskVolume(...), ...])
        """
        data = await self._ipc.request("system.disk")
        return DiskInfo.model_validate(data)

    async def get_processes(self, limit: int = _DEFAULT_PROCESS_LIMIT) -> ProcessInfo:
        """Retrieve top processes sorted by CPU usage.

        IPC method: system.processes
        Args:
            limit: Maximum number of processes to return (default 10).
        Returns: ProcessInfo(processes=[ProcessItem(...), ...])
        """
        data = await self._ipc.request("system.processes", {"limit": limit})
        return ProcessInfo.model_validate(data)

    async def get_system_snapshot(self) -> SystemSnapshot:
        """Retrieve an aggregated snapshot of all system telemetry domains.

        IPC method: system.all
        Returns: SystemSnapshot(cpu, memory, gpu, battery, disk, processes)
        """
        data = await self._ipc.request("system.all")
        return SystemSnapshot.model_validate(data)
