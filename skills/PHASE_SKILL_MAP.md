# TOM Phase → Skill Map

This file maps TOM's development phases (from `plan.md`) to the skills that are relevant
during each phase.

**Purpose:** Determine which skills an agent should consider loading for a given phase
without loading the entire skill library.

> Read `SKILL_INDEX.md` for the full skill descriptions and trigger conditions.

---

## Phase 0 — Foundation

**Goal:** Repository structure, tooling, configuration, CI, AGENTS.md, skills system.

| Priority | Skills |
|----------|--------|
| Required | `git/workflow`, `documentation/project-state` |
| Recommended | `coding/structured-logging` |
| Optional | None |

---

## Phase 1 — Rust Engine (tom-engine)

**Goal:** Build the always-on Rust engine: IPC server, system monitors, audio infrastructure,
wake-word detection, VAD, hotkeys, event system.

| Priority | Skills |
|----------|--------|
| Required | `rust/async-tokio`, `rust/ipc`, `rust/error-handling`, `rust/testing` |
| Required | `security/permission-model`, `security/secrets` |
| Required | `system-design/python-rust-boundary` |
| Recommended | `rust/event-system`, `coding/validation`, `coding/structured-logging` |
| Recommended | `testing/strategy` |
| Optional | `debugging/rust-errors` (when needed) |
| Optional | `git/workflow`, `documentation/project-state` (ongoing) |

---

## Phase 2 — Python Brain Core

**Goal:** TOM Orchestrator, Intent Router, Model Router, Tool Registry, IPC Client,
configuration loading, basic agent structure.

| Priority | Skills |
|----------|--------|
| Required | `python/ipc-client`, `python/tool-system`, `python/pydantic-agents` |
| Required | `security/permission-model`, `security/secrets` |
| Required | `system-design/python-rust-boundary`, `agent-development/task-lifecycle` |
| Recommended | `coding/validation`, `coding/structured-logging` |
| Recommended | `testing/strategy`, `testing/python-testing` |
| Optional | `debugging/python-agent` (when needed) |
| Optional | `agent-development/tool-design`, `lm-studio` (local model experiments) |

---

## Phase 3 — Memory System

**Goal:** SQLite-backed memory, Qdrant semantic retrieval, Memory Manager, memory policies,
embedding pipeline, user-controlled memory operations.

| Priority | Skills |
|----------|--------|
| Required | `python/memory-system`, `python/pydantic-agents` |
| Required | `security/permission-model`, `security/secrets` |
| Recommended | `coding/validation`, `testing/python-testing` |
| Optional | `system-design/resource-management` (for embedding resource usage) |

---

## Phase 4 — Voice Pipeline

**Goal:** Wake-word detection (Rust), VAD (Rust), Whisper STT, Kokoro TTS,
streaming voice pipeline, voice interruption/cancellation.

| Priority | Skills |
|----------|--------|
| Required | `rust/async-tokio`, `rust/ipc` (for audio/wake-word subsystems) |
| Required | `system-design/resource-management` (CPU vs GPU Whisper placement) |
| Required | `agent-development/task-lifecycle` (cancellation during voice) |
| Recommended | `python/ipc-client`, `coding/structured-logging` |
| Recommended | `testing/strategy` (voice pipeline benchmarks) |
| Optional | `debugging/rust-errors` (for audio subsystem issues) |

---

## Phase 5 — Model Routing & Resource Management

**Goal:** Model Router (Qwen3-1.7B), model provider abstraction, Resource Manager,
VRAM scheduling, model lifecycle management.

| Priority | Skills |
|----------|--------|
| Required | `system-design/resource-management` |
| Required | `python/pydantic-agents` (router agent) |
| Required | `agent-development/task-lifecycle` (task priority) |
| Recommended | `testing/python-testing`, `coding/structured-logging` |
| Recommended | `lm-studio` (model benchmarking & local runtime experiments) |
| Optional | `debugging/python-agent` |

---

## Phase 6 — Vision System

**Goal:** Vision pipeline, VLM selection manager (fast/primary/deep), OpenCV integration,
screenshot analysis, image understanding.

| Priority | Skills |
|----------|--------|
| Required | `system-design/resource-management` (VRAM for VLMs) |
| Required | `python/pydantic-agents` (vision agent) |
| Recommended | `agent-development/tool-design` (vision tools) |
| Recommended | `testing/strategy` (vision benchmarks) |

---

## Phase 7 — Security Hardening

**Goal:** Full permission layer, confirmation flows, secrets auditing, path validation,
security test suite.

| Priority | Skills |
|----------|--------|
| Required | `security/permission-model`, `security/secrets` |
| Required | `coding/validation`, `testing/strategy` |
| Recommended | `testing/python-testing`, `debugging/python-agent` |

---

## Phase 8 — Observability & Telemetry

**Goal:** Structured logging pipeline, metrics collection, latency telemetry,
TOM Monitor dashboard.

| Priority | Skills |
|----------|--------|
| Required | `coding/structured-logging`, `security/secrets` (log filtering) |
| Recommended | `testing/strategy` (benchmark automation) |

---

## All Phases (Always Relevant)

These skills apply throughout TOM's entire development lifecycle:

- `security/permission-model` — every tool, every phase
- `security/secrets` — every feature touching credentials
- `coding/validation` — every data boundary
- `git/workflow` — every commit
- `documentation/project-state` — every completed task

---

## Cross-Phase Decision Guide

| Situation | Load |
|-----------|------|
| Building anything in `rust/` | `rust/async-tokio` + `rust/error-handling` |
| Building anything in `python/agents/` | `python/pydantic-agents` |
| Building anything in `python/tools/` | `python/tool-system` + `security/permission-model` |
| Building anything in `python/memory/` | `python/memory-system` |
| Building anything in `python/ipc/` | `python/ipc-client` + `rust/ipc` |
| Making an architectural decision | `system-design/python-rust-boundary` |
| Adding AI models | `system-design/resource-management` |
| Debugging Rust | `debugging/rust-errors` |
| Debugging Python | `debugging/python-agent` |
