# TOM Current State Snapshot

## Current Status
- **Current Phase**: Phase 4 — Agent Framework & Local Model Routing (**OPEN — Iteration 5/6 Complete**)
- **Next Target**: **Phase 4, Iteration 6 — Phase 4 Exit Gate & Closeout**
- **Completed Phases & Milestones**:
  - **Phase 0 (Bootstrap)**: Project scaffold, Pydantic schemas, YAML config loader, JSON logging with secret redactor.
  - **Phase 1 (Rust Engine)**: Always-on `tom-engine`, Windows Named Pipe IPC (`\\.\pipe\tom-engine`), Tokio async runtime, hardware telemetry, audio & input foundations, 79 tests passing.
  - **Phase 2 (Python Core & IPC Client)**: Async IPC client, protocol v1 models/errors, reconnection state machine, high-level `EngineClient`, `LifecycleManager`, 168 tests passing (P95 roundtrip 0.359 ms).
  - **Phase 3 (Tools & Security)**: Complete 6-iteration deterministic tool pipeline, `PermissionEngine` (SAFE, ASK_USER, BLOCK), sandboxed file tools (`PathGuard`), system tools, and tool bootstrap seam (`setup_default_tools`) (406 verified tests passing).
  - **Phase 4 (Agent Framework & Local Model Routing)**:
    - Iteration 1: `Agent` base class (`python/tom/agents/base.py`), deterministic 6-state lifecycle (`IDLE`, `THINKING`, `ACTING`, `WAITING_CONFIRMATION`, `ERROR`, `TERMINATED`), explicit `VALID_TRANSITIONS` matrix, typed schemas (`AgentConfig`, `AgentIdentity`, `Role`, `Message`, `ConversationHistory`, `ToolCallMetadata`, `StateChangeEvent`, `InvalidStateTransitionError`), state change callbacks, 68 unit tests passing.
    - Iteration 2: `AgentDependencies` injectable container (`python/tom/agents/dependencies.py`), `AgentAwareConfirmationHook` state-synchronizing confirmation wrapper, `Agent.execute_tool()` with strict ToolExecutor routing (Decision 032), confirmation state alignment (`ACTING` ↔ `WAITING_CONFIRMATION`), full error containment, `CancelledError` → `TERMINATED` transition, `Role.TOOL_RESULT` observation appended to `ConversationHistory` on every outcome. 10 new unit tests, 78 total agent tests passing.
    - Iteration 3: Pluggable model provider boundary (`ModelProvider` / `LLMProvider` protocol in `python/tom/models/base.py` and `provider.py`), typed Pydantic v2 schemas (`ModelRequest`, `ModelResponse`, `StreamChunk`, `TokenUsage`, `ToolCallRequest`, `FinishReason` in `schemas/models.py`), deterministic test double `MockModelProvider` (`models/providers/mock.py`), OpenAI-compatible async HTTP provider `HttpModelProvider` and `LMStudioProvider` (`models/providers/http.py`, `models/lmstudio.py`) with full SSE streaming parser, structured error hierarchy (`ModelProviderError`, `ModelConnectionError`, `ModelAPIError`, `ModelTimeoutError`, `ModelResponseError`), cancellation safety, and optional `model_provider` field in `AgentDependencies`. 61 model unit tests + 3 live LM Studio smoke tests passing.
    - Iteration 4: Intent & Model Router (`python/tom/schemas/router.py` with `IntentDomain` enum and `RoutingDecision` schema, `python/tom/core/router.py` with `IntentRouter` providing Tier 1 fast heuristic classification < 1ms and Tier 2 model-backed classification via `LLMProvider`), `tests/unit/core/test_router.py`. 49 deterministic unit tests covering Tier 1 keyword/pattern routing, Tier 2 mock provider classification, ambiguous input fallback, provider failure/timeout recovery, and malformed model output handling. Decision 034: Two-Tier Intent and Model Routing Architecture.
    - Iteration 5: Agent Orchestrator (`python/tom/agents/orchestrator.py` with `AgentOrchestrator` providing multi-step reasoning loop, bounded execution, cooperative cancellation, and error recovery), `python/tom/core/context.py` (`CancellationToken`), `tests/unit/agents/test_agent_loop.py`. 24 deterministic unit tests covering initialization, Tier 1 direct tool routing, Tier 2 multi-step model loop, tool call execution, model error recovery, cooperative cancellation, state transitions, and step bounds. Decision 035: Step-Bounded Execution Loop & Cooperative Task Cancellation.
