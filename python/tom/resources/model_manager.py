"""Model Lifecycle Coordinator.

Provides lightweight, deterministic bookkeeping of model state, observed residency,
activity timestamps, idle detection, and explicit pluggable eviction hooks.

Distinguishes:
1. TOM's logical state (UNLOADED, READY, BUSY, IDLE, ERROR)
2. Observed or reported residency
3. Confirmed physical unload (when independently established)
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from tom.resources.manager import ModelRole, ResourceError
from tom.telemetry.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "InvalidStateTransitionError",
    "ModelLifecycleCoordinator",
    "ModelNotFoundError",
    "ModelRecord",
    "ModelRegistration",
    "ModelState",
]


class ModelState(StrEnum):
    """Deterministic lifecycle states of a model known to TOM."""

    UNLOADED = "unloaded"
    READY = "ready"
    BUSY = "busy"
    IDLE = "idle"
    ERROR = "error"


class ModelLifecycleError(ResourceError):
    """Base error for model lifecycle coordination."""


class ModelNotFoundError(ModelLifecycleError):
    """Raised when an operation targets an unregistered model."""


class InvalidStateTransitionError(ModelLifecycleError):
    """Raised when an invalid state transition is attempted."""


# Valid transition map: from_state -> set of valid target states
VALID_TRANSITIONS: dict[ModelState, set[ModelState]] = {
    ModelState.UNLOADED: {ModelState.READY, ModelState.ERROR},
    ModelState.READY: {ModelState.BUSY, ModelState.UNLOADED, ModelState.ERROR},
    ModelState.BUSY: {ModelState.IDLE, ModelState.READY, ModelState.ERROR},
    ModelState.IDLE: {ModelState.BUSY, ModelState.UNLOADED, ModelState.ERROR, ModelState.READY},
    ModelState.ERROR: {ModelState.UNLOADED, ModelState.READY},
}


@dataclass(frozen=True)
class ModelRegistration:
    """Registration metadata for a model known to TOM."""

    model_name: str
    role: ModelRole
    vram_required_mb: int = 0
    ram_required_mb: int = 0
    idle_timeout_seconds: float = 300.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelRecord:
    """Internal bookkeeping state for a registered model."""

    registration: ModelRegistration
    state: ModelState = ModelState.UNLOADED
    observed_residency: bool = False
    confirmed_physical_unload: bool = False
    last_active_at: float | None = None
    active_acquisitions: int = 0


class ModelLifecycleCoordinator:
    """Coordinates logical model state and residency tracking.

    Thread-safe via asyncio.Lock. Does not execute process killing or claim unverified
    physical unloads.
    """

    def __init__(
        self,
        clock: Callable[[], float] | None = None,
        default_eviction_hook: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        self._models: dict[str, ModelRecord] = {}
        self._lock = asyncio.Lock()
        self._clock = clock or time.time
        self._default_eviction_hook = default_eviction_hook

    async def register_model(self, registration: ModelRegistration) -> None:
        """Register a model for lifecycle tracking.

        If already registered, updates registration without losing existing active state.
        """
        async with self._lock:
            existing = self._models.get(registration.model_name)
            if existing is not None:
                existing.registration = registration
            else:
                self._models[registration.model_name] = ModelRecord(registration=registration)

    async def unregister_model(self, model_name: str) -> None:
        """Unregister a model from lifecycle tracking.

        Raises InvalidStateTransitionError if the model is currently BUSY.
        """
        async with self._lock:
            record = self._get_record_locked(model_name)
            if record.state == ModelState.BUSY:
                raise InvalidStateTransitionError(
                    f"Cannot unregister model '{model_name}' while BUSY"
                )
            del self._models[model_name]

    async def get_state(self, model_name: str) -> ModelState:
        """Get the current logical state of a model."""
        async with self._lock:
            return self._get_record_locked(model_name).state

    async def get_record(self, model_name: str) -> ModelRecord:
        """Get a copy or current view of the model's bookkeeping record."""
        async with self._lock:
            rec = self._get_record_locked(model_name)
            return ModelRecord(
                registration=rec.registration,
                state=rec.state,
                observed_residency=rec.observed_residency,
                confirmed_physical_unload=rec.confirmed_physical_unload,
                last_active_at=rec.last_active_at,
                active_acquisitions=rec.active_acquisitions,
            )

    async def list_models(self) -> list[ModelRegistration]:
        """List registrations for all tracked models."""
        async with self._lock:
            return [rec.registration for rec in self._models.values()]

    async def record_ready(self, model_name: str, observed_residency: bool = True) -> None:
        """Mark a model as READY (loaded / resident on server)."""
        async with self._lock:
            rec = self._get_record_locked(model_name)
            if rec.state == ModelState.READY:
                # Idempotent repeat
                rec.observed_residency = observed_residency
                return
            self._transition_locked(rec, ModelState.READY)
            rec.observed_residency = observed_residency
            rec.confirmed_physical_unload = False

    async def record_acquire(self, model_name: str) -> None:
        """Record acquisition of a model for inference (transitions to BUSY)."""
        async with self._lock:
            rec = self._get_record_locked(model_name)
            now = self._clock()
            if rec.state in (ModelState.READY, ModelState.IDLE):
                self._transition_locked(rec, ModelState.BUSY)
            elif rec.state == ModelState.BUSY:
                pass  # Nested/concurrent acquisition of already busy model
            else:
                self._transition_locked(rec, ModelState.BUSY)  # will raise invalid transition

            rec.active_acquisitions += 1
            rec.last_active_at = now

    async def record_release(self, model_name: str) -> None:
        """Record completion of inference with a model.

        When all active acquisitions finish, transitions from BUSY to IDLE.
        """
        async with self._lock:
            rec = self._get_record_locked(model_name)
            if rec.state != ModelState.BUSY:
                raise InvalidStateTransitionError(
                    f"Cannot release model '{model_name}' in state {rec.state}"
                )

            now = self._clock()
            if rec.active_acquisitions > 0:
                rec.active_acquisitions -= 1

            if rec.active_acquisitions == 0:
                self._transition_locked(rec, ModelState.IDLE)
                rec.last_active_at = now

    async def record_error(self, model_name: str, error: str | None = None) -> None:
        """Mark a model as having encountered an error."""
        async with self._lock:
            rec = self._get_record_locked(model_name)
            self._transition_locked(rec, ModelState.ERROR)
            rec.active_acquisitions = 0
            rec.last_active_at = self._clock()
            logger.warning(f"Model '{model_name}' marked ERROR: {error or 'unspecified error'}")

    async def record_unloaded(self, model_name: str, confirmed: bool = False) -> None:
        """Record model unload.

        confirmed: True only when physical unload is verified by external provider/server.
        """
        async with self._lock:
            rec = self._get_record_locked(model_name)
            if rec.state == ModelState.BUSY:
                raise InvalidStateTransitionError(
                    f"Cannot mark model '{model_name}' UNLOADED while BUSY"
                )
            if rec.state == ModelState.UNLOADED:
                # Idempotent
                rec.confirmed_physical_unload = confirmed
                rec.observed_residency = False
                return
            self._transition_locked(rec, ModelState.UNLOADED)
            rec.observed_residency = False
            rec.confirmed_physical_unload = confirmed
            rec.active_acquisitions = 0

    async def get_idle_models(self, now: float | None = None) -> list[str]:
        """Identify models currently IDLE whose idle timeout has elapsed.

        Does NOT automatically claim or trigger an unload.
        """
        async with self._lock:
            current_time = now if now is not None else self._clock()
            idle_models: list[str] = []
            for name, rec in self._models.items():
                if rec.state == ModelState.IDLE and rec.last_active_at is not None:
                    idle_duration = current_time - rec.last_active_at
                    if idle_duration >= rec.registration.idle_timeout_seconds:
                        idle_models.append(name)
            return idle_models

    async def trigger_eviction(
        self,
        model_name: str,
        hook: Callable[[str], Awaitable[bool]] | None = None,
    ) -> bool:
        """Explicitly trigger eviction for an idle or ready model via eviction hook.

        Returns True only if the eviction hook succeeds and confirms eviction.
        Does NOT corrupt state if the hook fails or raises an error.
        """
        async with self._lock:
            rec = self._get_record_locked(model_name)
            if rec.state == ModelState.BUSY:
                raise InvalidStateTransitionError(f"Cannot evict model '{model_name}' while BUSY")

        # Execute hook outside state lock to prevent deadlocks
        eviction_hook = hook or self._default_eviction_hook
        if eviction_hook is None:
            logger.info(f"No eviction hook configured for model '{model_name}'; state unchanged")
            return False

        try:
            success = await eviction_hook(model_name)
        except Exception:
            logger.exception(f"Eviction hook failed for model '{model_name}'")
            return False

        if success:
            async with self._lock:
                rec = self._models.get(model_name)
                if rec is not None and rec.state != ModelState.BUSY:
                    self._transition_locked(rec, ModelState.UNLOADED)
                    rec.observed_residency = False
                    rec.confirmed_physical_unload = True
            return True

        return False

    def _get_record_locked(self, model_name: str) -> ModelRecord:
        rec = self._models.get(model_name)
        if rec is None:
            raise ModelNotFoundError(f"Model '{model_name}' is not registered")
        return rec

    def _transition_locked(self, record: ModelRecord, target: ModelState) -> None:
        allowed = VALID_TRANSITIONS.get(record.state, set())
        if target not in allowed:
            raise InvalidStateTransitionError(
                f"Invalid transition for model '{record.registration.model_name}': "
                f"{record.state} -> {target}"
            )
        record.state = target
