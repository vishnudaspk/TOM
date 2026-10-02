"""Task lifecycle schemas for TOM Phase 8.

Adheres to:
- Phase 8 Architecture (Task Lifecycle & Schemas)
- Decision 051: Task Lifecycle State Machine (9 states, decoupled from AgentState)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TaskState(StrEnum):
    """9-state global task lifecycle, decoupled from AgentState (6-state turn lifecycle)."""

    CREATED = "CREATED"
    ROUTING = "ROUTING"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    COMPLETING = "COMPLETING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# Valid transitions — deterministic, not inferred
TASK_VALID_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.CREATED: {TaskState.ROUTING, TaskState.CANCELLED},
    TaskState.ROUTING: {
        TaskState.PLANNING,
        TaskState.EXECUTING,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.PLANNING: {
        TaskState.EXECUTING,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.EXECUTING: {
        TaskState.WAITING_CONFIRMATION,
        TaskState.PLANNING,  # replan
        TaskState.COMPLETING,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.WAITING_CONFIRMATION: {
        TaskState.EXECUTING,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.COMPLETING: {TaskState.COMPLETED, TaskState.FAILED},
    TaskState.COMPLETED: set(),
    TaskState.FAILED: set(),
    TaskState.CANCELLED: set(),
}

# Terminal states — no further transitions allowed
TASK_TERMINAL_STATES: frozenset[TaskState] = frozenset(
    {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}
)


class InvalidTaskTransitionError(Exception):
    """Raised when an invalid task state transition is attempted."""

    def __init__(self, from_state: TaskState, to_state: TaskState) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"Invalid task transition: {from_state.value} → {to_state.value}")


class StepResult(BaseModel):
    """Result of a single plan step execution."""

    step_id: str
    step_index: int
    tool_name: str
    success: bool
    output: Any = None
    error: str | None = None
    execution_time_ms: float = 0.0
    executed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TaskResult(BaseModel):
    """Final result of a completed task."""

    task_id: str
    state: TaskState
    summary: str = ""
    step_results: list[StepResult] = Field(default_factory=list)
    total_steps: int = 0
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None


class Task(BaseModel):
    """Top-level task entity with lifecycle state."""

    task_id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:12]}")
    goal: str = Field(..., min_length=1)
    state: TaskState = TaskState.CREATED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    max_steps: int = Field(default=15, ge=1, le=30)
    timeout_seconds: float = Field(default=300.0, gt=0.0, le=900.0)
    current_step_index: int = 0
    replan_count: int = 0
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
