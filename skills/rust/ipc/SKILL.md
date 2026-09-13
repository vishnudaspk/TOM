---
name: rust-ipc
description: >
  How to implement the Rust-side IPC server in tom-engine for Python ↔ Rust communication.
  Use when building, modifying, or debugging the IPC layer in tom-engine — including the
  IPC server, protocol schema, message handlers, versioning, or connection management.
  Also use when Python cannot reach the engine or when the protocol changes.
---

# Rust IPC — tom-engine

TOM's Python brain communicates with the Rust engine via a local JSON IPC protocol.
This skill covers the Rust-side implementation.

> See `python/ipc` skill for the Python client side.

---

## Design Constraints (from plan.md)

- Start with the simplest reliable local mechanism (JSON over local socket or named pipe).
- Do NOT introduce gRPC, protobuf, or distributed infrastructure at MVP stage.
- The protocol must be versioned so the engine can evolve independently.
- No IPC request should be able to hang the engine indefinitely — all handlers need timeouts.

---

## Protocol Shape

```json
// Request (Python → Rust)
{
  "id": "req_001",
  "version": 1,
  "method": "system.gpu_temperature",
  "params": {}
}

// Success response (Rust → Python)
{
  "id": "req_001",
  "version": 1,
  "success": true,
  "data": { "temperature_c": 61 }
}

// Error response
{
  "id": "req_001",
  "version": 1,
  "success": false,
  "error": { "code": "NOT_AVAILABLE", "message": "GPU sensor unavailable" }
}
```

Define schemas as Rust structs using `serde`:

```rust
use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
pub struct IpcRequest {
    pub id: String,
    pub version: u32,
    pub method: String,
    pub params: serde_json::Value,
}

#[derive(Serialize)]
pub struct IpcResponse {
    pub id: String,
    pub version: u32,
    pub success: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<IpcError>,
}
```

---

## Transport: Unix Socket / Named Pipe

On Windows, use a named pipe. On Linux/macOS, use a Unix domain socket.
Abstract the transport so it can be swapped without changing handler logic.

```rust
// Simplified server accept loop
pub async fn run(cancel: CancellationToken) -> anyhow::Result<()> {
    let listener = create_listener()?; // platform-specific
    loop {
        tokio::select! {
            _ = cancel.cancelled() => break,
            conn = listener.accept() => {
                let (stream, _) = conn?;
                let cancel_child = cancel.child_token();
                tokio::spawn(handle_connection(stream, cancel_child));
            }
        }
    }
    Ok(())
}
```

---

## Handler Dispatch

Use a method-to-handler map rather than a giant `match` block.
This makes adding new methods safe without touching the dispatcher core.

```rust
type Handler = Box<dyn Fn(serde_json::Value) -> BoxFuture<'static, serde_json::Value> + Send + Sync>;

// Register handlers
handlers.insert("system.cpu_usage", Box::new(|_| Box::pin(cpu::get_usage())));
handlers.insert("system.gpu_temperature", Box::new(|_| Box::pin(gpu::get_temperature())));
```

---

## Per-Request Timeout

Every handler invocation must run under a timeout.

```rust
use tokio::time::{timeout, Duration};

let result = timeout(Duration::from_millis(500), dispatch(request)).await;
match result {
    Ok(resp) => resp,
    Err(_) => IpcResponse::error(id, "TIMEOUT", "handler timed out"),
}
```

---

## Versioning

Include a `version` field in every message.

- Increment the protocol version when the schema changes in a breaking way.
- The server should reject requests with unsupported versions cleanly rather than panicking.

```rust
const SUPPORTED_PROTOCOL_VERSION: u32 = 1;

if request.version != SUPPORTED_PROTOCOL_VERSION {
    return IpcResponse::error(id, "VERSION_MISMATCH", "unsupported protocol version");
}
```

---

## Connection Lifecycle

Each connection is handled in its own Tokio task.
The connection task must:

1. Accept a cancel token.
2. Read framed messages (newline-delimited JSON or length-prefixed).
3. Dispatch to handlers.
4. Send responses.
5. Terminate cleanly on cancel or disconnect.

---

## Error Codes

Define a fixed set of error codes in `protocol.rs`:

```rust
pub mod error_code {
    pub const NOT_FOUND: &str = "NOT_FOUND";
    pub const INVALID_PARAMS: &str = "INVALID_PARAMS";
    pub const TIMEOUT: &str = "TIMEOUT";
    pub const NOT_AVAILABLE: &str = "NOT_AVAILABLE";
    pub const VERSION_MISMATCH: &str = "VERSION_MISMATCH";
    pub const INTERNAL: &str = "INTERNAL";
}
```

Avoid raw string errors in handler code — use these codes so Python can pattern-match reliably.

---

## Testing

Test the IPC layer with an in-process client, not by launching a subprocess.

```rust
#[tokio::test]
async fn test_gpu_temperature_handler() {
    let (client_stream, server_stream) = tokio::io::duplex(4096);
    // spawn server task on server_stream
    // send request on client_stream
    // assert response
}
```

---

## Related Skills

- `rust/async-tokio` — Async/Tokio patterns this skill builds on
- `rust/event-system` — Events emitted from handlers
- `python/ipc-client` — Python-side IPC client
- `system-design/python-rust-boundary` — Architectural guidelines for the boundary
