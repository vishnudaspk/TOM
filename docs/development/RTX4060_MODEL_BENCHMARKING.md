# RTX 4060 Candidate Model Benchmarking & Hardware Allocation Architecture

## 1. Executive Summary

This document establishes the empirical benchmarking protocol, hardware constraints, and resource governor rules for running local language and vision-language models on TOM's reference hardware:

* **Host System**: Intel Core i9-13900H, 16 GB physical RAM
* **Dedicated GPU**: NVIDIA GeForce RTX 4060 Laptop GPU (8,192 MB VRAM)
* **Operating System**: Windows 11 (Desktop Window Manager / DWM GPU consumer)
* **Target Runtime**: Local-first OpenAI-compatible endpoints (LM Studio / Bionic / llama.cpp)

Phase 8 Iteration 3 implements the **measurement infrastructure and safety governor** without speculative model downloads.

---

## 2. Hardware Budget & Constraint Envelope

### 2.1 Physical VRAM Breakdown

```text
Total Dedicated VRAM: 8,192 MB
├── Windows 11 DWM / OS Display Overhead:     ~800 MB - 1,000 MB
├── Configured Max TOM Allocation:           ~7,200 MB (config.resources.max_vram_mb)
├── Mandatory Safety Headroom Margin:         ~500 MB (preserved at all times)
└── Hard Usable Ceiling for TOM Models:      ~6,700 MB
```

### 2.2 Host System RAM Breakdown

```text
Total Physical System RAM: 16,384 MB
├── Windows 11 OS / Background Services:     ~4,000 MB - 5,000 MB
├── Configured Max TOM Memory:              ~12,000 MB (config.resources.max_ram_mb)
└── Available for Audio (Whisper/Kokoro):     Preserved on CPU to protect VRAM
```

---

## 3. Concurrency Invariant & Mutual Exclusion

### 3.1 The Multi-Model Contention Problem

On an 8 GB GPU:
* Reasoning Model (7B/8B Q4_K_M): requires **~5,200 MB** VRAM.
* Multimodal Vision-Language Model (7B VLM Q4_K_M): requires **~5,500 MB** VRAM.
* Total concurrent requirement: **~10,700 MB** (> 8,192 MB physical ceiling $\rightarrow$ GPU Out of Memory / Driver Crash / CUDA Context Eviction).

### 3.2 The Mutual Exclusion Invariant

> **Heavy local inference for the primary reasoning model and the local VLM must be strictly mutually exclusive.**

This rule is enforced deterministically by `tom.resources.ResourceManager`:
1. `ModelRole.REASONING` and `ModelRole.VLM` are designated `HEAVY_ROLES`.
2. Acquisition of a lease for any heavy role requires acquiring the internal `_heavy_lock` (`asyncio.Lock`).
3. If an inference task attempts to run a heavy role while another is active:
   - In non-blocking mode: raises `ResourceLockError` immediately.
   - In timeout mode: waits up to the configured timeout before raising `ResourceLockError`.
   - In standard mode: awaits release of the preceding lease.
4. Lightweight models (e.g. `ModelRole.ROUTER` at ~1.5B/1.7B, ~1,200 MB) do not hold the heavy lock and can run concurrently if VRAM budget permits.

### 3.3 Safe Acquire / Release Lifecycle

```text
TaskExecutor / Planner / VLM
         │
         ▼
  acquire(role, required_vram, required_ram)
         │
         ├── Acquire heavy_lock (if REASONING or VLM)
         ├── Check budget (software ceiling AND telemetry free VRAM - 500 MB headroom)
         │     └── If check fails: release heavy_lock, raise InsufficientResourceError
         └── Yield ResourceLease
                 │
                 ▼
          [Run Inference]
                 │
                 ▼ (finally / exception / cancellation)
          lease.release()
                 ├── Decrement software-tracked allocations
                 ├── Release heavy_lock
                 └── Return resources to available pool
```

---

## 4. Benchmark Metric Definitions

The benchmark harness (`python/tom/models/benchmark.py`) measures four empirical metrics:

| Metric | Unit | Target SLA (Router) | Target SLA (Reasoner) | Definition |
|---|---|---|---|---|
| **TTFT** | ms | < 350 ms | < 1,200 ms | **Time to First Token**: Elapsed time from dispatching `stream(request)` until the first non-empty content chunk is yielded. Measures UI reactivity. |
| **Throughput** | tokens/sec | > 35 tokens/s | > 18 tokens/s | **Generation Speed**: Total generated completion tokens divided by total generation duration in seconds. |
| **JSON Schema Fidelity** | % passing | > 99.0% | > 95.0% | **Structured Reliability**: Percentage of runs that successfully parse and validate against target Pydantic schemas (e.g. `Plan`) without schema hallucination. |
| **VRAM Footprint & Residency** | MB | < 1,500 MB | < 5,500 MB | **Dedicated GPU Memory**: Peak VRAM allocated during inference and net persistent residency measured via NVML. |

---

## 5. Distinction: Infrastructure vs. Model Acquisition

* **Iteration 3 Deliverable**: Standardized benchmark harness (`ModelBenchmarkSuite`), hardware resource governor (`ResourceManager`), offline test doubles (`MockModelProvider`, `MockTelemetryProvider`), and benchmark entry point (`bench_llm.py`).
* **Zero Speculative Downloads**: In accordance with TOM architectural rules, model weights (several gigabytes) are not speculatively downloaded into the repository during unit testing.
* **Evaluation Scaffolding**: Offline unit tests verify all calculations and lock transitions in < 0.5s with zero GPU requirements.

---

## 6. Candidate Models for RTX 4060 Evaluation

When local models are benchmarked via live endpoints (`TOM_BENCHMARK_LIVE=1`), the following candidate models and quantizations are evaluated:

### 6.1 Intent Router Candidates (~1.5B - 3B)
* `Qwen/Qwen2.5-1.5B-Instruct-GGUF` (Q4_K_M / Q5_K_M, ~1,100 - 1,400 MB VRAM)
* `Qwen/Qwen3-1.7B-GGUF` (Q4_K_M, ~1,200 MB VRAM)
* Role: Fast intent classification, tool routing (< 350 ms TTFT, > 35 tps).

### 6.2 Reasoning & Planning Candidates (~7B - 8B)
* `Qwen/Qwen2.5-7B-Instruct-GGUF` (Q4_K_M, ~4,800 - 5,200 MB VRAM)
* `Qwen/Qwen3-8B-GGUF` (Q4_K_M, ~5,200 - 5,600 MB VRAM)
* Role: Long-horizon DAG step decomposition, error recovery, JSON plan formulation.

### 6.3 Vision-Language Candidates (~7B VLM)
* `Qwen/Qwen2.5-VL-7B-Instruct-GGUF` (Q4_K_M, ~5,500 MB VRAM)
* Role: Complex visual reasoning and semantic UI groundings (mutually exclusive with 8B reasoner).

---

## 7. How to Execute Live Benchmarking

To benchmark a live model endpoint running in LM Studio or llama.cpp:

```powershell
# 1. Start your local model runtime (e.g. LM Studio on port 1234)
# 2. Run the benchmark tool
python tests/benchmarks/models/bench_llm.py --live --base-url http://127.0.0.1:1234/v1 --model qwen2.5-7b-instruct --iterations 5
```

The tool outputs a structured report with exact mean, min, max, p50, and p95 timings, JSON schema validity rate, and NVML VRAM residency.
