"""Unit tests for IPC Client Core, request correlation, and receive loop."""

import asyncio
import json
from collections.abc import Coroutine
from typing import Any

import pytest
from tom.ipc.client import NamedPipeIpcClient
from tom.ipc.errors import (
    ConnectionLostError,
    IpcTimeoutError,
    NotConnectedError,
    RemoteError,
)
from tom.ipc.protocol import PROTOCOL_VERSION, IpcRequest
from tom.schemas.config import IpcConfig


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Helper to run coroutines synchronously in tests without external pytest plugins."""
    return asyncio.run(coro)


class QueueMockTransport:
    """Mock implementation of TransportProtocol backed by an asyncio.Queue for client tests."""

    def __init__(self) -> None:
        self.inbox: asyncio.Queue[bytes] = asyncio.Queue()
        self.outbox: list[bytes] = []
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
        self.outbox.append(data)

    async def read_frame(self) -> bytes:
        if not self._connected:
            raise NotConnectedError("Transport is not connected")
        frame = await self.inbox.get()
        if frame == b"":  # Sentinel indicating EOF/disconnect
            self._connected = False
            raise ConnectionLostError("Connection closed by peer (EOF)")
        return frame

    @property
    def is_connected(self) -> bool:
        return self._connected


class TestIpcClientLifecycle:
    """Validate client connection establishment, state reporting, and teardown."""

    def test_client_connect_and_state(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            client = NamedPipeIpcClient(transport=transport)

            assert client.is_connected is False
            await client.connect()
            assert client.is_connected is True
            assert transport.connect_call_count == 1

            # Second connect is a safe no-op
            await client.connect()
            assert client.is_connected is True
            assert transport.connect_call_count == 1

            await client.close()
            assert client.is_connected is False
            assert transport.close_call_count == 1

        run_async(_test())

    def test_client_idempotent_close(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            client = NamedPipeIpcClient(transport=transport)

            await client.connect()
            assert client.is_connected is True

            await client.close()
            assert client.is_connected is False

            # Calling close repeatedly must not raise
            await client.close()
            await client.close()
            assert client.is_connected is False

        run_async(_test())

    def test_request_when_not_connected_raises(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            client = NamedPipeIpcClient(transport=transport)

            with pytest.raises(NotConnectedError):
                await client.request("engine.ping")

        run_async(_test())


class TestIpcRequestHandling:
    """Validate request generation, wire serialization, and correlation."""

    def test_request_generates_valid_id_and_protocol_v1(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            client = NamedPipeIpcClient(transport=transport)
            await client.connect()

            async def send_req() -> dict[str, Any]:
                return await client.request("engine.ping", {"param1": 123})

            req_task = asyncio.create_task(send_req())
            # Yield control to let request write to transport
            await asyncio.sleep(0.01)

            assert len(transport.outbox) == 1
            raw_frame = transport.outbox[0].rstrip(b"\n")
            parsed = json.loads(raw_frame.decode("utf-8"))

            assert parsed["method"] == "engine.ping"
            assert parsed["version"] == PROTOCOL_VERSION
            assert parsed["params"] == {"param1": 123}
            assert parsed["id"].startswith("req_")

            # Validate with Pydantic model
            req_model = IpcRequest.model_validate(parsed)
            assert req_model.id == parsed["id"]

            # Reply to complete the request
            response_json = json.dumps(
                {"id": parsed["id"], "version": 1, "success": True, "data": {"pong": True}}
            ).encode("utf-8")
            transport.inbox.put_nowait(response_json)

            data = await req_task
            assert data == {"pong": True}
            assert client.pending_count == 0

            await client.close()

        run_async(_test())

    def test_successful_response_correlation(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            client = NamedPipeIpcClient(transport=transport)
            await client.connect()

            async def reply_coroutine() -> None:
                while not transport.outbox:
                    await asyncio.sleep(0.005)
                req_json = json.loads(transport.outbox[0].decode("utf-8"))
                resp = {
                    "id": req_json["id"],
                    "version": 1,
                    "success": True,
                    "data": {"cpu_percent": 24.5},
                }
                transport.inbox.put_nowait(json.dumps(resp).encode("utf-8"))

            reply_task = asyncio.create_task(reply_coroutine())
            result = await client.request("system.cpu")
            await reply_task

            assert result == {"cpu_percent": 24.5}
            assert client.pending_count == 0
            await client.close()

        run_async(_test())

    def test_remote_error_response_raises_remote_error(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            client = NamedPipeIpcClient(transport=transport)
            await client.connect()

            async def reply_coroutine() -> None:
                while not transport.outbox:
                    await asyncio.sleep(0.005)
                req_json = json.loads(transport.outbox[0].decode("utf-8"))
                resp = {
                    "id": req_json["id"],
                    "version": 1,
                    "success": False,
                    "error": {"code": "NOT_FOUND", "message": "Method unknown"},
                }
                transport.inbox.put_nowait(json.dumps(resp).encode("utf-8"))

            reply_task = asyncio.create_task(reply_coroutine())
            with pytest.raises(RemoteError) as exc_info:
                await client.request("unknown.method")
            await reply_task

            assert exc_info.value.code == "NOT_FOUND"
            assert exc_info.value.message == "Method unknown"
            assert exc_info.value.is_not_found is True
            assert client.pending_count == 0
            await client.close()

        run_async(_test())


class TestConcurrencyAndOrdering:
    """Validate concurrent request correlation and out-of-order response handling."""

    def test_concurrent_requests_correlation(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            client = NamedPipeIpcClient(transport=transport)
            await client.connect()

            task1 = asyncio.create_task(client.request("system.cpu"))
            task2 = asyncio.create_task(client.request("system.memory"))
            task3 = asyncio.create_task(client.request("engine.ping"))

            # Wait for all 3 to be sent
            while len(transport.outbox) < 3:
                await asyncio.sleep(0.005)

            reqs = [json.loads(f.decode("utf-8")) for f in transport.outbox]
            assert len(reqs) == 3
            assert client.pending_count == 3

            # Respond to each
            for req in reqs:
                resp = {
                    "id": req["id"],
                    "version": 1,
                    "success": True,
                    "data": {"method_called": req["method"]},
                }
                transport.inbox.put_nowait(json.dumps(resp).encode("utf-8"))

            res1 = await task1
            res2 = await task2
            res3 = await task3

            assert res1 == {"method_called": "system.cpu"}
            assert res2 == {"method_called": "system.memory"}
            assert res3 == {"method_called": "engine.ping"}
            assert client.pending_count == 0

            await client.close()

        run_async(_test())

    def test_out_of_order_responses_resolve_correct_futures(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            client = NamedPipeIpcClient(transport=transport)
            await client.connect()

            task_a = asyncio.create_task(client.request("method.A"))
            task_b = asyncio.create_task(client.request("method.B"))
            task_c = asyncio.create_task(client.request("method.C"))

            while len(transport.outbox) < 3:
                await asyncio.sleep(0.005)

            req_a = json.loads(transport.outbox[0].decode("utf-8"))
            req_b = json.loads(transport.outbox[1].decode("utf-8"))
            req_c = json.loads(transport.outbox[2].decode("utf-8"))

            # Deliver responses in reverse order: C, then A, then B
            resp_c = {"id": req_c["id"], "version": 1, "success": True, "data": {"val": "C"}}
            resp_a = {"id": req_a["id"], "version": 1, "success": True, "data": {"val": "A"}}
            resp_b = {"id": req_b["id"], "version": 1, "success": True, "data": {"val": "B"}}

            transport.inbox.put_nowait(json.dumps(resp_c).encode("utf-8"))
            transport.inbox.put_nowait(json.dumps(resp_a).encode("utf-8"))
            transport.inbox.put_nowait(json.dumps(resp_b).encode("utf-8"))

            res_a, res_b, res_c = await asyncio.gather(task_a, task_b, task_c)

            assert res_a == {"val": "A"}
            assert res_b == {"val": "B"}
            assert res_c == {"val": "C"}
            assert client.pending_count == 0

            await client.close()

        run_async(_test())


class TestEdgeCasesAndFailureModes:
    """Validate unknown IDs, duplicates, malformed frames, and disconnects."""

    def test_unknown_response_id_is_safely_discarded(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            client = NamedPipeIpcClient(transport=transport)
            await client.connect()

            req_task = asyncio.create_task(client.request("engine.ping"))
            while not transport.outbox:
                await asyncio.sleep(0.005)

            req = json.loads(transport.outbox[0].decode("utf-8"))

            # First send an unknown response ID
            unknown_resp = {
                "id": "req_totally_unknown_id",
                "version": 1,
                "success": True,
                "data": {"discard": True},
            }
            transport.inbox.put_nowait(json.dumps(unknown_resp).encode("utf-8"))

            # Next send the matching response
            matching_resp = {
                "id": req["id"],
                "version": 1,
                "success": True,
                "data": {"pong": True},
            }
            transport.inbox.put_nowait(json.dumps(matching_resp).encode("utf-8"))

            result = await req_task
            assert result == {"pong": True}
            assert client.pending_count == 0

            await client.close()

        run_async(_test())

    def test_duplicate_response_is_safely_discarded(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            client = NamedPipeIpcClient(transport=transport)
            await client.connect()

            req_task = asyncio.create_task(client.request("engine.ping"))
            while not transport.outbox:
                await asyncio.sleep(0.005)

            req = json.loads(transport.outbox[0].decode("utf-8"))
            resp = {"id": req["id"], "version": 1, "success": True, "data": {"pong": True}}
            resp_bytes = json.dumps(resp).encode("utf-8")

            # Send duplicate responses
            transport.inbox.put_nowait(resp_bytes)
            transport.inbox.put_nowait(resp_bytes)

            result = await req_task
            assert result == {"pong": True}

            # Give receive loop a cycle to process second response
            await asyncio.sleep(0.01)
            assert client.pending_count == 0

            await client.close()

        run_async(_test())

    def test_malformed_response_json_is_safely_ignored(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            client = NamedPipeIpcClient(transport=transport)
            await client.connect()

            req_task = asyncio.create_task(client.request("engine.ping"))
            while not transport.outbox:
                await asyncio.sleep(0.005)

            req = json.loads(transport.outbox[0].decode("utf-8"))

            # Send corrupted data
            transport.inbox.put_nowait(b"NOT_A_VALID_JSON_OBJECT\n")

            # Then send valid response
            valid_resp = {"id": req["id"], "version": 1, "success": True, "data": {"pong": True}}
            transport.inbox.put_nowait(json.dumps(valid_resp).encode("utf-8"))

            result = await req_task
            assert result == {"pong": True}
            assert client.pending_count == 0

            await client.close()

        run_async(_test())

    def test_connection_lost_fails_all_pending_requests(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            client = NamedPipeIpcClient(transport=transport)
            await client.connect()

            task1 = asyncio.create_task(client.request("slow.method1"))
            task2 = asyncio.create_task(client.request("slow.method2"))

            while len(transport.outbox) < 2:
                await asyncio.sleep(0.005)

            assert client.pending_count == 2

            # Simulate peer connection drop (EOF)
            transport.inbox.put_nowait(b"")

            with pytest.raises(ConnectionLostError):
                await task1

            with pytest.raises(ConnectionLostError):
                await task2

            assert client.pending_count == 0
            assert client.is_connected is False

            await client.close()

        run_async(_test())

    def test_close_fails_pending_requests_and_stops_receive_task(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            client = NamedPipeIpcClient(transport=transport)
            await client.connect()

            req_task = asyncio.create_task(client.request("pending.method"))
            while not transport.outbox:
                await asyncio.sleep(0.005)

            assert client.pending_count == 1
            receive_task = client._receive_task
            assert receive_task is not None
            assert not receive_task.done()

            await client.close()

            with pytest.raises(ConnectionLostError) as exc_info:
                await req_task

            assert "client closed" in str(exc_info.value).lower()
            assert client.pending_count == 0
            assert client.is_connected is False
            assert receive_task.done()

        run_async(_test())

    def test_config_initialization(self) -> None:
        config = IpcConfig(pipe_name=r"\\.\pipe\custom-client", request_timeout_ms=6000)
        client = NamedPipeIpcClient(config=config)
        assert client._config.pipe_name == r"\\.\pipe\custom-client"
        assert client._config.request_timeout_ms == 6000


class TestTimeoutsAndCancellation:
    """Validate per-request timeout enforcement, shielding, and cancellation cleanup."""

    def test_configured_request_timeout_respected(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            config = IpcConfig(request_timeout_ms=150)
            client = NamedPipeIpcClient(config=config, transport=transport)
            await client.connect()

            with pytest.raises(IpcTimeoutError) as exc_info:
                await client.request("engine.slow_operation")

            assert exc_info.value.method == "engine.slow_operation"
            assert exc_info.value.timeout_ms == 150
            assert "timed out after 150ms" in str(exc_info.value)
            assert client.pending_count == 0

            await client.close()

        run_async(_test())

    def test_custom_timeout_override_respected(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            config = IpcConfig(request_timeout_ms=5000)
            client = NamedPipeIpcClient(config=config, transport=transport)
            await client.connect()

            with pytest.raises(IpcTimeoutError) as exc_info:
                await client.request("engine.fast_timeout", timeout_ms=30)

            assert exc_info.value.method == "engine.fast_timeout"
            assert exc_info.value.timeout_ms == 30
            assert client.pending_count == 0

            await client.close()

        run_async(_test())

    def test_timed_out_request_removed_from_pending(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            client = NamedPipeIpcClient(transport=transport)
            await client.connect()

            with pytest.raises(IpcTimeoutError):
                await client.request("slow.method", timeout_ms=30)

            # Confirm outbox contains the sent request
            assert len(transport.outbox) == 1
            req = json.loads(transport.outbox[0].decode("utf-8"))

            # Must be completely removed from _pending
            assert req["id"] not in client._pending
            assert client.pending_count == 0

            await client.close()

        run_async(_test())

    def test_late_response_after_timeout_safely_discarded(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            client = NamedPipeIpcClient(transport=transport)
            await client.connect()

            with pytest.raises(IpcTimeoutError):
                await client.request("slow.method", timeout_ms=30)

            req = json.loads(transport.outbox[0].decode("utf-8"))
            timed_out_id = req["id"]

            # Late response arrives after timeout expiry
            late_resp = {
                "id": timed_out_id,
                "version": 1,
                "success": True,
                "data": {"too_late": True},
            }
            transport.inbox.put_nowait(json.dumps(late_resp).encode("utf-8"))

            # Yield control to let receive loop process late response
            await asyncio.sleep(0.01)

            # Receive loop must still be healthy and client connected
            assert client.is_connected is True
            assert client.pending_count == 0

            # Normal subsequent request must succeed
            subsequent_task = asyncio.create_task(client.request("engine.ping"))
            while len(transport.outbox) < 2:
                await asyncio.sleep(0.005)

            req2 = json.loads(transport.outbox[1].decode("utf-8"))
            resp2 = {"id": req2["id"], "version": 1, "success": True, "data": {"pong": True}}
            transport.inbox.put_nowait(json.dumps(resp2).encode("utf-8"))

            res2 = await subsequent_task
            assert res2 == {"pong": True}
            assert client.pending_count == 0

            await client.close()

        run_async(_test())

    def test_caller_cancellation_does_not_cancel_underlying_future(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            client = NamedPipeIpcClient(transport=transport)
            await client.connect()

            req_task = asyncio.create_task(client.request("cancellable.method", timeout_ms=5000))

            while not transport.outbox:
                await asyncio.sleep(0.005)

            req = json.loads(transport.outbox[0].decode("utf-8"))
            req_id = req["id"]

            # Grab the underlying Future before cancelling caller task
            fut = client._pending[req_id]
            assert fut.done() is False

            # Cancel the caller task
            req_task.cancel()

            with pytest.raises(asyncio.CancelledError):
                await req_task

            # Underlying future was shielded, so it was NOT cancelled!
            assert fut.cancelled() is False

            # Request was cleanly popped from client._pending
            assert req_id not in client._pending
            assert client.pending_count == 0

            await client.close()

        run_async(_test())

    def test_late_response_after_caller_cancellation_safely_discarded(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            client = NamedPipeIpcClient(transport=transport)
            await client.connect()

            req_task = asyncio.create_task(client.request("cancelled.method", timeout_ms=5000))
            while not transport.outbox:
                await asyncio.sleep(0.005)

            req = json.loads(transport.outbox[0].decode("utf-8"))
            cancelled_id = req["id"]

            req_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await req_task

            assert client.pending_count == 0

            # Late response arrives for the cancelled request
            late_resp = {
                "id": cancelled_id,
                "version": 1,
                "success": True,
                "data": {"cancelled_result": True},
            }
            transport.inbox.put_nowait(json.dumps(late_resp).encode("utf-8"))

            await asyncio.sleep(0.01)
            assert client.is_connected is True

            # Subsequent request succeeds normally
            next_task = asyncio.create_task(client.request("engine.ping"))
            while len(transport.outbox) < 2:
                await asyncio.sleep(0.005)

            req2 = json.loads(transport.outbox[1].decode("utf-8"))
            resp2 = {"id": req2["id"], "version": 1, "success": True, "data": {"pong": True}}
            transport.inbox.put_nowait(json.dumps(resp2).encode("utf-8"))

            res2 = await next_task
            assert res2 == {"pong": True}
            assert client.pending_count == 0

            await client.close()

        run_async(_test())

    def test_concurrent_mixed_isolation_timeout_cancel_and_success(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            client = NamedPipeIpcClient(transport=transport)
            await client.connect()

            # Task A: will time out
            task_a = asyncio.create_task(client.request("method.A", timeout_ms=50))
            # Task B: will succeed normally
            task_b = asyncio.create_task(client.request("method.B", timeout_ms=5000))
            # Task C: will be cancelled
            task_c = asyncio.create_task(client.request("method.C", timeout_ms=5000))
            # Task D: will succeed normally
            task_d = asyncio.create_task(client.request("method.D", timeout_ms=5000))

            # Wait for all 4 requests to be written to transport
            while len(transport.outbox) < 4:
                await asyncio.sleep(0.005)

            assert client.pending_count == 4
            outbox_reqs = {
                json.loads(f.decode("utf-8"))["method"]: json.loads(f.decode("utf-8"))
                for f in transport.outbox
            }

            # Cancel Task C
            task_c.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task_c

            # Respond to Task B and Task D
            resp_b = {
                "id": outbox_reqs["method.B"]["id"],
                "version": 1,
                "success": True,
                "data": {"result": "B_success"},
            }
            resp_d = {
                "id": outbox_reqs["method.D"]["id"],
                "version": 1,
                "success": True,
                "data": {"result": "D_success"},
            }
            transport.inbox.put_nowait(json.dumps(resp_b).encode("utf-8"))
            transport.inbox.put_nowait(json.dumps(resp_d).encode("utf-8"))

            res_b = await task_b
            res_d = await task_d

            assert res_b == {"result": "B_success"}
            assert res_d == {"result": "D_success"}

            # Task A must time out
            with pytest.raises(IpcTimeoutError) as exc_a:
                await task_a
            assert exc_a.value.method == "method.A"
            assert exc_a.value.timeout_ms == 50

            # All 4 requests completed with zero leaks
            assert client.pending_count == 0

            await client.close()

        run_async(_test())

    def test_receive_loop_invalid_state_race_protection(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            client = NamedPipeIpcClient(transport=transport)
            await client.connect()

            req_task = asyncio.create_task(client.request("race.method", timeout_ms=5000))
            while not transport.outbox:
                await asyncio.sleep(0.005)

            req = json.loads(transport.outbox[0].decode("utf-8"))
            req_id = req["id"]

            fut = client._pending[req_id]

            # Simulate race: Future cancelled right before set_result
            fut.cancel()

            resp = {
                "id": req_id,
                "version": 1,
                "success": True,
                "data": {"won_race": True},
            }
            transport.inbox.put_nowait(json.dumps(resp).encode("utf-8"))

            # Give receive loop a cycle to process
            await asyncio.sleep(0.01)

            # Receive loop must handle InvalidStateError without dying
            assert client.is_connected is True

            with pytest.raises(asyncio.CancelledError):
                await req_task

            assert client.pending_count == 0
            await client.close()

        run_async(_test())

    def test_zero_leaks_after_repeated_timeouts_and_cancellations(self) -> None:
        async def _test() -> None:
            transport = QueueMockTransport()
            client = NamedPipeIpcClient(transport=transport)
            await client.connect()

            # Sequence of 5 fast timeouts
            for i in range(5):
                with pytest.raises(IpcTimeoutError):
                    await client.request(f"timeout.{i}", timeout_ms=10)
                assert client.pending_count == 0

            # Sequence of 5 cancellations
            for i in range(5):
                t = asyncio.create_task(client.request(f"cancel.{i}", timeout_ms=1000))
                await asyncio.sleep(0.005)
                t.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await t
                assert client.pending_count == 0

            assert client.pending_count == 0
            await client.close()

        run_async(_test())


class ReconnectingMockTransport:
    """Mock transport where the first connect() always succeeds;
    subsequent reconnect attempts can be forced to fail a controlled number of times.
    """

    def __init__(self, fail_reconnect_times: int = 0) -> None:
        self.inbox: asyncio.Queue[bytes] = asyncio.Queue()
        self.outbox: list[bytes] = []
        self._connected: bool = False
        self.connect_call_count: int = 0
        self.close_call_count: int = 0
        self._initial_connected: bool = False  # True once first connect succeeded
        self.fail_reconnect_times: int = fail_reconnect_times

    async def connect(self) -> None:
        self.connect_call_count += 1
        if self._initial_connected and self.fail_reconnect_times > 0:
            # Only fail *reconnect* attempts, not the initial connect
            self.fail_reconnect_times -= 1
            raise OSError("Simulated reconnection failure")
        self._connected = True
        self._initial_connected = True

    async def close(self) -> None:
        self.close_call_count += 1
        self._connected = False

    async def write_frame(self, data: bytes) -> None:
        self.outbox.append(data)

    async def read_frame(self) -> bytes:
        if not self._connected:
            raise ConnectionLostError("Transport not connected")
        frame = await self.inbox.get()
        if frame == b"":
            self._connected = False
            raise ConnectionLostError("Connection closed by peer (EOF)")
        return frame

    @property
    def is_connected(self) -> bool:
        return self._connected


class TestReconnection:
    """Validate reconnection state machine: state transitions, backoff, max retries, close."""

    def test_initial_state_is_disconnected(self) -> None:
        transport = ReconnectingMockTransport()
        client = NamedPipeIpcClient(transport=transport)
        from tom.ipc.protocol import ConnectionState

        assert client.state == ConnectionState.DISCONNECTED

    def test_state_transitions_to_connected_after_connect(self) -> None:
        async def _test() -> None:
            from tom.ipc.protocol import ConnectionState

            transport = ReconnectingMockTransport()
            client = NamedPipeIpcClient(transport=transport)
            assert client.state == ConnectionState.DISCONNECTED

            await client.connect()
            assert client.state == ConnectionState.CONNECTED

            await client.close()
            assert client.state == ConnectionState.CLOSED

        run_async(_test())

    def test_disconnect_triggers_reconnecting_state(self) -> None:
        """Simulating EOF on the transport should transition state to RECONNECTING."""

        async def _test() -> None:
            from tom.ipc.protocol import ConnectionState

            # Transport will always fail reconnects (so the loop parks in RECONNECTING)
            transport = ReconnectingMockTransport(fail_reconnect_times=999)
            client = NamedPipeIpcClient(
                transport=transport,
                base_delay_s=0.0,
                max_delay_s=0.0,
                enable_jitter=False,
            )
            # max_reconnect_attempts=0 means unlimited (loop never self-terminates)
            from tom.schemas.config import IpcConfig

            client._config = IpcConfig(max_reconnect_attempts=0)

            await client.connect()
            assert client.state == ConnectionState.CONNECTED

            # Trigger disconnect via EOF sentinel
            transport.inbox.put_nowait(b"")

            # Yield a few cycles for receive loop to exit and reconnect loop to start
            for _ in range(20):
                await asyncio.sleep(0.005)
                if client.state == ConnectionState.RECONNECTING:
                    break

            assert client.state == ConnectionState.RECONNECTING

            # Clean up: close should exit reconnect loop
            await client.close()
            assert client.state == ConnectionState.CLOSED

        run_async(_test())

    def test_reconnect_succeeds_after_transient_failure(self) -> None:
        """Client should recover CONNECTED after one failed reconnect attempt."""

        async def _test() -> None:
            from tom.ipc.protocol import ConnectionState

            # First reconnect attempt will fail; second will succeed
            transport = ReconnectingMockTransport(fail_reconnect_times=1)
            client = NamedPipeIpcClient(
                transport=transport,
                base_delay_s=0.0,
                max_delay_s=0.0,
                enable_jitter=False,
            )

            await client.connect()
            assert client.state == ConnectionState.CONNECTED
            assert transport.connect_call_count == 1

            # Trigger EOF → reconnect loop
            transport.inbox.put_nowait(b"")

            # Allow reconnect loop to attempt (fail once) and then succeed
            for _ in range(30):
                await asyncio.sleep(0.005)
                if client.state == ConnectionState.CONNECTED:
                    break

            assert client.state == ConnectionState.CONNECTED
            # connect called: 1 initial + 1 failed + 1 success = 3
            assert transport.connect_call_count == 3

            await client.close()

        run_async(_test())

    def test_max_reconnect_attempts_exhausted_transitions_to_disconnected(self) -> None:
        """After max_reconnect_attempts failures the client should reach DISCONNECTED."""

        async def _test() -> None:
            from tom.ipc.protocol import ConnectionState

            # Always fail reconnects
            transport = ReconnectingMockTransport(fail_reconnect_times=999)
            from tom.schemas.config import IpcConfig

            config = IpcConfig(max_reconnect_attempts=3)
            client = NamedPipeIpcClient(
                config=config,
                transport=transport,
                base_delay_s=0.0,
                max_delay_s=0.0,
                enable_jitter=False,
            )

            await client.connect()
            assert client.state == ConnectionState.CONNECTED

            # Trigger disconnect
            transport.inbox.put_nowait(b"")

            # Wait for all retries to exhaust (3 attempts × ~0ms each)
            for _ in range(50):
                await asyncio.sleep(0.005)
                if client.state == ConnectionState.DISCONNECTED:
                    break

            assert client.state == ConnectionState.DISCONNECTED
            # 1 initial connect + 3 reconnect attempts
            assert transport.connect_call_count == 4

            # Client is unusable but close() must still be safe
            await client.close()

        run_async(_test())

    def test_close_during_reconnecting_exits_cleanly(self) -> None:
        """close() while RECONNECTING must cancel the loop and reach CLOSED."""

        async def _test() -> None:
            from tom.ipc.protocol import ConnectionState

            # Never succeed reconnect
            transport = ReconnectingMockTransport(fail_reconnect_times=999)
            from tom.schemas.config import IpcConfig

            # max_reconnect_attempts=0 means unlimited; loop stays alive until close()
            config = IpcConfig(max_reconnect_attempts=0)
            client = NamedPipeIpcClient(
                config=config,
                transport=transport,
                base_delay_s=0.0,
                max_delay_s=0.0,
                enable_jitter=False,
            )

            await client.connect()
            # Trigger disconnect
            transport.inbox.put_nowait(b"")

            # Let reconnect loop start and reach RECONNECTING
            for _ in range(20):
                await asyncio.sleep(0.005)
                if client.state == ConnectionState.RECONNECTING:
                    break

            assert client.state == ConnectionState.RECONNECTING
            reconnect_task = client.reconnect_task
            assert reconnect_task is not None
            assert not reconnect_task.done()

            # Close must cancel the loop and reach CLOSED
            await client.close()
            assert client.state == ConnectionState.CLOSED
            assert reconnect_task.done()

        run_async(_test())

    def test_pending_requests_failed_immediately_on_disconnect(self) -> None:
        """In-flight requests must receive ConnectionLostError when transport drops."""

        async def _test() -> None:
            transport = ReconnectingMockTransport(fail_reconnect_times=999)
            from tom.schemas.config import IpcConfig

            config = IpcConfig(max_reconnect_attempts=1)
            client = NamedPipeIpcClient(
                config=config,
                transport=transport,
                base_delay_s=0.0,
                max_delay_s=0.0,
                enable_jitter=False,
            )

            await client.connect()

            req_task = asyncio.create_task(client.request("slow.method", timeout_ms=5000))
            while not transport.outbox:
                await asyncio.sleep(0.005)

            assert client.pending_count == 1

            # Simulate connection drop
            transport.inbox.put_nowait(b"")

            with pytest.raises(ConnectionLostError):
                await req_task

            assert client.pending_count == 0
            await client.close()

        run_async(_test())

    def test_backoff_delays_increase_with_attempts(self) -> None:
        """_backoff_delay must return monotonically non-decreasing values without jitter."""
        transport = ReconnectingMockTransport()
        client = NamedPipeIpcClient(
            transport=transport,
            base_delay_s=0.5,
            max_delay_s=30.0,
            enable_jitter=False,
        )

        delays = [client._backoff_delay(i) for i in range(1, 8)]
        # Each delay must be >= the previous
        for i in range(1, len(delays)):
            assert delays[i] >= delays[i - 1], f"Delay decreased at attempt {i + 1}"
        # Must cap at max_delay_s
        assert all(d <= 30.0 for d in delays)

    def test_reconnect_task_property_none_before_disconnect(self) -> None:
        """reconnect_task must be None when no reconnection is in progress."""
        transport = ReconnectingMockTransport()
        client = NamedPipeIpcClient(transport=transport)
        assert client.reconnect_task is None
