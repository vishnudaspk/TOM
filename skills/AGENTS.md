# TOM — Agent Instructions

These instructions apply to every AI coding agent working on TOM.

## 1. Core Principle

TOM follows a separation-of-concerns architecture:

1. Directive — what should happen
2. Orchestration — deciding what should happen
3. Execution — deterministic code that performs the operation

LLMs should make decisions.
Deterministic code should perform repeatable operations.

Never move deterministic business logic into an LLM prompt when
the behavior can be implemented reliably in code.

---

## 2. Before Starting Work

Always:

1. Read `docs/STATE.md`
2. Read `docs/HANDOFF.md`
3. Read `docs/TASKS.md`
4. Read relevant sections of `docs/ARCHITECTURE.md`
5. Inspect only files relevant to the current task

Do NOT scan the entire repository unless there is a clear reason.

---

## 3. Task Boundaries

Before modifying code:

- Identify the exact task
- Identify affected components
- Identify dependencies
- Check existing implementations
- Check existing tests

Do not redesign unrelated systems while implementing a task.

---

## 4. Deterministic Execution

Prefer:

LLM
→ decision
→ validated tool call
→ deterministic execution
→ structured result

Avoid asking the LLM to perform work that can be handled reliably
by Rust or Python code.

---

## 5. Existing Tools First

Before creating a new tool:

1. Search the existing tool implementations.
2. Reuse an existing tool if possible.
3. Extend an existing tool if appropriate.
4. Create a new tool only when necessary.

Do not duplicate functionality.

---

## 6. Error Handling / Self-Annealing

When implementation fails:

1. Read the complete error.
2. Identify the root cause.
3. Make the smallest appropriate fix.
4. Run the relevant tests.
5. Verify the fix.
6. Record important learnings.

Never hide or ignore failures.

Do not repeatedly retry operations that may consume
paid resources without user approval.

---

## 7. Architecture Changes

Do not silently change architectural decisions.

If a task reveals that an architectural change may be needed:

1. Document the observation.
2. Explain the proposed change.
3. Evaluate alternatives.
4. Test or benchmark when possible.
5. Update `docs/DECISIONS.md` after the decision is accepted.

---

## 8. Testing

Every implementation should be verified.

Prefer:

- unit tests
- integration tests
- type checking
- linting
- formatting
- targeted runtime tests

Do not claim a task is complete without verification.

---

## 9. Project State

After meaningful work, update:

`docs/STATE.md`
`docs/PROGRESS.md`
`docs/HANDOFF.md`

Keep these files concise.

They exist so another agent can understand the project
without reading the entire repository.

---

## 10. Agent Handoff

When handing work to another agent, record:

- task
- current status
- files changed
- tests performed
- known problems
- decisions made
- next recommended action

The repository is the shared communication layer between agents.

---

## 11. Minimal Context Principle

Never load more context than necessary.

Prefer:

STATE
→ HANDOFF
→ relevant architecture
→ relevant task
→ relevant source files
→ tests

Avoid:

entire repository
→ entire history
→ unrelated documentation

---

## 12. Security

Never:

- execute arbitrary destructive commands
- expose secrets
- modify credentials
- disable security controls to make a test pass
- introduce unnecessary network access
- bypass TOM's permission system

When an operation has meaningful external consequences,
request confirmation when required by the project security policy.

---

## 13. Completion

A task is complete only when:

- implementation is finished
- tests pass
- formatting/linting passes where applicable
- documentation is updated where necessary
- project state is updated
- handoff information is recorded