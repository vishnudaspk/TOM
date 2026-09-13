"""Unit tests for TOM error taxonomy and IPC exception hierarchy."""

import pytest
from tom.ipc.errors import (
    ConfigurationError,
    ConnectionError,
    ConnectionLostError,
    FrameTooLargeError,
    IpcError,
    IpcTimeoutError,
    LifecycleError,
    MaxRetriesExceededError,
    NotConnectedError,
    ProtocolError,
    RemoteError,
    TomError,
    TransportError,
)


class TestExceptionHierarchy:
    """Validate full TOM and IPC exception inheritance hierarchy."""

    def test_base_exceptions(self) -> None:
        assert issubclass(TomError, Exception)
        assert issubclass(IpcError, TomError)
        assert issubclass(LifecycleError, TomError)
        assert issubclass(ConfigurationError, TomError)

    def test_connection_hierarchy(self) -> None:
        assert issubclass(ConnectionError, IpcError)
        assert issubclass(NotConnectedError, ConnectionError)
        assert issubclass(ConnectionLostError, ConnectionError)
        assert issubclass(MaxRetriesExceededError, ConnectionError)

    def test_transport_hierarchy(self) -> None:
        assert issubclass(TransportError, IpcError)
        assert issubclass(FrameTooLargeError, TransportError)

    def test_other_ipc_errors(self) -> None:
        assert issubclass(ProtocolError, IpcError)
        assert issubclass(IpcTimeoutError, IpcError)
        assert issubclass(RemoteError, IpcError)


class TestExceptionInstantiation:
    """Validate all exception classes can be instantiated and carry expected attributes."""

    def test_tom_error(self) -> None:
        err = TomError("general error")
        assert str(err) == "general error"

    def test_ipc_error(self) -> None:
        err = IpcError("ipc failure")
        assert isinstance(err, TomError)
        assert str(err) == "ipc failure"

    def test_connection_error(self) -> None:
        err = ConnectionError("failed to connect")
        assert isinstance(err, IpcError)

    def test_not_connected_error(self) -> None:
        err = NotConnectedError("client offline")
        assert isinstance(err, ConnectionError)

    def test_connection_lost_error(self) -> None:
        err = ConnectionLostError("pipe broken")
        assert isinstance(err, ConnectionError)

    def test_max_retries_exceeded_error(self) -> None:
        err = MaxRetriesExceededError("retry failed", attempts=5)
        assert isinstance(err, ConnectionError)
        assert err.attempts == 5
        assert str(err) == "retry failed"

    def test_transport_error(self) -> None:
        err = TransportError("I/O error")
        assert isinstance(err, IpcError)

    def test_frame_too_large_error(self) -> None:
        err = FrameTooLargeError(size=2_000_000, max_bytes=1_048_576)
        assert isinstance(err, TransportError)
        assert err.size == 2_000_000
        assert err.max_bytes == 1_048_576
        assert "2000000 bytes" in str(err)
        assert "1048576 bytes" in str(err)

    def test_protocol_error(self) -> None:
        err = ProtocolError("malformed json frame")
        assert isinstance(err, IpcError)

    def test_ipc_timeout_error(self) -> None:
        err = IpcTimeoutError(method="engine.ping", timeout_ms=500)
        assert isinstance(err, IpcError)
        assert err.method == "engine.ping"
        assert err.timeout_ms == 500
        assert "engine.ping" in str(err)
        assert "500ms" in str(err)

    def test_lifecycle_and_configuration_errors(self) -> None:
        err1 = LifecycleError("shutdown failure")
        err2 = ConfigurationError("invalid yaml")
        assert isinstance(err1, TomError)
        assert isinstance(err2, TomError)


class TestRemoteErrorPredicates:
    """Validate RemoteError properties and error code mappings."""

    def test_remote_error_not_found(self) -> None:
        err = RemoteError("NOT_FOUND", "Method not registered")
        assert err.code == "NOT_FOUND"
        assert err.message == "Method not registered"
        assert err.is_not_found is True
        assert err.is_not_available is False
        assert err.is_timeout is False
        assert err.is_version_mismatch is False
        assert "[NOT_FOUND]: Method not registered" in str(err)

    def test_remote_error_not_available(self) -> None:
        err = RemoteError("NOT_AVAILABLE", "NVML driver unavailable")
        assert err.is_not_available is True
        assert err.is_not_found is False

    def test_remote_error_timeout(self) -> None:
        err = RemoteError("TIMEOUT", "Handler execution exceeded 500ms")
        assert err.is_timeout is True
        assert err.is_not_found is False

    def test_remote_error_version_mismatch(self) -> None:
        err = RemoteError("VERSION_MISMATCH", "Expected version 1")
        assert err.is_version_mismatch is True
        assert err.is_timeout is False

    def test_catchable_as_ipc_and_tom_error(self) -> None:
        with pytest.raises(TomError):
            raise RemoteError("INTERNAL", "Internal failure")

        with pytest.raises(IpcError):
            raise RemoteError("INTERNAL", "Internal failure")
