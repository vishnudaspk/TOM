# TOM Phase 4 — Agent Framework & Local Model Routing
## Implementation-Ready Architecture & Iteration Plan

**Author:** Principal AI Systems & Architecture Engineer  
**Baseline:** Phase 1 (Rust Engine), Phase 2 (Python Core & IPC), and Phase 3 (Deterministic Tools & Permission Engine) CLOSED & COMPLETE.  
**Verified Tests Baseline:** 406 total passed (280 Python unit, 47 Python integration, 79 Rust engine) | Zero regressions.  
**Status:** PLANNING DOCUMENT — Pending User Approval for Phase 4 Execution.

---

## 1. Phase 4 Objective & Scope

> Implement the foundational AI Agent abstraction, deterministic state machine, message/context model, pluggable model provider interface (with LM Studio/Bionic compatibility), and two-tier intent/model router. Connect this framework directly to Phase 3's centralized tool pipeline with cooperative cancellation, step bounding, and zero unvetted model dependencies.

At the completion of Phase 4, TOM will be able to:
1. Initialize and execute deterministic AI Agents governed by an explicit 6-state lifecycle (`IDLE`, `THINKING`, `ACTING`, `WAITING_CONFIRMATION`, `ERROR`, `TERMINATED`).
2. Maintain structured, bounded message histories (`Message`, `Role`, `ToolCallRequest`, `ToolCallResult`).
3. Safely execute multi-step tool calls by consuming Phase 3's `ToolExecutor`, `ToolRegistry`, and `PermissionEngine` with zero pipeline bypass.
4. Interact with language models via a decoupled `LLMProvider` protocol, supporting mock testing, local OpenAI-compatible endpoints (LM Studio / Bionic), and cloud fallbacks without requiring binary/CUDA compilation.
5. Classify incoming user requests through a two-tier Intent & Model Router (fast heuristics < 1ms + model-backed classification < 350ms) into typed intent domains (`SYSTEM`, `FILES`, `REASONING`, `MEMORY`, `VISION`, `CHAT`, `UNKNOWN`).
6. Cooperatively cancel active agent workflows, model requests, and tool executions with zero task leaks.
7. Adhere strictly to the 8GB VRAM (RTX 4060) and 16GB system RAM resource budget.

---

## 2. Roadmap Reconciliation & Architectural Scope

Comparing authoritative project records reveals the following architectural alignment:

| Source | Original Definition | Reconciled Phase 4 Execution Scope |
| :--- | :--- | :--- |
| `plan.md` | §4 Python Brain owns Orchestrator, Intent Router, Model Router, Planner, LLM providers, Agent workflows. §10 Task State (`EXECUTING`, `WAITING_CONFIRMATION`, etc.). §12 Tiny Router (Qwen3-1.7B). §13 Reasoning (Qwen3-8B). | Fully adopted. Distinguishes high-level intent routing from model selection. Keeps Agent abstraction decoupled from physical inference weights. |
| `IMPLEMENTATIONPLAN.md` | Split across Phase 4 (Local LLM), Phase 5 (Router & Task Lifecycle), and Phase 6 (Agent Framework & Tool Orchestration). | **Synthesized into Phase 4 (Agent Framework & Local Model Routing)**. Tools already completed in Phase 3; the Agent framework is their direct consumer. Heavy weight compilation (GGUF CUDA / vLLM) is decoupled into provider adapters. |
| `STATE.md` / `HANDOFF.md` | Directs Phase 4 as: *Agent Framework & Local Model Routing*, starting with *Iteration 1: Agent Definition, Loop & State Machine*. | Authoritative starting baseline. Reconciled sequence executes Agent loop, tool consumption, provider abstraction, and routing incrementally. |

---

## 3. Mandatory Architectural & Development Rules

