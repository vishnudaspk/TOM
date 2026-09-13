# TOM Phase 2 — Python Core & IPC Client
## Implementation-Ready Architecture & Iteration Plan

**Author:** Principal Software Architect (planning pass)  
**Baseline:** Phase 1 complete — all 10 iterations verified, 79 Rust tests passing.  
**Audience:** Gemini/Antigravity coding agent executing Phase 2 one iteration at a time.  
**Status:** PLANNING DOCUMENT — Do not implement until approved.

---

## 1. Phase 2 Objective

> Establish a reliable, strongly typed, observable, cancellable, and reconnecting Python ↔ Rust IPC foundation so that Python becomes TOM's orchestration layer while Rust remains the low-level systems engine.

At the end of Phase 2, Python must be able to:

- Start up and reach the running `tom-engine` process.
- Connect through the Windows Named Pipe `\\.\pipe\tom-engine`.
- Serialize protocol v1 requests and deserialize protocol v1 responses using Pydantic v2.
- Issue requests asynchronously, correlating responses by request ID even under concurrency.
- Enforce per-request timeouts without leaking pending state.
- Handle asyncio cancellation cleanly.
- Reconnect automatically after engine disconnection using bounded exponential backoff.
- Detect engine lifecycle changes through periodic health checks.
- Shut down cleanly, leaving zero orphaned tasks.
- Expose a clean `EngineClient` API to future Python subsystems (Phase 3+).
- Provide structured logging for every IPC operation.
- Be thoroughly testable without requiring the Rust process for unit tests.

