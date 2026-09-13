---
name: python-tool-system
description: >
  How to design, register, and execute tools in TOM's Python tool layer. Use when creating
  a new TOM tool (system, computer, files, web, AI, personal), modifying the tool registry,
  implementing the permission/risk model for tools, or adding tool timeout/cancellation
  support. Also use when a tool is executing arbitrary code instead of going through the
  registry, or when a tool lacks proper permission classification.
---

# Python Tool System

TOM's tools are the execution bridge between LLM intent and deterministic action.
Every tool must be explicit, typed, permissioned, and timeout-bounded.

---

## Core Rule

The LLM never executes system commands directly.

```
LLM generates tool call
    │
    ▼
Tool Registry (tools/registry.py)  ← validates tool name exists
    │
    ▼
Permission Layer (security/permissions.py)  ← SAFE / ASK / BLOCK
    │
    ▼
Tool Executor (tools/executor.py)  ← runs with timeout + cancellation
    │
    ▼
Validated result returned to LLM
```

---

## Tool Definition

Every tool exposes:

```python
from pydantic import BaseModel
from typing import Literal

class ToolDefinition(BaseModel):
    name: str                # e.g. "system.gpu_temperature"
    description: str         # shown to the LLM
    input_schema: type       # Pydantic model
    output_schema: type      # Pydantic model
    permission_level: Literal["SAFE", "ASK", "BLOCK"]
    risk_level: Literal["low", "medium", "high"]
    timeout_ms: int          # max execution time
    cancellable: bool        # whether mid-execution cancel is safe
```

---

## Permission Levels

| Level | Meaning | Examples |
|-------|---------|---------|
| `SAFE` | Execute without asking | CPU usage, GPU temp, battery, time |
| `ASK` | Require user confirmation | Delete file, install software, close app |
| `BLOCK` | Never execute via LLM | Arbitrary shell, credential access |

Define the level in the tool definition — do not derive it at runtime from the LLM's words.

---

## Tool Categories (from plan.md)

```
SYSTEM  — CPU, GPU, RAM, battery, processes, disk, network
COMPUTER — open/close app, screenshot, keyboard, mouse, window
FILES   — search, read, create, rename, move, copy, delete, organize
WEB     — search, browse, retrieve, extract
AI      — image generation, image analysis, transcription
PERSONAL — reminders, calendar, notes, personal workflows
```

Most `SYSTEM` tools map to IPC calls to the Rust engine.
`COMPUTER` tools require elevated permissions and often user confirmation.

---

## Tool Implementation Pattern

```python
from pydantic import BaseModel
from tom.security.permissions import require_permission
from tom.ipc.client import IPCClient

class GpuTempInput(BaseModel):
    pass

class GpuTempOutput(BaseModel):
    temperature_c: float

async def get_gpu_temperature(
    params: GpuTempInput,
    ipc: IPCClient,
) -> GpuTempOutput:
    result = await ipc.call("system.gpu_temperature", {})
    return GpuTempOutput(temperature_c=result["temperature_c"])
```

---

## Dangerous Operations Pattern

For `ASK`-level tools, the executor pauses and emits a confirmation request.

```python
# tools/executor.py
if tool.permission_level == "ASK":
    confirmed = await confirmation.request(
        task_id=ctx.task_id,
        description=f"Delete file: {params.path}",
    )
    if not confirmed:
        raise ToolCancelled("user denied permission")
```

Task state moves to `WAITING_FOR_CONFIRMATION` during this pause.

---

## Timeout Enforcement

Every tool execution must honour its declared `timeout_ms`.

```python
import asyncio

async def execute_with_timeout(tool: ToolDefinition, params, ctx):
    try:
        return await asyncio.wait_for(
            tool.handler(params, ctx),
            timeout=tool.timeout_ms / 1000,
        )
    except asyncio.TimeoutError:
        raise ToolTimeout(f"{tool.name} exceeded {tool.timeout_ms}ms timeout")
```

---

## Rust-Backed Tools

Most `SYSTEM` tools delegate to the Rust engine via IPC. Do not reimplement system
monitoring in Python.

```python
async def get_cpu_usage(params, ctx) -> CpuUsageOutput:
    raw = await ctx.ipc.call("system.cpu_usage", {})
    return CpuUsageOutput(**raw)
```

---

## Adding a New Tool

1. Define input/output Pydantic models in `tools/schemas.py`.
2. Classify the permission level and risk.
3. Set a realistic `timeout_ms`.
4. Implement the handler in the appropriate tool module.
5. Register in `tools/registry.py`.
6. Write a unit test for the handler.
7. Write an integration test if the tool calls external resources.

---

## What NOT to Do

- Never call `subprocess.run(llm_generated_command)` — this is the core anti-pattern.
- Never bypass the permission layer for convenience.
- Never add a tool with `timeout_ms = 0` or no timeout.
- Never register a tool that returns arbitrary unvalidated data to the LLM.

---

## Related Skills

- `python/pydantic-agents` — How agents call tools
- `security/permission-model` — Permission system design
- `python/ipc-client` — System tools backed by the Rust engine
