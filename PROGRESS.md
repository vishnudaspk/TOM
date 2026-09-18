# TOM Development Progress Ledger

## Phase 0 — Foundation / Project Bootstrap (COMPLETE)
- **Objective**: Repository scaffold, configuration system, structured logging, packaging, and tooling.
- **Key Result**: 15-phase roadmap, directory tree, `pyproject.toml`, Pydantic v2 schemas (`schemas/config.py`), YAML loader (`core/config.py`), JSON logger with secret redactor (`telemetry/logging.py`).
- **Tests**: 9 passed.

---

## Phase 1 — Rust Engine (`tom-engine`) (COMPLETE)
- **Objective**: Always-on background engine for IPC, system telemetry, audio capture/playback, and global hotkeys.
- **Key Result**: 10/10 iterations complete. Windows Named Pipe server (`\\.\pipe\tom-engine`), Tokio async runtime, protocol v1 NDJSON framing, bounded shutdown (< 12 ms), P95 IPC latency 43.69 µs.
- **Tests**: 79 passed (`cargo test`).

---

## Phase 2 — Python Core & IPC Client (COMPLETE)
- **Objective**: Asynchronous Python IPC client communicating with `tom-engine`, typed models, reconnection, and lifecycle management.
- **Key Result**: 8/8 iterations complete. `NamedPipeIpcClient` with UUID4 correlation, timeout/cancellation shielding, exponential backoff reconnection, typed `EngineClient`, and `LifecycleManager`. Live P95 latency 0.359 ms (< 10ms target).
- **Tests**: 168 passed (153 unit + 15 integration).

---

## Phase 3 — Deterministic Tools & Permission Engine (CLOSED & COMPLETE — 6/6 Iterations)
- **Objective**: Deterministic, typed, testable tool execution layer with 3-tier pre-execution permission engine (`SAFE`, `ASK_USER`, `BLOCK`), path-traversal guards, sub-millisecond dispatch overhead, and complete pipeline integration.

### Completed Iterations:
- **Iteration 1 — Tool Definition, Base Models & Tool Registry**:
  - `python/tom/tools/registry.py`, `python/tom/tools/__init__.py`.
  - Implemented `ToolDefinition`, `ToolResult`, `PermissionLevel`, `ToolRegistry`, `@tool` decorator with automated schema extraction, and OpenAI/Qwen compatible JSON schema export.
  - Tests: 39 passed (`tests/unit/tools/test_registry.py`).
  - Decision 026: Tool system foundation strictly decoupled from LLM/agent layers.
- **Iteration 2 — Security & Permission Engine**:
  - `python/tom/security/permissions.py`, `python/tom/security/confirmation.py`, `python/tom/security/__init__.py`.
  - Implemented `PermissionEngine`, `PermissionDecision`, blocklist scanning (unconditional `BLOCK`), tool policy overrides, and `ConfirmationHook` (`ConsoleConfirmationHook`, `CallbackConfirmationHook`, testing hooks) defaulting to denial on timeout.
  - Tests: 33 passed (`tests/unit/security/test_permissions.py`).
  - Decision 027: Centralized pre-execution security boundary and safe failure defaults.
- **Iteration 3 — Tool Executor (Pre-Execution Validation, Timeouts & Cancellation)**:
  - `python/tom/tools/executor.py`, `python/tom/tools/__init__.py`.
  - Implemented `ToolExecutor` orchestrating registry lookup, centralized permission check, confirmation, Pydantic argument validation, timeout deadlines, clean cancellation with zero task leaks, monotonic execution duration measurement, and `ToolResult` packaging. Supports async and sync tools seamlessly.
  - Tests: 22 passed (`tests/unit/tools/test_executor.py`).
  - Decision 028: Tool execution pipeline, cancellation safety, and sub-millisecond dispatch.