### Rule 1: Virtual Environment Isolation (`.venv`)
All Python development, package management, and test execution must strictly occur within:
```text
C:\Users\vishnuu\Projects\TOM\.venv
```
Verify interpreter before any command:
```powershell
.\.venv\Scripts\python.exe -c "import sys; print(sys.executable)"
```

### Rule 2: Dependency Discipline (No Premature AI Stacks)
* **Zero Heavy AI Dependencies in Phase 4**: Do NOT install or compile `torch`, `llama-cpp-python`, `vLLM`, `transformers`, `accelerate`, or `whisper`.
* Phase 4 model interactions are mediated via standard HTTP interfaces (`httpx` already in `.venv`) conforming to OpenAI-compatible endpoints (e.g. local LM Studio / Bionic server at `http://localhost:1234/v1`).
* Unit testing is 100% deterministic using `MockModelProvider`.

### Rule 3: Strict Tool Pipeline Invocation (Zero Bypass)
Agents must **never** invoke tool functions directly, import tool handlers, or evaluate permissions locally. All tool operations must flow through:
```text
Agent
  ↓
ToolExecutor.execute(name, params)
  ↓
ToolRegistry (lookup & schema)
  ↓
PermissionEngine (SAFE / ASK_USER / BLOCK)
  ↓
ConfirmationHook (if ASK_USER)
  ↓
Deterministic Handler (system / files)
  ↓
ToolResult
```

### Rule 4: Cooperative Cancellation & Zero Task Leaks
* Every agent execution must receive a `CancellationToken` (wrapping `asyncio.Event`).
* Active thinking or tool execution tasks must be cleanly awaited and cancelled upon interruption.
* Cancelled tasks must transition to `TERMINATED` (or `CANCELLED`) and never leave orphaned background tasks.

### Rule 5: Step Bounding & Safe Termination
* Multi-step agent loops must enforce `max_steps` (default: 10).
* Exceeding `max_steps` transitions to `ERROR` / `TERMINATED` rather than entering an unbounded loop.

---

## 4. Phase 4 Subsystem Architecture

```text
                               User / Caller Request
                                        │
                                        ▼
                           ┌──────────────────────────┐
                           │   Intent & Model Router  │
                           │     (core/router.py)     │
                           └────────────┬─────────────┘
                                        │
               ┌────────────────────────┴────────────────────────┐
               ▼                                                 ▼
        Fast Direct Tool                               Complex Agent Workflow
     (system.*, files.list)                             (Reasoning / Multi-step)
               │                                                 │
               ▼                                                 ▼
        ToolExecutor                                  ┌────────────────────┐
                                                      │    Base Agent      │
                                                      │ (agents/base.py)   │
                                                      └─────────┬──────────┘
                                                                │
                                        ┌───────────────────────┴───────────────────────┐
                                        ▼                                               ▼
                           ┌──────────────────────────┐                   ┌───────────────────────────┐
                           │      Model Provider      │                   │       Tool Executor       │
                           │    (models/providers)    │                   │      (tools/executor)     │
                           │  - MockProvider (Tests)  │                   │  - PermissionEngine       │
                           │  - HttpProvider (LM/Bionic)│                 │  - ConfirmationHook       │
                           └──────────────────────────┘                   │  - Deterministic Tools    │
                                                                          └───────────────────────────┘
```

---

## 5. Iteration Plan

### Iteration 1 — Agent Definition, State Machine & Context Model
* **Objective**: Implement the base `Agent` class, deterministic 6-state lifecycle state machine, and structured conversation/context data models.
* **Prerequisites**: Phase 3 complete (test baseline 406).
* **Target Files**:
  * `python/tom/schemas/agent.py`
  * `python/tom/agents/base.py`
  * `python/tom/agents/__init__.py`
  * `tests/unit/agents/test_agent_state.py`
  * `tests/unit/agents/test_context.py`
