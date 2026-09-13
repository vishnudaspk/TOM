---
name: rust-testing
description: >
  How to write tests for tom-engine Rust code. Use when writing unit tests, async tests,
  integration tests, or when setting up test fixtures for Rust subsystems. Also use when
  a Rust test is failing for non-obvious reasons (e.g. async runtime issues, mock setup).
---

# Rust Testing — tom-engine

Tests are a first-class requirement. Every subsystem in `tom-engine` must have unit tests.
Integration tests cover interactions between subsystems (e.g. IPC round-trips).

---

## Async Tests

Use `#[tokio::test]` for async test functions.

```rust
#[tokio::test]
async fn test_cpu_usage_returns_valid_percentage() {
    let usage = cpu::get_usage().await.expect("cpu usage should succeed");
    assert!(usage >= 0.0 && usage <= 100.0, "usage out of range: {usage}");
}
```

For tests that need a custom runtime configuration:

```rust
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn test_concurrent_handlers() { ... }
```

---

## Cancellation Tests

Test that subsystems shut down cleanly when cancelled.

```rust
#[tokio::test]
async fn test_audio_capture_cancels_cleanly() {
    use tokio_util::sync::CancellationToken;
    use tokio::time::{timeout, Duration};

    let cancel = CancellationToken::new();
    let handle = tokio::spawn(audio::capture::run(cancel.child_token()));

    // Let it run briefly
    tokio::time::sleep(Duration::from_millis(50)).await;

    cancel.cancel();
    let result = timeout(Duration::from_millis(500), handle).await;
    assert!(result.is_ok(), "subsystem did not shut down within timeout");
}
```

---

## IPC Handler Tests

Test handlers in isolation using an in-process duplex stream.
Do not depend on a real socket file for unit tests.

```rust
#[tokio::test]
async fn test_gpu_temperature_handler() {
    let (client, server) = tokio::io::duplex(4096);
    let cancel = CancellationToken::new();

    tokio::spawn(ipc::serve_connection(server, cancel.child_token()));

    // Write a request to client, read a response
    let request = r#"{"id":"t1","version":1,"method":"system.gpu_temperature","params":{}}"#;
    write_line(client_writer, request).await;
    let response: IpcResponse = read_json(client_reader).await;

    assert!(response.success);
    cancel.cancel();
}
```

---

## Test Structure

Follow the TOM repo test layout:

```
rust/tom-engine/
└── src/
    └── system/
        ├── cpu.rs
        └── tests/
            └── cpu_tests.rs
```

Or use inline `#[cfg(test)]` modules for small tests:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_sensor_ok() { ... }
}
```

Use inline modules for unit tests of a single module.
Use separate test files for integration tests crossing module boundaries.

---

## Mocking

Prefer dependency injection over global state, so tests can inject fake implementations.

```rust
pub trait SystemMonitor: Send + Sync {
    async fn cpu_usage(&self) -> Result<f32>;
}

pub struct FakeMonitor;
impl SystemMonitor for FakeMonitor {
    async fn cpu_usage(&self) -> Result<f32> { Ok(42.0) }
}
```

This avoids the need for complex mock libraries and keeps tests fast.

---

## Error Path Tests

Every error path should have at least one test.

```rust
#[tokio::test]
async fn test_handler_returns_error_for_unknown_method() {
    let response = dispatch(IpcRequest {
        id: "t1".into(),
        version: 1,
        method: "unknown.method".into(),
        params: json!({}),
    }).await;

    assert!(!response.success);
    assert_eq!(response.error.unwrap().code, "NOT_FOUND");
}
```

---

## Timing Tests

Avoid `sleep`-based timing assertions where possible. Use channel synchronisation instead.

```rust
// Instead of: sleep(100ms); assert!(thing_happened)
// Use: send on a channel when thing happens; receive in test with timeout
let (tx, mut rx) = tokio::sync::oneshot::channel();
// subsystem sends on tx when event occurs
let result = tokio::time::timeout(Duration::from_millis(200), rx).await;
assert!(result.is_ok(), "event did not arrive in time");
```

---

## Related Skills

- `rust/async-tokio` — Async runtime context for tests
- `rust/ipc` — IPC handler testing patterns
- `testing/strategy` — Overall TOM test strategy
- `debugging/rust-errors` — When tests fail unexpectedly
