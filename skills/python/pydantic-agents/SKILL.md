---
name: python-pydantic-agents
description: >
  How to build and structure Pydantic AI agents in TOM's Python brain layer.
  Use when creating or modifying any Pydantic AI agent (assistant agent, task agent,
  router agent), defining typed dependencies, structuring agent tools, or handling
  agent result validation. Also use when an agent produces unstructured or invalid output.
---

# Python — Pydantic AI Agents in TOM

TOM's Python layer uses Pydantic AI to implement typed, structured agents.
This skill covers how to write agents correctly within TOM's architecture.

---

## Core Architecture Rule

`core/` owns TOM's execution loop. Pydantic AI is used inside `agents/` — it does not
define TOM's overall structure.

```
TOM Orchestrator (core/orchestrator.py)
    │
    ▼
Pydantic AI Agent (agents/assistant.py or agents/task_agent.py)
    │
    ▼
Tool Manager (tools/executor.py)  ← NOT direct DB/system calls
```

Agents must never bypass the Tool Manager, Memory Manager, or Permission Layer.

---

## Agent Structure

```python
from pydantic_ai import Agent
from pydantic_ai.models import KnownModelName
from pydantic import BaseModel
from typing import Any

class TOMDependencies(BaseModel):
    """Typed dependencies injected at runtime — no mutable globals."""
    tool_manager: Any      # tools.executor.ToolManager
    memory_manager: Any    # memory.manager.MemoryManager
    task_context: Any      # schemas.tasks.TaskContext
    cancel_token: Any      # asyncio.Event or similar

assistant_agent = Agent(
    model="openai:qwen3-8b",  # or configured provider
    deps_type=TOMDependencies,
    result_type=AgentResult,
    system_prompt=SYSTEM_PROMPT,
)
```

---

## Typed Outputs

Always define a `result_type` Pydantic model. Never return raw strings.

```python
class AgentResult(BaseModel):
    response_text: str
    tools_called: list[str] = []
    memory_stored: bool = False
    requires_confirmation: bool = False
```

This ensures that LLM output is validated before reaching TOM's orchestrator.

---

## Tools

Tools are registered functions that the agent can call. Every tool must:

1. Accept a `RunContext[TOMDependencies]` first argument.
2. Use typed parameters and return types.
3. Go through `deps.tool_manager` for actual execution.
4. Not directly access databases, filesystem, or system APIs.

```python
@assistant_agent.tool
async def get_system_info(
    ctx: RunContext[TOMDependencies],
    metric: str,
) -> dict:
    """Get a system metric from the Rust engine via TOM's tool manager."""
    return await ctx.deps.tool_manager.execute("system", metric, params={})
```

---

## Memory Tools

Memory access goes through the Memory Manager. Never let an agent construct raw SQL.

```python
@assistant_agent.tool
async def search_memory(
    ctx: RunContext[TOMDependencies],
    query: str,
    limit: int = 5,
) -> list[MemoryRecord]:
    """Search TOM's memory for relevant past information."""
    return await ctx.deps.memory_manager.search(query, limit=limit)
```

---

## Cancellation

Check the cancel token at the start of long operations. Pydantic AI does not automatically
propagate Python asyncio cancellation through LLM streaming — handle it explicitly.

```python
@assistant_agent.tool
async def long_running_tool(ctx: RunContext[TOMDependencies]) -> str:
    if ctx.deps.cancel_token.is_set():
        raise RuntimeError("task cancelled before tool execution")
    # proceed
```

---

## Model Provider Abstraction

Do not hardcode the Pydantic AI model string in agent definitions.
Load it from TOM's model configuration.

```python
def build_assistant_agent(config: ModelConfig) -> Agent:
    return Agent(
        model=config.reasoning_model,
        deps_type=TOMDependencies,
        result_type=AgentResult,
        system_prompt=SYSTEM_PROMPT,
    )
```

This allows switching from Qwen3-8B to another model by changing `models.yaml`.

---

## Structured Output Reliability

Smaller models (Qwen3-1.7B router) may be less reliable with complex JSON.
For the router, prefer the simplest possible output schema.

```python
class RouterOutput(BaseModel):
    route: Literal["system", "reasoning", "vision", "memory", "files", "web", "chat"]
    confidence: float = 1.0
```

Keep router schemas narrow. Complex schemas belong to the reasoning model.

---

## Error Handling

Wrap agent runs in try/except. Pydantic AI raises `ModelRetry` for validation failures.

```python
from pydantic_ai import ModelRetry

try:
    result = await agent.run(user_prompt, deps=deps)
except ModelRetry:
    # Log and escalate or fall back
    raise
except Exception as e:
    logger.error("agent run failed", error=str(e))
    raise
```

---

## Related Skills

- `python/tool-system` — Tool registry and executor the agents call into
- `python/memory-system` — Memory Manager API
- `python/ipc-client` — How Python reaches the Rust engine
- `system-design/python-rust-boundary` — What belongs in Python vs Rust
