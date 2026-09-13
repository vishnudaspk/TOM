---
name: lm-studio
description: >
  How to use LM Studio (Bionic) as a local development and benchmarking runtime for TOM.
  Use when discovering local models, loading/unloading models, configuring context length
  or GPU offloading, running controlled inference tests, benchmarking TTFT, throughput,
  or switch time, monitoring VRAM/RAM/GPU usage, and testing candidate model configurations
  against TOM's 8GB VRAM budget. Reinforces that LM Studio is strictly an experimentation
  runtime decoupled from TOM's permanent model architecture.
---

# LM Studio — Experimentation & Benchmarking Runtime

LM Studio (and its CLI/headless engine `lms` / Bionic) serves as TOM's **experimentation
and development runtime**. It is NOT a permanent architectural dependency.

---

## Architectural Separation of Concerns

Always maintain strict decoupling across these four concepts:

```text
+-------------------------------------------------------------+
| 1. Model Selection                                          |
|    "Which model handles this task?" (e.g., Qwen3-8B)        |
+-------------------------------------------------------------+
| 2. Model Configuration                                      |
|    "What context, temperature, and quantization are set?"   |
+-------------------------------------------------------------+
| 3. Inference Runtime (e.g., LM Studio during dev)           |
|    "What local engine executes weights and exposes an API?" |
+-------------------------------------------------------------+
| 4. Resource Management                                      |
|    "Can this model fit into the 8GB VRAM budget right now?" |
+-------------------------------------------------------------+
```

### Critical Rules
- **Do NOT hardcode LM Studio-specific endpoints or SDKs** across TOM core application logic.
- TOM interacts with model runtimes solely through its `LLMProvider` abstraction (defaulting to standard OpenAI-compatible HTTP endpoints like `http://127.0.0.1:1234/v1`).
- The final production inference runtime will be selected later based on benchmarked latency, memory footprint, and headless deployment capabilities.

---

## Hardware Budget Guardrails (RTX 4060 8GB)

Every experiment must respect TOM's local workstation ceiling:
- **GPU VRAM Ceiling:** 8,192 MB (RTX 4060 Laptop).
- **Safe VRAM Allocation Target:** <= 6,000 MB for primary reasoning model to retain headroom for Windows DWM and transient VLM/image workloads.
- **System RAM Ceiling:** 16,384 MB total.
- **Over-budget action:** If model weights + KV-cache exceed 7,000 MB VRAM, immediately down-quantize (e.g., Q5_K_M -> Q4_K_M) or offload specific layers to CPU.

---

## CLI & Runtime Discovery (`lms`)

When working with LM Studio on Windows, interact via the `lms` CLI or HTTP REST endpoints:

### 1. Check Runtime Status & Server Health
```powershell
# Verify CLI availability
lms --version

# Check server status
lms status

# Query active models via OpenAI-compatible endpoint
curl http://127.0.0.1:1234/v1/models
```

### 2. Discover Locally Downloaded Models
```powershell
# List all models downloaded in local LM Studio cache
lms ls
```

### 3. Load Model with Controlled Offload & Context
```powershell
# Load candidate model with explicit context length and GPU layers
lms load qwen3-8b-instruct --gpu max --context-length 8192

# Or load with CPU offload constraints when budgeting VRAM
lms load qwen3-8b-instruct --gpu 0.8 --context-length 4096
```

### 4. Unload Model (Freeing VRAM)
```powershell
# Always unload before switching candidate models or testing competing workloads
lms unload --all
# Or unload specific model:
lms unload qwen3-8b-instruct
```

---

## Controlled Inference & Benchmarking Procedure

When benchmarking a candidate model configuration, run controlled tests using streaming
requests to measure true user-perceived performance.

### Required Metrics to Record

