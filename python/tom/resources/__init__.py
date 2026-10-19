"""TOM Resources Subsystem.

Responsible for VRAM and RAM hardware limits, telemetry monitoring,
model acquisition constraints, and mutual exclusion locking.
"""

from tom.resources.manager import (
    EngineTelemetryProvider,
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
from tom.resources.model_manager import (
    InvalidStateTransitionError,
    ModelLifecycleCoordinator,
    ModelNotFoundError,
    ModelRecord,
    ModelRegistration,
    ModelState,
)
from tom.resources.policies import (
    PriorityResourceQueue,
    ResourceWaiter,
    TaskPriority,
)

__all__ = [
    "EngineTelemetryProvider",
    "InsufficientResourceError",
    "InvalidStateTransitionError",
    "MockTelemetryProvider",
    "ModelLifecycleCoordinator",
    "ModelNotFoundError",
    "ModelRecord",
    "ModelRegistration",
    "ModelRole",
    "ModelState",
    "PriorityResourceQueue",
    "ResourceError",
    "ResourceLease",
    "ResourceLockError",
    "ResourceManager",
    "ResourceTelemetry",
    "ResourceTelemetryProvider",
    "ResourceWaiter",
    "SystemTelemetryProvider",
    "TaskPriority",
]
