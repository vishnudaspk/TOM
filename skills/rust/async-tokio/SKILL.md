---
name: rust-async-tokio
description: >
  How to design and implement async Rust code using Tokio in the TOM project (tom-engine).
  Use when writing or reviewing any async Rust code in tom-engine — including audio pipelines,
  IPC server/client, event dispatch, wake-word detection, system monitors, or any Tokio task.
  Also use when debugging Tokio runtime issues, task panics, shutdown races, or cancellation problems.
---

# Rust Async / Tokio — TOM Engine

This skill covers async Rust patterns used in `tom-engine`. The engine is TOM's always-on
nervous system. It must remain lightweight, correct, and reliably cancellable.

---

## Runtime Setup

Use a single multi-thread Tokio runtime for `tom-engine`.

```rust
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // startup
}
```

Avoid creating secondary runtimes inside subsystems. Spawn tasks on the shared runtime.

---

## Task Spawning

Use `tokio::spawn` for independent concurrent tasks.

```rust
let handle = tokio::spawn(async move {
    subsystem.run(cancel_token).await
});
```

**Hold onto `JoinHandle`s** — dropping a handle does not cancel the task.
Collect handles and `await` them during shutdown.

---

## Cancellation (CancellationToken)

Every long-running task must accept a `CancellationToken` from `tokio_util::sync`.

```rust
use tokio_util::sync::CancellationToken;

async fn run_audio_capture(cancel: CancellationToken) {
    loop {
        tokio::select! {
            _ = cancel.cancelled() => break,
            result = capture_frame() => {
                // process frame
            }
        }
    }
    // cleanup here
}
```

**Key rules:**
- Never use `loop { }` without checking the cancel token.
- Prefer `tokio::select!` to interleave cancellation with work.
- Use `.child_token()` to propagate cancellation to sub-tasks.
- Cancellation stops future work; it does not undo completed operations.

```rust
let child = parent_cancel.child_token();
tokio::spawn(subsystem(child));
```

---

## Channels

Use typed Tokio channels for inter-task communication.

| Channel | Use case |
|---------|----------|
| `tokio::sync::mpsc` | Multiple producers → single consumer (most common) |
| `tokio::sync::broadcast` | One-to-many event fan-out (e.g. events bus) |
| `tokio::sync::watch` | Latest-value state sharing (e.g. resource state) |
| `tokio::sync::oneshot` | Single request/response pair (e.g. IPC reply) |

Match buffer sizes to realistic throughput. A buffer of 64–256 is a reasonable starting point;
adjust based on measured backpressure.

---

## Shared State

Prefer message passing over shared mutable state.

When shared state is required:

```rust
use tokio::sync::Mutex;
use std::sync::Arc;

let shared = Arc::new(Mutex::new(State::default()));
```

Prefer `tokio::sync::Mutex` (async) over `std::sync::Mutex` across `.await` points.

Use `std::sync::Mutex` only for brief non-async critical sections where no `.await` occurs
inside the lock guard.

---

## Error Handling

Use `anyhow::Result` in application code. Use typed errors in library boundaries.

```rust
async fn run() -> anyhow::Result<()> {
    do_something().await.context("audio capture failed")?;
    Ok(())
}
```

**Never silently swallow errors in spawned tasks.** Log and propagate them.

```rust
let handle = tokio::spawn(async {
    if let Err(e) = run_subsystem().await {
        tracing::error!("subsystem failed: {e:#}");
    }
});
```

---

## Shutdown Pattern

Implement clean shutdown in every subsystem.

```rust
pub async fn run(cancel: CancellationToken) -> anyhow::Result<()> {
    // run until cancelled
    cancel.cancelled().await;
    // release resources
    Ok(())
}
```

At top level, collect handles and await them:

```rust
let mut handles = vec![
    tokio::spawn(audio::run(cancel.child_token())),
    tokio::spawn(ipc::run(cancel.child_token())),
];
cancel.cancel(); // trigger shutdown
for h in handles {
    let _ = h.await;
}
```

---

## Timeouts

Every external or blocking operation must have a timeout.

```rust
use tokio::time::{timeout, Duration};

let result = timeout(Duration::from_millis(500), ipc_call()).await
    .context("IPC request timed out")?;
```

No operation should be able to hang the engine indefinitely.

---

## Blocking Work

CPU-intensive or blocking I/O must not run on the async executor directly.

```rust
tokio::task::spawn_blocking(|| {
    // blocking work here
}).await?;
```

Examples: audio codec processing, file hashing, any `std` blocking I/O.

---

## Common Mistakes

| Mistake | Correct approach |
|---------|-----------------|
| `std::thread::sleep` in async code | `tokio::time::sleep` |
| Holding `std::sync::MutexGuard` across `.await` | `tokio::sync::Mutex` |
| Ignoring `JoinHandle` | Store and await handles at shutdown |
| No cancellation in long loops | `tokio::select!` with `cancel.cancelled()` |
| Blocking I/O on async thread | `spawn_blocking` |

---

## Related Skills

- `rust/ipc` — IPC server built on top of Tokio async
- `rust/event-system` — Tokio broadcast channel event bus
- `rust/error-handling` — Error types and propagation patterns
- `rust/testing` — Testing async Rust code