- **Environment**: Dedicated `.venv` at `C:\Users\vishnuu\Projects\TOM\.venv` (Python 3.11.9, pytest 9.1.1, ruff 0.16.7).

## Mandatory Constraints & Rules
1. **Virtual Environment Isolation**: Always use `C:\Users\vishnuu\Projects\TOM\.venv`. Never pollute global or external Python environments. Verify `sys.executable` before installs.
2. **Dependency Discipline**: Strictly install dependencies required by the active phase. Never install Phase 4+ dependencies (heavy PyTorch, llama-cpp, Qdrant) prematurely.
3. **LLM/Agent Independence for Tools**: Tools are deterministic, typed Python capabilities. They must never import, call, or depend on LLMs, prompts, routers, Qwen, LM Studio, or agents.
4. **Centralized Pre-Execution Permission Enforcement**: The 3-tier security model (`SAFE`, `ASK_USER`, `BLOCK`) is evaluated centrally by `PermissionEngine` *before* execution. Individual tools never evaluate their own safety. `BLOCK` operations are unconditionally denied; `ASK_USER` requires explicit confirmation via `ConfirmationHook`.
5. **Hardware & Resource Limits**: Strictly bounded to 8GB VRAM (RTX 4060) and 16GB system RAM. Whisper STT defaults to CPU int8.
6. **Phase Closeout Convention**: At the completion of the final iteration of every phase:
   1. Run phase exit gate.
   2. Update `STATE.md`, `PROGRESS.md`, and `HANDOFF.md`.
   3. Migrate any unique historical context from the phase implementation plan.
   4. Delete the completed phase implementation plan file.
   5. Verify deletion and purge stale references.
   6. Verify repository sanity and mark the phase CLOSED.
   7. Only then begin the next phase in a subsequent session.

## Architecture Essentials
- **Dual-Core Stack**: Python Brain (orchestration, agents, tools, memory, model routing) + Rust Engine (`tom-engine`: hardware sensors, audio, hotkeys, IPC server).
- **Tool Pipeline**: Request → `ToolRegistry` (lookup & schema) → `PermissionEngine` (pre-execution policy check) → `ConfirmationHook` (if ASK_USER) → `ToolExecutor` (Pydantic validation, timeout, cancellation) → Deterministic Tools (`system`, `files`) → `ToolResult`.
- **Model Provider Seam**: `Agent` → `ModelProvider` (abstract protocol) → `HttpModelProvider` / `LMStudioProvider` → OpenAI-compatible endpoint (local `http://127.0.0.1:1234/v1` or remote Bionic). Unit testing relies on `MockModelProvider` with zero network overhead.
- **IPC Protocol**: Protocol v1, Windows Named Pipe `\\.\pipe\tom-engine`, NDJSON framing bounded by 1MB.

