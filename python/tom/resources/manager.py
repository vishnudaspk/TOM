"""Hardware Resource Governor & Telemetry Subsystem.

Responsible for TOM's runtime resource limits on RTX 4060 (8 GB VRAM, 16 GB RAM).
Enforces:
- 500 MB VRAM safety headroom margin
- Mutual exclusion between heavy reasoning models and local VLMs
- Safe, cancellation-resistant acquire/release lease semantics
- Hardware telemetry via NVML and psutil with graceful fallbacks
"""

from __future__ import annotations

import asyncio
import ctypes
import subprocess
import time
import uuid
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any

import psutil
from pydantic import BaseModel, ConfigDict, Field

from tom.resources.policies import PriorityResourceQueue, TaskPriority
from tom.resources.types import ModelRole
from tom.schemas.config import ResourcesConfig
from tom.telemetry.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "EngineTelemetryProvider",
    "InsufficientResourceError",
    "MockTelemetryProvider",
    "ModelRole",
    "ResourceError",
    "ResourceLease",
    "ResourceLockError",
    "ResourceManager",
    "ResourceTelemetry",
    "ResourceTelemetryProvider",
    "SystemTelemetryProvider",
]

# Removed ModelRole class definition - now imported from types.py


class ResourceError(Exception):
    """Base exception for resource governor errors."""


class InsufficientResourceError(ResourceError):
    """Raised when available VRAM/RAM cannot satisfy the requested allocation."""


class ResourceLockError(ResourceError):
    """Raised when mutually exclusive model execution lock cannot be acquired."""


class ResourceTelemetry(BaseModel):
    """Snapshot of system and GPU hardware resource metrics."""

    model_config = ConfigDict(extra="ignore")

    vram_total_mb: float = Field(ge=0.0, description="Total dedicated GPU VRAM in MB")
    vram_used_mb: float = Field(ge=0.0, description="Currently occupied GPU VRAM in MB")
    vram_free_mb: float = Field(ge=0.0, description="Available GPU VRAM in MB")
    ram_total_mb: float = Field(ge=0.0, description="Total physical system RAM in MB")
    ram_used_mb: float = Field(ge=0.0, description="Currently occupied system RAM in MB")
    ram_free_mb: float = Field(ge=0.0, description="Available system RAM in MB")
    gpu_available: bool = Field(default=True, description="Whether GPU telemetry is active")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Measurement timestamp",
    )


class ResourceTelemetryProvider:
    """Abstract provider for hardware resource telemetry."""

    def get_telemetry(self) -> ResourceTelemetry:
        """Fetch current hardware telemetry snapshot."""
        raise NotImplementedError

    def is_available(self) -> bool:
        """Return True if GPU hardware telemetry is accessible."""
        raise NotImplementedError


