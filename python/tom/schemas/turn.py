"""Pydantic schemas for TOM Phase 9 conversational turn management.

Adheres to:
- Phase 9 Implementation Plan §9 (Iteration 9.4)
- Deterministic, structured context only — no coreference/NLP engine here.
  The model remains responsible for natural reasoning over referents such as
  "that" or "the previous one" (Phase 9 Architectural Principle 4).
- skills/coding/validation: Pydantic v2, strict bounds, timezone-aware datetimes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tom.schemas.task import TASK_TERMINAL_STATES, TaskState


class ResponseAction(StrEnum):
    """Deterministic response decision for one conversational turn."""

    SPEAK_DIRECT = "speak_direct"
    FAST_ACK_THEN_WORK = "fast_ack_then_work"
    SILENT_ACT = "silent_act"
    CLARIFY = "clarify"
    REPORT_PROGRESS = "report_progress"


class TurnStatus(StrEnum):
    """Lifecycle of a single conversational turn."""

    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    INTERRUPTED = "INTERRUPTED"


class EntityKind(StrEnum):
    """Coarse classification of a conversational entity referent."""

    FILE = "file"
    DIRECTORY = "directory"
    MEMORY = "memory"
    SCREEN = "screen"
    TASK = "task"
    TOOL = "tool"
    QUERY = "query"
    APP = "app"
    GENERIC = "generic"


class EntityReferent(BaseModel):
    """A named, structured handle on something mentioned in an earlier turn.

    ``name`` is the stable slot key (``last_file``, ``last_query``, ...).
    ``label`` is the natural-language description handed to the model so that
    "that file" / "the previous one" can be resolved by ordinary reasoning
    instead of a rule-based parser.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64, description="Stable referent slot key")
    kind: EntityKind = Field(default=EntityKind.GENERIC, description="Entity classification")
    label: str = Field(default="", max_length=200, description="Human-readable description")
    value: str = Field(default="", max_length=2000, description="Concrete value (path, query, ...)")
    turn_id: str = Field(default="", max_length=64, description="Turn that produced this referent")
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timezone-aware last update timestamp (UTC)",
    )

    @field_validator("updated_at")
    @classmethod
    def ensure_utc(cls, v: datetime) -> datetime:
        return v if v.tzinfo is not None else v.replace(tzinfo=UTC)

    def describe(self) -> str:
        """Render a one-line natural-language description for prompt context."""
        text = self.label.strip() or self.name.replace("_", " ")
        return f"{text}: {self.value}" if self.value else text


class BackgroundTaskSnapshot(BaseModel):
    """Read-only view of a Phase 8 ``TaskManager`` background task."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=64)
    goal: str = Field(min_length=1, max_length=2000)
    state: TaskState = Field(default=TaskState.CREATED)
    current_step_index: int = Field(default=0, ge=0, le=1000)
    max_steps: int = Field(default=1, ge=1, le=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.state in TASK_TERMINAL_STATES


class TurnRecord(BaseModel):
    """One completed (or interrupted) turn, retained in bounded history."""

    model_config = ConfigDict(extra="forbid")

    turn_id: str = Field(min_length=1, max_length=64)
    turn_index: int = Field(ge=0, le=1_000_000)
    user_text: str = Field(default="", max_length=8000)
    assistant_text: str = Field(default="", max_length=8000)
    response_action: ResponseAction | None = None
    status: TurnStatus = Field(default=TurnStatus.COMPLETED)
    tool_names: tuple[str, ...] = Field(default=(), max_length=64)
    referents: dict[str, EntityReferent] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def interrupted(self) -> bool:
        return self.status is TurnStatus.INTERRUPTED


class TurnContext(BaseModel):
    """Structured context for the turn currently being executed.

    Supplied to the existing model (9.2 ``AgentOrchestrator.stream()``) and to
    ``ResponsePolicy.evaluate()``. It carries facts, not interpretations: the
    model performs the natural-language reasoning over the referents.
    """

    model_config = ConfigDict(extra="forbid")

    turn_id: str = Field(
        default_factory=lambda: f"turn_{uuid.uuid4().hex[:12]}",
        max_length=64,
    )
    turn_index: int = Field(default=0, ge=0, le=1_000_000)
    user_text: str = Field(default="", max_length=8000)
    status: TurnStatus = Field(default=TurnStatus.ACTIVE)
    has_anaphoric_reference: bool = Field(
        default=False,
        description="True when the utterance contains a back-reference marker",
    )
    planned_tools: tuple[str, ...] = Field(default=(), max_length=64)
    referents: dict[str, EntityReferent] = Field(
        default_factory=dict,
        description="Entity referents available at the start of this turn",
    )
    history: tuple[TurnRecord, ...] = Field(
        default=(),
        max_length=64,
        description="Bounded snapshot of prior turns",
    )
    background_tasks: tuple[BackgroundTaskSnapshot, ...] = Field(
        default=(),
        max_length=64,
        description="Snapshot of active Phase 8 background tasks",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("started_at")
    @classmethod
    def ensure_utc(cls, v: datetime) -> datetime:
        return v if v.tzinfo is not None else v.replace(tzinfo=UTC)

    @property
    def is_follow_up(self) -> bool:
        """True when the utterance refers back to earlier context."""
        return self.has_anaphoric_reference and bool(self.referents or self.history)

    @property
    def has_running_task(self) -> bool:
        return any(not task.is_terminal for task in self.background_tasks)

    def referent(self, name: str) -> EntityReferent | None:
        return self.referents.get(name)

    def to_model_context(self) -> dict[str, Any]:
        """Serialize the context as plain data for the existing model prompt."""
        return {
            "turn_id": self.turn_id,
            "turn_index": self.turn_index,
            "user_text": self.user_text,
            "status": self.status.value,
            "is_follow_up": self.is_follow_up,
            "planned_tools": list(self.planned_tools),
            "referents": {
                name: ref.model_dump(mode="json") for name, ref in self.referents.items()
            },
            "recent_turns": [record.model_dump(mode="json") for record in self.history],
            "background_tasks": [task.model_dump(mode="json") for task in self.background_tasks],
            "metadata": dict(self.metadata),
        }


class ResponseDecision(BaseModel):
    """Full deterministic outcome of a ``ResponsePolicy`` evaluation."""

    model_config = ConfigDict(extra="forbid")

    action: ResponseAction = Field(description="Selected response action")
    ack_text: str | None = Field(
        default=None,
        max_length=200,
        description="Immediate acknowledgment text (FAST_ACK_THEN_WORK only)",
    )
    reason: str = Field(default="", max_length=200, description="Deterministic rule identifier")
    latency_bearing_tools: tuple[str, ...] = Field(
        default=(),
        max_length=64,
        description="Tools that triggered FAST_ACK_THEN_WORK",
    )


__all__ = [
    "BackgroundTaskSnapshot",
    "EntityKind",
    "EntityReferent",
    "ResponseAction",
    "ResponseDecision",
    "TurnContext",
    "TurnRecord",
    "TurnStatus",
]
