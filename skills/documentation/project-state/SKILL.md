---
name: documentation-project-state
description: >
  How to keep TOM's project state documents (STATE.md, HANDOFF.md, PROGRESS.md,
  DECISIONS.md) accurate and useful. Use after completing any meaningful implementation
  work, when preparing a handoff to another agent or session, or when an architectural
  decision is made. Also use to understand what these files should contain.
---

# Documentation — Project State

TOM's state documents are the shared communication channel between agents and sessions.
An agent that reads these files should be able to understand the project state without
scanning the entire codebase.

---

## File Purposes

| File | Purpose |
|------|---------|
| `docs/STATE.md` | What is currently implemented and working |
| `docs/HANDOFF.md` | What the next agent needs to know to continue |
| `docs/PROGRESS.md` | Phase-level progress and milestone tracking |
| `docs/DECISIONS.md` | Architectural decisions and their rationale |
| `docs/TASKS.md` | Current task list (what is in progress / next) |

---

## `docs/STATE.md`

Describe the current implementation state factually.

Good content:
- Which subsystems are implemented and tested.
- Known working integrations.
- Known gaps or partial implementations.
- Current blockers.

Bad content:
- Future plans (that goes in TASKS or HANDOFF).
- Speculation.
- Architecture explanation (that's in ARCHITECTURE.md).

Example structure:
```markdown
# TOM — Current State

Last updated: 2026-09-12

## Implemented
- tom-engine: IPC server (JSON/named pipe), basic system monitors (CPU, GPU)
- Python IPC client with timeout and error handling

## Partially Implemented
- Memory manager: SQLite backend complete; Qdrant integration in progress

## Not Started
- Voice pipeline
- Vision system
- Permission confirmation flow
```

---

## `docs/HANDOFF.md`

Written for the next agent or session. Should be immediately actionable.

Required sections:
- **What was just completed** (brief, factual)
- **Files changed** (list)
- **Tests run** (which tests, did they pass)
- **Known problems** (any issues found, not yet fixed)
- **Decisions made** (brief, link to DECISIONS.md for details)
- **Next recommended action** (what to do next)

Keep it concise. An agent should read it in under 2 minutes.

---

## `docs/DECISIONS.md`

Record architectural decisions with context and rationale.

Format:
```markdown
## Decision: Use named pipes for IPC on Windows

**Date:** 2026-09-10
**Status:** Accepted

**Context:** IPC needed between Python and Rust on Windows. Options were:
local TCP, named pipes, WebSocket.

**Decision:** Named pipes for MVP — simpler, no port management, OS-managed.

**Rationale:** plan.md recommends the simplest reliable mechanism. Named
pipes require no firewall configuration and are well-supported on Windows.

**Consequences:** IPC is Windows-specific for now. Abstract transport interface
allows future addition of Unix socket on Linux.
```

---

## `docs/PROGRESS.md`

Track development phase progress. Update milestone completion percentages.

```markdown
# TOM Development Progress

## Phase 0 — Foundation (Current)
- [x] Repository structure
- [x] Cargo workspace and pyproject.toml
- [/] Initial AGENTS.md and skills system
- [ ] CI/CD setup

## Phase 1 — Rust Engine
- [ ] IPC server
- [ ] System monitors
...
```

---

## When to Update

Update state documents:
- After completing an implementation task.
- After a test suite passes.
- After making an architectural decision.
- Before ending a session (handoff).
- When a known problem is discovered or resolved.

Do not update them speculatively or with intended future state.

---

## Keep It Concise

These documents exist for fast orientation. If a section grows beyond one page,
consider linking to a more detailed document rather than expanding inline.

---

## Related Skills

- `git/workflow` — Committing state document updates
- `system-design/python-rust-boundary` — Decisions that belong in DECISIONS.md