- **Iteration 4+5 (Combined) — Deterministic System Tools & Sandboxed File Tools**:
  - `python/tom/tools/system.py`, `python/tom/tools/files.py`, `python/tom/tools/__init__.py`.
  - System tools: 7 tools (`system.cpu_info`, `system.memory_info`, `system.gpu_info`, `system.battery_info`, `system.disk_info`, `system.list_processes`, `system.get_snapshot`). Dual-mode: `EngineClient` delegation with graceful stdlib/psutil fallback. All `SAFE`.
  - File tools: 6 tools (`files.read_file`, `files.list_directory`, `files.search_files`, `files.file_info`, `files.write_file`, `files.delete_file`). `PathGuard` security barrier with traversal rejection (`..`), Windows reserved name blocking (`CON`, `PRN`, `AUX`, `NUL`, `COM1-9`, `LPT1-9`), `is_relative_to()` whitelist boundary, and 5MB read limit. Read ops `SAFE`, mutations `ASK_USER`.
  - Tests: 33 new (16 system + 17 file). Total Python unit: 280.
  - Decision 029: Combined System Tools & Sandboxed Filesystem Architecture.
- **Iteration 6 — Integration Pipeline & Exit Gate (Closeout)**:
  - `python/tom/tools/bootstrap.py`, `python/tom/tools/__init__.py`, `tests/integration/tools/test_tool_pipeline.py`.
  - Canonical tool registration bootstrap seam `setup_default_tools` wiring all 13 built-in tools (7 system + 6 file).
  - 32 end-to-end integration tests verified in `tests/integration/tools/test_tool_pipeline.py`.
  - Decision 030: Canonical Tool Registration Seam (`setup_default_tools`).
  - Phase 3 Exit Gate: ALL CRITERIA PASSED.
  - Phase 3 formally marked CLOSED; execution artifacts cleaned up per Phase Closeout Rule.

---

## Phase 4 — Agent Framework & Local Model Routing (IN PROGRESS — 5/6 Iterations Complete)
- **Objective**: Foundational AI Agent abstraction, deterministic state machine, message/context models, pluggable model provider interface (LM Studio/Bionic compatibility), two-tier intent/model routing, and multi-step agent loop with cancellation.

### Completed Iterations:
- **Iteration 1 — Agent Definition, State Machine & Context Model**:
  - `python/tom/schemas/agent.py`, `python/tom/agents/base.py`, `python/tom/agents/__init__.py`.
  - Implemented `AgentState` enum (`IDLE`, `THINKING`, `ACTING`, `WAITING_CONFIRMATION`, `ERROR`, `TERMINATED`).
  - Implemented `AgentConfig`, `AgentIdentity`, `Role` enum, `Message` model, and `ConversationHistory` bounded container.
  - Implemented `Agent` base class enforcing `VALID_TRANSITIONS` matrix and state change callback dispatch.
  - Tests: 68 passed (`tests/unit/agents/test_agent_state.py`, `tests/unit/agents/test_context.py`).
  - Decision 031: Agent State Machine Architecture & Invariants.
- **Iteration 2 — Tool System Integration & Execution Safety**:
  - `python/tom/agents/dependencies.py`, `python/tom/agents/base.py`, `python/tom/agents/__init__.py`.
  - `AgentDependencies` injectable container carrying `ToolRegistry`, `ToolExecutor`, `PathGuard`, and optional `LifecycleManager`.
  - `AgentAwareConfirmationHook` for transparent lifecycle state synchronization during confirmation prompts.
  - `Agent.execute_tool()` with strict `ToolExecutor.execute()` routing (Decision 032), error containment, and `Role.TOOL_RESULT` observation recording.
  - Tests: 10 new (`tests/unit/agents/test_agent_tools.py`), 78 total agent tests passing.
  - Decision 032: Centralized Tool Invocation Safety.
