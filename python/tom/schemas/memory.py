"""Pydantic schemas and models for TOM Memory subsystem.

Adheres to:
- PHASE5_IMPLEMENTATIONPLAN.md (Iteration 1)
- Decision 037: MemoryManager as Single Entry Point & SQLite Source of Truth
- skills/coding/validation: Strict bounds, timezone-aware datetimes, Pydantic v2 validation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MemoryType(StrEnum):
    """Categorical classification of memory records."""

    EPISODIC = "EPISODIC"
    SEMANTIC = "SEMANTIC"
    PREFERENCE = "PREFERENCE"
    TASK = "TASK"
    EVENT = "EVENT"


class MemoryImportance(StrEnum):
    """Importance hierarchy for memory retention and retrieval scoring."""

    LOW = "LOW"
    USEFUL = "USEFUL"
    IMPORTANT = "IMPORTANT"
    CRITICAL = "CRITICAL"


IMPORTANCE_WEIGHTS: dict[MemoryImportance, int] = {
    MemoryImportance.LOW: 1,
    MemoryImportance.USEFUL: 2,
    MemoryImportance.IMPORTANT: 3,
    MemoryImportance.CRITICAL: 4,
}


def _ensure_tz_aware(dt: datetime | None) -> datetime | None:
    """Ensure a datetime object is timezone-aware in UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


class MemoryRecord(BaseModel):
    """Authoritative memory record model."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique UUID string identifying the memory record",
    )
    type: MemoryType = Field(description="Categorical type of the memory")
    content: str = Field(min_length=1, description="Text content of the memory")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when the memory was created (UTC)",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when the memory was last updated (UTC)",
    )
    importance: MemoryImportance = Field(
        default=MemoryImportance.USEFUL,
        description="Importance level of the memory",
    )
    source: str | None = Field(
        default=None,
        description="Origin of the memory (e.g., conversation, user_explicit, task_result)",
    )
    user_confirmed: bool = Field(
        default=False,
        description="Whether the memory was explicitly confirmed by the user",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Optional expiration timestamp (UTC)",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary structured metadata associated with the memory",
    )

    @field_validator("created_at", "updated_at", "expires_at", mode="after")
    @classmethod
    def validate_timezones(cls, value: datetime | None) -> datetime | None:
        """Enforce timezone-aware datetimes."""
        return _ensure_tz_aware(value)

    @field_validator("memory_id", mode="after")
    @classmethod
    def validate_memory_id(cls, value: str) -> str:
        """Validate that memory_id is non-empty."""
        if not value or not value.strip():
            raise ValueError("memory_id must not be empty")
        return value.strip()


class MemoryQuery(BaseModel):
    """Search and retrieval query parameters."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        default="",
        description="Search query string for keyword or semantic matching",
    )
    types: list[MemoryType] | None = Field(
        default=None,
        description="Optional list of MemoryTypes to filter by",
    )
    importance_min: MemoryImportance | None = Field(
        default=None,
        description="Minimum importance level threshold",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="Maximum number of records to return",
    )
    include_expired: bool = Field(
        default=False,
        description="Whether to include expired records in search results",
    )


class MemorySearchResult(BaseModel):
    """Scored search result wrapping a MemoryRecord."""

    model_config = ConfigDict(extra="forbid")

    record: MemoryRecord = Field(description="The retrieved memory record")
    score: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Relevance score normalized between 0.0 and 1.0",
    )


__all__ = [
    "IMPORTANCE_WEIGHTS",
    "MemoryImportance",
    "MemoryQuery",
    "MemoryRecord",
    "MemorySearchResult",
    "MemoryType",
]
