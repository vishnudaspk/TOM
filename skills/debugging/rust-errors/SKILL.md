---
name: debugging-rust-errors
description: >
  How to diagnose and fix common Rust compilation errors, runtime panics, and async
  issues in tom-engine. Use when facing borrow checker errors, lifetime issues, async
  runtime panics, Tokio task failures, or mysterious test failures in Rust code.
  Covers the most frequent issues encountered in async Rust systems.
---

# Debugging — Rust Errors in tom-engine

This skill covers the most common Rust issues in `tom-engine` and how to diagnose them
efficiently.

---

## Borrow Checker / Lifetime Issues

### "cannot borrow as mutable because it is also borrowed as immutable"

Look for overlapping borrows in the same scope. Restructure to separate the borrow lifetimes.

```rust
// Problem: both borrows active at same time
let x = map.get("key");    // immutable borrow
map.insert("key2", val);   // mutable borrow → compile error

// Fix: drop the first borrow before the mutation
let x = map.get("key").copied(); // copy the value
map.insert("key2", val);
```

### Lifetime in async functions

The compiler often rejects borrows across `.await` points.

```rust
// Problem
async fn process(data: &Data) -> Result<()> {
    some_async_call(data).await?; // data borrow held across await
    Ok(())
}

// Fix: clone or use owned types across await points
async fn process(data: Data) -> Result<()> { ... }
// or: Arc<Data> if sharing is needed
```

---

## Async / Tokio Issues

### "future is not `Send`"

Caused by holding a non-`Send` type across an `.await` point.

Common culprits:
- `std::sync::MutexGuard` across `.await`
- `Rc<T>` in a spawned task
- Raw pointers

Fix: replace `std::sync::Mutex` with `tokio::sync::Mutex`, or restructure so the guard
is dropped before the `.await`.

### Task silently exits

Dropped `JoinHandle` — the task is cancelled when the handle is dropped.

```rust
// Problem: handle is dropped immediately
tokio::spawn(my_task());

// Fix: store and await the handle at shutdown
let handle = tokio::spawn(my_task());
// ... later ...
handle.await?;
```

### "future cannot be sent between threads safely"

One of the types captured by the async block is not `Send`. Use `Arc<Mutex<T>>` instead
of raw `Rc<T>` or non-thread-safe types.

### Deadlock

Two tasks each hold a lock and wait for the other.

Diagnosis:
1. Add `tracing::debug!` before and after each lock acquisition.
2. Check if any two locks are always acquired in the same order across all code paths.
3. Ensure no lock is held while awaiting another future that also acquires a lock.

---

## IPC / Serialisation Issues

### Unexpected `null` in JSON response

A `skip_serializing_if = "Option::is_none"` field is missing a value.
Check that all required fields are populated before serialising.

### `serde` deserialization fails

Enable the `serde` `deny_unknown_fields` attribute in tests to catch schema mismatches:

```rust
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct IpcRequest { ... }
```

Then add a failing test with the real payload to pinpoint the mismatch.

---

## Cancellation Issues

### Task doesn't stop when cancelled

The loop inside the task is not checking the cancel token.

```rust
// Problem — blocking loop never checks cancel
loop {
    let data = read_sensor().await;
    process(data);
}

// Fix — use select! to interleave cancel check
loop {
    tokio::select! {
        _ = cancel.cancelled() => break,
        data = read_sensor() => process(data),
    }
}
```

### Task stops too late (resource not released)

The cleanup code after the loop is not being reached. Check that no `?` between
the `cancel.cancelled()` and the cleanup line short-circuits execution.

---

## Common Compile Error Messages

| Message | Likely cause |
|---------|-------------|
| `cannot borrow X as mutable, as it is not declared as mutable` | Add `mut` |
| `the trait bound ... is not satisfied` | Missing `impl` or wrong type parameter |
| `future is not Send` | Non-Send type across `.await` |
| `value used here after move` | Clone or borrow before move |
| `lifetime may not live long enough` | Borrow held across async boundary |
| `type annotations needed` | Add explicit type or turbofish |

---

## Debug Logging

Add `RUST_LOG=debug` to see tracing output:

```powershell
$env:RUST_LOG="tom_engine=debug,tokio=warn"
cargo run
```

Add strategic trace points:

```rust
tracing::debug!(component = "audio", "entering capture loop");
```

---

## When Tests Fail

1. Read the full error — don't just read the last line.
2. Check if it's a compilation error or a runtime panic.
3. For runtime panics: look for the stack trace — it shows the exact file and line.
4. For async tests: check that `#[tokio::test]` is used, not plain `#[test]`.
5. For intermittent failures: look for race conditions or test ordering dependencies.

---

## Related Skills

- `rust/async-tokio` — Correct async patterns that avoid these issues
- `rust/error-handling` — How to handle errors rather than panic
- `rust/testing` — Writing tests that catch these issues early
