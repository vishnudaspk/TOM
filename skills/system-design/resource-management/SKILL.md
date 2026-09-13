---
name: system-design-resource-management
description: >
  How to design and implement resource-aware code in TOM, given the constrained hardware
  (RTX 4060 8GB VRAM, 16GB RAM, Intel i9-13900H). Use when building the Resource Manager,
  model lifecycle code, VRAM scheduling, or any feature that loads AI models. Also use
  when debugging GPU OOM errors, model loading races, or performance degradation from
  concurrent model usage.
---

# System Design — Resource Management

TOM runs on constrained local hardware. Every design decision involving AI models must
account for limited GPU VRAM. This skill covers resource-aware patterns.

---

## Hardware Constraints (from plan.md)

```
RTX 4060 Laptop — 8 GB VRAM
16 GB RAM
Intel i9-13900H
```

The 8 GB VRAM budget is the primary constraint. Multiple models cannot safely coexist
on the GPU simultaneously.

---

## Workload Classification

| CPU / RAM | GPU / VRAM |
|-----------|-----------|
| Rust engine | Qwen3-1.7B Router |
| Wake word detector | Qwen3-8B Reasoning |
| VAD | Vision Models (Gemma, Ministral) |
| Whisper (preferred CPU, benchmarked) | Image Generation |
| Kokoro TTS (CPU) | GPU-accelerated CV (optional) |
| SQLite | |
| Qdrant | |
| TOM Python orchestration | |

**Kokoro-82M runs on CPU** — its small size does not justify consuming VRAM.

**Whisper placement** must be determined by benchmarking on the target laptop before committing
to a default. Provide `Auto`, `CPU`, and `GPU` options in config.

---

## Model Lifecycle States

```
UNLOADED → LOADING → READY → BUSY → IDLE → UNLOADING → UNLOADED
                                              ↑ (triggered by VRAM needed elsewhere)
```

```python
from enum import Enum

class ModelState(Enum):
    UNLOADED = "unloaded"
    LOADING = "loading"
    READY = "ready"
    BUSY = "busy"
    IDLE = "idle"
    UNLOADING = "unloading"
    ERROR = "error"
```

The Resource Manager tracks state for every AI model.

---

## VRAM Budget Principle

**Never assume concurrent GPU models are safe.**

Before loading a model:
1. Check current VRAM usage (via Rust engine IPC: `system.vram_usage`).
2. Check if another large model is currently BUSY or READY.
3. If insufficient VRAM: queue the request or unload an idle model first.

```python
async def can_load_model(self, model: ModelConfig) -> bool:
    vram_free = await self.ipc.call("system.vram_free_mb")
    return vram_free["mb"] >= model.vram_required_mb + VRAM_SAFETY_MARGIN_MB
```

Always include a safety margin (e.g. 512 MB) above the model's stated size.

---

## Model Residency Policy

```
Qwen3-1.7B Router → normally resident (small, used frequently)
Qwen3-8B           → load on demand, unload when VRAM needed for VLM
VLMs               → load on demand, one at a time
```

Frequently used models should remain warm if VRAM allows.
Large models should be unloaded after a configurable idle timeout.

---

## Priority Order (from plan.md)

```
1. Voice interaction (STT → Router → LLM → TTS)
2. User's active request
3. Router inference
4. Main reasoning
5. Vision
6. Computer vision
7. Background AI tasks
8. Maintenance / indexing
```

Interactive user operations always preempt background tasks.

---

## Concurrency Policy

| Situation | Policy |
|-----------|--------|
| Two GPU models both needed simultaneously | Queue the second; run sequentially |
| Background image gen while user is talking | Pause/queue background task |
| Whisper running while Qwen3-8B is busy | Depends on Whisper placement (benchmark result) |
| Qdrant indexing in background | CPU-only, lower priority, yields to interactive work |

Use `Parallel`, `Queued`, `Preemptible`, or `Exclusive` resource modes per plan.md.

---

## Resource Manager Responsibilities

The Python `resources/manager.py` must know:

- Current VRAM used / available / reserved
- Current RAM used / available
- CPU utilisation
- Which models are loaded, loading, busy, idle
- Whether concurrent inference is safe
- Model loading and unloading costs (measured at startup)
- Active background tasks and their priority

---

## Model Selection vs Resource Scheduling

These are separate concerns:

```
Model Router     → "Which model should perform this task?"
Resource Manager → "Can that model run right now, and where?"
```

Example flow:

```
Vision request
    ↓
Vision Manager selects Gemma 4 E4B
    ↓
Resource Manager checks VRAM
    ↓
Qwen3-8B currently BUSY?
  YES → Queue vision request until Qwen3-8B goes IDLE
  NO  → Load VLM and execute
```

---

## Benchmarking Requirement

Before committing to any model placement (CPU vs GPU) or VRAM budget decision:

1. Benchmark on the actual development laptop.
2. Measure: latency, VRAM usage, CPU utilisation, effect on concurrent models.
3. Record results in `data/benchmarks/`.
4. Document the decision in `docs/DECISIONS.md`.

---

## Related Skills

- `system-design/python-rust-boundary` — Rust provides VRAM/GPU metrics via IPC
- `python/ipc-client` — Fetching resource metrics from tom-engine
- `testing/benchmarking` — How to run TOM's benchmark suite
