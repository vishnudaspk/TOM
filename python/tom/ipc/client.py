"""IPC Client Core for TOM.

Provides NamedPipeIpcClient:
- Asynchronous request correlation over TransportProtocol
- Unique request ID tracking with pending Future registry
- Single background receive loop
- Strong validation of responses and remote error mapping
- Safe teardown with deterministic pending request failure
"""

import asyncio
import json
import random
import time
from typing import Any

from tom.ipc.errors import (
    ConnectionLostError,
    IpcTimeoutError,
    MaxRetriesExceededError,
    NotConnectedError,
    RemoteError,
    TransportError,
)
from tom.ipc.protocol import (
    PROTOCOL_VERSION,
    ConnectionState,
    IpcRequest,
    IpcResponse,
    new_request_id,
)
from tom.ipc.transport import NamedPipeTransport, TransportProtocol
from tom.schemas.config import IpcConfig
from tom.telemetry.logging import get_logger

logger = get_logger("tom.ipc.client")


class NamedPipeIpcClient:
    """Core IPC client communicating with tom-engine over TransportProtocol."""

    def __init__(
        self,
        config: IpcConfig | None = None,
        transport: TransportProtocol | None = None,
        *,
        base_delay_s: float = 0.5,
        max_delay_s: float = 30.0,
        enable_jitter: bool = True,
    ) -> None:
        """Initialize IPC client with configuration and transport seam.

        If transport is not provided, NamedPipeTransport is constructed
        from the given or default IpcConfig.
        """
        self._config = config or IpcConfig()
        self._transport = transport or NamedPipeTransport(config=self._config)
        self._pending: dict[str, asyncio.Future[IpcResponse]] = {}
        self._receive_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._closed: bool = False
        self._state: ConnectionState = ConnectionState.DISCONNECTED
        self._base_delay_s: float = base_delay_s
        self._max_delay_s: float = max_delay_s
        self._enable_jitter: bool = enable_jitter

    @property
    def state(self) -> ConnectionState:
        """Return the current connection state."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """True if the client is connected and its receive loop is actively running."""
        return (
            self._state == ConnectionState.CONNECTED
            and not self._closed
            and self._transport.is_connected
            and self._receive_task is not None
            and not self._receive_task.done()
        )

    @property
    def transport(self) -> TransportProtocol:
        """Return the underlying transport instance."""
        return self._transport

    @property
    def pending_count(self) -> int:
        """Return the number of requests currently awaiting responses."""
        return len(self._pending)

    @property
    def reconnect_task(self) -> asyncio.Task[None] | None:
        """Return the active reconnection background task, if any."""
        return self._reconnect_task

    def _set_state(self, new_state: ConnectionState) -> None:
        """Update connection state and log the state transition."""
        if self._state == new_state:
            return
        old_state = self._state
        self._state = new_state
        logger.info(
            "ipc_client_state_transition",
            previous_state=str(old_state),
            new_state=str(new_state),
        )

    def _backoff_delay(self, attempt: int) -> float:
        """Compute exponential backoff delay with optional full jitter.

        Formula: min(max_delay, base_delay * 2^(attempt - 1))
        With full jitter: uniform random in [0, computed_delay]
        """
        raw_delay = min(self._max_delay_s, self._base_delay_s * (2 ** (attempt - 1)))
        if self._enable_jitter and raw_delay > 0:
            return random.uniform(0.0, raw_delay)
        return raw_delay

    async def connect(self) -> None:
        """Establish transport connection and start the background receive loop.

        Raises:
            ConnectionError: If connection to engine fails.
        """
        if self._state == ConnectionState.CONNECTED:
            return

        self._closed = False

        # Cancel any active reconnect loop if connect is called explicitly
        if self._reconnect_task is not None and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

        self._set_state(ConnectionState.CONNECTING)

        try:
            await self._transport.connect()
        except Exception:
            self._set_state(ConnectionState.DISCONNECTED)
            raise

        loop = asyncio.get_running_loop()
        self._receive_task = loop.create_task(
            self._receive_loop(),
            name="tom_ipc_receive_loop",
        )
        self._set_state(ConnectionState.CONNECTED)
        logger.info(
            "ipc_client_connected",
            pipe_name=getattr(self._transport, "pipe_name", "unknown"),
        )

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        """Send an IPC request and await its correlated response.

        Args:
            method: Registered engine method name (e.g. 'engine.ping').
            params: Optional invocation parameter dictionary.
            timeout_ms: Optional per-request timeout in milliseconds. If omitted,
                defaults to the configured IpcConfig.request_timeout_ms.

        Returns:
            Dictionary containing response data payload on success.

        Raises:
            NotConnectedError: If client is not connected.
            IpcTimeoutError: If response is not received within the timeout.
            RemoteError: If Rust engine returns success=false.
            ConnectionLostError: If connection drops while request is in flight.
        """
        if not self.is_connected:
            raise NotConnectedError("IPC client is not connected to engine")

        req_id = new_request_id()
        req = IpcRequest(
            id=req_id,
            version=PROTOCOL_VERSION,
            method=method,
            params=params or {},
        )

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[IpcResponse] = loop.create_future()
        self._pending[req.id] = fut

        effective_timeout_ms = (
            timeout_ms if timeout_ms is not None else self._config.request_timeout_ms
        )
        timeout_s = effective_timeout_ms / 1000.0

        start_time = time.perf_counter()
        logger.debug(
            "ipc_request_sent",
            request_id=req.id,
            method=method,
            timeout_ms=effective_timeout_ms,
        )

        try:
            frame = json.dumps(req.model_dump(mode="json")).encode("utf-8")
            await self._transport.write_frame(frame)

            try:
                response = await asyncio.wait_for(
                    asyncio.shield(fut),
                    timeout=timeout_s,
                )
            except TimeoutError:
                duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                logger.warning(
                    "ipc_request_timeout",
                    request_id=req.id,
                    method=method,
                    timeout_ms=effective_timeout_ms,
                    duration_ms=duration_ms,
                )
                raise IpcTimeoutError(method, effective_timeout_ms) from None
            except asyncio.CancelledError:
                duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                logger.debug(
                    "ipc_request_cancelled",
                    request_id=req.id,
                    method=method,
                    duration_ms=duration_ms,
                )
                raise

            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

            if not response.success:
                err = response.error
                err_code = err.code if err else "INTERNAL"
                err_msg = err.message if err else "Unknown remote error"
                logger.warning(
                    "ipc_remote_error",
                    request_id=response.id,
                    method=method,
                    error_code=err_code,
                    duration_ms=duration_ms,
                )
                raise RemoteError(err_code, err_msg)

            logger.debug(
                "ipc_response_received",
                request_id=response.id,
                method=method,
                duration_ms=duration_ms,
                success=True,
            )
            return response.data or {}

        finally:
            self._pending.pop(req.id, None)

    async def _receive_loop(self) -> None:
        """Background task reading wire frames and resolving correlated Futures."""
        logger.debug("ipc_receive_loop_started")
        disconnected_by_error = False
        try:
            while self._transport.is_connected:
                try:
                    frame = await self._transport.read_frame()
                except (ConnectionLostError, NotConnectedError) as err:
                    logger.warning("ipc_receive_loop_disconnected", error=str(err))
                    disconnected_by_error = True
                    break
                except TransportError as err:
                    logger.warning("ipc_receive_loop_transport_error", error=str(err))
                    disconnected_by_error = True
                    break

                try:
                    response = IpcResponse.model_validate_json(frame)
                except Exception as parse_err:
                    logger.warning("ipc_response_malformed", error=str(parse_err))
                    continue

                fut = self._pending.get(response.id)
                if fut is None:
                    logger.warning(
                        "ipc_response_unknown_id",
                        request_id=response.id,
                    )
                    continue

                if fut.done():
                    logger.warning(
                        "ipc_response_duplicate_or_late",
                        request_id=response.id,
                    )
                    continue

                try:
                    fut.set_result(response)
                except asyncio.InvalidStateError:
                    logger.warning(
                        "ipc_response_future_already_resolved",
                        request_id=response.id,
                    )

        except asyncio.CancelledError:
            logger.debug("ipc_receive_loop_cancelled")
            raise
        except Exception as exc:
            logger.error("ipc_receive_loop_unexpected_error", error=str(exc))
            disconnected_by_error = True
        finally:
            msg = "IPC client closed" if self._closed else "Transport connection lost"
            self._fail_pending(ConnectionLostError(msg))
            logger.debug("ipc_receive_loop_stopped")

        # If we broke out due to a transport error (not due to intentional close/cancel),
        # kick off automatic reconnection.
        if disconnected_by_error and not self._closed:
            self._set_state(ConnectionState.RECONNECTING)
            loop = asyncio.get_running_loop()
            self._reconnect_task = loop.create_task(
                self._reconnect_loop(),
                name="tom_ipc_reconnect_loop",
            )

    def _fail_pending(self, exc: Exception) -> None:
        """Fail all currently pending request Futures with the specified exception."""
        pending_items = list(self._pending.items())
        self._pending.clear()
        for _req_id, fut in pending_items:
            if not fut.done():
                fut.set_exception(exc)

    async def _reconnect_loop(self) -> None:
        """Background reconnection task: retry transport.connect() with exponential backoff.

        Exits when:
        - Connection is re-established (state → CONNECTED, receive loop restarted).
        - Max reconnect attempts exceeded (state → DISCONNECTED, raises MaxRetriesExceededError logged).
        - Client is closed (state == CLOSED, exits silently).
        """
        attempt = 0
        max_attempts = self._config.max_reconnect_attempts

        logger.info(
            "ipc_reconnect_loop_started",
            max_attempts=max_attempts,
        )

        while self._state != ConnectionState.CLOSED:
            attempt += 1

            if max_attempts > 0 and attempt > max_attempts:
                logger.error(
                    "ipc_reconnect_max_retries_exceeded",
                    attempts=attempt - 1,
                    max_attempts=max_attempts,
                )
                self._set_state(ConnectionState.DISCONNECTED)
                # Raise so callers awaiting the task see the failure.
                raise MaxRetriesExceededError(
                    f"Failed to reconnect after {max_attempts} attempt(s)"
                )

            delay = self._backoff_delay(attempt)
            logger.info(
                "ipc_reconnect_attempt",
                attempt=attempt,
                max_attempts=max_attempts,
                delay_s=round(delay, 3),
            )

            # Wait the backoff delay; bail early if closed.
            if delay > 0:
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    logger.debug("ipc_reconnect_loop_cancelled_during_sleep")
                    return
            else:
                # Zero delay: still yield so other tasks (e.g. close()) can run.
                await asyncio.sleep(0)

            if self._state == ConnectionState.CLOSED:
                return

            try:
                await self._transport.connect()
            except asyncio.CancelledError:
                logger.debug("ipc_reconnect_loop_cancelled_during_connect")
                return
            except Exception as exc:
                logger.warning(
                    "ipc_reconnect_attempt_failed",
                    attempt=attempt,
                    error=str(exc),
                )
                continue  # Retry with next backoff delay

            # Successfully reconnected — restart receive loop.
            if self._state == ConnectionState.CLOSED:
                # Closed while we were connecting; clean up.
                await self._transport.close()
                return

            loop = asyncio.get_running_loop()
            self._receive_task = loop.create_task(
                self._receive_loop(),
                name="tom_ipc_receive_loop",
            )
            self._set_state(ConnectionState.CONNECTED)
            logger.info(
                "ipc_reconnect_succeeded",
                attempt=attempt,
                pipe_name=getattr(self._transport, "pipe_name", "unknown"),
            )
            return

    async def close(self) -> None:
        """Idempotently close the IPC client, stop receive loop, and release transport."""
        if self._closed:
            return
        self._closed = True
        self._set_state(ConnectionState.CLOSED)

        # Cancel reconnect loop first so it doesn't restart the receive loop.
        if self._reconnect_task is not None and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except (asyncio.CancelledError, MaxRetriesExceededError):
                pass
            self._reconnect_task = None

        if self._receive_task is not None and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        self._fail_pending(ConnectionLostError("IPC client closed"))
        await self._transport.close()
        logger.info("ipc_client_closed")
