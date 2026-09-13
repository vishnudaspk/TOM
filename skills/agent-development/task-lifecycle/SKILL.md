---
name: agent-development-task-lifecycle
description: >
  How TOM's task lifecycle works and how to implement components that respect it.
  Use when implementing the orchestrator, task manager, planner, or any subsystem
  that needs to update or observe task state. Also use when debugging a task that
  appears to be stuck, silently completing, or transitioning to the wrong state.
---

# Agent Development — Task Lifecycle

Every TOM user request is tracked as a task with an explicit lifecycle.
This skill covers the states, transitions, and rules that govern task management.

---

## Task States (from plan.md)

```
CREATED
    ↓
ROUTING
    ↓
PLANNING
    ↓
EXECUTING
    ↓
WAITING / WAITING_FOR_CONFIRMATION
    ↓
COMPLETING
    ↓
COMPLETED
```

**Error path:** Any active state → `FAILED`

**Cancellation path:** Any active state → `CANCELLATION_REQUESTED` → `CANCELLED`

**Confirmation path:**
```
EXECUTING → WAITING_FOR_CONFIRMATION
    → User: yes → EXECUTING
    → User: no / timeout → CANCELLED or FAILED
```

---

## Core Rules

1. **Every active task must have an explicit state.** Never assume a task is done.
2. **State transitions must be observable and intentional** — emit an event for each.
3. **Only the Task Manager / Orchestrator updates global task state.** Subsystems report
   events; they do not override task state directly.
4. **A cancelled task must not be recorded as completed.**

---

## Task Record

```python
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class TaskRecord(BaseModel):
    task_id: str
    parent_task_id: Optional[str]  # for sub-tasks spawned by agents
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    current_step: Optional[str]
    request: str                    # original user request
    route: Optional[str]            # routing decision
    progress: float = 0.0           # 0.0–1.0
    cancellation_state: Optional[str]
    waiting_reason: Optional[str]
    error: Optional[str]
    metadata: dict = {}
```

---

## State Transitions with Events

Each transition emits a typed event:

```python
class TaskEvent:
    task_id: str
    event_type: str   # e.g. "TaskCreated", "TaskRoutingStarted"
    timestamp: datetime
    data: dict

# Example transitions
TaskCreated
TaskRoutingStarted → TaskRoutingCompleted
TaskPlanningStarted → TaskPlanningCompleted
TaskExecutionStarted
TaskWaiting
ConfirmationRequested → ConfirmationGranted | ConfirmationDenied
TaskCompleting → TaskCompleted
TaskFailed
CancellationRequested → TaskCancelled
```

---

## Cancellation Rules

When cancellation is requested:

1. Set task status to `CANCELLATION_REQUESTED`.
2. Signal the active tool/agent/LLM call to stop.
3. Do not start any new tool calls.
4. Do not continue planned steps.
5. Release temporary resources.
6. Set final status to `CANCELLED`.
7. Emit `TaskCancelled` event.

**Cancellation does not undo completed operations.** If a file was already moved,
the task manager notes the partial completion in metadata. The subsystem determines
if rollback is possible.

---

## Recovery States

When a tool fails during `EXECUTING`:

```
EXECUTING → tool fails
    → Recovery handler
        → Retry (→ EXECUTING)
        → Alternative tool (→ EXECUTING)
        → Re-plan (→ PLANNING)
        → Ask user (→ WAITING)
        → Abort (→ FAILED)
```

The orchestrator decides recovery strategy. Subsystems report `ToolFailed`, not the
final task outcome.

---

## Sub-tasks

When the planner spawns sub-tasks (e.g. multi-step agent workflows):

```python
class TaskRecord:
    parent_task_id: Optional[str]  # links sub-tasks to the parent
```

The parent task remains `EXECUTING` until all sub-tasks complete or one fails.

---

## Background Tasks

Background tasks (file indexing, embedding generation) also have explicit lifecycle.
They have lower priority than interactive tasks.

When an interactive task starts:
- Background tasks move to `WAITING` or `PAUSED`.
- Resources are reserved for the interactive task.
- Background tasks resume when the interactive task completes.

---

## Implementing a Subsystem That Reports to the Task Manager

```python
# Correct: subsystem reports events, does not set task state
async def execute_tool(task_id: str, tool: ToolDefinition, ...):
    try:
        result = await tool.handler(...)
        await event_bus.publish(ToolCompleted(task_id=task_id, result=result))
    except Exception as e:
        await event_bus.publish(ToolFailed(task_id=task_id, error=str(e)))

# Task Manager listens to ToolCompleted/ToolFailed and updates task state
```

---

## Related Skills

- `agent-development/tool-design` — How tools behave during EXECUTING
- `python/pydantic-agents` — Agents operate within the EXECUTING state
- `security/permission-model` — WAITING_FOR_CONFIRMATION state
- `system-design/resource-management` — Background task priority and pausing
