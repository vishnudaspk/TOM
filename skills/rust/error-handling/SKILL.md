---
name: rust-error-handling
description: >
  How to handle errors correctly in tom-engine Rust code. Use when writing error types,
  propagating errors across async boundaries, deciding between anyhow and typed errors,
  or implementing error recovery in Rust subsystems. Also use when reviewing Rust code
  for silent error suppression or incorrect panic usage.
---

# Rust Error Handling — tom-engine

`tom-engine` must never silently discard errors. This skill covers the practical patterns
for error handling used throughout the Rust engine.

---

## Rule: No Silent Failures

Every error that could indicate a subsystem problem must be logged and surfaced.

```rust
// Wrong — swallows error
let _ = do_thing();

// Correct — log and propagate
do_thing().context("failed to initialize audio")?;
```

---

## Application Code: `anyhow`

Use `anyhow::Result` and `anyhow::Context` in application-level code (subsystem runners, handlers, main).

```rust
use anyhow::{Context, Result};

pub async fn run() -> Result<()> {
    let device = open_audio_device().context("audio device unavailable")?;
    // ...
    Ok(())
}
```

Add `.context()` at every `?` point that crosses a logical boundary, so error messages
chain naturally: `"IPC server failed: audio device unavailable: permission denied"`.

---

## Library / Protocol Boundaries: Typed Errors

Use explicit typed errors (`thiserror`) when:
- The error must be matched by callers (e.g. IPC error codes, tool result errors).
- The error crosses a public API boundary.
- You need serialization of error information.

```rust
use thiserror::Error;

#[derive(Debug, Error)]
pub enum SystemError {
    #[error("GPU sensor unavailable")]
    GpuUnavailable,

    #[error("sensor read timed out after {ms}ms")]
    Timeout { ms: u64 },

    #[error("unsupported platform: {0}")]
    UnsupportedPlatform(String),
}
```

---

## Panics

Panics are only acceptable for:
- Programmer errors caught at startup (invalid config, unreachable match arms).
- Test assertions.

Never panic in production runtime paths. Replace `unwrap()` with `?` or `.expect("reason")` where
the reason is a programming invariant, not a runtime condition.

```rust
// Acceptable: startup-time invariant
let config = Config::load().expect("config must be valid at startup");

// Not acceptable: runtime path
let val = map.get("key").unwrap(); // can panic if map changes
```

---

## Spawned Task Error Handling

Tasks spawned with `tokio::spawn` that fail silently are a bug.

```rust
// Wrong: failure disappears
tokio::spawn(async { let _ = subsystem().await; });

// Correct: log or propagate
let handle = tokio::spawn(async {
    if let Err(e) = subsystem().await {
        tracing::error!("subsystem failed: {e:#}");
    }
});
```

For critical subsystems, send the error through a channel so the top-level supervisor
can decide whether to restart or shut down.

---

## Error in IPC Responses

When a handler fails, return a structured error response. Never let a handler panic
and take down the connection.

```rust
async fn dispatch(req: IpcRequest) -> IpcResponse {
    match handle(req).await {
        Ok(data) => IpcResponse::ok(req.id, data),
        Err(e) => {
            tracing::warn!("handler error: {e:#}");
            IpcResponse::error(req.id, "INTERNAL", &e.to_string())
        }
    }
}
```

---

## Logging Conventions

Log errors at the appropriate level:

| Situation | Level |
|-----------|-------|
| Recoverable expected condition | `warn!` |
| Unexpected but non-fatal | `error!` |
| Subsystem cannot continue | `error!` then propagate |
| Debug-only info | `debug!` or `trace!` |

Always use structured fields where useful:

```rust
tracing::error!(component = "audio", error = %e, "capture failed");
```

---

## Common Mistakes

| Mistake | Correct |
|---------|---------|
| `unwrap()` in runtime code | `?` or `.context()` |
| `let _ = result` | Handle or log |
| Generic `"error"` message | Descriptive `.context()` |
| Panic in handler | Return error response |
| Losing error type at boundary | Use typed error or preserve with `.context()` |

---

## Related Skills

- `rust/async-tokio` — Error propagation across async boundaries
- `rust/testing` — Testing error paths
- `debugging/rust-errors` — Diagnosing and debugging Rust errors
