# Stage 3: Multi-Model Residency & Swap Pipeline — DESIGN DOCUMENT

**Constraints:** RTX 4060 8GB (~7000 MB usable for TOM), 16GB RAM. One large model resident at a time. Another llama-server (Graphify, :8080) and desktop apps also hold VRAM.

---

## 1. Roles in `models.yaml`

```yaml
models:
  chat:
    model_name: "Qwen2.5-7B-Instruct-GGUF"
    quantization: "Q4_K_M"
    vram_required_mb: 4531
    context_length: 8192
    temperature: 0.7
    server_args:
      - "--ctx-size"
      - "8192"
      - "-ngl"
      - "99"
      - "--flash-attn"
      - "--cache-type-k"
      - "q8_0"
      - "--cache-type-v"
      - "q8_0"

  code:
    model_name: "Qwen2.5-Coder-7B-GGUF"
    quantization: "Q4_K_M"
    vram_required_mb: 5200
    context_length: 8192
    temperature: 0.2
    server_args:
      - "--ctx-size"
      - "8192"
      - "-ngl"
      - "99"
      - "--flash-attn"
      - "--cache-type-k"
      - "q8_0"
      - "--cache-type-v"
      - "q8_0"

  vision:
    model_name: "Qwen2.5-VL-3B-Instruct-GGUF"  # ~3B VL model
    quantization: "Q4_K_M"
    vram_required_mb: 2200
    context_length: 4096
    mmproj: "mmproj-Qwen2.5-VL-3B-Q4_K_M.gguf"
    server_args:
      - "--ctx-size"
      - "4096"
      - "-ngl"
      - "99"
      - "--flash-attn"
      - "--cache-type-k"
      - "q8_0"
      - "--cache-type-v"
      - "q8_0"
      - "--mmproj"
      - "mmproj-Qwen2.5-VL-3B-Q4_K_M.gguf"
```

**Exact files to download for vision:**
- `Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf` (from `Qwen/Qwen2.5-VL-3B-Instruct-GGUF` on HuggingFace)
- `mmproj-Qwen2.5-VL-3B-Q4_K_M.gguf` (same repo, mmproj file for vision projector)

---

## 2. Residency Tracking (Separate from Request Leases)

### Source of Truth
- **NVML (pynvml)** with `nvidia-smi` CLI fallback
- **llama-server PID** that TOM owns/launches
- **Per-role residency record** in `ResourceManager._resident_vram` (IMPLEMENTED)

### Current Implementation (ResourceManager)
The `_resident_vram: dict[ModelRole, int]` dictionary in `ResourceManager` tracks models already loaded in llama-server:
- `register_resident_model(role, vram_mb)` — called by orchestrator when `/v1/models` confirms a model is loaded
- `clear_resident_model(role)` — clears registration on provider connection error
- `get_resident_vram(role)` — returns registered VRAM for budget checks

### Budget Check with Residency
```python
# In check_budget() and _check_budget_for_role():
# Available VRAM for resident model = free_vram + resident_vram - headroom
# This prevents double-counting the model's own VRAM
total_resident_vram = sum(self._resident_vram.values())
available_free_vram = telem.vram_free_mb + total_resident_vram - self.safety_headroom_mb
```

### Residency State Machine (Future: ModelLifecycleCoordinator)
```python
class ModelResidency:
    role: ModelRole                    # CHAT | CODE | VISION
    vram_mb: int                       # from models.yaml (measured: chat=4531)
    pid: int | None                    # llama-server PID (None = not resident)
    server_args: list[str]             # per-role server args
    loaded_at: datetime | None
    last_used_at: datetime | None
```

---

## 3. Swap Pipeline

### Trigger
- `ModelLifecycleCoordinator.ensure_resident(role)` called by orchestrator before inference
- If `current_resident != role` → initiate swap

### Steps (fail-closed, atomic)

```python
async def swap_to_role(target_role: ModelRole) -> None:
    # 1. DRAIN LEASES
    await resource_manager.drain_role(current_resident_role)
    # Wait for all active leases of current role to complete/release
    
    # 2. STOP SERVER (graceful → SIGTERM → wait 5s → SIGKILL)
    await stop_llama_server(current_pid)
    
    # 3. POLL NVML UNTIL VRAM DROPS
    target_free = target_role_vram_mb + 500  # headroom
    await poll_nvml_until_free(target_free, timeout=30s)
    # If timeout → fail closed (ResourceError)
    
    # 4. SPAWN NEW SERVER with role's server_args
    pid = await spawn_llama_server(
        model_path=target_model_path,
        args=target_server_args,
        port=65400
    )
    
    # 5. POLL /health
    await wait_for_health("http://127.0.0.1:65400/v1", timeout=60s)
    
    # 6. VERIFY MODEL ID via /v1/models
    models = await get("/v1/models")
    assert target_model_name in models[0]["model"]
    
    # 7. RECORD RESIDENCY (update ResourceManager._resident_vram)
    coordinator.set_resident(ModelResidency(
        role=target_role,
        vram_mb=target_vram_mb,
        pid=pid,
        server_args=target_server_args,
        loaded_at=now(),
    ))
    # Also clear old residency
    resource_manager.clear_resident_model(old_role)
    resource_manager.register_resident_model(target_role, target_vram_mb)
```