## Key Active Architectural Decisions
- **001–005**: 15-phase roadmap, Windows Named Pipes, strict Pydantic v2 validation, SQLite + Qdrant memory separation, Whisper CPU placement.
- **006–010**: Incremental iterations, 5s bounded shutdown, protocol v1 NDJSON framing, 500ms server-side request timeout.
- **011–016**: Throttled async `SystemMonitor` mutex, graceful telemetry degradation, broadcast `EventBus`, audio/input mock seams.
- **017–019**: Request `extra="forbid"` and response `extra="ignore"`, asyncio named pipe transport, single background receive loop with UUID4 correlation.
- **020–022**: Builtin `TimeoutError` and `asyncio.shield` cancellation protection, backoff reconnection state machine, thin `EngineClient` returning flat Pydantic models.
- **023–024**: Idempotent `LifecycleManager`, clean task audits, live IPC benchmark < 0.4 ms P95 (target < 10ms).
- **025**: Phase 3 6-iteration modular plan; path-traversal guards and directory whitelisting for file tools.
- **026**: `ToolDefinition`, `ToolResult`, `PermissionLevel`, `ToolRegistry`, and `@tool` decorator. Dynamic schema extraction via `create_model`.
- **027**: Centralized `PermissionEngine` with blocklist scanning (unconditional `BLOCK`), tool-specific policy overrides, and decoupled `ConfirmationHook` implementations defaulting to denial on timeout.
- **028**: `ToolExecutor` strictly enforces linear pipeline: Registry Lookup → Permission Check → Confirmation → Argument Validation → Timeout/Cancellation Invocation → `ToolResult`. Async/sync tools seamlessly supported; monotonic `time.perf_counter()` timing; inner execution tasks cleanly cancelled and awaited with zero task leaks on timeout or caller cancellation.
- **029**: Combined System Tools & Sandboxed Filesystem Architecture — zero new dependencies, stdlib/psutil fallback, strict `PathGuard` security boundary, `ASK_USER` for all mutations.
- **030**: Canonical Tool Registration Seam via `tom.tools.bootstrap.setup_default_tools` — registers built-in tools into any target registry without introducing LLM or agent dependencies.
- **031**: Agent State Machine Architecture & Invariants — Dedicated 6-state lifecycle (`IDLE`, `THINKING`, `ACTING`, `WAITING_CONFIRMATION`, `ERROR`, `TERMINATED`) owned strictly by `Agent` instances. State mutations are validated against an explicit transition matrix. `TERMINATED` is genuinely terminal with zero outgoing transitions.
- **032**: Centralized Tool Invocation Safety — `Agent` never invokes tool handlers directly, evaluates permissions independently, or bypasses `ToolExecutor`. All tool operations flow exclusively through `ToolExecutor.execute()`. `AgentAwareConfirmationHook` wraps the executor's confirmation hook to synchronize agent lifecycle state with ASK_USER confirmation flows. Error containment guarantees no exceptions escape `execute_tool()` except `CancelledError`.
- **033**: Decoupled Model Provider Protocol & LM Studio (Bionic) Compatibility — The Agent layer communicates with language models strictly through the abstract `ModelProvider` (or `LLMProvider`) protocol, completely decoupled from runtime implementations, model identifiers, or network endpoints. Concrete providers (`LMStudioProvider`, `HttpModelProvider`) encapsulate HTTP communication against OpenAI-compatible endpoints (`/v1/chat/completions`) using async `httpx`, handling both complete and SSE streaming generation. Unit tests use `MockModelProvider` ensuring zero network dependencies, while future remote providers (e.g. Bionic) can fulfill the identical contract without altering Agent logic.

## Current Test Baseline
- **Python Unit**: **492 passed** (`pytest tests/unit/ -v`).
- **Python Integration**: **50 passed** (`pytest tests/integration/ -v`).
- **Total Python**: **542 passed** (+24 in Phase 4 Iteration 5).
- **Rust Engine**: **79 passed** (`cargo test` in `rust/tom-engine`).
- **Total Verified Tests**: **621 passed** (+24 since Phase 4 Iteration 4).
- **Quality Gates**: Ruff clean (0 violations, 0 diffs), Cargo clippy/fmt clean.

## Immediate Next Task
- **Phase 4, Iteration 6 — Phase 4 Exit Gate & Closeout**: Run exit gate, update documentation, and formally close Phase 4.