* **Components**:
  * `AgentState` enum: `IDLE`, `THINKING`, `ACTING`, `WAITING_CONFIRMATION`, `ERROR`, `TERMINATED`.
  * `AgentConfig`: `name`, `max_steps`, `system_prompt`, `temperature`.
  * `AgentIdentity`: Unique agent instance identifier, creation timestamp.
  * `Role` enum: `SYSTEM`, `USER`, `ASSISTANT`, `TOOL_CALL`, `TOOL_RESULT`.
  * `Message`: Typed message model with content, role, tool call metadata, timestamps.
  * `ConversationHistory`: Bounded, ordered message container with trimming and serialization.
  * `Agent` base class: Manages state transitions, enforces valid transition matrix, raises `InvalidStateTransitionError` on illegal moves, dispatches state change callbacks.
* **State Transition Matrix**:
  * `IDLE` → `THINKING`, `TERMINATED`
  * `THINKING` → `ACTING`, `WAITING_CONFIRMATION`, `IDLE`, `ERROR`, `TERMINATED`
  * `ACTING` → `THINKING`, `WAITING_CONFIRMATION`, `IDLE`, `ERROR`, `TERMINATED`
  * `WAITING_CONFIRMATION` → `ACTING`, `IDLE`, `ERROR`, `TERMINATED`
  * `ERROR` → `IDLE`, `TERMINATED`
  * `TERMINATED` → (Terminal state; no transitions allowed)
* **Tests**:
  * State transition tests (all valid transitions succeed; invalid transitions raise `InvalidStateTransitionError`).
  * Message history invariants (ordering preserved, append operations validated).
  * Context bounding and serialization tests.
* **Required Skills**: `python/pydantic-agents`, `agent-development/task-lifecycle`, `coding/validation`, `testing/python-testing`.
* **Exit Criteria**: All state transitions deterministic and validated; message models validate with Pydantic v2; unit tests pass 100%.
* **Architectural Decision**: **Decision 031: Agent State Machine Architecture & Invariants**.

---

### Iteration 2 — Tool System Integration & Execution Safety
* **Objective**: Wire Phase 3's `ToolRegistry` and `ToolExecutor` into the Agent execution pipeline with strict safety guarantees.
* **Prerequisites**: Iteration 1 complete.
* **Target Files**:
  * `python/tom/agents/dependencies.py`
  * `python/tom/agents/base.py`
  * `tests/unit/agents/test_agent_tools.py`
* **Components**:
  * `AgentDependencies`: Injectable dependency container carrying `ToolRegistry`, `ToolExecutor`, `PathGuard`, and optional `LifecycleManager`.
  * `Agent.execute_tool(tool_name, params)`: Dispatches tool through `ToolExecutor`.
  * Transition to `ACTING` during execution; transition to `WAITING_CONFIRMATION` if confirmation is requested.
  * Formatting `ToolResult` into `Message(role=Role.TOOL_RESULT)` and updating `ConversationHistory`.
  * Error containment: Tool execution failures and permission denials do not crash the agent; they are converted to observation messages.
* **Tests**:
  * SAFE tool execution via agent (e.g. `system.cpu_info`) updates history and returns to `THINKING` or `IDLE`.
  * ASK_USER tool confirmation flow: Agent enters `WAITING_CONFIRMATION`; on approval executes and records result; on denial records denial.
  * BLOCK tool rejection: Agent cleanly records permission denial without crashing.
  * Verification that agent cannot bypass `ToolExecutor` (pipeline integrity check).
* **Required Skills**: `python/tool-system`, `security/permission-model`, `agent-development/tool-design`, `testing/python-testing`.
* **Exit Criteria**: Tools execute strictly through `ToolExecutor`; state transitions reflect execution state; 100% of tool integration tests pass.
* **Architectural Decision**: **Decision 032: Centralized Tool Invocation Safety (Strict ToolExecutor Routing)**.

---

