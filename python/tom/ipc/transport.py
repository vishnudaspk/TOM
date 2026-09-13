"""Windows Named Pipe transport and NDJSON framing for TOM IPC.

Provides raw stream connection and newline-delimited JSON framing:
- TransportProtocol interface for mockability
- NamedPipeTransport using asyncio Proactor named pipe support
- Enforces MAX_FRAME_BYTES (1 MB) limit to prevent unbounded memory allocation
- Connection timeout and safety read timeout enforcement
- Idempotent cleanup and connection state reporting
"""

import asyncio
from typing import Any, Protocol, runtime_checkable

from tom.ipc.errors import (
    ConnectionError,
    ConnectionLostError,
    FrameTooLargeError,
    NotConnectedError,
    TransportError,
)
from tom.ipc.protocol import MAX_FRAME_BYTES, PIPE_NAME
from tom.schemas.config import IpcConfig
from tom.telemetry.logging import get_logger

logger = get_logger("tom.ipc.transport")


@runtime_checkable
class TransportProtocol(Protocol):
    """Protocol defining the asynchronous transport contract for IPC."""

    async def connect(self) -> None:
        """Establish transport connection to IPC endpoint."""
        ...

    async def close(self) -> None:
        """Close transport connection cleanly and idempotently."""
        ...

    async def read_frame(self) -> bytes:
        """Read a single newline-delimited frame without trailing delimiter."""
        ...

    async def write_frame(self, data: bytes) -> None:
        """Write a payload framed with a single trailing newline."""
        ...

    @property
    def is_connected(self) -> bool:
        """True if the transport is currently connected and open."""
        ...