| Metric | Target / Unit | How to Measure |
|---|---|---|
| **Load / Switch Time** | Seconds (`s`) | Time from `load` invocation until first inference-ready state |
| **TTFT** (Time to First Token) | Milliseconds (`ms`) | Elapsed time from request send to receiving the first streaming chunk |
| **Throughput** | Tokens/sec (`tps`) | `(total_eval_tokens - 1) / (generation_time_seconds)` |
| **Peak VRAM** | Megabytes (`MB`) | Measured via NVML / `nvidia-smi` during active generation |
| **KV-Cache Impact** | Megabytes (`MB`) | VRAM delta between idle model load and saturated context window |
| **RAM Footprint** | Megabytes (`MB`) | Process working set of `lms` / runtime host |
| **GPU Utilization** | Percent (`%`) | Peak compute load on RTX 4060 during decode |

---

## Benchmarking Protocol Implementation

Use standard Python `httpx` or `urllib` to benchmark against the runtime without coupling to proprietary libraries:

```python
import time
import httpx
import json

def benchmark_inference(
    model_id: str,
    prompt: str,
    base_url: str = "http://127.0.0.1:1234/v1",
    max_tokens: int = 256,
) -> dict[str, float]:
    """Execute a controlled streaming inference request and measure TTFT and throughput."""
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": True,
        "temperature": 0.2,
    }

    start_time = time.perf_counter()
    first_token_time = None
    token_count = 0

    with httpx.stream("POST", f"{base_url}/chat/completions", json=payload, timeout=60.0) as resp:
        for line in resp.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:].strip()
            if data_str == "[DONE]":
                break
            chunk = json.loads(data_str)
            delta = chunk["choices"][0].get("delta", {}).get("content", "")
            if delta:
                if first_token_time is None:
                    first_token_time = time.perf_counter()
                token_count += 1

    end_time = time.perf_counter()

    ttft_ms = round((first_token_time - start_time) * 1000, 2) if first_token_time else 0.0
    gen_duration = end_time - (first_token_time or start_time)
    tokens_per_sec = round(token_count / gen_duration, 2) if gen_duration > 0 else 0.0

    return {
        "ttft_ms": ttft_ms,
        "tokens_per_sec": tokens_per_sec,
        "total_tokens": token_count,
        "total_duration_s": round(end_time - start_time, 3),
    }
```

---

## Concurrency & Contention Testing

TOM requires deterministic multi-subsystem behavior. Use LM Studio to test:
1. **Parallel Requests:** Dispatch 2-3 simultaneous tool/agent queries. Observe whether LM Studio serializes or handles them concurrently, and record TTFT degradation.
2. **Context Window Expansion:** Benchmark at 2K, 4K, 8K, and 16K context. Note the exact VRAM step-up from the KV-cache to avoid out-of-memory crashes.
3. **Model Switch Overhead:** Unload router model -> load reasoning model. Measure latency. If switch overhead > 3.0 seconds, models must either reside concurrently (if VRAM allows) or router must remain on CPU/tiny weights.

---

## Standard Benchmark Recording Template

When evaluating candidate configurations, record results in this structured format:

```markdown
### Benchmark Run: [Model Name & Quantization]
- **Date / Environment:** [Date], Windows 11, RTX 4060 (8GB), Driver [Version]
- **Runtime:** LM Studio / Bionic (OpenAI API local endpoint)
- **Model Parameters:** Context: [e.g. 8192], GPU Offload: [e.g. Max / 33 layers]
- **Load Time:** [X.X] s
- **TTFT:** [X] ms
- **Throughput:** [X.X] tokens/sec
- **VRAM Idle:** [X,XXX] MB
- **VRAM Peak (Generation):** [X,XXX] MB
- **Budget Compliance:** [PASS / FAIL] (<= 6,000 MB VRAM target)
- **Observations:** [Quality, tool-calling precision, or stability notes]
```

---

## Runtime Decoupling Verification Checklist

Before accepting any model configuration into TOM's codebase, verify:
- [ ] No TOM module imports or executes `lms` commands directly in production code paths.
- [ ] Endpoint is configured via `config/models.yaml` (e.g. `base_url: http://127.0.0.1:1234/v1`).
- [ ] Switching `base_url` to another server (e.g. vLLM, Ollama, llama.cpp server) requires zero code changes.
- [ ] Tested model stays within the 8GB RTX 4060 ceiling under maximum planned context length.
