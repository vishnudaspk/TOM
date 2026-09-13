"""Unit tests for IPC transport layer, Windows Named Pipe transport, and NDJSON framing."""

import asyncio
from collections.abc import Coroutine
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from tom.ipc.errors import (
    ConnectionError,
    ConnectionLostError,
    FrameTooLargeError,
    NotConnectedError,
    TransportError,
)
from tom.ipc.protocol import MAX_FRAME_BYTES, PIPE_NAME
from tom.ipc.transport import NamedPipeTransport, TransportProtocol
from tom.schemas.config import IpcConfig


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Helper to run coroutines synchronously in tests without external pytest plugins."""
    return asyncio.run(coro)


class DummyTransport(asyncio.Transport):
    """Minimal asyncio.Transport for unit testing StreamWriter/Reader pairs."""

    def __init__(self, protocol: asyncio.BaseProtocol | None = None) -> None:
        super().__init__()
        self.written: list[bytes] = []
        self._closed: bool = False
        self._protocol = protocol

    def write(self, data: bytes) -> None:
        if self._closed:
            raise OSError("Transport already closed")
        self.written.append(data)

    def is_closing(self) -> bool:
        return self._closed

    def close(self) -> None:
        self._closed = True
        if self._protocol is not None:
            self._protocol.connection_lost(None)


class MockTransport:
    """Mock implementation of TransportProtocol for downstream testing seams."""

    def __init__(self, responses: list[bytes] | None = None) -> None:
        self._responses: list[bytes] = list(responses or [])
        self._response_iter = iter(self._responses)
        self._written_frames: list[bytes] = []
        self._connected: bool = False
        self.close_call_count: int = 0
        self.connect_call_count: int = 0

    async def connect(self) -> None:
        self.connect_call_count += 1
        self._connected = True

    async def close(self) -> None:
        self.close_call_count += 1
        self._connected = False

    async def write_frame(self, data: bytes) -> None:
        if not self._connected:
            raise NotConnectedError("Transport is not connected")
        if len(data) > MAX_FRAME_BYTES:
            raise FrameTooLargeError(len(data), MAX_FRAME_BYTES)
        payload = data.rstrip(b"\r\n") + b"\n"
        if len(payload) > MAX_FRAME_BYTES + 1:
            raise FrameTooLargeError(len(payload), MAX_FRAME_BYTES)
        self._written_frames.append(payload)

    async def read_frame(self) -> bytes:
        if not self._connected:
            raise NotConnectedError("Transport is not connected")
        try:
            frame = next(self._response_iter)
        except StopIteration:
            self._connected = False
            raise ConnectionLostError("Connection closed by peer (EOF)") from None

        payload = frame.rstrip(b"\r\n")
        if len(payload) > MAX_FRAME_BYTES or len(frame) > MAX_FRAME_BYTES + 1:
            raise FrameTooLargeError(len(frame), MAX_FRAME_BYTES)
        return payload

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def last_written_frame(self) -> bytes | None:
        return self._written_frames[-1] if self._written_frames else None

    @property
    def written_frames(self) -> list[bytes]:
        return list(self._written_frames)


def create_connected_pipe_transport() -> tuple[
    NamedPipeTransport, asyncio.StreamReader, DummyTransport
]:
    """Helper creating an already-connected NamedPipeTransport backed by in-memory streams."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=MAX_FRAME_BYTES * 2)
    protocol = asyncio.StreamReaderProtocol(reader)
    dummy_transport = DummyTransport(protocol)
    writer = asyncio.StreamWriter(dummy_transport, protocol, reader, loop)

    transport = NamedPipeTransport(connect_timeout_s=1.0, read_timeout_s=1.0)
    transport._reader = reader
    transport._writer = writer
    transport._connected = True
    return transport, reader, dummy_transport


class TestTransportProtocolCompliance:
    """Validate protocol conformance and type checks."""

    def test_mock_transport_conforms_to_protocol(self) -> None:
        mock = MockTransport()
        assert isinstance(mock, TransportProtocol)

    def test_named_pipe_transport_conforms_to_protocol(self) -> None:
        transport = NamedPipeTransport()
        assert isinstance(transport, TransportProtocol)