class SystemTelemetryProvider(ResourceTelemetryProvider):
    """Production telemetry provider reading RAM via psutil and VRAM via NVML.

    Gracefully falls back across:
    1. pynvml (if installed)
    2. ctypes NVML (nvml.dll on Windows or libnvidia-ml.so on Linux)
    3. nvidia-smi CLI execution
    4. Graceful unavailable state (never crashes if GPU is missing)
    """

    def __init__(self, gpu_device_id: int = 0) -> None:
        self._gpu_device_id = gpu_device_id
        self._ctypes_nvml: Any | None = None
        self._nvml_initialized = False
        self._nvml_failed = False
        self._init_nvml()

    def _init_nvml(self) -> None:
        if self._nvml_failed or self._nvml_initialized:
            return
        try:
            # Try native ctypes nvml.dll / libnvidia-ml.so
            try:
                self._ctypes_nvml = ctypes.CDLL("nvml.dll")
            except Exception:
                self._ctypes_nvml = ctypes.CDLL("libnvidia-ml.so")

            self._ctypes_nvml.nvmlInit_v2()
            self._nvml_initialized = True
        except Exception:
            self._nvml_failed = True
            self._ctypes_nvml = None

    def _query_vram_ctypes(self) -> tuple[float, float, float] | None:
        if not self._nvml_initialized or self._ctypes_nvml is None:
            return None

        class _NvmlMemory(ctypes.Structure):
            _fields_ = [
                ("total", ctypes.c_ulonglong),
                ("free", ctypes.c_ulonglong),
                ("used", ctypes.c_ulonglong),
            ]

        try:
            handle = ctypes.c_void_p()
            ret = self._ctypes_nvml.nvmlDeviceGetHandleByIndex_v2(
                self._gpu_device_id, ctypes.byref(handle)
            )
            if ret != 0:
                return None
            mem = _NvmlMemory()
            ret = self._ctypes_nvml.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(mem))
            if ret != 0:
                return None
            return (
                mem.total / (1024.0 * 1024.0),
                mem.used / (1024.0 * 1024.0),
                mem.free / (1024.0 * 1024.0),
            )
        except Exception:
            return None

    def _query_vram_nvidiasmi(self) -> tuple[float, float, float] | None:
        try:
            cmd = [
                "nvidia-smi",
                f"--id={self._gpu_device_id}",
                "--query-gpu=memory.total,memory.used,memory.free",
                "--format=csv,noheader,nounits",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0, check=True)
            line = res.stdout.strip()
            parts = [float(p.strip()) for p in line.split(",")]
            if len(parts) == 3:
                return (parts[0], parts[1], parts[2])
        except Exception:
            pass
        return None

    def is_available(self) -> bool:
        if self._nvml_initialized:
            return True
        return self._query_vram_nvidiasmi() is not None

    def get_telemetry(self) -> ResourceTelemetry:
        # System RAM via psutil
        try:
            mem = psutil.virtual_memory()
            ram_total = mem.total / (1024.0 * 1024.0)
            ram_free = mem.available / (1024.0 * 1024.0)
            ram_used = mem.used / (1024.0 * 1024.0)
        except Exception as e:
            logger.warning("system_telemetry_ram_query_failed", error=str(e))
            ram_total, ram_used, ram_free = 16384.0, 8192.0, 8192.0

        # GPU VRAM
        vram_data = self._query_vram_ctypes()
        if vram_data is None:
            vram_data = self._query_vram_nvidiasmi()

        if vram_data is not None:
            total_vram, used_vram, free_vram = vram_data
            return ResourceTelemetry(
                vram_total_mb=total_vram,
                vram_used_mb=used_vram,
                vram_free_mb=free_vram,
                ram_total_mb=ram_total,
                ram_used_mb=ram_used,
                ram_free_mb=ram_free,
                gpu_available=True,
            )

        return ResourceTelemetry(
            vram_total_mb=0.0,
            vram_used_mb=0.0,
            vram_free_mb=0.0,
            ram_total_mb=ram_total,
            ram_used_mb=ram_used,
            ram_free_mb=ram_free,
            gpu_available=False,
        )


