"""Unit tests for memory schemas and validation.

Adheres to:
- PHASE5_IMPLEMENTATIONPLAN.md (Iteration 1)
- skills/coding/validation
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from tom.schemas.memory import (
    IMPORTANCE_WEIGHTS,
    MemoryImportance,
    MemoryQuery,
    MemoryRecord,
    MemorySearchResult,
    MemoryType,
)


def test_memory_type_enum_values():
    """Verify all required MemoryType enum members exist."""
    assert MemoryType.EPISODIC == "EPISODIC"
    assert MemoryType.SEMANTIC == "SEMANTIC"
    assert MemoryType.PREFERENCE == "PREFERENCE"
    assert MemoryType.TASK == "TASK"
    assert MemoryType.EVENT == "EVENT"


def test_memory_importance_enum_and_weights():
    """Verify all MemoryImportance levels and monotonic weights."""
    assert MemoryImportance.LOW == "LOW"
    assert MemoryImportance.USEFUL == "USEFUL"
    assert MemoryImportance.IMPORTANT == "IMPORTANT"
    assert MemoryImportance.CRITICAL == "CRITICAL"

    assert (
        IMPORTANCE_WEIGHTS[MemoryImportance.LOW]
        < IMPORTANCE_WEIGHTS[MemoryImportance.USEFUL]
        < IMPORTANCE_WEIGHTS[MemoryImportance.IMPORTANT]
        < IMPORTANCE_WEIGHTS[MemoryImportance.CRITICAL]
    )


def test_memory_record_default_instantiation():
    """Verify standard default instantiation of a MemoryRecord."""
    rec = MemoryRecord(
        type=MemoryType.PREFERENCE,
        content="User prefers dark mode.",
    )
    assert rec.memory_id is not None
    assert len(rec.memory_id) > 0
    assert rec.type == MemoryType.PREFERENCE
    assert rec.content == "User prefers dark mode."
    assert rec.importance == MemoryImportance.USEFUL
    assert rec.source is None
    assert rec.user_confirmed is False
    assert rec.expires_at is None
    assert rec.metadata == {}
    assert rec.created_at.tzinfo is not None
    assert rec.updated_at.tzinfo is not None


def test_memory_record_all_fields_explicit():
    """Verify MemoryRecord with all explicit attributes."""
    created = datetime.now(UTC) - timedelta(hours=2)
    updated = datetime.now(UTC) - timedelta(hours=1)
    expires = datetime.now(UTC) + timedelta(days=30)
    meta = {"category": "theme", "version": 1}

    rec = MemoryRecord(
        memory_id="custom-uuid-1234",
        type=MemoryType.TASK,
        content="Deploy v2 to production.",
        created_at=created,
        updated_at=updated,
        importance=MemoryImportance.CRITICAL,
        source="user_explicit",
        user_confirmed=True,
        expires_at=expires,
        metadata=meta,
    )

    assert rec.memory_id == "custom-uuid-1234"
    assert rec.type == MemoryType.TASK
    assert rec.content == "Deploy v2 to production."
    assert rec.importance == MemoryImportance.CRITICAL
    assert rec.source == "user_explicit"
    assert rec.user_confirmed is True
    assert rec.expires_at == expires
    assert rec.metadata == meta


def test_memory_record_naive_datetime_coerced_to_utc():
    """Verify naive datetimes are automatically enriched with UTC timezone."""
    naive_dt = datetime(2026, 9, 21, 12, 0, 0)
    rec = MemoryRecord(
        type=MemoryType.EVENT,
        content="System rebooted.",
        created_at=naive_dt,
        updated_at=naive_dt,
        expires_at=naive_dt,
    )
    assert rec.created_at.tzinfo == UTC
    assert rec.updated_at.tzinfo == UTC
    assert rec.expires_at.tzinfo == UTC


def test_memory_record_extra_fields_forbidden():
    """Verify extra unexpected fields raise ValidationError."""
    with pytest.raises(ValidationError):
        MemoryRecord(
            type=MemoryType.SEMANTIC,
            content="Some fact.",
            unexpected_field="disallowed",
        )


def test_memory_record_empty_content_rejected():
    """Verify empty or zero-length content string is rejected."""
    with pytest.raises(ValidationError):
        MemoryRecord(type=MemoryType.SEMANTIC, content="")


def test_memory_query_defaults_and_validation():
    """Verify MemoryQuery defaults and extra forbidding."""
    query = MemoryQuery()
    assert query.text == ""
    assert query.types is None
    assert query.importance_min is None
    assert query.limit == 10
    assert query.include_expired is False

    custom_query = MemoryQuery(
        text="python",
        types=[MemoryType.SEMANTIC, MemoryType.PREFERENCE],
        importance_min=MemoryImportance.IMPORTANT,
        limit=5,
        include_expired=True,
    )
    assert custom_query.text == "python"
    assert len(custom_query.types) == 2
    assert custom_query.limit == 5

    # Bounds validation on limit
    with pytest.raises(ValidationError):
        MemoryQuery(limit=0)
    with pytest.raises(ValidationError):
        MemoryQuery(limit=1001)

    # Forbid extra fields
    with pytest.raises(ValidationError):
        MemoryQuery(extra_flag=True)


def test_memory_search_result_validation():
    """Verify MemorySearchResult validation and bounds."""
    rec = MemoryRecord(type=MemoryType.EPISODIC, content="Logged in.")
    res = MemorySearchResult(record=rec, score=0.85)
    assert res.record.content == "Logged in."
    assert res.score == 0.85

    # Bounds check on score
    with pytest.raises(ValidationError):
        MemorySearchResult(record=rec, score=-0.1)
    with pytest.raises(ValidationError):
        MemorySearchResult(record=rec, score=1.1)

    # Extra fields forbidden
    with pytest.raises(ValidationError):
        MemorySearchResult(record=rec, score=0.5, rank=1)