- **Iteration 3 — Model Provider Interface & LM Studio / Bionic Compatibility**:
  - `python/tom/models/base.py`, `python/tom/models/provider.py`, `python/tom/schemas/models.py`, `python/tom/models/schemas.py`, `python/tom/models/providers/mock.py`, `python/tom/models/providers/http.py`, `python/tom/models/lmstudio.py`, `python/tom/models/__init__.py`, `python/tom/agents/dependencies.py` (updated).
  - Implemented abstract `ModelProvider` (or `LLMProvider`) protocol defining `generate()`, `stream()`, and `check_health()` async methods.
  - Implemented Pydantic v2 schemas: `ModelRequest`, `ModelResponse`, `StreamChunk`, `TokenUsage`, `ToolCallRequest`, `FinishReason`.
  - Implemented `MockModelProvider`: 100% deterministic test double with scripted responses, simulated latency, error injection, and async generator chunk streaming.
  - Implemented `HttpModelProvider` and `LMStudioProvider`: Async HTTP client mapping TOM requests to OpenAI-compatible `/v1/chat/completions`, with Server-Sent Events (SSE) streaming, reasoning-token extraction (`reasoning_content`), authorization header redaction in telemetry, and clean cancellation handling.
  - Implemented clean error hierarchy: `ModelProviderError`, `ModelConnectionError`, `ModelAPIError`, `ModelTimeoutError`, `ModelResponseError`.
  - Updated `AgentDependencies` to include `model_provider: LLMProvider | None` without tightly coupling `Agent` to concrete provider classes.
  - Tests: 61 unit tests (`tests/unit/models/test_providers.py`) + 3 live LM Studio integration smoke tests (`tests/integration/models/test_lmstudio_smoke.py`). Total Python unit: 419, total Python integration: 50. Total Python: 469 passed.
  - Decision 033: Decoupled Model Provider Protocol & LM Studio (Bionic) Compatibility.

### Current Verified Test Baseline:
- Python Unit: 468 passed | Python Integration: 50 passed | Rust: 79 passed | Total: 597 passed (+49 in Iteration 4).
- Quality Gates: Ruff 0 violations / 0 diffs, Cargo clippy/fmt clean.

---

- **Iteration 4 — Intent & Model Routing Engine**:
  - `python/tom/schemas/router.py`, `python/tom/core/router.py`, `tests/unit/core/test_router.py`.
  - Implemented `IntentDomain` enum (`SYSTEM`, `FILES`, `REASONING`, `MEMORY`, `VISION`, `CHAT`, `UNKNOWN`) and `RoutingDecision` schema (domain, confidence, target_tool, route_tier, latency_ms).
  - Implemented `IntentRouter` with Tier 1 (fast deterministic heuristic < 1ms via keyword/pattern matching) and Tier 2 (model-backed classification via `LLMProvider` < 350ms target).
  - Safe handling: provider failure, timeout, and malformed output fall back to `REASONING` with low confidence.
  - Decision 034: Two-Tier Intent and Model Routing Architecture.
  - Tests: 49 passed (`tests/unit/core/test_router.py`).

  - **Iteration 5 — Agent Orchestrator, Cancellation & Error Recovery**:
    - `python/tom/agents/orchestrator.py` (`AgentOrchestrator` with multi-step reasoning loop, bounded execution, cooperative cancellation, and error recovery), `python/tom/core/context.py` (`CancellationToken`), `tests/unit/agents/test_agent_loop.py`.
    - Implemented `AgentOrchestrator`: pipeline `User Prompt → IntentRouter → Direct Tool or Agent Loop → Response` with step limits, cooperative cancellation tokens, and structured error recovery.
    - Implemented `CancellationToken`: cooperative cancellation primitive wrapping `asyncio.Event` for cross-task signalling.
    - Direct tool execution path with result formatting (BaseModel → JSON, dict/list → JSON, fallback → string).
    - Multi-step agent loop (`_run_agent_loop`) with per-step cancellation checks, `THINKING → ACTING → THINKING` transitions via `execute_tool`, and graceful termination on cancellation or max-steps.
    - Safe handling: model errors return `"Model error: ..."`, max steps exceeded sets `ERROR` state, cancellation returns `"Task cancelled"` and transitions to `TERMINATED`.
    - Decision 035: Step-Bounded Execution Loop & Cooperative Task Cancellation.
    - Tests: 24 passed (`tests/unit/agents/test_agent_loop.py`).

---

### Current Verified Test Baseline:
- Python Unit: **492 passed** (`pytest tests/unit/ -v`) (+24 in Iteration 5).
- Python Integration: **50 passed** (`pytest tests/integration/ -v`).
- Rust Engine: **79 passed** (`cargo test` in `rust/tom-engine`).
- Total Verified Tests: **621 passed** (0 failures, 0 regressions).
- Quality Gates: Ruff 0 violations / 0 diffs, Cargo clippy/fmt clean.

---

## Next Milestone
