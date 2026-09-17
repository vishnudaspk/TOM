# TOM Skill Index

This index lists every available skill, its purpose, trigger condition, relevant phases,
and skill dependencies.

**Purpose:** Use this file to determine which skill(s) to load for a given task.
Do NOT load all skills — load only what is relevant to the current task.

---

## How to Use This Index

1. Identify your current task (e.g. "implement IPC server in Rust").
2. Check which TOM development phase this belongs to.
3. Find relevant skills from the table below.
4. Load those skills before starting work.

---

## Skill Table

| Skill | Location | Use When | Phase(s) | Depends On |
|-------|----------|----------|----------|------------|
| **Rust: Async / Tokio** | `rust/async-tokio/` | Writing/reviewing any async Rust code in tom-engine | Phase 1 | — |
| **Rust: IPC** | `rust/ipc/` | Building/modifying the IPC server in tom-engine | Phase 1 | rust/async-tokio |
| **Rust: Error Handling** | `rust/error-handling/` | Writing error handling, reviewing for silent failures | Phase 1 | — |
| **Rust: Event System** | `rust/event-system/` | Building/modifying the Rust event bus | Phase 1 | rust/async-tokio |
| **Rust: Testing** | `rust/testing/` | Writing unit/integration tests for tom-engine | Phase 1 | rust/async-tokio |
| **Python: Pydantic Agents** | `python/pydantic-agents/` | Creating/modifying AI agents in TOM's Python brain | Phase 2+ | python/tool-system |
| **Python: Tool System** | `python/tool-system/` | Creating tools, modifying tool registry/executor | Phase 2+ | security/permission-model |
| **Python: Memory System** | `python/memory-system/` | Building/using the SQLite+Qdrant memory layer | Phase 2+ | — |
| **Python: IPC Client** | `python/ipc-client/` | Building Python-side IPC client, IPC calls | Phase 1+ | rust/ipc |
| **Security: Permission Model** | `security/permission-model/` | Classifying tool risk, confirmation flows, auditing | All phases | — |
| **Security: Secrets** | `security/secrets/` | Handling credentials, secrets storage, log filtering | All phases | — |
| **System Design: Python/Rust Boundary** | `system-design/python-rust-boundary/` | Deciding what belongs in Python vs Rust | Phase 1+ | — |
| **System Design: Resource Management** | `system-design/resource-management/` | VRAM budget, model lifecycle, concurrency | Phase 2+ | system-design/python-rust-boundary |
| **Testing: Strategy** | `testing/strategy/` | Planning tests for a new subsystem | All phases | — |
| **Testing: Python Testing** | `testing/python-testing/` | Writing Python unit/integration tests | Phase 2+ | testing/strategy |
| **Debugging: Rust Errors** | `debugging/rust-errors/` | Diagnosing borrow checker, async, IPC issues | Phase 1+ | — |
| **Debugging: Python Agent** | `debugging/python-agent/` | Diagnosing wrong tool calls, validation errors, IPC timeouts | Phase 2+ | — |
| **Agent Dev: Tool Design** | `agent-development/tool-design/` | Designing individual agent-callable tools | Phase 2+ | security/permission-model |
| **Agent Dev: Task Lifecycle** | `agent-development/task-lifecycle/` | Implementing orchestrator, task manager, planner | Phase 2+ | — |
| **Coding: Validation** | `coding/validation/` | Writing input validation at subsystem boundaries | All phases | — |
| **Coding: Structured Logging** | `coding/structured-logging/` | Adding logging to a new subsystem | All phases | security/secrets |
| **Model: LM Studio** | `lm-studio/` | Benchmarking candidate models, measuring TTFT, throughput, VRAM, and experimenting with local runtimes | Phase 2+ | system-design/resource-management |
| **Git: Workflow** | `git/workflow/` | Commits, PRs, .gitignore, pre-commit checks | All phases | — |
| **Documentation: Project State** | `documentation/project-state/` | Updating STATE.md, HANDOFF.md after work | All phases | — |
| **Documentation: Phase Closeout** | `documentation/phase-closeout/` | Formally closing/archiving a completed phase, updating STATE/PROGRESS/HANDOFF, deleting completed PHASE[N]_IMPLEMENTATIONPLAN.md, verifying deletion, purging stale references | All phases | documentation/project-state |

---

## Quick Reference by Task Type

### "I'm experimenting with local models or benchmarking"
→ `lm-studio` + `system-design/resource-management`

### "I'm building a Rust subsystem"
→ `rust/async-tokio` + `rust/error-handling` + `rust/testing`
→ Add `rust/ipc` if it involves the IPC server
→ Add `rust/event-system` if it emits events

### "I'm building a Python agent tool"
→ `python/tool-system` + `agent-development/tool-design` + `security/permission-model`

### "I'm adding a new AI agent"
→ `python/pydantic-agents` + `agent-development/task-lifecycle`

### "I'm implementing memory"
→ `python/memory-system` + `security/permission-model`

### "I'm doing a security review"
→ `security/permission-model` + `security/secrets` + `coding/validation`

### "I'm debugging a Rust issue"
→ `debugging/rust-errors`

### "I'm debugging a Python/agent issue"
→ `debugging/python-agent`

### "I'm about to commit"
→ `git/workflow`

### "I just finished a task"
→ `documentation/project-state`

### "I just finished a phase / I'm closing out a phase"
→ `documentation/phase-closeout` + `documentation/project-state`

---

## Scope Reminder

Skills are loaded selectively based on the task at hand.
Do not load the entire `/skills` directory.

Read `PHASE_SKILL_MAP.md` to understand which skills are relevant to each development phase.
