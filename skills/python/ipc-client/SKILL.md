---
name: python-ipc-client
description: >
  How to implement and use TOM's Python-side IPC client for communicating with tom-engine.
  Use when building the IPC client, adding a new IPC method call, handling IPC connection
  failures, or subscribing to events forwarded from the Rust engine. Also use when the
  Python side cannot reach the engine or when IPC calls are timing out.
---

# Python IPC Client

TOM's Python brain communicates with the Rust engine (`tom-engine`) through a local JSON IPC
protocol. This skill covers the Python client in `ipc/client.py`.

---

## Design Constraints (from plan.md)

- JSON over local socket / named pipe — no gRPC at MVP.
- Every call must have a timeout — no call should hang Python indefinitely.
- IPC calls should be async — do not block the event loop.
- The protocol version must match what the engine expects.

---

## Client Interface

```python
from pydantic import BaseModel
from typing import Any

class IPCClient:
    """Async JSON IPC client for tom-engine communication."""

    async def call(
        self,
        method: str,
        params: dict[str, Any] = {},
        timeout_ms: int = 500,
    ) -> dict[str, Any]:
        """
        Send a request to tom-engine and return the data payload.
        Raises IPCError on failure, IPCTimeout on timeout.
        """
        ...

    async def subscribe_events(self) -> AsyncIterator[dict]:
        """
        Subscribe to events forwarded from the Rust engine.
        Yields parsed event dicts.
        """
        ...
```

---

## Request/Response Format

```python
import uuid

def build_request(method: str, params: dict) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "version": IPC_PROTOCOL_VERSION,  # from ipc/protocol.py
        "method": method,
        "params": params,
    }
```

Use `asyncio.wait_for` with the declared timeout:

```python
async def call(self, method, params={}, timeout_ms=500):
    request = build_request(method, params)
    try:
        raw = await asyncio.wait_for(
            self._send_and_receive(request),
            timeout=timeout_ms / 1000,
        )
    except asyncio.TimeoutError:
        raise IPCTimeout(f"IPC call '{method}' timed out after {timeout_ms}ms")

    response = parse_response(raw)
    if not response["success"]:
        raise IPCError(response.get("error", {}))
    return response.get("data", {})
```

---

## Error Types

```python
class IPCError(Exception):
    def __init__(self, error: dict):
        self.code = error.get("code", "UNKNOWN")
        self.message = error.get("message", "IPC error")
        super().__init__(f"IPC error [{self.code}]: {self.message}")

class IPCTimeout(Exception):
    pass

class IPCConnectionError(Exception):
    pass
```

Callers can catch specific error types:

```python
try:
    result = await ipc.call("system.gpu_temperature")
except IPCTimeout:
    logger.warning("GPU temperature call timed out")
    return None
except IPCError as e:
    if e.code == "NOT_AVAILABLE":
        return None
    raise
```

---

## Connection Management

The client should handle reconnection gracefully. The Rust engine may restart
independently of the Python process.

```python
async def _ensure_connected(self):
    if not self._connected:
        try:
            await self._connect()
        except OSError as e:
            raise IPCConnectionError(f"Cannot reach tom-engine: {e}")
```

On connection failure, callers receive `IPCConnectionError`, not a Python crash.

---

## Protocol Version Constant

Define the protocol version in `ipc/protocol.py`, not inline in client code.

```python
# ipc/protocol.py
IPC_PROTOCOL_VERSION = 1
```

Bump this alongside the Rust-side version when the schema changes.

---

## Usage in Tools

System tools call the IPC client through the tool manager's injected context:

```python
# tools/system.py
async def get_gpu_temperature(params, ctx) -> GpuTempOutput:
    data = await ctx.ipc.call("system.gpu_temperature")
    return GpuTempOutput(temperature_c=data["temperature_c"])
```

The IPC client is injected via `TOMDependencies` — not imported as a global.

---

## Testing

Test the IPC client against a fake engine, not a real socket.

```python
class FakeIPCClient:
    async def call(self, method: str, params: dict = {}, timeout_ms: int = 500) -> dict:
        if method == "system.gpu_temperature":
            return {"temperature_c": 65.0}
        raise IPCError({"code": "NOT_FOUND", "message": "unknown method"})
```

Inject `FakeIPCClient` in unit tests for tool handlers.

---

## Related Skills

- `rust/ipc` — The Rust engine side of the protocol
- `python/tool-system` — How tools use the IPC client
- `system-design/python-rust-boundary` — Architectural boundary guidelines
