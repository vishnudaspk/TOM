"""TOM Resources Subsystem.

Responsible for VRAM and RAM hardware limits, telemetry monitoring,
model acquisition constraints, and mutual exclusion locking.
"""

from tom.resources.manager import (
    InsufficientResourceError,
    MockTelemetryProvider,
    ModelRole,
    ResourceError,
    ResourceLease,
    ResourceLockError,
    ResourceManager,
    ResourceTelemetry,
    ResourceTelemetryProvider,
    SystemTelemetryProvider,
)

__all__ = [
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
