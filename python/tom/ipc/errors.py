"""IPC error hierarchy and exception taxonomy for TOM.

Adheres to plan.md and PHASE2_IMPLEMENTATIONPLAN.md:
- Base exception TomError
- IpcError and category-specific subclasses
- Transport, connection, protocol, timeout, and remote error variants
- RemoteError helper predicates
"""


class TomError(Exception):
    """Base exception for all TOM Python errors."""


class IpcError(TomError):
    """Base exception for IPC communication errors."""


class ConnectionError(IpcError):
    """Cannot connect to or reach the Rust engine pipe."""


class NotConnectedError(ConnectionError):
    """Request attempted while IPC client is not connected."""


class ConnectionLostError(ConnectionError):
    """IPC connection dropped unexpectedly."""


class MaxRetriesExceededError(ConnectionError):
    """Reconnection failed after reaching maximum configured attempts."""

    def __init__(
        self,
        message: str = "Maximum reconnection attempts exceeded",
        attempts: int = 0,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts


class TransportError(IpcError):
    """Transport-level error (I/O, framing, encoding)."""


class FrameTooLargeError(TransportError):
    """Frame exceeds MAX_FRAME_BYTES limit."""

    def __init__(self, size: int, max_bytes: int = 1_048_576) -> None:
        super().__init__(f"Frame of {size} bytes exceeds maximum allowed size of {max_bytes} bytes")
        self.size = size
        self.max_bytes = max_bytes


class ProtocolError(IpcError):
    """Protocol violation (unsupported version, malformed JSON, schema mismatch)."""


class IpcTimeoutError(IpcError):
    """Python-side timeout waiting for IPC response."""

    def __init__(self, method: str, timeout_ms: int) -> None:
        super().__init__(f"IPC call '{method}' timed out after {timeout_ms}ms")
        self.method = method
        self.timeout_ms = timeout_ms


class RemoteError(IpcError):
    """Rust engine returned an error response (success=false)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"Remote engine error [{code}]: {message}")
        self.code = code
        self.message = message

    @property
    def is_not_found(self) -> bool:
        """True if method was not registered in engine dispatcher."""
        return self.code == "NOT_FOUND"

    @property
    def is_not_available(self) -> bool:
        """True if requested hardware/resource is unavailable."""
        return self.code == "NOT_AVAILABLE"

    @property
    def is_timeout(self) -> bool:
        """True if engine-side execution timed out."""
        return self.code == "TIMEOUT"

    @property
    def is_version_mismatch(self) -> bool:
        """True if protocol version mismatch occurred."""
        return self.code == "VERSION_MISMATCH"


class LifecycleError(TomError):
    """Startup, shutdown, or service lifecycle coordination errors."""


class ConfigurationError(TomError):
    """Configuration loading, parsing, or validation errors."""
