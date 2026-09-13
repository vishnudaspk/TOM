"""IPC protocol schema and wire constants for TOM.

Strongly typed Pydantic v2 models mirroring the Rust engine IPC protocol v1:
- Protocol constants (version, pipe name, max frame size)
- Standard error codes
- Request and response models
- Connection state definitions
"""

import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROTOCOL_VERSION: int = 1
PIPE_NAME: str = r"\\.\pipe\tom-engine"
MAX_FRAME_BYTES: int = 1_048_576  # 1 MB


class ErrorCode(StrEnum):
    """Standard error codes defined across the Python <-> Rust IPC boundary."""

    NOT_FOUND = "NOT_FOUND"
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_PARAMS = "INVALID_PARAMS"
    TIMEOUT = "TIMEOUT"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    INTERNAL = "INTERNAL"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class ConnectionState(StrEnum):
    """IPC client connection states for lifecycle and reconnection tracking."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    CLOSED = "closed"


def new_request_id() -> str:
    """Generate a unique, non-empty correlation request ID."""
    return f"req_{uuid.uuid4().hex[:12]}"


class IpcRequest(BaseModel):
    """Inbound IPC request payload sent from Python to Rust.

    Enforces strict validation and forbids extra fields to prevent
    unintended parameters on the wire.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="Correlation request ID")
    version: int = Field(default=PROTOCOL_VERSION, ge=1, le=1, description="Protocol version")
    method: str = Field(..., min_length=1, description="Registered IPC method name")
    params: dict[str, Any] = Field(default_factory=dict, description="Method invocation parameters")

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("request id must not be empty or whitespace")
        return v

    @field_validator("method")
    @classmethod
    def validate_method(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("request method must not be empty or whitespace")
        return v


class IpcError(BaseModel):
    """Structured IPC error returned by Rust when success is false.

    Uses extra='ignore' for forward compatibility with future engine metadata.
    """

    model_config = ConfigDict(extra="ignore")

    code: str = Field(..., description="Standardized error code")
    message: str = Field(..., description="Human-readable error explanation")


class IpcResponse(BaseModel):
    """Outbound IPC response payload returned from Rust to Python.

    On success, `data` is present and `error` is None.
    On failure, `error` is present and `data` is None.
    Uses extra='ignore' to allow forward-compatible extension of engine response fields.
    """

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="Correlated request ID")
    version: int = Field(..., description="Protocol version of response")
    success: bool = Field(..., description="Whether the request succeeded")
    data: dict[str, Any] | None = Field(default=None, description="Result payload on success")
    error: IpcError | None = Field(default=None, description="Error details on failure")


# ---------------------------------------------------------------------------
# Typed telemetry response models — mirrors Rust wire contract exactly.
# All models use extra="ignore" for forward compatibility.
# ---------------------------------------------------------------------------


class PingResponse(BaseModel):
    """Response model for engine.ping.

    Wire: {"pong": bool, "version": str}
    """

    model_config = ConfigDict(extra="ignore")

    pong: bool
    version: str


class StatusResponse(BaseModel):
    """Response model for engine.status.

    Wire: {"engine": str, "status": str}
    """

    model_config = ConfigDict(extra="ignore")

    engine: str
    status: str


class CpuInfo(BaseModel):
    """Structured CPU telemetry snapshot.

    Wire: {"usage_percent": f64, "core_count": usize, "frequency_mhz": u64}
    """

    model_config = ConfigDict(extra="ignore")

    usage_percent: float
    core_count: int
    frequency_mhz: int


class MemoryInfo(BaseModel):
    """Structured memory telemetry snapshot (all sizes in bytes).

    Wire: {"total_bytes": u64, "used_bytes": u64, "available_bytes": u64,
           "swap_total_bytes": u64, "swap_used_bytes": u64}
    """

    model_config = ConfigDict(extra="ignore")

    total_bytes: int
    used_bytes: int
    available_bytes: int
    swap_total_bytes: int
    swap_used_bytes: int


class GpuInfo(BaseModel):
    """Structured GPU telemetry — flat model accommodating both Rust wire shapes.

    Rust serializes GpuInfo as an untagged enum:
      Available:   {"available": true, "vendor": str, "name": str,
                    "utilization_percent": f64, "memory_total_bytes": u64,
                    "memory_used_bytes": u64}
      Unavailable: {"available": false, "reason": str}

    Python uses a single flat model with all hardware fields Optional so both
    shapes validate without discriminator complexity.
    """

    model_config = ConfigDict(extra="ignore")

    available: bool
    # Fields present only when available=True
    vendor: str | None = None
    name: str | None = None
    utilization_percent: float | None = None
    memory_total_bytes: int | None = None
    memory_used_bytes: int | None = None
    # Field present only when available=False
    reason: str | None = None


class BatteryInfo(BaseModel):
    """Structured battery telemetry — flat model accommodating both Rust wire shapes.

    Rust serializes BatteryInfo as an untagged enum:
      Available:   {"available": true, "percent": f64, "charging": bool}
      Unavailable: {"available": false, "reason": str}

    Python uses a flat model with hardware fields Optional.
    """

    model_config = ConfigDict(extra="ignore")

    available: bool
    # Fields present only when available=True
    percent: float | None = None
    charging: bool | None = None
    # Field present only when available=False
    reason: str | None = None


class DiskVolume(BaseModel):
    """Information about a single disk volume or mount point.

    Mirrors Rust DiskItem wire fields:
    {"mount_point": str, "total_bytes": u64, "available_bytes": u64}
    """

    model_config = ConfigDict(extra="ignore")

    mount_point: str
    total_bytes: int
    available_bytes: int


class DiskInfo(BaseModel):
    """Structured disk telemetry snapshot.

    Wire: {"disks": [DiskVolume, ...]}
    """

    model_config = ConfigDict(extra="ignore")

    disks: list[DiskVolume]


class ProcessItem(BaseModel):
    """Information about an individual running process.

    Wire: {"pid": u32, "name": str, "cpu_percent": f64, "memory_bytes": u64}
    """

    model_config = ConfigDict(extra="ignore")

    pid: int
    name: str
    cpu_percent: float
    memory_bytes: int


class ProcessInfo(BaseModel):
    """Structured process telemetry snapshot.

    Wire: {"processes": [ProcessItem, ...]}
    """

    model_config = ConfigDict(extra="ignore")

    processes: list[ProcessItem]


class SystemSnapshot(BaseModel):
    """Aggregated system telemetry snapshot (system.all).

    Wire: {"cpu": CpuInfo, "memory": MemoryInfo, "gpu": GpuInfo,
           "battery": BatteryInfo, "disk": DiskInfo,
           "processes": [ProcessItem, ...]}

    Note: Rust SystemSnapshot embeds processes as a flat Vec<ProcessItem>
    (not a nested ProcessInfo struct), so Python mirrors this directly.
    """

    model_config = ConfigDict(extra="ignore")

    cpu: CpuInfo
    memory: MemoryInfo
    gpu: GpuInfo
    battery: BatteryInfo
    disk: DiskInfo
    processes: list[ProcessItem]