### Failure Handling (Fail Closed)
- **Any step fails** → `ResourceError` raised
- **Attempt restore** → try to reload `chat` model (default resident)
- **Never leave two large models loaded** → if swap partially completes, kill both servers and restart chat
- **On catastrophic failure** → `RuntimeError`, TOM shuts down gracefully

---

## 4. Preflight Check

```python
async def preflight_load(role: ModelRole) -> None:
    telem = await nvml_get_telemetry()
    required = role_vram_mb[role] + 500  # headroom
    free = telem.vram_free_mb
    
    if free < required:
        # Identify other VRAM holders
        holders = await identify_vram_holders()  # e.g., "Graphify (:8080): 1200MB", "Desktop: 800MB"
        raise InsufficientResourceError(
            f"Cannot load {role}: need {required}MB free VRAM, only {free}MB available. "
            f"Current holders: {', '.join(holders)}. Close other GPU apps or use --no-engine."
        )
```

---

## 5. HttpModelProvider — Loopback Only

```python
def __init__(self, base_url: str, ...):
    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1", "[::1]"):
        raise ValueError(f"HttpModelProvider only allows loopback hosts, got: {host}")
    # ... rest of init
```

---

## 6. Test Plan

### Unit Tests (Fakes)
- `FakeProcessLauncher`: records spawn/stop calls, returns fake PIDs
- `FakeNVML`: returns scripted VRAM values per call
- Test swap pipeline logic without real GPU/processes
- Test preflight rejection with actionable message
- Test loopback host enforcement

### Live Swap Test (Marked `@pytest.mark.live`)
```python
@pytest.mark.live
async def test_swap_chat_to_code():
    """Kills :65400, loads code model, verifies residency."""
    # Requires manual approval to run
    # 1. Ensure chat model is resident
    # 2. Call ensure_resident(CODE)
    # 3. Verify swap completes, /v1/models shows code model
    # 4. Run inference with code model
    # 5. Swap back to chat
    # 6. Verify chat model restored
```
**NOT RUN UNTIL APPROVED** — requires physical GPU and llama.cpp binary.

---

## 7. What Already Exists (No Duplication)

| Component | File | Status |
|-----------|------|--------|
| `ResourceManager._resident_vram` | `resources/manager.py` | ✅ Implemented this session |
| `register_resident_model/clear_resident_model/get_resident_vram` | `resources/manager.py` | ✅ Implemented this session |
| Budget check with residency accounting | `resources/manager.py` | ✅ Implemented this session |
| `HttpModelProvider.list_models()` | `models/providers/http.py` | ✅ Implemented this session |
| Orchestrator residency detection | `agents/orchestrator.py` | ✅ Implemented this session |
| `ModelRequest.grammar` | `schemas/models.py` | ✅ Added this session |
| `_build_request_payload` includes grammar | `models/providers/http.py` | ✅ Added this session |
| DEBUG logging of request body | `models/providers/http.py` | ✅ Added this session |
| Tier 2 grammar in router | `core/router.py` | ✅ Added this session |
| `reasoning_vram_mb` in `AgentConfig` | `schemas/agent.py` | ✅ Added this session |
| Orchestrator uses `reasoning_vram_mb` | `agents/orchestrator.py` | ✅ Added this session |
| `TelemetryConfig` with RotatingFileHandler | `schemas/config.py`, `telemetry/logging.py` | ✅ Added this session |
| `MockModelProvider.last_request` | `models/providers/mock.py` | ✅ Added this session |
| Unit test for grammar in request | `tests/unit/core/test_router.py` | ✅ Added this session |
| Residency unit tests | `tests/unit/resources/test_resource_manager.py` | ✅ Added this session |
| E2E CLI test (real path) | `tests/e2e/test_cli_real_path.py` | ✅ Added this session |

### Remaining Work for Stage 3 Implementation
1. `ModelLifecycleCoordinator` class (new file) — wraps ResourceManager residency + swap logic
2. `ModelResidency` dataclass (in coordinator)
3. `spawn_llama_server` / `stop_llama_server` (process management)
4. `poll_nvml_until_free` / `identify_vram_holders` (NVML utilities)
5. `ensure_resident(role)` with swap pipeline
6. Preflight check integration in orchestrator
7. Loopback host validation in `HttpModelProvider.__init__`
8. `@pytest.mark.live` swap test

---

## 8. Integration Points

- **Orchestrator** → calls `coordinator.ensure_resident(ModelRole.REASONING)` before `_think()`
- **Router** → calls `coordinator.ensure_resident(ModelRole.REASONING)` before Tier 2 classification
- **VisionManager** → calls `coordinator.ensure_resident(ModelRole.VISION)` before VLM inference
- **ResourceManager** → provides leases; coordinator provides residency (separate concerns)

---

## 9. Rollback Safety

- All state changes in coordinator are transactional (in-memory, persisted on success)
- On any swap failure: kill spawned process, restore previous PID if alive, else restart chat
- Never mutate `ResourceManager` allocations during swap (only leases)
- NVML polling has hard timeout (30s VRAM drop, 60s health)

---

## 10. Key Design Decision: No Duplicate Mechanism

The `_resident_vram` dict in `ResourceManager` is the single source of truth for residency tracking.
`ModelLifecycleCoordinator` will **wrap** `ResourceManager` and add:
- Process management (spawn/stop llama-server)
- Swap orchestration
- Per-role server args and PID tracking
- Persistence of residency state across restarts

It will NOT create a separate residency tracking system.

---

**END OF DESIGN DOCUMENT** — No implementation edits in this session.