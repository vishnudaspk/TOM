---
name: coding-structured-logging
description: >
  How to implement structured logging and observability in TOM. Use when adding logging
  to a new subsystem, setting up the telemetry layer, adding metrics, or ensuring that
  logs do not contain secrets. Covers both Python (structlog or standard logging with JSON
  formatter) and Rust (tracing crate) patterns consistent with plan.md's observability design.
---

# Coding — Structured Logging

TOM uses structured logging throughout. Every important event must be observable,
filterable, and secret-free.

---

## Design Rules (from plan.md)

- Use structured JSON logs, not plain text strings.
- Never log secrets (API keys, passwords, tokens, credentials).
- Every major pipeline stage should have measurable timing information.
- Logs must be filterable by component, event type, and request ID.

---

## Python — structlog (recommended)

```python
import structlog

logger = structlog.get_logger(__name__)

# Structured event with fields
logger.info("tool_call_started",
    component="tool_executor",
    tool="system.gpu_temperature",
    task_id=task_id,
)

logger.error("tool_call_failed",
    component="tool_executor",
    tool="system.gpu_temperature",
    error=str(e),
    latency_ms=elapsed_ms,
)
```

Configure structlog to output JSON in production:

```python
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
```

---

## Python — Secrets Filter

Apply a processor that redacts known sensitive fields before output:

```python
SENSITIVE_FIELD_NAMES = frozenset({
    "api_key", "password", "token", "secret", "credential", "key"
})

def redact_secrets(logger, method, event_dict):
    for key in list(event_dict.keys()):
        if key.lower() in SENSITIVE_FIELD_NAMES:
            event_dict[key] = "***REDACTED***"
    return event_dict
```

Add this processor to the structlog pipeline before the renderer.

---

## Rust — tracing crate

```rust
use tracing::{info, warn, error, debug};

// Structured fields
info!(component = "audio", device = %device_name, "capture started");

warn!(component = "ipc", error = %e, method = %request.method, "handler failed");

error!(component = "wakeword", "detector panic, restarting");
```

Use `tracing_subscriber` for output formatting:

```rust
tracing_subscriber::fmt()
    .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
    .json()          // structured JSON output
    .init();
```

---

## Latency Measurement

Log timing at every major pipeline stage. Follow the naming from plan.md:

```python
# Python
import time

start = time.monotonic()
result = await llm.generate(prompt)
latency_ms = int((time.monotonic() - start) * 1000)

logger.info("llm_generation_complete",
    component="models.reasoning",
    task_id=task_id,
    latency_ms=latency_ms,
    tokens_generated=result.token_count,
)
```

```rust
// Rust
let start = std::time::Instant::now();
let result = sensor.read().await;
let elapsed_ms = start.elapsed().as_millis();
info!(component = "system.gpu", latency_ms = elapsed_ms, "gpu temp read");
```

---

## Required Fields Per Log Entry

At minimum, every log entry should include:

| Field | Description |
|-------|-------------|
| `timestamp` | ISO 8601 UTC |
| `component` | Subsystem name (e.g. `tool_executor`, `memory.manager`) |
| `event` | What happened (e.g. `tool_call`, `memory_stored`) |
| `task_id` | When inside a task context |
| `latency_ms` | For timed operations |
| `error` | Error string if applicable |

---

## Log Levels

| Level | Use case |
|-------|---------|
| `DEBUG` | Detailed internal state, development only |
| `INFO` | Normal operations, pipeline events |
| `WARN` | Recoverable unexpected conditions |
| `ERROR` | Subsystem failure, unrecoverable within the operation |

Do not use `ERROR` for expected conditions (e.g. model not loaded yet).

---

## What NOT to Log

- Secrets, API keys, passwords, tokens.
- Full user conversation text in production (privacy).
- Binary data or large buffers inline.
- Stack traces from LLM-generated content (may contain user data).

---

## Related Skills

- `security/secrets` — Secrets filtering in the log pipeline
- `system-design/resource-management` — Metrics for VRAM and model state
- `coding/validation` — Validate data before it reaches the log sink
