"""TOM IPC subsystem.

Provides IPC protocol models, wire framing constants, exception taxonomy,
transport mechanisms, and client interfaces for communicating with tom-engine.
"""

from tom.ipc.client import NamedPipeIpcClient
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
from tom.ipc.protocol import (
    MAX_FRAME_BYTES,
    PIPE_NAME,
    PROTOCOL_VERSION,
    ConnectionState,
    ErrorCode,
    IpcRequest,
    IpcResponse,
    new_request_id,
)
from tom.ipc.protocol import (
    IpcError as IpcErrorPayload,
)
from tom.ipc.transport import (
    NamedPipeTransport,
    TransportProtocol,
)

__all__ = [
    "MAX_FRAME_BYTES",
    "PIPE_NAME",
    "PROTOCOL_VERSION",
    "ConfigurationError",
    "ConnectionError",
    "ConnectionLostError",
    "ConnectionState",
    "ErrorCode",
    "FrameTooLargeError",
    "IpcError",
    "IpcErrorPayload",
    "IpcRequest",
    "IpcResponse",
    "IpcTimeoutError",
    "LifecycleError",
    "MaxRetriesExceededError",
    "NamedPipeIpcClient",
    "NamedPipeTransport",
    "NotConnectedError",
    "ProtocolError",
    "RemoteError",
    "TomError",
    "TransportError",
    "TransportProtocol",
    "new_request_id",
]