class EngineTelemetryProvider(ResourceTelemetryProvider):
    """Bridges Rust Named Pipe IPC telemetry to ResourceManager with snapshot caching.

    Maintains a thread-safe, short-lived cache (default 250 ms) to support
    synchronous check_budget() evaluations without event-loop blocking.
    Falls back gracefully to a secondary provider (default: SystemTelemetryProvider)
    if the engine client is unavailable or returns an error.
    """

    def __init__(
        self,
        engine_client: Any,
        fallback_provider: ResourceTelemetryProvider | None = None,
        cache_ttl_seconds: float = 0.250,
    ) -> None:
        self._engine_client = engine_client
        self._fallback = fallback_provider or SystemTelemetryProvider()
        self._cache_ttl = cache_ttl_seconds
        self._cached_telemetry: ResourceTelemetry | None = None
        self._last_refresh_time: float = 0.0
        self._refresh_lock = asyncio.Lock()

    async def refresh_snapshot(self) -> ResourceTelemetry:
        """Asynchronously query Rust engine and update cached snapshot."""
        async with self._refresh_lock:
            now = time.monotonic()
            if (
                self._cached_telemetry is not None
                and (now - self._last_refresh_time) < self._cache_ttl
            ):
                return self._cached_telemetry

            try:
                gpu = await self._engine_client.get_gpu()
                mem = await self._engine_client.get_memory()

                vram_total = (getattr(gpu, "memory_total_bytes", None) or 0) / (1024.0 * 1024.0)
                vram_used = (getattr(gpu, "memory_used_bytes", None) or 0) / (1024.0 * 1024.0)
                vram_free = max(0.0, vram_total - vram_used)

                ram_total = getattr(mem, "total_bytes", 0) / (1024.0 * 1024.0)
                ram_used = getattr(mem, "used_bytes", 0) / (1024.0 * 1024.0)
                ram_free = getattr(mem, "available_bytes", 0) / (1024.0 * 1024.0)

                self._cached_telemetry = ResourceTelemetry(
                    vram_total_mb=vram_total,
                    vram_used_mb=vram_used,
                    vram_free_mb=vram_free,
                    ram_total_mb=ram_total,
                    ram_used_mb=ram_used,
                    ram_free_mb=ram_free,
                    gpu_available=bool(getattr(gpu, "available", False)),
                )
                self._last_refresh_time = now
                return self._cached_telemetry
            except Exception as exc:
                logger.warning("engine_telemetry_refresh_failed", error=str(exc))
                fallback_telem = self._fallback.get_telemetry()
                self._cached_telemetry = fallback_telem
                self._last_refresh_time = now
                return fallback_telem

    def get_telemetry(self) -> ResourceTelemetry:
        """Synchronously return current snapshot; falls back if uninitialized."""
        if self._cached_telemetry is not None:
            return self._cached_telemetry
        return self._fallback.get_telemetry()

    def is_available(self) -> bool:
        if self._cached_telemetry is not None:
            return self._cached_telemetry.gpu_available
        return self._fallback.is_available()


class MockTelemetryProvider(ResourceTelemetryProvider):
    """Deterministic, configurable telemetry test double for unit testing."""

    def __init__(
        self,
        vram_total_mb: float = 8192.0,
        vram_used_mb: float = 1000.0,
        vram_free_mb: float = 7192.0,
        ram_total_mb: float = 16384.0,
        ram_used_mb: float = 6000.0,
        ram_free_mb: float = 10384.0,
        gpu_available: bool = True,
        should_fail: bool = False,
    ) -> None:
        self.vram_total_mb = vram_total_mb
        self.vram_used_mb = vram_used_mb
        self.vram_free_mb = vram_free_mb
        self.ram_total_mb = ram_total_mb
        self.ram_used_mb = ram_used_mb
        self.ram_free_mb = ram_free_mb
        self.gpu_available = gpu_available
        self.should_fail = should_fail

    def is_available(self) -> bool:
        return self.gpu_available and not self.should_fail

    def get_telemetry(self) -> ResourceTelemetry:
        if self.should_fail:
            raise ResourceError("Mock telemetry failure requested")
        return ResourceTelemetry(
            vram_total_mb=self.vram_total_mb,
            vram_used_mb=self.vram_used_mb,
            vram_free_mb=self.vram_free_mb,
            ram_total_mb=self.ram_total_mb,
            ram_used_mb=self.ram_used_mb,
            ram_free_mb=self.ram_free_mb,
            gpu_available=self.gpu_available,
        )