Phase 2 does **NOT** implement:
- Phase 3+ features (tools, memory, models, voice, vision, agents).
- Event streaming across the IPC boundary (Phase 1's `EventBus` is internal Rust only; see §13).
- Any change to the Rust engine.

---

## 2. Current Phase 1 Baseline

### Verified state

| Check | Result |
|---|---|
| `cargo fmt --check` | PASS (0 diffs) |
| `cargo check` | PASS (0 warnings) |
| `cargo clippy --all-targets --all-features -- -D warnings` | PASS (0 warnings) |
| `cargo test` | 79/79 PASS |
| `pytest tests/unit/ -v` | 9/9 PASS |

### What exists in Python already

| File | Description |
|---|---|
| `python/tom/__init__.py` | Package root |
| `python/tom/core/__init__.py` | Core module root |
| `python/tom/core/config.py` | Configuration loader (YAML + env override → TOMConfig) |
| `python/tom/ipc/__init__.py` | Empty IPC module root (placeholder) |
| `python/tom/schemas/config.py` | TOMConfig, IpcConfig, and all other Pydantic schemas |
| `python/tom/telemetry/logging.py` | Structured JSON logger with secret redaction (TomLoggerAdapter) |
| `tests/unit/core/` | Config loader tests |
| `tests/unit/telemetry/` | Logging tests |

### IpcConfig already in schemas/config.py

```python
class IpcConfig(BaseModel):
    pipe_name: str = r"\\.\pipe\tom-engine"
    connection_timeout_ms: int = 3000   # 3 s
    request_timeout_ms: int = 5000      # 5 s — intentionally > Rust's 500ms
    max_reconnect_attempts: int = 5
```

These values are the **existing configuration source of truth**. Phase 2 must read from `IpcConfig`; it must not hardcode values.

---

## 3. Architectural Boundary

```
Python Brain (Phase 2+)
  Future subsystems (tools, agents, memory, voice, ...)
      |
      v
  python/tom/core/engine.py      <- EngineClient (high-level typed API)
      |
      v
  python/tom/ipc/client.py       <- NamedPipeIpcClient (correlation + reconnect)
      |
      v
  python/tom/ipc/transport.py    <- NamedPipeTransport (framing, raw I/O)
      |
      v
  python/tom/ipc/protocol.py     <- Pydantic models + wire constants
  python/tom/ipc/errors.py       <- Exception hierarchy
  python/tom/core/lifecycle.py   <- Python startup/shutdown coordinator

      | Windows Named Pipe  \\.\pipe\tom-engine
      v

Rust Engine (Phase 1 — COMPLETE, DO NOT MODIFY)
  rust/tom-engine/src/ipc/server.rs
  rust/tom-engine/src/ipc/dispatcher.rs
  rust/tom-engine/src/ipc/handlers.rs
```

### Why each module exists

| Module | Role | Why separate |
|---|---|---|
| `protocol.py` | Pydantic models; wire constants | Single source of protocol truth; testable in isolation |
| `errors.py` | Exception hierarchy | Callers catch specific categories without importing transport |
| `transport.py` | Raw pipe I/O + framing | Mockable; keeps OS-level code isolated |
| `client.py` | Correlation, timeouts, reconnection | Main async complexity |
| `core/engine.py` | High-level typed API | Hides raw dict from future subsystems |
| `core/lifecycle.py` | Startup/shutdown coordinator | Manages services in ordered sequence |

---

## 4. Rust IPC Contract (Authoritative — Derived From Source)

### 4.1 Transport

- **Endpoint**: `\\.\pipe\tom-engine` (PIPE_NAME in server.rs)
- **Framing**: Newline-delimited JSON — every message ends with exactly `\n`
- **Max frame**: `MAX_FRAME_BYTES = 1,048,576` bytes (1 MB)
- **Connection**: Tokio Windows Named Pipe `ClientOptions`; reconnect by reopening

### 4.2 Request wire format (Python → Rust)

```json
{
  "id":      "<non-empty string, no leading/trailing whitespace>",
  "version": 1,
  "method":  "<non-empty string, no leading/trailing whitespace>",
  "params":  {}
}
```

Rust validation (IpcRequest::validate):
1. `version != 1` → VERSION_MISMATCH
2. `id.trim().is_empty()` → INVALID_REQUEST
3. `method.trim().is_empty()` → INVALID_REQUEST

### 4.3 Response wire format (Rust → Python)

Success:
```json
{"id": "...", "version": 1, "success": true, "data": {...}}
```

Error:
```json
{"id": "...", "version": 1, "success": false, "error": {"code": "...", "message": "..."}}
```

**Critical**: Rust uses `#[serde(skip_serializing_if = "Option::is_none")]` on both `data` and `error`.
- On success: the `error` key is **absent** (not null).
- On error: the `data` key is **absent** (not null).
- Python must declare both as `Optional[...] = None`.

### 4.4 Error codes (exhaustive)

| Code | Meaning |
|---|---|
| `NOT_FOUND` | Method not registered |
| `INVALID_REQUEST` | Empty id/method or malformed JSON |
| `INVALID_PARAMS` | Params rejected by handler |
| `TIMEOUT` | Rust-side handler exceeded 500ms |
| `VERSION_MISMATCH` | version field != 1 |
| `INTERNAL` | Rust internal error |
| `NOT_AVAILABLE` | Hardware sensor unavailable |

### 4.5 Registered methods

| Method | Params | Notes |
|---|---|---|
| `engine.ping` | `{}` | Returns `{"pong": true, "version": "<ENGINE_VERSION>"}` |
| `engine.status` | `{}` | Returns `{"engine": "tom-engine", "status": "running"}` |
| `system.cpu` | `{}` | usage_percent, core_count, frequency_mhz |
| `system.memory` | `{}` | total_bytes, used_bytes, available_bytes, swap fields |
| `system.gpu` | `{}` | available: bool; fields present only if available=true |
| `system.battery` | `{}` | available: bool; fields present only if available=true |
| `system.disk` | `{}` | `{"disks": [...]}` |
| `system.processes` | `{"limit": N}` | Default limit = 10 |
| `system.all` | `{}` | Aggregated {cpu, memory, gpu, battery, disk, processes} |

### 4.6 Dispatcher timeout

Rust-side per-request timeout: **500ms**.
Python request timeout must be **larger** (default: 5000ms per IpcConfig) so Rust's TIMEOUT error arrives before Python gives up. This preserves error observability — when a handler is slow, Python receives `RemoteError(code="TIMEOUT")` instead of a silent Python timeout.

---

## 5. Python IPC Architecture

### Module layout (final target after all iterations)

```
python/tom/
├── __init__.py                      (exists)
├── core/
│   ├── __init__.py                  (exists)
│   ├── config.py                    (exists — DO NOT MODIFY)
│   ├── engine.py                    [NEW] High-level EngineClient API
│   └── lifecycle.py                 [NEW] Python startup/shutdown coordinator
└── ipc/
    ├── __init__.py                  (exists — extend)
    ├── protocol.py                  [NEW] Pydantic models + wire constants
    ├── errors.py                    [NEW] Exception hierarchy
    ├── transport.py                 [NEW] Raw pipe framing layer
    └── client.py                    [NEW] Async IPC client

tests/
├── unit/
│   ├── core/                        (exists)
│   ├── telemetry/                   (exists)
│   └── ipc/                         [NEW]
│       ├── __init__.py
│       ├── test_protocol.py
│       ├── test_errors.py
│       ├── test_transport.py
│       ├── test_client.py
│       └── test_engine_client.py
└── integration/
    └── python_rust/                 [NEW]
        ├── __init__.py
        └── test_live_ipc.py
```

---

## 6. Protocol Model Design (protocol.py)

### Constants

```python
PROTOCOL_VERSION: int = 1
PIPE_NAME: str = r"\\.\pipe\tom-engine"
MAX_FRAME_BYTES: int = 1_048_576

class ErrorCode(str, Enum):
    NOT_FOUND        = "NOT_FOUND"
    INVALID_REQUEST  = "INVALID_REQUEST"
    INVALID_PARAMS   = "INVALID_PARAMS"
    TIMEOUT          = "TIMEOUT"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    INTERNAL         = "INTERNAL"
    NOT_AVAILABLE    = "NOT_AVAILABLE"
```

### IpcRequest model

```python
class IpcRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., min_length=1)
    version: int = Field(PROTOCOL_VERSION, ge=1, le=1)
    method: str = Field(..., min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
```

`extra="forbid"` on requests — Python controls what it sends; unknown fields are a bug.

**Request ID generation**:
```python
def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:12]}"
```

### IpcError model

```python
class IpcError(BaseModel):
    model_config = ConfigDict(extra="ignore")  # forward compat
    code: str
    message: str
```

### IpcResponse model

```python
class IpcResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")  # forward compat
    id: str
    version: int
    success: bool
    data: dict[str, Any] | None = None   # absent on error → None
    error: IpcError | None = None        # absent on success → None
```

`extra="ignore"` on responses — Rust may add fields in future versions; Python must stay forward-compatible.

### Serialization rules

- Requests: `model.model_dump(mode="json")` → `json.dumps(...)` + `"\n"`
- Responses: `json.loads(line.rstrip())` → `IpcResponse.model_validate(raw_dict)`

---

## 7. Transport Design (transport.py)

### Responsibility boundary

`NamedPipeTransport` owns:
- Opening/closing the Windows Named Pipe connection.
- Reading newline-delimited frames with MAX_FRAME_BYTES enforcement.
- Writing serialized JSON frames with `\n` terminator.
- UTF-8 encoding/decoding.
- Raising `TransportError` on I/O failures.

`NamedPipeTransport` does NOT own:
- Request IDs, response correlation, reconnection, Pydantic validation.

### TransportProtocol interface

```python
class TransportProtocol(Protocol):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def read_frame(self) -> bytes: ...
    async def write_frame(self, data: bytes) -> None: ...
    @property
    def is_connected(self) -> bool: ...
```

This interface enables `MockTransport` injection in all unit tests.

### Windows Named Pipe connection

Use `asyncio.ProactorEventLoop.create_pipe_connection` (Windows-only; available since Python 3.8):

```python
async def connect(self) -> None:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=MAX_FRAME_BYTES * 2)
    protocol = asyncio.StreamReaderProtocol(reader)
    try:
        transport, _ = await asyncio.wait_for(
            loop.create_pipe_connection(lambda: protocol, self._pipe_name),
            timeout=self._connect_timeout_s,
        )
    except FileNotFoundError:
        raise ConnectionError(f"Pipe not found: {self._pipe_name}")
    except asyncio.TimeoutError:
        raise ConnectionError(f"Timeout after {self._connect_timeout_s}s")
    self._writer = asyncio.StreamWriter(transport, protocol, reader, loop)
    self._reader = reader
    self._connected = True
```

No `pywin32` required for the connection itself. If `create_pipe_connection` has instability issues, `pywin32.win32file.CreateFile` in a thread pool executor is the documented fallback — surface this to the user before adding the dependency.

### Frame reading

```python
async def read_frame(self) -> bytes:
    line = await self._reader.readline()
    if len(line) > MAX_FRAME_BYTES:
        raise FrameTooLargeError(len(line))
    return line.rstrip(b"\n")
```

### Frame writing

```python
async def write_frame(self, data: bytes) -> None:
    self._writer.write(data + b"\n")
    await self._writer.drain()
```

---

## 8. Request Correlation Design (client.py)

### Pending request registry

```python
_pending: dict[str, asyncio.Future[IpcResponse]]
```

Key: `request.id`. Value: `asyncio.Future` resolved when response arrives.

### Send path (simplified)

```python
async def request(self, method: str, params: dict = {}, *, timeout_ms: int | None = None) -> dict:
    req = IpcRequest(id=new_request_id(), version=PROTOCOL_VERSION, method=method, params=params)
    fut: asyncio.Future[IpcResponse] = asyncio.get_running_loop().create_future()
    self._pending[req.id] = fut
    try:
        frame = json.dumps(req.model_dump(mode="json")).encode()
        await self._transport.write_frame(frame)
        timeout_s = (timeout_ms or self._config.request_timeout_ms) / 1000
        response = await asyncio.wait_for(asyncio.shield(fut), timeout=timeout_s)
        if not response.success:
            raise RemoteError(response.error.code, response.error.message)
        return response.data or {}
    except asyncio.TimeoutError:
        raise IpcTimeoutError(method, timeout_ms or self._config.request_timeout_ms)
    finally:
        self._pending.pop(req.id, None)
```

`asyncio.shield(fut)` prevents the future from being cancelled if the outer `wait_for` times out, ensuring the `finally` block can cleanly remove it from `_pending`.

### Receive loop (background task)

```python
async def _receive_loop(self) -> None:
    while True:
        try:
            frame = await self._transport.read_frame()
            response = IpcResponse.model_validate_json(frame)
            fut = self._pending.get(response.id)
            if fut and not fut.done():
                fut.set_result(response)
            # Unknown/duplicate ID: log warning, discard
        except Exception as exc:
            self._on_receive_error(exc)
            break
```

### Correlation edge cases

| Scenario | Behavior |
|---|---|
| Response arrives after timeout | Future removed from `_pending`; response discarded. No leak. |
| Response arrives after cancellation | Same — future removed in `finally`. |
| Duplicate response ID | `fut.done()` is True → WARNING logged, discarded. |
| Unknown response ID | Not in `_pending` → WARNING logged, discarded. |
| Connection loss with pending requests | `_fail_pending(ConnectionLostError(...))` fails all futures deterministically. |
| ID collision | UUID4 hex-12 = 48-bit space; negligible probability within session. |

---

## 9. Timeout Design

### Timeout layers

| Layer | Value | Where enforced | Behavior on expiry |
|---|---|---|---|
| Transport read (safety) | 30s | `NamedPipeTransport` | `TransportError` → reconnect |
| Python request timeout | 5000ms (IpcConfig) | `client.request()` | `IpcTimeoutError` to caller |
| Rust handler timeout | 500ms | Rust dispatcher | `TIMEOUT` error code in response |
| Connection timeout | 3000ms (IpcConfig) | `transport.connect()` | `ConnectionError` → reconnect |
| Shutdown timeout | 5s | `lifecycle.py` | Force-cancel remaining tasks |

### Python vs. Rust ordering

Python timeout (5000ms) > Rust timeout (500ms). Rust's TIMEOUT error arrives first as `RemoteError(code="TIMEOUT")`. Python's timeout fires only if no response arrives at all (pipe stall). This preserves error observability.

---

## 10. Cancellation Design

### asyncio cancellation behavior

`CancelledError` propagates normally through `await client.request(...)`. The `finally` block removes the pending future from `_pending`. No leak.

**Does cancellation propagate to Rust?** No — Rust has no cancellation protocol in Phase 2. If Python cancels, Rust may complete the work anyway; the response arrives, finds no matching future, and is discarded. This is correct and safe.

**Phase 2 documented limitation**: Cancellation does not stop Rust-side work. For Phase 2, all handlers complete in < 500ms, so this is acceptable. Cross-IPC cancellation is a Phase 3+ enhancement.

### Shutdown cancellation

```python
async def close(self) -> None:
    if self._receive_task and not self._receive_task.done():
        self._receive_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._receive_task
    self._fail_pending(ConnectionLostError("Client closing"))
    await self._transport.close()
```

---

## 11. Reconnection State Machine

### States

```
DISCONNECTED
    | connect()
    v
CONNECTING ──── failure ──> DISCONNECTED (schedule retry)
    | success
    v
CONNECTED
    | transport error / receive loop exits
    v
RECONNECTING ──── max retries exceeded ──> DISCONNECTED (permanent)
    | success
    v
CONNECTED
    | close() called
    v
CLOSED (terminal)
```

### State type

```python
class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING   = "connecting"
    CONNECTED    = "connected"
    RECONNECTING = "reconnecting"
    CLOSED       = "closed"
```

### Reconnection policy

- Strategy: **exponential backoff with full jitter**
- Base delay: 0.5s
- Max delay: 30s
- Jitter: `random.uniform(0, computed_delay)`
- Max attempts: `IpcConfig.max_reconnect_attempts` (default: 5)
- After max attempts: `MaxRetriesExceededError`; state → DISCONNECTED

```python
def _backoff_delay(self, attempt: int) -> float:
    delay = min(30.0, 0.5 * (2 ** attempt))
    return random.uniform(0, delay)
```

### Pending requests during disconnect

All pending futures immediately failed with `ConnectionLostError`. Requests do NOT queue during reconnection — callers receive `NotConnectedError` and can retry at the application level.

**Decision rationale**: Queuing adds complexity and ordering surprises. Local reconnection is fast (< 1s). Callers that need retry implement it explicitly.

### Shutdown interaction

`close()` sets state to `CLOSED`. Reconnect loop checks state and exits immediately. No retries after `close()`.

---

## 12. Lifecycle Design (lifecycle.py)

### Python startup sequence

```
1. configure_logging()
2. load_config() → TOMConfig
3. EngineClient(config.ipc) instantiated
4. await engine_client.connect()
5. await engine_client.ping()     ← verifies Rust is alive
6. await engine_client.status()   ← logs Rust version and status
7. (Phase 3+: initialize tools, memory, models, ...)
8. Python core ready
```

### Python shutdown sequence

```
1. Receive shutdown signal
2. Stop accepting new work
3. Cancel in-flight Python tasks
4. await engine_client.close()   ← cleans up receive loop + transport
5. (Phase 3+: stop tools, memory, models, ...)
6. logging shutdown
```

### Does Python start Rust?

**Decision**: No. In Phase 2, Python connects to an already-running `tom-engine`. If the pipe is not present, `connect()` raises `ConnectionError` and the reconnection loop begins. A future phase can add auto-launch. This is the minimal safe approach.

### LifecycleManager

```python
class LifecycleManager:
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def run_until_shutdown(self) -> None: ...
    @property
    def engine(self) -> EngineClient: ...
```

---

## 13. Event Design

### Current state

Phase 1's `EventBus` (Iteration 7) is an **internal Rust broadcast channel**. It is NOT exposed through IPC. There is no `engine.subscribe` method and no streaming protocol.

**Phase 2 decision**: Event streaming across IPC is **NOT implemented**. Reasons: no streaming protocol in Phase 1; out of scope for Phase 2; would require new IPC design.

**Phase 2 provides**: A Python-side `EngineEventBus` stub with the right API shape so future code compiles.

```python
class EngineEventBus:
    """Placeholder. Phase 2: no events cross IPC boundary."""
    async def subscribe(self) -> AsyncIterator[dict]:
        raise NotImplementedError("Event streaming not implemented until Phase 4+")
```

---

## 14. Error Taxonomy (errors.py)

```python
class TomError(Exception):
    """Base for all TOM Python errors."""

class IpcError(TomError):
    """IPC communication errors."""

class ConnectionError(IpcError):
    """Cannot connect to or reach the Rust engine pipe."""

class NotConnectedError(ConnectionError):
    """Request attempted while client is not CONNECTED."""

class ConnectionLostError(ConnectionError):
    """Connection dropped unexpectedly."""

class MaxRetriesExceededError(ConnectionError):
    """Reconnection failed after max_reconnect_attempts."""
    attempts: int

class TransportError(IpcError):
    """Transport-level error (I/O, framing, encoding)."""

class FrameTooLargeError(TransportError):
    """Frame exceeds MAX_FRAME_BYTES."""
    size: int

class ProtocolError(IpcError):
    """Protocol violation (bad version, malformed JSON, Pydantic)."""

class IpcTimeoutError(IpcError):
    """Python-side timeout waiting for response."""
    method: str
    timeout_ms: int

class RemoteError(IpcError):
    """Rust returned success=false."""
    code: str
    message: str

    @property
    def is_not_found(self) -> bool: return self.code == "NOT_FOUND"
    @property
    def is_not_available(self) -> bool: return self.code == "NOT_AVAILABLE"
    @property
    def is_timeout(self) -> bool: return self.code == "TIMEOUT"
    @property
    def is_version_mismatch(self) -> bool: return self.code == "VERSION_MISMATCH"

class LifecycleError(TomError):
    """Startup/shutdown sequence errors."""

class ConfigurationError(TomError):
    """Configuration loading or validation errors."""
```

### Retryability

| Exception | Retryable |
|---|---|
| `ConnectionError` | YES (reconnect loop) |
| `NotConnectedError` | YES (wait for reconnect) |
| `MaxRetriesExceededError` | NO |
| `TransportError` | YES (triggers reconnect) |
| `FrameTooLargeError` | NO (programming error) |
| `ProtocolError` | NO (version mismatch = code fix) |
| `IpcTimeoutError` | MAYBE |
| `RemoteError(NOT_AVAILABLE)` | NO |
| `RemoteError(TIMEOUT)` | MAYBE |
| `RemoteError(NOT_FOUND)` | NO |
| `LifecycleError` | NO |

---

## 15. Logging Design

### Mandatory fields per IPC operation

```python
# Request sent:
logger.debug("ipc_request_sent", request_id=req.id, method=req.method,
             connection_state=self.state.value)

# Response received (success):
logger.debug("ipc_response_received", request_id=response.id, method=method,
             duration_ms=elapsed_ms, success=True)

# Remote error:
logger.warning("ipc_remote_error", request_id=response.id, method=method,
               error_code=response.error.code, duration_ms=elapsed_ms)

# Timeout:
logger.warning("ipc_request_timeout", method=method, timeout_ms=timeout_ms,
               request_id=req.id)

# Connection state change:
logger.info("ipc_connection_state_changed", old_state=old.value, new_state=new.value,
            attempt=attempt_count)
```

### Rules

- `debug`: individual request/response pairs
- `info`: connection state changes, start/stop
- `warning`: remote errors, timeouts, unknown/duplicate IDs
- `error`: unrecoverable (max retries, lifecycle failures)
- **Never log**: `params` or `data` payloads (may contain PII or large buffers)
- **Always use** `TomLoggerAdapter` from `python/tom/telemetry/logging.py`
- All log output passes through existing `redact_secrets()`

---

## 16. Security Model

Phase 2 enforces:
1. **Protocol version validation**: Reject `version != 1` responses → `ProtocolError`.
2. **Response validation**: All wire responses through Pydantic before use.
3. **Endpoint pinning**: Pipe name from `IpcConfig.pipe_name`, not runtime user input.
4. **No network fallback**: Named pipe only. No TCP, no network socket.
5. **No shell execution**: Client sends only JSON method calls.
6. **Payload logging ban**: params/data never logged.
7. **Secret redaction**: Via existing `redact_secrets()`.
8. **Frame size enforcement**: `MAX_FRAME_BYTES = 1MB` on writes (mirrors Rust).

---

## 17. Configuration

All IPC settings flow from `python/tom/schemas/config.py → IpcConfig`. No new fields needed in Phase 2. Backoff constants live as module-level defaults in `client.py`.

---

## 18. Resource Management

Phase 2 uses:
- **One `asyncio.Task`** (receive loop) per `NamedPipeIpcClient`.
- **One `asyncio.StreamReader + StreamWriter`** pair.
- **One `dict`** for pending requests (bounded by concurrent request count).
- **No thread pool** for normal operations.
- **No `asyncio.TaskGroup`** in Phase 2 (introduced in Phase 3+ when multiple services run).

---

## 19. Performance Targets

Phase 1 Rust baseline: Average 43.69µs, P95 68.8µs (within Rust using duplex).

| Metric | Target |
|---|---|
| Python client overhead | < 3ms |
| End-to-end `ping()` P95 | < 10ms |
| Connection establishment | < 100ms |
| First reconnect | < 1s |
| Idle CPU | < 0.1% |

Benchmark in Iteration 8: 100 sequential `engine.ping()` calls, report P50/P95/P99.

---

## 20. Testing Architecture

### Principle

Unit tests use `MockTransport` — no Rust process required. Integration tests use the real pipe.

```python
class MockTransport:
    def __init__(self, responses: list[dict]):
        self._responses = iter(responses)
        self._last_sent: bytes = b""
    
    async def connect(self): pass
    async def close(self): pass
    async def write_frame(self, data: bytes): self._last_sent = data
    async def read_frame(self) -> bytes:
        r = next(self._responses)
        return json.dumps(r).encode()
    
    @property
    def is_connected(self) -> bool: return True
```

Inject via: `NamedPipeIpcClient(config, transport=MockTransport([...]))`

### Test layers

1. **Protocol** (`test_protocol.py`): Serialization, deserialization, missing fields, extra field behavior, error codes match Rust, `new_request_id()` uniqueness.
2. **Errors** (`test_errors.py`): All exception classes; hierarchy; `RemoteError` properties.
3. **Transport** (`test_transport.py`): Frame has `\n`, oversized raises `FrameTooLargeError`, connect timeout, `close()` idempotent.
4. **Client** (`test_client.py`): Success, remote error, timeout, cancellation, unknown ID, duplicate ID, connection drop, reconnect states, max retries.
5. **Engine client** (`test_engine_client.py`): All typed methods; `NOT_AVAILABLE` responses return typed unavailable models.
6. **Integration** (`test_live_ipc.py`, requires running engine — skip if absent): ping, status, all telemetry methods, concurrent requests, invalid method, lifecycle cycles.

---

## 21. Iteration Breakdown

| # | Title | Boundary |
|---|---|---|
| 1 | Protocol Models & Errors | Wire contract in code; exception taxonomy |
| 2 | Transport Layer | OS pipe I/O + framing; mockable |
| 3 | IPC Client Core | Correlation + send/receive loop |
| 4 | Timeouts & Cancellation | Edge case hardening |
| 5 | Reconnection State Machine | State transitions + backoff |
| 6 | High-Level Engine Client API | Typed domain models |
| 7 | Lifecycle Coordinator | Start/stop orchestration |
| 8 | Integration Hardening & Exit Gate | Live tests + benchmark |

---

## 22. Per-Iteration Detailed Specification

---

### Iteration 1 — Protocol Models & Errors

**Objective**: Pydantic v2 models that precisely mirror the Rust wire format, and the complete error taxonomy.

**Files to create**:
- `python/tom/ipc/protocol.py`
- `python/tom/ipc/errors.py`
- `tests/unit/ipc/__init__.py`
- `tests/unit/ipc/test_protocol.py`
- `tests/unit/ipc/test_errors.py`

**Files to modify**:
- `python/tom/ipc/__init__.py` — add exports

**Files that must NOT change**:
- All Rust files; `schemas/config.py`; `core/config.py`; `telemetry/logging.py`; `IMPLEMENTATIONPLAN.md`

**Implementation tasks**:
1. `protocol.py`: `PROTOCOL_VERSION = 1`, `PIPE_NAME`, `MAX_FRAME_BYTES`, `ErrorCode` enum, `IpcRequest`, `IpcError`, `IpcResponse`, `new_request_id()`.
2. `errors.py`: Full exception hierarchy from §14.
3. `test_protocol.py`: All model behaviors (serialization, deserialization, extra field behavior, all 7 ErrorCode values, request ID generation).
4. `test_errors.py`: All exception classes instantiable; hierarchy correct; `RemoteError` properties.

**Required skills**: `coding/validation`, `testing/python-testing`

**Verification commands**:
```powershell
pytest tests/unit/ -v
ruff check python/tom/ipc/protocol.py python/tom/ipc/errors.py
```

**Acceptance criteria**:
- [ ] `IpcRequest` serialization matches Rust wire format exactly
- [ ] `IpcResponse` with absent `error` → `error=None`; with absent `data` → `data=None`
- [ ] `extra="forbid"` on `IpcRequest` rejects unknown fields
- [ ] `extra="ignore"` on `IpcResponse` accepts unknown fields
- [ ] All 7 ErrorCode string values match Rust exactly (copy from `error_code` module)
- [ ] All exception classes instantiable; hierarchy is TomError > IpcError > ...
- [ ] `new_request_id()` produces unique non-empty strings
- [ ] All 9 existing tests still pass

**Exit gate**: `pytest tests/unit/ -v` — all pass. `ruff check python/` — clean.

---

### Iteration 2 — Transport Layer

**Objective**: `NamedPipeTransport` with NDJSON framing; `TransportProtocol` interface for mocking.

**Files to create**:
- `python/tom/ipc/transport.py`
- `tests/unit/ipc/test_transport.py`

**Files to modify**:
- `python/tom/ipc/__init__.py` — export `NamedPipeTransport`, `TransportProtocol`

**Implementation tasks**:
1. `TransportProtocol` (Python `Protocol`): `connect`, `close`, `read_frame`, `write_frame`, `is_connected`.
2. `NamedPipeTransport`: `create_pipe_connection` based connection; `readline()` frame reading with size check; `write + drain` frame writing.
3. `MockTransport` in test file for downstream injection.
4. Transport unit tests: frame has `\n`, oversized raises `FrameTooLargeError`, missing pipe raises `ConnectionError`, timeout raises `ConnectionError`, `close()` idempotent.

**Required skills**: `python/ipc-client`

**Acceptance criteria**:
- [ ] `write_frame(b"hello")` results in `b"hello\n"` on wire
- [ ] Frame > `MAX_FRAME_BYTES` raises `FrameTooLargeError`
- [ ] Missing pipe raises `ConnectionError` (not `FileNotFoundError`)
- [ ] Timeout raises `ConnectionError`
- [ ] `close()` twice does not raise
- [ ] `is_connected` True after connect, False after close

**Exit gate**: `pytest tests/unit/ipc/ -v` — all pass.

---

### Iteration 3 — IPC Client Core

**Objective**: `NamedPipeIpcClient` with `_pending` dict, background receive loop, and `request()` method.

**Files to create**:
- `python/tom/ipc/client.py`
- `tests/unit/ipc/test_client.py`

**Files to modify**:
- `python/tom/ipc/__init__.py` — export `NamedPipeIpcClient`

**Implementation tasks**:
1. `NamedPipeIpcClient.__init__(config: IpcConfig, transport: TransportProtocol | None = None)`.
2. `_pending: dict[str, asyncio.Future[IpcResponse]]`.
3. `async def request(method, params, *, timeout_ms) -> dict` — full implementation with `asyncio.shield`.
4. `async def _receive_loop()` — background task.
5. `_fail_pending(exc)` — fails all pending futures.
6. `async def connect()` — transport.connect() + start receive loop task.
7. `async def close()` — cancel receive loop, fail pending, close transport.
8. Log all operations with `TomLoggerAdapter`.

**Tests**: Successful request → data. Remote error → `RemoteError`. Timeout → `IpcTimeoutError`. Unknown ID → warning only. Duplicate ID → warning only. Drop → `ConnectionLostError`. After `close()`, `_pending` is empty.

**Exit gate**: `pytest tests/unit/ipc/ -v` — all pass.

---

### Iteration 4 — Timeouts & Cancellation

**Objective**: Harden and fully test timeout and cancellation edge cases.

**Files to modify**:
- `python/tom/ipc/client.py` — verify `asyncio.shield` usage and all cleanup paths
- `tests/unit/ipc/test_client.py` — add edge cases

**Additional tests**:
- Timeout → `_pending` empty immediately after
- Cancellation → `_pending` empty immediately after
- `request()` on closed/disconnected client → `NotConnectedError`
- `close()` during pending request → `ConnectionLostError` to caller; clean shutdown
- Back-to-back requests with tight timeouts do not cross-contaminate

**Acceptance criteria**:
- [ ] After timeout: `len(self._pending) == 0`
- [ ] After cancellation: `len(self._pending) == 0`
- [ ] `request()` on disconnected raises `NotConnectedError`
- [ ] All timeout values sourced from `IpcConfig`, none hardcoded in `client.py`

**Exit gate**: `pytest tests/unit/ipc/ -v` — all pass including edge cases.

---

### Iteration 5 — Reconnection State Machine

**Objective**: `ConnectionState`, reconnect loop, exponential backoff, state transitions.

**Files to modify**:
- `python/tom/ipc/protocol.py` — add `ConnectionState` enum
- `python/tom/ipc/client.py` — add `_state`, `_reconnect_loop()`, `state` property, backoff
- `tests/unit/ipc/test_client.py` — reconnection tests

**Implementation tasks**:
1. Add `ConnectionState` to `protocol.py`.
2. Add `_state: ConnectionState` to client with initial `DISCONNECTED`.
3. `_reconnect_loop()`: loop calling `transport.connect()` with backoff; check state == CLOSED to exit.
4. On receive loop error: set `RECONNECTING`, `_fail_pending(...)`, start reconnect loop.
5. On max retries: log `MaxRetriesExceededError`; set `DISCONNECTED`.
6. On `close()` during reconnect: set `CLOSED`; exit loop.
7. `state` property.

**Tests**: Connect → CONNECTED. Simulate disconnect → RECONNECTING → CONNECTED. Max failures → `MaxRetriesExceededError`. `close()` during RECONNECTING → CLOSED, no more retries.

**Acceptance criteria**:
- [ ] State transitions logged at INFO
- [ ] Backoff delays increase exponentially (verifiable via test with tiny delays)
- [ ] `max_reconnect_attempts` respected
- [ ] `close()` exits reconnect loop cleanly
- [ ] State observable via `client.state`

**Exit gate**: `pytest tests/unit/ipc/ -v` — all pass.

---

### Iteration 6 — High-Level Engine Client API

**Objective**: `EngineClient` in `python/tom/core/engine.py` with typed Pydantic response models.

**Files to create**:
- `python/tom/core/engine.py`
- `tests/unit/ipc/test_engine_client.py`

**Implementation tasks**:
1. Add response models to `protocol.py`:
   - `PingResponse(pong: bool, version: str)`
   - `StatusResponse(engine: str, status: str)`
   - `CpuInfo(usage_percent: float, core_count: int, frequency_mhz: float | None = None)`
   - `MemoryInfo(total_bytes: int, used_bytes: int, available_bytes: int)` + swap fields
   - `GpuInfo(available: bool, name: str | None = None, utilization_percent: float | None = None, ...)` — all hardware fields Optional
   - `BatteryInfo(available: bool, percent: float | None = None, charging: bool | None = None)`
   - `DiskVolume(mount_point: str, total_bytes: int, available_bytes: int)`
   - `DiskInfo(disks: list[DiskVolume])`
   - `ProcessInfo(pid: int, name: str, cpu_percent: float, memory_bytes: int)`
   - `ProcessesInfo(processes: list[ProcessInfo])`
   - `SystemSnapshot(cpu: CpuInfo, memory: MemoryInfo, gpu: GpuInfo, battery: BatteryInfo, disk: DiskInfo, processes: ProcessesInfo)`
2. `EngineClient(ipc_client: NamedPipeIpcClient)`:
   - `async def ping() -> PingResponse`
   - `async def status() -> StatusResponse`
   - `async def get_cpu() -> CpuInfo`
   - `async def get_memory() -> MemoryInfo`
   - `async def get_gpu() -> GpuInfo`
   - `async def get_battery() -> BatteryInfo`
   - `async def get_disk() -> DiskInfo`
   - `async def get_processes(limit: int = 10) -> ProcessesInfo`
   - `async def get_system_snapshot() -> SystemSnapshot`
3. Each method: `data = await self._ipc.request(...)` → `Model.model_validate(data)`.
4. Unit tests with `MockTransport`.

**Acceptance criteria**:
- [ ] `ping()` returns `PingResponse(pong=True, ...)`
- [ ] `get_gpu()` with `{"available": false}` returns `GpuInfo(available=False)` without error
- [ ] `get_battery()` with `{"available": false}` returns `BatteryInfo(available=False)` without error
- [ ] `RemoteError` propagates from IPC client through `EngineClient`
- [ ] No raw dict access in callers — all return typed models

**Exit gate**: `pytest tests/unit/ipc/ -v` — all pass.

---

### Iteration 7 — Lifecycle Coordinator

**Objective**: `LifecycleManager` coordinating Python startup and shutdown.

**Files to create**:
- `python/tom/core/lifecycle.py`
- `tests/unit/ipc/test_lifecycle.py`

**Implementation tasks**:
1. `LifecycleManager(config: TOMConfig)`:
   - `async def start()`: configure_logging → `NamedPipeIpcClient(config.ipc)` → `EngineClient(ipc)` → `ipc.connect()` → `engine.ping()` → `engine.status()` → log ready.
   - `async def stop()`: close engine → log done.
   - `async def run_until_shutdown()`: asyncio event wait loop + Ctrl+C handling.
   - `engine: EngineClient` property (raises `LifecycleError` if not started).
2. Startup failure → `LifecycleError` with helpful message.
3. `stop()` idempotent.
4. Unit tests with mocked `EngineClient`.

**Acceptance criteria**:
- [ ] `start()` calls `ping()` then `status()` before declaring ready
- [ ] `start()` failure raises `LifecycleError`
- [ ] `stop()` idempotent
- [ ] `engine` property raises `LifecycleError` before `start()`

**Exit gate**: `pytest tests/unit/ -v` — all pass.

---

### Iteration 8 — Integration Hardening & Phase 2 Exit Gate

**Objective**: End-to-end validation against the real Rust engine. Concurrency, reconnect, performance, resource checks.

**Files to create**:
- `tests/integration/__init__.py`
- `tests/integration/python_rust/__init__.py`
- `tests/integration/python_rust/test_live_ipc.py`
- `tests/integration/python_rust/test_lifecycle.py`
- `tests/integration/python_rust/bench_ipc.py` (benchmark script)

**Files to modify**:
- `pyproject.toml` — add `asyncio_mode = "auto"` to `[tool.pytest.ini_options]`
- `STATE.md`, `PROGRESS.md`, `HANDOFF.md` — update after all tests pass

**Integration test checklist**:
- [ ] `ping()` → `pong=True`
- [ ] `status()` → `engine="tom-engine"`, `status="running"`
- [ ] `get_cpu()` → `CpuInfo` with valid fields
- [ ] `get_memory()` → `MemoryInfo` with `total_bytes > 0`
- [ ] `get_gpu()` → `GpuInfo` (available may be False — that's OK)
- [ ] `get_battery()` → `BatteryInfo` (available may be False)
- [ ] `get_disk()` → `DiskInfo` with at least one disk
- [ ] `get_processes(limit=5)` → ≤ 5 processes
- [ ] `get_system_snapshot()` → all 6 domains present
- [ ] 10 concurrent `ping()` via `asyncio.gather` → all return `pong=True`, all IDs unique
- [ ] `request("nonexistent.method", {})` → `RemoteError(code="NOT_FOUND")`
- [ ] Request with `version=999` → `RemoteError(code="VERSION_MISMATCH")`
- [ ] `LifecycleManager.start()` → `stop()` → `start()` → `stop()` (2 cycles, no leaks)
- [ ] P95 `ping()` latency < 10ms (from `bench_ipc.py`)
- [ ] Zero leaked asyncio tasks after 100 requests

**Final verification commands**:
```powershell
# Unit (no Rust required)
pytest tests/unit/ -v

# Integration (requires running tom-engine; skip gracefully if absent)
pytest tests/integration/ -v

# Benchmark
python tests/integration/python_rust/bench_ipc.py

# Linting
ruff check python/ tests/
ruff format --check python/ tests/

# Rust regression (must still pass)
cd rust\tom-engine && cargo test
```

**Exit gate**: All criteria met. STATE.md updated to Phase 2 COMPLETE.

---

## 23. Phase 2 Exit Gate (Full Checklist)

```
[ ] pytest tests/unit/ -v                              ALL PASS
[ ] pytest tests/integration/python_rust/ -v           ALL PASS (or skip if no engine)
[ ] ruff check python/ tests/                          0 violations
[ ] ruff format --check python/ tests/                 0 diffs
[ ] cargo test (in rust/tom-engine)                    79/79 PASS
[ ] Python P95 ping latency                            < 10ms
[ ] No leaked asyncio tasks after 100 requests         VERIFIED
[ ] LifecycleManager 2-cycle test                      PASS
[ ] 10-concurrent-request test                         PASS, all IDs correct
[ ] STATE.md updated: Phase 2 COMPLETE                 DONE
[ ] PROGRESS.md: all 8 iterations recorded             DONE
[ ] HANDOFF.md: Phase 3 starting point written         DONE
```

---

## 24. Documentation / State Management

### After each iteration

- **`STATE.md`**: Update "Current Task" to next iteration; add new files to "Implemented Components"; update test counts.
- **`PROGRESS.md`**: Record iteration number, title, files changed, test results, decisions made.

### After Phase 2 complete

- **`STATE.md`**: Phase 2 COMPLETE. All new Python components documented.
- **`PROGRESS.md`**: Full iteration history.
- **`HANDOFF.md`**: Rewrite "Starting Point" section for Phase 3.

### Must NOT be modified

- `IMPLEMENTATIONPLAN.md`
- `PHASE2_IMPLEMENTATIONPLAN.md` (this file)

---

## 25. Gemini Implementation Handoff

**This section is the primary instruction set for the agent executing Phase 2.**

### What Phase 2 accomplishes

Python becomes TOM's orchestration layer by establishing a reliable, typed, reconnecting async IPC client over the Windows Named Pipe implemented in Phase 1 Rust.

### Before writing any code — READ THESE

1. `IMPLEMENTATIONPLAN.md` §Phase 2 (lines 134–160) — do not modify.
2. `STATE.md` — understand current state.
3. `PROGRESS.md` — understand Phase 1 history.
4. `HANDOFF.md` — read the Rust IPC contract section carefully.
5. `PHASE2_IMPLEMENTATIONPLAN.md` (this document) — your primary execution guide.
6. `rust/tom-engine/src/ipc/protocol.rs` — verify wire format against §4.
7. `python/tom/schemas/config.py` — understand `IpcConfig` values.
8. `python/tom/telemetry/logging.py` — understand `TomLoggerAdapter` usage.

### Load these skills before each implementation session

- `skills/python/ipc-client/SKILL.md`
- `skills/coding/validation/SKILL.md`
- `skills/coding/structured-logging/SKILL.md`
- `skills/testing/python-testing/SKILL.md`
- `skills/system-design/python-rust-boundary/SKILL.md`

### Iteration execution order

Execute **one iteration at a time**, in order 1→8. Do not start Iteration N+1 until:
- All tests for Iteration N pass
- `STATE.md` and `PROGRESS.md` are updated

### What must NOT change (ever, in Phase 2)

- Any file in `rust/tom-engine/`
- `IMPLEMENTATIONPLAN.md`
- `PHASE2_IMPLEMENTATIONPLAN.md`
- `python/tom/schemas/config.py`
- `python/tom/core/config.py`
- `python/tom/telemetry/logging.py`
- `tests/unit/core/` (existing tests)
- `tests/unit/telemetry/` (existing tests)

### After each iteration — run these

```powershell
pytest tests/unit/ -v          # must all pass
ruff check python/ tests/      # must be clean
```

### When to stop and report instead of improvising

STOP and report to the user if:
- `create_pipe_connection` is unavailable and the alternative requires `pywin32` — confirm before adding the dependency.
- Any test failure cannot be resolved without architectural changes.
- The Rust engine returns unexpected response format not matching §4.
- Any design decision in this plan conflicts with a concrete implementation discovery.

Do NOT silently change the architecture. Surface conflicts explicitly.

### No new runtime dependencies needed

`asyncio` is stdlib. `pydantic` is already in `pyproject.toml`. Add `asyncio_mode = "auto"` to pytest config in Iteration 8.

---

## 26. Architectural Decisions (All Resolved)

| # | Question | Decision | Rationale |
|---|---|---|---|
| 1 | Separate `transport.py` from `client.py`? | **YES** | Transport is mockable OS I/O; client is async correlation logic |
| 2 | Where does framing live? | `transport.py` | Framing is a transport concern |
| 3 | Where does JSON serialization happen? | `client.py` produces bytes; `transport.py` handles byte I/O | Clean separation |
| 4 | Where does Pydantic validation happen? | `client.py` parses `IpcResponse`; `engine.py` parses data fields | Two-layer |
| 5 | How are request IDs generated? | `req_{uuid4().hex[:12]}` | Human-readable, unique, no collision risk |
| 6 | How are pending requests stored? | `dict[str, asyncio.Future[IpcResponse]]` | O(1) lookup; bounded |
| 7 | Response arrives after timeout? | Discard (future removed in `finally`) | No leak |
| 8 | Response arrives after cancellation? | Discard (future removed in `finally`) | No leak |
| 9 | Pending requests after disconnect? | Immediately failed with `ConnectionLostError` | Deterministic failure |
| 10 | Queue requests during reconnect? | **NO** — fail with `NotConnectedError` | No ordering surprises |
| 11 | Backoff strategy? | Exponential with full jitter, 0.5s base, 30s cap | Standard; prevents thundering herd |
| 12 | State exposed how? | `client.state: ConnectionState` property | Observable without polling |
| 13 | Lifecycle coordinates IPC how? | `LifecycleManager` → `EngineClient` → `NamedPipeIpcClient` | Clear ownership chain |
| 14 | How does Python know Rust is healthy? | `ping()` on startup; no background polling in Phase 2 | Sufficient for Phase 2 |
| 15 | Does Python start Rust? | **NO** — connects to already-running engine | Minimal for Phase 2 |
| 16 | Rust lifecycle events in Python? | **NOT in Phase 2** — EventBus is internal Rust | No streaming protocol exists |
| 17 | Protocol version mismatch behavior? | `ProtocolError`; not retried | Requires code fix |
| 18 | Remote Rust errors mapped how? | `RemoteError(code, message)` with properties | Clean; code preserved |
| 19 | Malformed JSON behavior? | `ProtocolError`; restart connection | Indicates a bug |
| 20 | Oversized frame behavior? | `FrameTooLargeError`; not retried | Programming error |
| 21 | Shutdown vs. reconnect interaction? | `close()` → CLOSED; reconnect loop exits immediately | No retries after close |
| 22 | Task tracking? | `_receive_task`, `_reconnect_task` as explicit attributes | Cancellable; no leaks |
| 23 | Task exception surfacing? | `add_done_callback()` on receive task | Unexpected exceptions logged |
| 24 | Mandatory log fields? | `request_id`, `method`, `duration_ms`, `connection_state` | Minimum for observability |
| 25 | Mock vs. real Rust in tests? | Unit: all mock. Integration: real Rust (skip if absent) | Clean separation |

---

## 27. Known Constraints

1. **Windows-only transport**: `create_pipe_connection` is Windows-only. Phase 2 is intentionally Windows-only, matching Phase 1.
2. **Python 3.11+**: Enforced by `pyproject.toml`.
3. **No pywin32 by default**: Prefer stdlib `asyncio`. Fall back to `pywin32` only if `create_pipe_connection` is unstable — confirm with user first.
4. **Rust EventBus not exposed**: Internal events not available to Python in Phase 2.
5. **Single pipe connection**: One client, one connection. Multiple clients are supported by Rust but not needed in Phase 2.
6. **No message replay after reconnect**: Requests in flight during disconnect are failed; no replay.

---

## 28. Future Compatibility Notes

1. **Event streaming**: When Phase 4+ adds events, add `subscribe()` to `EngineClient`. Do not change the request/response pipe.
2. **Protocol v2**: Update `PROTOCOL_VERSION` in `protocol.py` if Rust bumps. The `extra="ignore"` on `IpcResponse` handles new fields transparently.
3. **Process management**: Future phases may have Python spawn `tom-engine`. `LifecycleManager.start()` signature stays the same.
4. **Cross-platform**: If TOM is ported to Linux/macOS, add `UnixSocketTransport` implementing `TransportProtocol`. `NamedPipeIpcClient` needs no changes.
5. **Multi-client**: Rust already supports multiple simultaneous connections. Adding a second Python client requires only instantiating a second `NamedPipeIpcClient`.
