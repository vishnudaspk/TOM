# TOM Agent Handoff

## Current Position
- **Current Phase**: Phase 4 — Agent Framework & Local Model Routing (**IN PROGRESS — Iteration 5/6 Complete**)
- **Next Target**: **Phase 4, Iteration 6 — Phase 4 Exit Gate & Closeout**
- **Completed Milestones**:
  - **Phase 0**: Project foundation, schemas, YAML config loader, structured logging.
  - **Phase 1**: Rust engine (`tom-engine`), named pipe IPC server, system telemetry, audio/hotkey foundations (79 tests).
  - **Phase 2**: Python core, named pipe IPC client, typed `EngineClient`, `LifecycleManager` (168 tests, P95 0.359 ms).
  - **Phase 3 (Tools & Security — 6/6 Iterations Complete)**: Complete deterministic tool pipeline, `PermissionEngine`, sandboxed file tools (`PathGuard`), system tools, bootstrap seam (`setup_default_tools`) (406 tests).
  - **Phase 4 (Agent Framework & Local Model Routing — Iterations 1, 2, 3, 4, & 5 Complete)**:
    - Iteration 1: `python/tom/schemas/agent.py` (`AgentState`, `AgentConfig`, `AgentIdentity`, `Role`, `Message`, `ConversationHistory`, `ToolCallMetadata`, `StateChangeEvent`, `InvalidStateTransitionError`), `python/tom/agents/base.py` (`Agent`, `VALID_TRANSITIONS` matrix, strict transition validation, observer callbacks). Decision 031.
    - Iteration 2: `python/tom/agents/dependencies.py` (`AgentDependencies`), `python/tom/agents/base.py` updated (`AgentAwareConfirmationHook`, `Agent.execute_tool()`). 10 new unit tests in `tests/unit/agents/test_agent_tools.py`. Decision 032.
    - Iteration 3: Pluggable model provider boundary: `ModelProvider` / `LLMProvider` protocol in `python/tom/models/base.py` and `provider.py`, typed schemas in `schemas/models.py` and `models/schemas.py`, deterministic test double `MockModelProvider` in `models/providers/mock.py`, OpenAI-compatible HTTP adapter `HttpModelProvider` and `LMStudioProvider` in `models/providers/http.py` and `models/lmstudio.py`, full SSE streaming parser with reasoning token support (`reasoning_content`), structured error hierarchy, `model_provider` injected into `AgentDependencies`. 61 unit tests (`tests/unit/models/test_providers.py`) and 3 live LM Studio integration smoke tests (`tests/integration/models/test_lmstudio_smoke.py`). Decision 033.
    - Iteration 4: Two-Tier Intent & Model Router: `python/tom/schemas/router.py` (`IntentDomain` enum, `RoutingDecision` schema), `python/tom/core/router.py` (`IntentRouter` with Tier 1 heuristic < 1ms and Tier 2 model-backed classification via `LLMProvider`), `tests/unit/core/test_router.py` (49 tests covering Tier 1, Tier 2 mock, ambiguous input, provider failure, malformed output). Decision 034.
    - Iteration 5: Agent Orchestrator: `python/tom/agents/orchestrator.py` (`AgentOrchestrator` with multi-step reasoning loop, bounded execution, cooperative cancellation, and error recovery), `python/tom/core/context.py` (`CancellationToken`), `tests/unit/agents/test_agent_loop.py` (24 tests covering initialization, Tier 1 direct tool routing, Tier 2 multi-step model loop, tool call execution, model error recovery, cooperative cancellation, state transitions, and step bounds). Decision 035.
- **Environment**: Dedicated `.venv` at `C:\Users\vishnuu\Projects\TOM\.venv` (Python 3.11.9, pytest 9.1.1, ruff 0.16.7).
- **Current Verified Test Baseline**:
  - Python Unit: **492 passed** (`pytest tests/unit/ -v`)
  - Python Integration: **50 passed** (`pytest tests/integration/ -v`)
  - Total Python: **542 passed**
  - Rust: **79 passed** (`cargo test` in `rust/tom-engine`)
  - Total Verified Tests: **621 passed** (0 failures, 0 regressions)
  - Quality Gates: Ruff clean (0 violations, 0 diffs), Cargo clippy/fmt clean

---

## Iteration 4 — Completed

The Two-Tier Intent & Model Router is implemented and tested:

- **`python/tom/schemas/router.py`**: `IntentDomain` enum (`SYSTEM`, `FILES`, `REASONING`, `MEMORY`, `VISION`, `CHAT`, `UNKNOWN`) and `RoutingDecision` schema (domain, confidence, target_tool, route_tier, latency_ms).
- **`python/tom/core/router.py`**: `IntentRouter` class providing:
  - **Tier 1** (fast heuristic < 1ms): keyword/pattern matching for obvious system/file requests and direct tool name patterns (`system.*`, `files.*`).
  - **Tier 2** (model-backed via `LLMProvider`): classifies ambiguous prompts using a classification prompt; safe fallback to `REASONING` on provider failure, timeout, or malformed output.
- **`tests/unit/core/test_router.py`**: 49 deterministic tests covering Tier 1 paths, Tier 2 with mock provider, ambiguous input, provider failure/timeout, and malformed model output.

---

## Iteration 5 — Completed

The Agent Orchestrator, Cancellation, and Error Recovery systems are implemented and tested:

- **`python/tom/agents/orchestrator.py`**: `AgentOrchestrator` class providing:
  - **Pipeline**: `User Prompt → IntentRouter → Direct Tool or Agent Loop → Response`
  - **Tier 1 path**: Direct tool execution via `_execute_direct_tool` with result formatting (BaseModel → JSON, dict/list → JSON, fallback → string).
  - **Tier 2 path**: Multi-step agent loop (`_run_agent_loop`) — model → tool → observation loop bounded by `max_steps`.
  - **Cancellation**: Cooperative cancellation via `CancellationToken`; checks before each step, between tool calls, and at loop start; returns `"Task cancelled"` and transitions to `TERMINATED`.
  - **Error recovery**: Model errors return `"Model error: ..."` with `ERROR` state; max steps exceeded sets `ERROR` state and returns `"Maximum steps exceeded"`.
- **`python/tom/core/context.py`**: `CancellationToken` class wrapping `asyncio.Event` for cooperative cross-task cancellation signalling.
- **`tests/unit/agents/test_agent_loop.py`**: 24 deterministic tests covering initialization, Tier 1 direct tool routing, Tier 2 multi-step model loop, tool call execution, model error recovery (provider error, timeout, generic exception), cooperative cancellation, direct tool error handling, state transitions, model request construction, step bounds, and empty prompt handling.

---

## Mandatory Development Rules for Next Agent

1. **Virtual Environment**: Always use `C:\Users\vishnuu\Projects\TOM\.venv`. Never pollute external Python environments.
2. **Phase Boundary Discipline**: Implement ONLY Iteration 6 (Phase 4 Exit Gate). Do not implement beyond Phase 4.
3. **No Heavy AI Dependencies**: Do NOT install `torch`, `llama-cpp-python`, `transformers`, `vLLM`, `qdrant-client`, or `whisper`.
4. **Phase 4 Plan Remains Active**: Do NOT delete `PHASE4_IMPLEMENTATIONPLAN.md` until Iteration 6 and Phase 4 exit gate are complete.