### Iteration 3 — Model Provider Interface & LM Studio / Bionic Compatibility
* **Objective**: Implement the decoupled language model provider protocol, deterministic mock provider for unit testing, and OpenAI-compatible HTTP provider for local LM Studio / Bionic endpoints.
* **Prerequisites**: Iteration 2 complete.
* **Target Files**:
  * `python/tom/schemas/models.py`
  * `python/tom/models/base.py`
  * `python/tom/models/providers/mock.py`
  * `python/tom/models/providers/http.py`
  * `python/tom/models/__init__.py`
  * `tests/unit/models/test_providers.py`
* **Components**:
  * Data Models: `ModelRequest`, `ModelResponse`, `StreamChunk`, `TokenUsage`, `ToolCallRequest`.
  * `LLMProvider` abstract protocol:
    ```python
    async def generate(self, request: ModelRequest) -> ModelResponse: ...
    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]: ...
    async def check_health(self) -> bool: ...
    ```
  * `MockModelProvider`: Programmed responses, scripted tool calls, latency simulation, and configurable error injection.
  * `HttpModelProvider`: Thin, robust `httpx.AsyncClient` communicating with OpenAI-compatible endpoints (`/v1/chat/completions`). Supports local LM Studio (Bionic) on `http://127.0.0.1:1234/v1` and cloud APIs. Automatic secret redactor for authorization headers.
  * Streaming parser: Translates SSE chunks into `StreamChunk` objects.
* **Tests**:
  * Mock provider returns planned responses and tool calls.
  * HTTP provider correctly formats requests, headers, and tool definitions.
  * HTTP provider handles network disconnects, HTTP 500, timeouts, and malformed JSON gracefully.
  * Secret redactor prevents API keys from appearing in logs or error traces.
* **Required Skills**: `lm-studio`, `system-design/resource-management`, `coding/structured-logging`, `testing/python-testing`.
* **Exit Criteria**: Model provider strictly decoupled from agent logic; zero binary/CUDA dependencies; unit tests pass 100%.
* **Architectural Decision**: **Decision 033: Decoupled Model Provider Protocol & LM Studio (Bionic) Seam**.

---

### Iteration 4 — Intent & Model Routing Engine
* **Objective**: Build the two-tier routing system classifying user requests into intent domains (`SYSTEM`, `FILES`, `REASONING`, `MEMORY`, `VISION`, `CHAT`, `UNKNOWN`) and selecting model runtime routes.
* **Prerequisites**: Iteration 3 complete.
* **Target Files**:
  * `python/tom/schemas/routing.py`
  * `python/tom/core/router.py`
  * `python/tom/models/router.py`
  * `tests/unit/core/test_router.py`
* **Components**:
  * `IntentType` enum: `SYSTEM`, `FILES`, `REASONING`, `MEMORY`, `VISION`, `CHAT`, `UNKNOWN`.
  * `RoutingDecision` schema: `intent`, `route`, `confidence`, `reasoning_model`, `direct_tool_name`, `estimated_vram_mb`.
  * Tier 1 (Fast Rule Heuristic): Sub-millisecond regex/keyword classifier for obvious deterministic tool commands (e.g. "cpu usage", "list files", "disk space").
  * Tier 2 (Model-Backed Classifier): Lightweight classification request using `LLMProvider` (targeting Qwen3-1.7B latency < 350ms) for ambiguous or complex prompts.
  * Resource Budget Constraints: Checks estimated VRAM against 8GB hardware limit before routing to heavy models.
* **Tests**:
  * Tier 1 classifies system/file requests in < 1ms without invoking LLM.
  * Tier 2 invokes model provider and returns valid structured `RoutingDecision`.
  * Ambiguous requests gracefully default to `REASONING` with safe confidence scores.
  * Router never crashes on malformed inputs.
* **Required Skills**: `agent-development/task-lifecycle`, `coding/validation`, `testing/python-testing`.
* **Exit Criteria**: Routing latency budgets met; classification accuracy verified on test prompt suite; unit tests pass 100%.
* **Architectural Decision**: **Decision 034: Two-Tier Intent and Model Routing Architecture**.

