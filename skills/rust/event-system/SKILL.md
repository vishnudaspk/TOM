---
name: rust-event-system
description: >
  How to design and implement the Rust event bus / event dispatch system in tom-engine.
  Use when building or modifying the event infrastructure (event types, subscription,
  dispatch, fan-out). Also use when integrating a new subsystem with the event system,
  or when debugging missed events or broadcast overflow.
---

# Rust Event System — tom-engine

The event system is how `tom-engine` subsystems communicate state changes to Python
and to each other. It should be observable, typed, and non-blocking.

---

## Design Goals

- Typed events — no raw string messages.
- Non-blocking publish — emitters must not stall while receivers are slow.
- Selective subscription — subscribers receive only the events they need.
- Observable by Python through IPC — Python subscribes to event streams.
- Reliable shutdown — no lingering receivers after cancellation.

---

## Implementation: Tokio Broadcast

Use `tokio::sync::broadcast` as the underlying channel for fan-out events.

```rust
use tokio::sync::broadcast;

pub struct EventBus {
    sender: broadcast::Sender<TomEvent>,
}

impl EventBus {
    pub fn new(capacity: usize) -> Self {
        let (sender, _) = broadcast::channel(capacity);
        Self { sender }
    }

    pub fn subscribe(&self) -> broadcast::Receiver<TomEvent> {
        self.sender.subscribe()
    }

    pub fn publish(&self, event: TomEvent) {
        // Errors only if no receivers — acceptable
        let _ = self.sender.send(event);
    }
}
```

A capacity of 256 is a reasonable default. Adjust based on event volume.

---

## Event Types

Define events as a typed enum. Group by subsystem.

```rust
#[derive(Clone, Debug)]
pub enum TomEvent {
    // System monitoring
    System(SystemEvent),
    // Audio pipeline
    Audio(AudioEvent),
    // Wake word
    WakeWord(WakeWordEvent),
    // IPC
    Ipc(IpcEvent),
}

#[derive(Clone, Debug)]
pub enum SystemEvent {
    CpuHighLoad { percent: f32 },
    GpuHighTemperature { celsius: f32 },
    LowVramWarning { available_mb: u64 },
}

#[derive(Clone, Debug)]
pub enum WakeWordEvent {
    Detected { confidence: f32, timestamp_ms: u64 },
}
```

`Clone` is required for broadcast channels. Keep event payloads small — avoid embedding
large buffers in events.

---

## Subscribing to Events

A subscriber receives a copy of every event published after it subscribed.

```rust
async fn watch_wakeword(mut rx: broadcast::Receiver<TomEvent>, cancel: CancellationToken) {
    loop {
        tokio::select! {
            _ = cancel.cancelled() => break,
            result = rx.recv() => {
                match result {
                    Ok(TomEvent::WakeWord(WakeWordEvent::Detected { .. })) => {
                        // handle
                    }
                    Err(broadcast::error::RecvError::Lagged(n)) => {
                        tracing::warn!("event bus lagged, skipped {n} events");
                    }
                    _ => {}
                }
            }
        }
    }
}
```

---

## Handling Lag

If a subscriber is slow, `broadcast::RecvError::Lagged` is returned.

For non-critical consumers (logging, telemetry), log the lag and continue.
For critical consumers, either increase capacity or process events faster.

---

## Forwarding Events to Python

A dedicated IPC bridge task subscribes to the event bus and sends relevant events
to connected Python clients via the IPC server.

```text
EventBus (Rust)
    → IPC bridge task
    → IPC connection
    → Python ipc/client.py
```

Events forwarded to Python should be serialised as JSON in the established IPC protocol format.

---

## Event Naming Convention

Events should read as facts, not commands.

```
✓  WakeWordDetected
✓  GpuHighTemperature
✓  IpcClientConnected
✗  DetectWakeWord   (imperative — this is a command, not an event)
✗  OnGpuTempChange  (ambiguous "On" prefix)
```

---

## Shared EventBus

Wrap the `EventBus` in `Arc` and inject it into subsystems at startup.

```rust
let bus = Arc::new(EventBus::new(256));

tokio::spawn(audio::run(bus.clone(), cancel.child_token()));
tokio::spawn(wakeword::run(bus.clone(), cancel.child_token()));
tokio::spawn(ipc::run(bus.clone(), cancel.child_token()));
```

---

## Related Skills

- `rust/async-tokio` — Async patterns the event system is built on
- `rust/ipc` — How events reach Python
- `system-design/python-rust-boundary` — Which events cross the boundary and how