class ResourceLease:
    """An active hardware allocation lease returned by ResourceManager.

    IMPORTANT: Releasing a lease only decrements TOM's internal accounting
    counters and releases the mutual-exclusion lock. It does NOT guarantee
    that physical GPU memory has been freed by the inference server
    (llama.cpp). The server runs as a separate process and TOM cannot
    observe or force CUDA memory release. The 500 MB safety headroom
    margin is designed to absorb fragmentation and in-flight server work.
    """

    def __init__(
        self,
        manager: ResourceManager,
        role: ModelRole,
        vram_mb: int,
        ram_mb: int,
    ) -> None:
        self._manager = manager
        self.role = role
        self.vram_mb = vram_mb
        self.ram_mb = ram_mb
        self.lease_id = f"lease_{uuid.uuid4().hex[:8]}"
        self.acquired_at = datetime.now(UTC)
        self._released = False

    @property
    def is_active(self) -> bool:
        """Return True if lease has not yet been released."""
        return not self._released

    async def release(self) -> None:
        """Release this resource lease idempotently.

        This only updates TOM's internal accounting and releases the
        _heavy_lock. The underlying inference server may still hold
        GPU memory for in-flight requests that were cancelled but not
        yet terminated on the server side.
        """
        if not self._released:
            self._released = True
            await self._manager.release(self)

    async def __aenter__(self) -> ResourceLease:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        await self.release()


class _AcquisitionContext(AbstractAsyncContextManager[ResourceLease]):
    """Context manager and awaitable for resource lease acquisition."""

    def __init__(
        self,
        manager: ResourceManager,
        role: ModelRole,
        required_vram_mb: int,
        required_ram_mb: int,
        blocking: bool,
        timeout: float | None,
        priority: int = 1,  # Default: USER_INTERACTIVE
    ) -> None:
        self._manager = manager
        self._role = role
        self._required_vram_mb = required_vram_mb
        self._required_ram_mb = required_ram_mb
        self._blocking = blocking
        self._timeout = timeout
        self.priority = priority  # Stored for future arbitration integration
        self._lease: ResourceLease | None = None

    def __await__(self) -> Any:
        return self._manager._acquire_internal(
            self._role,
            self._required_vram_mb,
            self._required_ram_mb,
            self._blocking,
            self._timeout,
            self.priority,
        ).__await__()

    async def __aenter__(self) -> ResourceLease:
        self._lease = await self._manager._acquire_internal(
            self._role,
            self._required_vram_mb,
            self._required_ram_mb,
            self._blocking,
            self._timeout,
            self.priority,
        )
        return self._lease

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        if self._lease is not None:
            await self._lease.release()


