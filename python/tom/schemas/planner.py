"""Pydantic schemas and exceptions for task planning and step decomposition.

Adheres to:
- Phase 8 Architecture (Planning Architecture)
- Decision 051: Task Lifecycle State Machine & Step Decomposition
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PlannerValidationError(Exception):
    """Base exception for all plan generation and validation errors."""


class UnknownToolError(PlannerValidationError):
    """Raised when a plan step references a tool not registered in ToolRegistry."""


class MissingDependencyError(PlannerValidationError):
    """Raised when a step depends on a step_id that does not exist in the plan."""


class SelfDependencyError(PlannerValidationError):
    """Raised when a step depends on itself."""


class DuplicateStepIdError(PlannerValidationError):
    """Raised when multiple steps share the same step_id."""


class CycleDetectedError(PlannerValidationError):
    """Raised when step dependencies form a cycle (not a valid DAG)."""


class StepDependency(BaseModel):
    """Dependency declaration for a step in an execution plan."""

    model_config = ConfigDict(extra="ignore")

    step_id: str = Field(..., min_length=1, description="ID of the prerequisite step")
    required_output_key: str | None = Field(
        default=None,
        description="Optional key from prerequisite step output required by this step",
    )


class PlanStep(BaseModel):
    """Single discrete step within a task execution plan."""

    model_config = ConfigDict(extra="ignore")

    step_id: str = Field(
        default_factory=lambda: f"step_{uuid.uuid4().hex[:8]}",
        description="Unique identifier for the step",
    )
    description: str = Field(..., min_length=1, description="Human-readable summary of the step")
    tool_name: str = Field(..., min_length=1, description="Target tool name to invoke")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments passed to the tool",
    )
    dependencies: list[StepDependency] = Field(
        default_factory=list,
        description="Prerequisite steps that must execute before this step",
    )
    preconditions: list[str] = Field(
        default_factory=list,
        description="Pre-execution environment or state assertions",
    )
    expected_outcome: str = Field(
        ...,
        min_length=1,
        description="Expected post-execution state or verification criterion",
    )
    requires_revalidation: bool = Field(
        default=False,
        description="Whether GUI target must be revalidated before physical input actuation",
    )
    revalidation_target: str | None = Field(
        default=None,
        description="Visual target or text descriptor to revalidate if requires_revalidation is True",
    )
    is_terminal: bool = Field(
        default=False,
        description="Whether this step concludes task execution",
    )


class Plan(BaseModel):
    """Structured plan composed of validated, dependency-ordered steps."""

    model_config = ConfigDict(extra="ignore")

    plan_id: str = Field(
        default_factory=lambda: f"plan_{uuid.uuid4().hex[:12]}",
        description="Unique identifier for the plan",
    )
    goal: str = Field(..., min_length=1, description="Top-level goal this plan achieves")
    steps: list[PlanStep] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Bounded sequence of plan steps (1 to 20)",
    )
    estimated_complexity: str = Field(
        default="medium",
        description="Complexity estimate: low, medium, high",
    )
    rationale: str = Field(
        default="",
        description="Planner reasoning and decomposition rationale",
    )