---

### Iteration 5 — Multi-Step Agent Loop, Cancellation & Error Recovery
* **Objective**: Assemble the complete multi-turn reasoning and tool execution loop with step limits, cooperative cancellation tokens, and error recovery.
* **Prerequisites**: Iteration 4 complete.
* **Target Files**:
  * `python/tom/core/context.py`
  * `python/tom/agents/orchestrator.py`
  * `tests/unit/agents/test_agent_loop.py`
* **Components**:
  * `CancellationToken`: Cooperative cancellation primitive wrapping `asyncio.Event`. Propagated to both `LLMProvider` and `ToolExecutor`.
  * `AgentOrchestrator.run(prompt)` loop:
    1. Check cancellation token.
    2. Route request via `Router`.
    3. If direct tool: execute tool, format response, complete.
    4. If multi-step agent: loop `THINKING` (model provider) → `ACTING` (tool executor) → `OBSERVATION` (history append) until model produces final text or `max_steps` reached.
  * Bounded steps: `max_steps` guard prevents infinite execution loops.
  * Cancellation handling: Cancelling the token aborts active HTTP requests or tool tasks, transitions agent to `TERMINATED`, and cleans up all asyncio tasks.
  * Error recovery: Tool errors returned to model as observations for self-correction.
* **Tests**:
  * Multi-step execution: Prompt requiring 2 tool calls successfully executes and terminates with final answer.
  * Cancellation during thinking: Immediate termination, zero leaked tasks.
  * Cancellation during tool execution: Interruption honored, tool task cancelled.
  * Max steps exceeded: Halts gracefully with structured error result.
* **Required Skills**: `agent-development/task-lifecycle`, `python/pydantic-agents`, `debugging/python-agent`, `testing/python-testing`.
* **Exit Criteria**: Multi-turn agent loop verified; cooperative cancellation tested with zero task leakage; unit tests pass 100%.
* **Architectural Decision**: **Decision 035: Step-Bounded Execution Loop & Cooperative Cancellation**.

---

### Iteration 6 — End-to-End Pipeline Integration, Exit Gate & Phase Closeout
* **Objective**: Perform end-to-end integration tests connecting the real tool pipeline to the agent loop, validate all Phase 4 exit criteria, and execute the permanent Phase Closeout procedure.
* **Prerequisites**: Iterations 1–5 complete.
* **Target Files**:
  * `tests/integration/agents/test_agent_pipeline.py`
  * `STATE.md`, `PROGRESS.md`, `HANDOFF.md`
* **Components**:
  * End-to-end integration test suite exercising live `ToolRegistry` (system + files), `PermissionEngine`, `HttpModelProvider` (or mock server), and `AgentOrchestrator`.
  * Verification of dispatch latency (< 1ms tool overhead, < 5ms agent dispatch).
  * Phase 4 Exit Gate execution.
  * Phase Closeout Execution (via `skills/documentation/phase-closeout/SKILL.md`):
    1. Verify test baseline and quality gates.
    2. Update `STATE.md` (mark Phase 4 CLOSED, record Decision 031–035).
    3. Update `PROGRESS.md` (record 6/6 iterations complete).
    4. Update `HANDOFF.md` (for Phase 5 transition).
    5. Migrate unique historical context.
    6. Delete `PHASE4_IMPLEMENTATIONPLAN.md` and verify deletion (`Test-Path` == `False`).
    7. Purge stale references.
    8. Sanity check repository.
* **Tests**:
  * Full integration test: User prompt → Intent Router → Agent → Mock/HTTP Model → `ToolExecutor` → Result.
  * Full regression test suite: Python unit, Python integration, Rust engine tests.
* **Required Skills**: `documentation/phase-closeout`, `documentation/project-state`, `testing/python-testing`.
* **Exit Criteria**: All Phase 4 exit criteria satisfied; repository clean; Phase 4 marked CLOSED.

---