class TestTransportInitialization:
    """Validate configuration options and defaults."""

    def test_default_initialization(self) -> None:
        transport = NamedPipeTransport()
        assert transport.pipe_name == PIPE_NAME
        assert transport.connect_timeout_s == 3.0
        assert transport.read_timeout_s == 30.0
        assert transport.is_connected is False

    def test_custom_parameters(self) -> None:
        transport = NamedPipeTransport(
            pipe_name=r"\\.\pipe\custom-test",
            connect_timeout_s=1.5,
            read_timeout_s=10.0,
        )
        assert transport.pipe_name == r"\\.\pipe\custom-test"
        assert transport.connect_timeout_s == 1.5
        assert transport.read_timeout_s == 10.0

    def test_ipc_config_initialization(self) -> None:
        config = IpcConfig(
            pipe_name=r"\\.\pipe\tom-from-config",
            connection_timeout_ms=4500,
        )
        transport = NamedPipeTransport(config=config)
        assert transport.pipe_name == r"\\.\pipe\tom-from-config"
        assert transport.connect_timeout_s == 4.5
        assert transport.read_timeout_s == 30.0


class TestTransportConnect:
    """Validate connection attempts, timeouts, and error mappings."""

    def test_connect_missing_pipe_raises_connection_error(self) -> None:
        async def _test() -> None:
            transport = NamedPipeTransport(
                pipe_name=r"\\.\pipe\tom-engine-unlikely-nonexistent-pipe",
                connect_timeout_s=1.0,
            )
            with pytest.raises(ConnectionError) as exc_info:
                await transport.connect()

            assert "Named pipe not found" in str(
                exc_info.value
            ) or "Cannot connect to named pipe" in str(exc_info.value)
            assert transport.is_connected is False

        run_async(_test())

    def test_connect_timeout_raises_connection_error(self) -> None:
        async def _test() -> None:
            transport = NamedPipeTransport(connect_timeout_s=0.01)

            async def slow_create(*args: Any, **kwargs: Any) -> tuple[Any, Any]:
                await asyncio.sleep(1.0)
                return (DummyTransport(), None)

            with patch.object(transport, "_create_connection", side_effect=slow_create):
                with pytest.raises(ConnectionError) as exc_info:
                    await transport.connect()

                assert "timed out" in str(exc_info.value)
                assert transport.is_connected is False

        run_async(_test())

    def test_connect_success_via_seam(self) -> None:
        async def _test() -> None:
            transport = NamedPipeTransport(connect_timeout_s=1.0)

            async def fake_create(loop: Any, protocol: Any) -> tuple[asyncio.BaseTransport, Any]:
                return (DummyTransport(protocol), protocol)

            with patch.object(transport, "_create_connection", side_effect=fake_create):
                await transport.connect()
                assert transport.is_connected is True

                # Re-connecting when already connected is a safe no-op
                await transport.connect()
                assert transport.is_connected is True

            await transport.close()
            assert transport.is_connected is False

        run_async(_test())

    def test_unsupported_event_loop_raises_transport_error(self) -> None:
        async def _test() -> None:
            transport = NamedPipeTransport()
            mock_loop = AsyncMock(spec=[])  # Loop without create_pipe_connection

            with pytest.raises(TransportError) as exc_info:
                await transport._create_connection(mock_loop, AsyncMock())

            assert "Windows ProactorEventLoop is required" in str(exc_info.value)

        run_async(_test())


