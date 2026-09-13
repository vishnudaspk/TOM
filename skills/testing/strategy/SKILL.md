---
name: testing-strategy
description: >
  TOM's overall testing strategy: what to test, at which layer, with which tools.
  Use when starting a new subsystem and planning its tests, when deciding between
  unit vs integration vs agent tests, or when setting up the test infrastructure
  for a new TOM component. Load this skill first, then load the language-specific
  testing skill (rust/testing or testing/python-testing) for implementation details.
---

# Testing Strategy

TOM's test structure mirrors its architectural boundaries.
Tests exist to catch regressions, validate contracts between subsystems, and ensure
security invariants hold.

---

## Test Layers (from plan.md)

```
tests/
├── unit/           — single module, no external dependencies
├── integration/    — multiple subsystems interacting
├── agent/          — LLM-in-the-loop agent behaviour
├── voice/          — voice pipeline tests
├── vision/         — vision pipeline tests
├── security/       — permission and secrets tests
└── benchmarks/     — performance measurement (not pass/fail)
```

---

## Unit Tests

Scope: one module or function. No real databases, network calls, or IPC.

Examples:
- `tools/registry.py` — tool registration and lookup
- `memory/policies.py` — memory policy decisions for given inputs
- `security/permissions.py` — permission level classification
- `ipc/protocol.py` — request/response serialisation
- Rust: individual sensor handlers, protocol parsing, event dispatch

Use mocks/fakes for all external dependencies (IPC client, database, LLM).

---

## Integration Tests

Scope: two or more components interacting.

Examples:
- Python ↔ Rust IPC round-trip (real socket, fake handler)
- Memory Manager: store → embed → search retrieval
- Tool executor: dispatch → permission check → tool handler → result
- Model provider: provider abstraction → mock model → structured output

Integration tests may use in-process fakes (not mocks) for infrastructure components.

---

## Agent Tests

Scope: full agent runs with LLM in the loop.

These are expensive — run selectively, not on every commit.

Examples:
- Router: correct intent classification for 20 representative inputs
- Planner: correct multi-step plan for a complex task
- Tool use: correct tool selection and argument generation
- Memory: correct memory retrieval and insertion

Record agent test results with timestamps in `data/benchmarks/`.

---

## Security Tests

Every permission boundary must be tested:

- A `BLOCK`-level tool call should never execute.
- An `ASK`-level tool should pause and await confirmation.
- Secret values must not appear in any log output.
- An LLM-generated tool call with invalid parameters must be rejected by the validator.

```python
def test_block_level_tool_never_executes():
    tool = registry.get("dangerous.arbitrary_shell")
    result = await executor.execute(tool, params={}, ctx=test_ctx)
    # Should raise PermissionBlocked, never execute
```

---

## Benchmark Tests

Benchmarks are not pass/fail — they record performance metrics for comparison.

Use `tests/benchmarks/` for:
- LLM TTFT and throughput at different quantisations
- STT latency on CPU vs GPU
- TTS time to first audio
- Memory search latency
- IPC round-trip latency
- End-to-end voice pipeline latency

Store results in `data/benchmarks/` with hardware specs and timestamps.

---

## What Every Subsystem Needs

Before a subsystem is considered complete:

- [ ] Unit tests for all public functions
- [ ] Error path coverage (at least one test per error type)
- [ ] Integration test with adjacent subsystem
- [ ] Security test if the subsystem handles permissions, secrets, or user data
- [ ] Benchmark if it is latency-sensitive

---

## Test Naming

Use descriptive names that state the condition and expected outcome:

```python
# Good
def test_memory_manager_rejects_credential_like_content():

# Bad
def test_memory_1():
```

---

## No Tests → Not Complete

A task is not complete until its tests pass.
Never mark an implementation complete without running its tests.

---

## Related Skills

- `rust/testing` — Rust-specific test patterns
- `testing/python-testing` — Python-specific test patterns
- `debugging/test-failures` — When tests fail unexpectedly
