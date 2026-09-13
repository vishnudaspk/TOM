---
name: system-design-python-rust-boundary
description: >
  Architectural guidelines for the Python/Rust boundary in TOM. Use when deciding whether
  a new feature belongs in Python (brain) or Rust (engine), when considering moving
  existing logic across the boundary, or when designing a new subsystem that spans both
  layers. Also use when reviewing a PR where logic may be on the wrong side.
---

# System Design — Python / Rust Boundary

The Python/Rust boundary is TOM's most important architectural division.
Getting it wrong creates tight coupling, performance problems, or security issues.

---

## The Core Split (from plan.md)

| Question | Owner |
|----------|-------|
| "What should TOM do?" | **Python** |
| "What is happening on the machine, and what low-level operation should be performed?" | **Rust** |

---

## Python Owns (Brain)

```
TOM Orchestrator          — main execution loop
Intent Router             — route classification
Model Router              — which model handles this
Planner                   — multi-step task planning
LLM providers             — Qwen3-8B, VLMs, STT, TTS orchestration
Memory                    — SQLite + Qdrant via Memory Manager
Tool registry             — tool definitions, registry, execution
Web                       — search, browse, retrieve
Vision orchestration      — coordinate fast/primary/deep VLM selection
Agent workflows           — Pydantic AI agents
Permission decisions      — SAFE / ASK / BLOCK
Error recovery            — retry, re-plan, ask user
Configuration             — yaml config loading
Telemetry                 — metrics, structured logging
```

---

## Rust Owns (Engine)

```
Audio capture             — microphone management
Audio playback            — buffered speaker output
Wake-word detection       — Rust-based detector, <100ms latency target
Voice activity detection  — VAD
System monitoring         — CPU, GPU, RAM, battery, processes, disk, network
Hotkeys                   — global keyboard shortcuts
Keyboard primitives       — low-level key injection
Mouse primitives          — low-level mouse events
IPC server                — JSON socket server
Event bus                 — internal Rust event dispatch
High-performance utilities — anything requiring consistent sub-ms latency
```

---

## Decision Rules

**Put it in Rust if:**
- It needs to run always-on at near-zero CPU overhead.
- It needs sub-100ms hardware-level latency (audio, wake-word, hotkey).
- It directly manages hardware devices (microphone, audio, GPU sensors).
- It must run without the Python interpreter being active.

**Put it in Python if:**
- It involves LLM calls, model orchestration, or agent logic.
- It needs rich data processing, database access, or API integration.
- It coordinates between multiple subsystems.
- Fast iteration and flexibility matter more than latency.

**When uncertain:**
- Build a prototype in Python first.
- Move to Rust only if measured performance requires it.
- Document the reason for the placement.

---

## Communication Pattern

Python calls Rust over IPC. Rust emits events that Python subscribes to.

```
Python → IPC request → Rust handler → IPC response → Python
Rust event → IPC event channel → Python subscriber
```

Requests are synchronous from Python's perspective (async/await + timeout).
Events are asynchronous — Python subscribes and processes as they arrive.

---

## What MUST NOT Cross the Boundary Wrong-Way

| Wrong | Correct |
|-------|---------|
| Python directly calling OS-level audio APIs | Rust provides audio service; Python orchestrates |
| Rust containing LLM inference logic | Python owns all AI model orchestration |
| LLM prompt construction in Rust | All prompt logic in Python |
| Memory (SQLite/Qdrant) access in Rust | Memory Manager in Python; Rust sends raw data |
| Permission decisions in Rust | Python's permission layer decides; Rust executes |

---

## Evolving the Boundary

If performance measurement reveals Python is the bottleneck in a specific subsystem,
consider moving that subsystem's hot path to Rust. But:

1. Benchmark first — do not prematurely optimise.
2. Document the reason in `docs/DECISIONS.md`.
3. Update the IPC protocol if new methods are needed.
4. Keep the Python orchestration wrapper intact — only move the inner implementation.

---

## IPC Protocol Versioning

Both sides must agree on the protocol version. Any breaking change to the message format
requires a version bump in `ipc/protocol.py` (Python) and `src/ipc/protocol.rs` (Rust).

---

## Related Skills

- `rust/ipc` — Rust IPC server implementation
- `python/ipc-client` — Python IPC client implementation
- `system-design/resource-management` — GPU/VRAM/CPU allocation across both layers