## 6. Phase 4 Exit Gate Checklist

```text
[ ] pytest tests/unit/ -v                              ALL PASS
[ ] pytest tests/integration/ -v                      ALL PASS
[ ] ruff check python/ tests/                          0 violations
[ ] ruff format --check python/ tests/                 0 diffs
[ ] cargo test (in rust/tom-engine)                    79/79 PASS (Zero Rust regressions)
[ ] Agent state machine deterministic                  100% VERIFIED (Invalid transitions raise error)
[ ] ToolExecutor integration non-bypassable           100% ENFORCED (Zero direct tool calls)
[ ] Cooperative cancellation verified                  0 leaked asyncio tasks on interrupt
[ ] Zero premature binary/CUDA AI dependencies        VERIFIED (No torch, llama-cpp-python, vLLM)
[ ] LM Studio / Bionic compatibility confirmed         OpenAI-compatible HTTP provider tested
[ ] Two-tier routing latency verified                  < 1ms heuristic, < 350ms model tier
[ ] Python dependencies installed in .venv             VERIFIED
[ ] Phase closeout workflow completed                  PHASE4_IMPLEMENTATIONPLAN.md deleted & verified
[ ] STATE.md, PROGRESS.md, HANDOFF.md updated          DONE
```

---

## 7. Dependency Changes

Phase 4 maintains strict dependency discipline:
* **Existing in `.venv`**: `pydantic` (schemas), `httpx` (HTTP provider), `psutil` (system tools), `pyyaml` (config), `asyncio` (standard library).
* **New dependencies for Phase 4**: **NONE required**.
  * `httpx` is already installed and handles OpenAI-compatible JSON and SSE streaming communication for local LM Studio / Bionic.
  * Pydantic v2 is already installed and handles all state/context/routing validation.
* **Explicitly Prohibited**: `torch`, `llama-cpp-python`, `transformers`, `vLLM`, `qdrant-client`, `whisper`.

---

## 8. Architectural Decisions Sequence

* **Decision 031: Agent State Machine Architecture & Invariants**
  * Dedicated 6-state lifecycle (`IDLE`, `THINKING`, `ACTING`, `WAITING_CONFIRMATION`, `ERROR`, `TERMINATED`) owned strictly by `Agent` instances. State mutations are validated against an explicit transition matrix.
* **Decision 032: Centralized Tool Invocation Safety (Strict ToolExecutor Routing)**
  * Agents never invoke tools directly or evaluate security policies. All tool requests pass through `ToolExecutor` to preserve Phase 3's `SAFE`, `ASK_USER`, and `BLOCK` protections.
* **Decision 033: Decoupled Model Provider Protocol & LM Studio (Bionic) Compatibility**
  * Model inference is abstracted behind `LLMProvider`. Local execution uses OpenAI-compatible HTTP endpoints (supporting LM Studio / Bionic) with zero binary/CUDA compiler coupling in the core agent framework.
* **Decision 034: Two-Tier Intent and Model Routing Architecture**
  * Fast rule heuristics (< 1ms) handle deterministic tool requests; lightweight model classification (< 350ms) handles ambiguous requests. High-level intent is kept separate from physical model selection.
* **Decision 035: Step-Bounded Execution Loop & Cooperative Task Cancellation**
  * Multi-turn agent loops enforce `max_steps` bounds and consume `CancellationToken` primitives, guaranteeing prompt and leak-free cancellation across thinking, streaming, and tool execution.

---

## 9. Phase Closeout Requirements

Per the project's permanent `documentation-phase-closeout` skill:
1. Upon completing Iteration 6 and passing all exit gate checks, `STATE.md`, `PROGRESS.md`, and `HANDOFF.md` will be finalized.
2. Unique architectural notes will be preserved in permanent documentation.
3. `PHASE4_IMPLEMENTATIONPLAN.md` will be permanently deleted and verified.
4. Repository sanity will be verified before any Phase 5 work begins.