class TestTransportFraming:
    """Validate frame writing, reading, NDJSON delimiters, and limits."""

    def test_write_frame_not_connected_raises(self) -> None:
        async def _test() -> None:
            transport = NamedPipeTransport()
            with pytest.raises(NotConnectedError):
                await transport.write_frame(b'{"test": 1}')

        run_async(_test())

    def test_read_frame_not_connected_raises(self) -> None:
        async def _test() -> None:
            transport = NamedPipeTransport()
            with pytest.raises(NotConnectedError):
                await transport.read_frame()

        run_async(_test())

    def test_write_frame_adds_single_newline(self) -> None:
        async def _test() -> None:
            transport, _, dummy_raw = create_connected_pipe_transport()

            await transport.write_frame(b'{"method": "engine.ping"}')
            assert dummy_raw.written == [b'{"method": "engine.ping"}\n']

        run_async(_test())

    def test_write_frame_strips_existing_newline_before_adding_one(
        self,
    ) -> None:
        async def _test() -> None:
            transport, _, dummy_raw = create_connected_pipe_transport()

            await transport.write_frame(b'{"method": "engine.ping"}\n')
            assert dummy_raw.written == [b'{"method": "engine.ping"}\n']

            await transport.write_frame(b'{"method": "engine.status"}\r\n')
            assert dummy_raw.written[-1] == b'{"method": "engine.status"}\n'

        run_async(_test())

    def test_write_frame_boundary_at_max_frame_bytes(self) -> None:
        async def _test() -> None:
            transport, _, dummy_raw = create_connected_pipe_transport()

            exact_frame = b"x" * MAX_FRAME_BYTES
            await transport.write_frame(exact_frame)
            assert dummy_raw.written == [exact_frame + b"\n"]

        run_async(_test())

    def test_write_frame_oversized_raises(self) -> None:
        async def _test() -> None:
            transport, _, _ = create_connected_pipe_transport()

            oversized_frame = b"x" * (MAX_FRAME_BYTES + 1)
            with pytest.raises(FrameTooLargeError) as exc_info:
                await transport.write_frame(oversized_frame)

            assert exc_info.value.size == MAX_FRAME_BYTES + 1
            assert exc_info.value.max_bytes == MAX_FRAME_BYTES

        run_async(_test())

    def test_read_frame_removes_newline(self) -> None:
        async def _test() -> None:
            transport, reader, _ = create_connected_pipe_transport()

            reader.feed_data(b'{"pong": true}\n')
            frame = await transport.read_frame()
            assert frame == b'{"pong": true}'

        run_async(_test())

    def test_read_frame_removes_crlf(self) -> None:
        async def _test() -> None:
            transport, reader, _ = create_connected_pipe_transport()

            reader.feed_data(b'{"pong": true}\r\n')
            frame = await transport.read_frame()
            assert frame == b'{"pong": true}'

        run_async(_test())

    def test_read_frame_boundary_at_max_frame_bytes(self) -> None:
        async def _test() -> None:
            transport, reader, _ = create_connected_pipe_transport()

            exact_payload = b"k" * MAX_FRAME_BYTES
            reader.feed_data(exact_payload + b"\n")
            frame = await transport.read_frame()
            assert frame == exact_payload

        run_async(_test())

    def test_read_frame_oversized_raises(self) -> None:
        async def _test() -> None:
            transport, reader, _ = create_connected_pipe_transport()

            oversized = b"z" * (MAX_FRAME_BYTES + 2) + b"\n"
            reader.feed_data(oversized)
            with pytest.raises(FrameTooLargeError):
                await transport.read_frame()

        run_async(_test())

    def test_read_frame_eof_raises_connection_lost(self) -> None:
        async def _test() -> None:
            transport, reader, _ = create_connected_pipe_transport()

            reader.feed_eof()
            with pytest.raises(ConnectionLostError) as exc_info:
                await transport.read_frame()

            assert "EOF" in str(exc_info.value)
            assert transport.is_connected is False

        run_async(_test())

    def test_read_frame_partial_eof_without_newline_raises(self) -> None:
        async def _test() -> None:
            transport, reader, _ = create_connected_pipe_transport()

            reader.feed_data(b'{"incomplete": true')
            reader.feed_eof()
            with pytest.raises(ConnectionLostError) as exc_info:
                await transport.read_frame()

            assert "before newline delimiter" in str(exc_info.value)
            assert transport.is_connected is False

        run_async(_test())

    def test_read_frame_timeout_raises_transport_error(self) -> None:
        async def _test() -> None:
            transport, _, _ = create_connected_pipe_transport()
            transport._read_timeout_s = 0.01

            with pytest.raises(TransportError) as exc_info:
                await transport.read_frame()

            assert "timed out" in str(exc_info.value)

        run_async(_test())


class TestTransportLifecycle:
    """Validate close semantics and idempotent teardown."""

    def test_idempotent_close(self) -> None:
        async def _test() -> None:
            transport, _, dummy_raw = create_connected_pipe_transport()
            assert transport.is_connected is True

            await transport.close()
            assert transport.is_connected is False
            assert dummy_raw.is_closing() is True

            # Second close must not raise
            await transport.close()
            assert transport.is_connected is False

        run_async(_test())


class TestMockTransportImplementation:
    """Validate MockTransport behaves as expected for subsequent testing iterations."""

    def test_mock_transport_read_and_write(self) -> None:
        async def _test() -> None:
            responses = [b'{"id": "req_1", "success": true}']
            mock = MockTransport(responses=responses)

            assert mock.is_connected is False
            await mock.connect()
            assert mock.is_connected is True

            await mock.write_frame(b'{"method": "engine.ping"}')
            assert mock.last_written_frame == b'{"method": "engine.ping"}\n'

            received = await mock.read_frame()
            assert received == b'{"id": "req_1", "success": true}'

            # Next read should raise EOF since responses were exhausted
            with pytest.raises(ConnectionLostError):
                await mock.read_frame()
            assert mock.is_connected is False

            await mock.close()
            assert mock.close_call_count == 1

        run_async(_test())
