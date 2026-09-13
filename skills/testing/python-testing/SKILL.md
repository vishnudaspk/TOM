---
name: testing-python-testing
description: >
  How to write Python tests for TOM's brain layer using pytest and pytest-asyncio.
  Use when writing unit tests or integration tests for Python modules (agents, tools,
  memory, security, IPC client, orchestrator). Also use when a Python test is failing
  for async/event-loop reasons or when setting up test fixtures for TOM components.
---

# Python Testing — TOM Brain Layer

TOM's Python tests use `pytest` and `pytest-asyncio`. This skill covers the patterns
used across the Python codebase.

---

## Setup

Dependencies:
```toml
[project.optional-dependencies]
test = [
    "pytest",
    "pytest-asyncio",
    "pytest-cov",
]
```

`pyproject.toml` configuration:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

---

## Async Tests

```python
import pytest

@pytest.mark.asyncio  # not needed if asyncio_mode = "auto"
async def test_memory_search_returns_relevant_results():
    manager = MemoryManager(sqlite=FakeSQLite(), qdrant=FakeQdrant(), ...)
    await manager.store("User prefers Kokoro for TTS", memory_type="preference", ...)
    results = await manager.search("TTS engine preference")
    assert len(results) > 0
    assert "Kokoro" in results[0].content
```

---

## Fixtures

Use `pytest.fixture` for shared setup. Scope `session` for expensive initialisation.

```python
# tests/conftest.py
import pytest

@pytest.fixture
def fake_ipc():
    return FakeIPCClient()

@pytest.fixture
def tool_executor(fake_ipc):
    return ToolExecutor(ipc=fake_ipc, permission_layer=FakePermissions())
```

---

## Fakes Over Mocks

Prefer lightweight hand-written fakes over `unittest.mock.Mock` for complex dependencies.
Fakes are easier to understand and maintain.

```python
class FakeIPCClient:
    """Fake IPC client for testing tool implementations."""
    def __init__(self, responses: dict = {}):
        self.responses = responses
        self.calls: list[str] = []

    async def call(self, method: str, params: dict = {}, timeout_ms: int = 500) -> dict:
        self.calls.append(method)
        if method in self.responses:
            return self.responses[method]
        raise IPCError({"code": "NOT_FOUND", "message": f"no fake for {method}"})
```

---

## Testing Permission Boundaries

```python
async def test_block_level_tool_raises_permission_blocked(tool_executor):
    tool = ToolDefinition(
        name="dangerous.arbitrary_shell",
        permission_level="BLOCK",
        ...
    )
    with pytest.raises(PermissionBlocked):
        await tool_executor.execute(tool, params={})
```

```python
async def test_ask_level_tool_pauses_for_confirmation(tool_executor, fake_confirmation):
    fake_confirmation.set_response(True)  # user says yes
    result = await tool_executor.execute(file_delete_tool, params={"path": "/tmp/test"})
    assert fake_confirmation.was_called
    assert result.success
```

---

## Testing Memory Manager

```python
async def test_memory_manager_rejects_credentials():
    manager = build_test_memory_manager()
    with pytest.raises(MemoryPolicyRejection):
        await manager.store(
            content="api_key=sk-1234abcd",
            memory_type="long_term",
            importance="useful",
            source="conversation",
        )
```

---

## Testing Agent Tools

Test tools with the fake IPC client, not by running the full agent:

```python
async def test_get_gpu_temperature_returns_celsius(fake_ipc):
    fake_ipc.responses["system.gpu_temperature"] = {"temperature_c": 72.5}
    result = await get_gpu_temperature(params=GpuTempInput(), ctx=build_ctx(fake_ipc))
    assert result.temperature_c == 72.5
```

---

## Test File Layout

Mirror the source structure:

```
python/tom/memory/manager.py        → tests/unit/memory/test_manager.py
python/tom/tools/system.py          → tests/unit/tools/test_system.py
python/tom/security/permissions.py  → tests/unit/security/test_permissions.py
```

---

## Coverage

Run with coverage to identify untested paths:

```bash
pytest --cov=tom --cov-report=term-missing tests/unit/
```

Aim for high coverage on security-critical modules (`security/`, `tools/executor.py`,
`memory/policies.py`).

---

## What to Test in Every Module

1. Happy path (expected input → correct output).
2. Error paths (wrong input → correct exception).
3. Edge cases (empty input, None, boundary values).
4. Security invariants (secrets not leaked, permissions enforced).

---

## Related Skills

- `testing/strategy` — Overall test layer definitions
- `rust/testing` — Rust-side test patterns
- `debugging/test-failures` — When tests fail unexpectedly