class NamedPipeTransport:
    """Windows Named Pipe transport using asyncio Proactor event loop.

    Handles connection lifecycle and newline-delimited framing bounded
    by MAX_FRAME_BYTES.
    """

    def __init__(
        self,
        pipe_name: str = PIPE_NAME,
        connect_timeout_s: float = 3.0,
        read_timeout_s: float = 30.0,
        config: IpcConfig | None = None,
    ) -> None:
        """Initialize transport with pipe endpoint and timeout parameters.

        If `config` is provided, `pipe_name` and `connect_timeout_s` are
        derived from `config.pipe_name` and `config.connection_timeout_ms`.
        """
        if config is not None:
            self._pipe_name = config.pipe_name
            self._connect_timeout_s = config.connection_timeout_ms / 1000.0
        else:
            self._pipe_name = pipe_name
            self._connect_timeout_s = connect_timeout_s

        self._read_timeout_s = read_timeout_s
        self._connected = False
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._write_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        """Return True if connection is open and active."""
        if not self._connected or self._writer is None:
            return False
        return not self._writer.is_closing()

    @property
    def pipe_name(self) -> str:
        """Return the target pipe name."""
        return self._pipe_name

    @property
    def connect_timeout_s(self) -> float:
        """Return the connection timeout in seconds."""
        return self._connect_timeout_s

    @property
    def read_timeout_s(self) -> float:
        """Return the read safety timeout in seconds."""
        return self._read_timeout_s

    async def connect(self) -> None:
        """Connect to the Windows Named Pipe within connect_timeout_s.

        Raises ConnectionError on connection failure, missing pipe, or timeout.
        """
        if self.is_connected:
            return

        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=MAX_FRAME_BYTES * 2)
        protocol = asyncio.StreamReaderProtocol(reader)

        logger.debug("ipc_transport_connecting", pipe_name=self._pipe_name)

        try:
            transport, _ = await asyncio.wait_for(
                self._create_connection(loop, protocol),
                timeout=self._connect_timeout_s,
            )
        except TimeoutError as err:
            logger.warning(
                "ipc_transport_connect_timeout",
                pipe_name=self._pipe_name,
                timeout_s=self._connect_timeout_s,
            )
            raise ConnectionError(
                f"Connection to named pipe '{self._pipe_name}' timed out after {self._connect_timeout_s}s"
            ) from err
        except FileNotFoundError as err:
            logger.warning("ipc_transport_pipe_not_found", pipe_name=self._pipe_name)
            raise ConnectionError(f"Named pipe not found: '{self._pipe_name}': {err}") from err
        except (ConnectionRefusedError, PermissionError, OSError) as err:
            logger.warning(
                "ipc_transport_connection_failed",
                pipe_name=self._pipe_name,
                error=str(err),
            )
            raise ConnectionError(
                f"Cannot connect to named pipe '{self._pipe_name}': {err}"
            ) from err

        self._writer = asyncio.StreamWriter(transport, protocol, reader, loop)
        self._reader = reader
        self._connected = True
        logger.info("ipc_transport_connected", pipe_name=self._pipe_name)

    async def _create_connection(
        self,
        loop: asyncio.AbstractEventLoop,
        protocol: asyncio.StreamReaderProtocol,
    ) -> tuple[asyncio.BaseTransport, Any]:
        """Low-level pipe connection seam, isolated for testing and platform checks."""
        if hasattr(loop, "create_pipe_connection"):
            return await loop.create_pipe_connection(lambda: protocol, self._pipe_name)  # type: ignore[attr-defined]

        raise TransportError(
            "Current event loop does not support create_pipe_connection. "
            "Windows ProactorEventLoop is required."
        )

    async def write_frame(self, data: bytes) -> None:
        """Write raw frame data ending with exactly one newline delimiter.

        Raises:
            NotConnectedError: If transport is not open.
            FrameTooLargeError: If payload exceeds MAX_FRAME_BYTES.
            ConnectionLostError: If write fails due to broken pipe.
        """
        if not self.is_connected or self._writer is None:
            raise NotConnectedError("Transport is not connected")

        if len(data) > MAX_FRAME_BYTES:
            raise FrameTooLargeError(size=len(data), max_bytes=MAX_FRAME_BYTES)

        payload = data.rstrip(b"\r\n") + b"\n"
        if len(payload) > MAX_FRAME_BYTES + 1:
            raise FrameTooLargeError(size=len(payload), max_bytes=MAX_FRAME_BYTES)

        async with self._write_lock:
            try:
                self._writer.write(payload)
                await self._writer.drain()
            except (OSError, ConnectionResetError) as err:
                self._connected = False
                logger.warning("ipc_transport_write_failed", error=str(err))
                raise ConnectionLostError(f"Transport write failed: {err}") from err

        logger.debug("ipc_frame_written", size_bytes=len(payload))

    async def read_frame(self) -> bytes:
        """Read a single newline-delimited frame, removing the trailing newline.

        Raises:
            NotConnectedError: If transport is not open.
            TransportError: If reading times out after read_timeout_s.
            FrameTooLargeError: If line exceeds MAX_FRAME_BYTES.
            ConnectionLostError: On EOF or socket disconnect.
        """
        if not self.is_connected or self._reader is None:
            raise NotConnectedError("Transport is not connected")

        try:
            line = await asyncio.wait_for(
                self._reader.readline(),
                timeout=self._read_timeout_s,
            )
        except TimeoutError as err:
            logger.warning(
                "ipc_transport_read_timeout",
                timeout_s=self._read_timeout_s,
            )
            raise TransportError(f"Transport read timed out after {self._read_timeout_s}s") from err
        except ValueError as err:
            # Raised by asyncio StreamReader if line exceeded buffer limit without delimiter
            logger.warning("ipc_transport_frame_too_large_buffer", error=str(err))
            raise FrameTooLargeError(size=MAX_FRAME_BYTES + 1, max_bytes=MAX_FRAME_BYTES) from err
        except (OSError, ConnectionResetError) as err:
            self._connected = False
            logger.warning("ipc_transport_read_failed", error=str(err))
            raise ConnectionLostError(f"Transport read failed: {err}") from err

        if not line:
            self._connected = False
            logger.info("ipc_transport_eof_received")
            raise ConnectionLostError("Connection closed by peer (EOF)")

        if not line.endswith(b"\n"):
            self._connected = False
            logger.warning("ipc_transport_incomplete_frame_eof")
            raise ConnectionLostError(
                "Connection closed by peer before newline delimiter was received"
            )

        payload = line.rstrip(b"\r\n")

        if len(payload) > MAX_FRAME_BYTES or len(line) > MAX_FRAME_BYTES + 1:
            logger.warning(
                "ipc_transport_frame_too_large",
                frame_bytes=len(line),
                max_bytes=MAX_FRAME_BYTES,
            )
            raise FrameTooLargeError(size=len(line), max_bytes=MAX_FRAME_BYTES)

        logger.debug("ipc_frame_read", size_bytes=len(payload))
        return payload

    async def close(self) -> None:
        """Cleanly and idempotently close the transport connection."""
        self._connected = False
        writer = self._writer
        self._writer = None
        self._reader = None

        if writer is not None:
            try:
                if not writer.is_closing():
                    writer.close()
                await asyncio.wait_for(writer.wait_closed(), timeout=0.5)
            except Exception as err:
                logger.debug("ipc_transport_close_suppressed", error=str(err))

        logger.debug("ipc_transport_closed", pipe_name=self._pipe_name)