class ResourceManager:
    """Hardware resource governor enforcing budgets, headroom, and mutual exclusion.

    RTX 4060 Laptop GPU target constraints:
    - 8 GB physical VRAM
    - 16 GB system RAM
    - ~500 MB safety headroom margin preserved at all times
    - Heavy reasoning and local VLM models are mutually exclusive
    """

    HEAVY_ROLES = {ModelRole.REASONING, ModelRole.VLM}

    def __init__(
        self,
        config: ResourcesConfig | None = None,
        telemetry_provider: ResourceTelemetryProvider | None = None,
        safety_headroom_mb: int = 500,
        clock: Any | None = None,
    ) -> None:
        self.config = config or ResourcesConfig()
        self.telemetry = telemetry_provider or SystemTelemetryProvider(
            gpu_device_id=self.config.gpu_device_id
        )
        self.safety_headroom_mb = safety_headroom_mb
        self._clock = clock

        self._allocated_vram_mb = 0
        self._allocated_ram_mb = 0
        self._active_leases: dict[str, ResourceLease] = {}

        self._heavy_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._priority_queue = PriorityResourceQueue(clock=clock)
        self._heavy_lock_reserved = False
        self._current_heavy_vram_mb = 0

    @property
    def allocated_vram_mb(self) -> int:
        """Currently tracked software VRAM allocations in MB."""
        return self._allocated_vram_mb

    @property
    def allocated_ram_mb(self) -> int:
        """Currently tracked software RAM allocations in MB."""
        return self._allocated_ram_mb

    @property
    def active_leases_count(self) -> int:
        """Number of active resource leases."""
        return len(self._active_leases)

    @property
    def is_heavy_inference_active(self) -> bool:
        """Return True if a heavy model (reasoning or VLM) holds the execution lock."""
        return self._heavy_lock.locked()

    def get_telemetry(self) -> ResourceTelemetry:
        """Query underlying telemetry provider."""
        try:
            return self.telemetry.get_telemetry()
        except Exception as e:
            logger.warning("resource_manager_telemetry_fetch_failed", error=str(e))
            # Fallback to software-tracked state
            return ResourceTelemetry(
                vram_total_mb=float(self.config.max_vram_mb),
                vram_used_mb=float(self._allocated_vram_mb),
                vram_free_mb=float(max(0, self.config.max_vram_mb - self._allocated_vram_mb)),
                ram_total_mb=float(self.config.max_ram_mb),
                ram_used_mb=float(self._allocated_ram_mb),
                ram_free_mb=float(max(0, self.config.max_ram_mb - self._allocated_ram_mb)),
                gpu_available=False,
            )

    def check_budget(self, required_vram_mb: int = 0, required_ram_mb: int = 0) -> bool:
        """Check if requested resources can be admitted without violating constraints."""
        if required_vram_mb < 0 or required_ram_mb < 0:
            raise ValueError(
                f"Resource requirements must be non-negative, got vram={required_vram_mb}, ram={required_ram_mb}"
            )

        usable_vram_ceiling = max(0, self.config.max_vram_mb - self.safety_headroom_mb)

        # Software budget check
        if self._allocated_vram_mb + required_vram_mb > usable_vram_ceiling:
            return False

        if self._allocated_ram_mb + required_ram_mb > self.config.max_ram_mb:
            return False

        # Live telemetry check
        try:
            telem = self.get_telemetry()
            if telem.gpu_available and required_vram_mb > 0:
                # Live free VRAM minus 500MB safety headroom must cover required VRAM
                available_free_vram = telem.vram_free_mb - self.safety_headroom_mb
                if required_vram_mb > available_free_vram:
                    return False

            if required_ram_mb > 0:
                if required_ram_mb > telem.ram_free_mb:
                    return False
        except Exception:
            # Graceful fallback: rely on software tracked limits
            pass

        return True

    def acquire(
        self,
        role: ModelRole,
        required_vram_mb: int = 0,
        required_ram_mb: int = 0,
        blocking: bool = True,
        timeout: float | None = None,
        priority: int = 1,  # Default: TaskPriority.USER_INTERACTIVE
    ) -> _AcquisitionContext:
        """Request a resource lease for the given model role.

        Can be used both as an awaitable:
            lease = await manager.acquire(ModelRole.REASONING, ...)
            try: ... finally: await lease.release()

        Or as an async context manager:
            async with manager.acquire(ModelRole.REASONING, ...) as lease:
                ...

        priority: TaskPriority numeric value (lower = higher priority).
          Lower-priority (background) callers should pass TaskPriority.BACKGROUND.
          The priority parameter is used for PriorityResourceQueue arbitration
          when capacity is contended.

        IMPORTANT: The returned lease only tracks TOM's internal resource
        accounting. Releasing it does NOT guarantee the inference server
        (llama.cpp) has freed physical GPU memory. The server runs as a
        separate process; cancelled requests may continue executing
        server-side. The 500 MB safety headroom absorbs this uncertainty.
        """
        return _AcquisitionContext(
            manager=self,
            role=role,
            required_vram_mb=required_vram_mb,
            required_ram_mb=required_ram_mb,
            blocking=blocking,
            timeout=timeout,
            priority=priority,
        )

    async def _acquire_internal(
        self,
        role: ModelRole,
        required_vram_mb: int,
        required_ram_mb: int,
        blocking: bool,
        timeout: float | None,
        priority: int = 1,
    ) -> ResourceLease:
        if required_vram_mb < 0 or required_ram_mb < 0:
            raise ValueError(
                f"Resource requirements must be non-negative, got vram={required_vram_mb}, ram={required_ram_mb}"
            )

        # Proactively refresh snapshot if provider supports it
        if hasattr(self.telemetry, "refresh_snapshot") and callable(
            self.telemetry.refresh_snapshot
        ):
            try:
                await self.telemetry.refresh_snapshot()
            except Exception as exc:
                logger.debug("telemetry_refresh_skipped", error=str(exc))

        # Fast path: try immediate grant under _state_lock
        heavy_role = role in self.HEAVY_ROLES
        lease: ResourceLease | None = None

        async with self._state_lock:
            if not self._check_budget_for_role(role, required_vram_mb, required_ram_mb):
                raise InsufficientResourceError(
                    f"Insufficient resources for {role}: required {required_vram_mb}MB VRAM "
                    f"(headroom {self.safety_headroom_mb}MB), {required_ram_mb}MB RAM"
                )

            if self._can_admit_immediate_locked(role, required_vram_mb, required_ram_mb):
                lease = self._grant_immediate_locked(role, required_vram_mb, required_ram_mb)
                if heavy_role:
                    self._heavy_lock_reserved = True

        if lease is not None:
            if heavy_role:
                try:
                    await self._heavy_lock.acquire()
                except BaseException:
                    async with self._state_lock:
                        self._heavy_lock_reserved = False
                    await asyncio.shield(self.release(lease))
                    raise
                async with self._state_lock:
                    self._heavy_lock_reserved = False
            return lease

        # Not immediately grantable (heavy lock held) - only queue if blocking
        if not blocking:
            raise ResourceLockError(
                f"Heavy inference lock is already held; cannot acquire for {role}"
            )

        # Enqueue and wait for grant
        waiter = await self._priority_queue.enqueue(
            role=role,
            vram_mb=required_vram_mb,
            ram_mb=required_ram_mb,
            priority=TaskPriority(priority),
        )

        try:
            if timeout is not None:
                await asyncio.wait_for(waiter.ready_event.wait(), timeout=timeout)
            else:
                await waiter.ready_event.wait()

            if waiter.cancelled:
                raise asyncio.CancelledError()

            # Waiter was granted; for heavy roles, acquire the lock now
            granted_lease = getattr(waiter, "lease", None)
            if granted_lease is None:
                raise ResourceError("Waiter granted but no lease assigned")

            if heavy_role:
                try:
                    await self._heavy_lock.acquire()
                except BaseException:
                    async with self._state_lock:
                        self._heavy_lock_reserved = False
                    await asyncio.shield(self.release(granted_lease))
                    raise
                async with self._state_lock:
                    self._heavy_lock_reserved = False
            return granted_lease

        except TimeoutError:
            # Clean up waiter on timeout
            lease_to_cleanup = getattr(waiter, "lease", None)
            if lease_to_cleanup is not None:
                await asyncio.shield(self.release(lease_to_cleanup))
            else:
                await self._priority_queue.remove(waiter.waiter_id)
            raise ResourceLockError(
                f"Timeout of {timeout}s exceeded waiting for resource ({role})"
            ) from None
        except asyncio.CancelledError:
            # Clean up waiter on cancellation
            lease_to_cleanup = getattr(waiter, "lease", None)
            if lease_to_cleanup is not None:
                await asyncio.shield(self.release(lease_to_cleanup))
            else:
                await self._priority_queue.remove(waiter.waiter_id)
            raise

    def _check_budget_for_role(
        self, role: ModelRole, required_vram_mb: int, required_ram_mb: int
    ) -> bool:
        """Check budget for a role, accounting for current heavy holder's VRAM if applicable."""
        if role in self.HEAVY_ROLES:
            effective_allocated = self._allocated_vram_mb - self._current_heavy_vram_mb
            usable_vram_ceiling = max(0, self.config.max_vram_mb - self.safety_headroom_mb)
            if effective_allocated + required_vram_mb > usable_vram_ceiling:
                return False
            if self._allocated_ram_mb + required_ram_mb > self.config.max_ram_mb:
                return False

            # Live telemetry check (same as check_budget)
            try:
                telem = self.get_telemetry()
                if telem.gpu_available and required_vram_mb > 0:
                    available_free_vram = telem.vram_free_mb - self.safety_headroom_mb
                    if required_vram_mb > available_free_vram:
                        return False
                if required_ram_mb > 0:
                    if required_ram_mb > telem.ram_free_mb:
                        return False
            except Exception:
                pass

            return True
        return self.check_budget(required_vram_mb, required_ram_mb)

    def _can_admit_immediate_locked(self, role: ModelRole, vram_mb: int, ram_mb: int) -> bool:
        """Check if a request can be granted immediately (must hold _state_lock)."""
        if not self._check_budget_for_role(role, vram_mb, ram_mb):
            return False
        if role in self.HEAVY_ROLES:
            return not self._heavy_lock.locked() and not self._heavy_lock_reserved
        return True

    def _grant_immediate_locked(self, role: ModelRole, vram_mb: int, ram_mb: int) -> ResourceLease:
        """Grant a lease immediately (must hold _state_lock)."""
        self._allocated_vram_mb += vram_mb
        self._allocated_ram_mb += ram_mb
        if role in self.HEAVY_ROLES:
            self._current_heavy_vram_mb = vram_mb

        lease = ResourceLease(
            manager=self,
            role=role,
            vram_mb=vram_mb,
            ram_mb=ram_mb,
        )
        self._active_leases[lease.lease_id] = lease
        logger.info(
            "resource_lease_acquired",
            role=str(role),
            lease_id=lease.lease_id,
            vram_mb=vram_mb,
            ram_mb=ram_mb,
            total_allocated_vram_mb=self._allocated_vram_mb,
        )
        return lease

    async def _dispatch_next_waiter(self) -> None:
        """Grant the next admissible waiter from the priority queue (must hold _state_lock)."""

        def can_admit(w: Any) -> bool:
            # For heavy roles, check budget excluding current heavy holder's VRAM
            # since they will release when the lock is acquired
            if w.role in self.HEAVY_ROLES:
                effective_allocated = self._allocated_vram_mb - self._current_heavy_vram_mb
                usable_vram_ceiling = max(0, self.config.max_vram_mb - self.safety_headroom_mb)
                if effective_allocated + w.vram_mb > usable_vram_ceiling:
                    return False
                if self._allocated_ram_mb + w.ram_mb > self.config.max_ram_mb:
                    return False
                return not self._heavy_lock.locked() and not self._heavy_lock_reserved

            # For light roles, check budget normally
            if not self.check_budget(w.vram_mb, w.ram_mb):
                return False
            return True

        now = self._clock() if self._clock is not None else None
        waiter = await self._priority_queue.pop_admissible(can_admit, now=now)
        if waiter is None:
            return

        self._allocated_vram_mb += waiter.vram_mb
        self._allocated_ram_mb += waiter.ram_mb

        lease = ResourceLease(
            manager=self,
            role=waiter.role,
            vram_mb=waiter.vram_mb,
            ram_mb=waiter.ram_mb,
        )
        self._active_leases[lease.lease_id] = lease
        waiter.lease = lease  # type: ignore[attr-defined]
        waiter.granted = True

        if waiter.role in self.HEAVY_ROLES:
            self._heavy_lock_reserved = True
            self._current_heavy_vram_mb = waiter.vram_mb

        waiter.ready_event.set()

    async def release(self, lease: ResourceLease) -> None:
        """Release a resource lease and free associated locks and budgets."""
        heavy_role = lease.role in self.HEAVY_ROLES

        async with self._state_lock:
            if lease.lease_id not in self._active_leases:
                return  # Idempotent

            del self._active_leases[lease.lease_id]
            self._allocated_vram_mb = max(0, self._allocated_vram_mb - lease.vram_mb)
            self._allocated_ram_mb = max(0, self._allocated_ram_mb - lease.ram_mb)

            if heavy_role:
                self._current_heavy_vram_mb = 0
                self._heavy_lock_reserved = False
                if self._heavy_lock.locked():
                    self._heavy_lock.release()

            # Dispatch next waiter while still holding _state_lock
            await self._dispatch_next_waiter()

        logger.info(
            "resource_lease_released",
            role=str(lease.role),
            lease_id=lease.lease_id,
            remaining_allocated_vram_mb=self._allocated_vram_mb,
        )
