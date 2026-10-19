"""TOM security subsystem."""

from tom.security.audit import SecurityAuditLogger
from tom.security.confirmation import (
    AlwaysAllowConfirmationHook,
    AlwaysDenyConfirmationHook,
    CallbackConfirmationHook,
    ConfirmationHook,
    ConfirmationRequest,
    ConfirmationTracker,
    ConsoleConfirmationHook,
)
from tom.security.permissions import (
    ConfirmationDeniedError,
    ConfirmationRequiredError,
    ConfirmationTimeoutError,
    PermissionDecision,
    PermissionDeniedError,
    PermissionEngine,
    PermissionLevel,
    SecurityError,
)
from tom.security.policies import PathPolicy
from tom.security.secrets import (
    SecretScrubber,
    get_default_scrubber,
    scrub_object,
    scrub_string,
    shannon_entropy,
)

__all__ = [
    "AlwaysAllowConfirmationHook",
    "AlwaysDenyConfirmationHook",
    "CallbackConfirmationHook",
    "ConfirmationDeniedError",
    "ConfirmationHook",
    "ConfirmationRequest",
    "ConfirmationRequiredError",
    "ConfirmationTimeoutError",
    "ConfirmationTracker",
    "ConsoleConfirmationHook",
    "PathPolicy",
    "PermissionDecision",
    "PermissionDeniedError",
    "PermissionEngine",
    "PermissionLevel",
    "SecretScrubber",
    "SecurityAuditLogger",
    "SecurityError",
    "get_default_scrubber",
    "scrub_object",
    "scrub_string",
    "shannon_entropy",
]
