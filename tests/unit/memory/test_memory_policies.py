"""Unit tests for deterministic memory policy evaluation.

Adheres to:
- PHASE5_IMPLEMENTATIONPLAN.md (Iteration 3)
- Decision 039: Memory Security Policies & Non-Bypassable Memory Tools
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tom.memory.policies import (
    MemoryPolicy,
    PolicyDecision,
    calculate_shannon_entropy,
)
from tom.schemas.memory import MemoryImportance, MemoryRecord, MemoryType


def _record(
    content: str = "User prefers concise technical summaries.",
    *,
    importance: MemoryImportance = MemoryImportance.USEFUL,
    user_confirmed: bool = False,
    expires_at: datetime | None = None,
    created_at: datetime | None = None,
    metadata: dict | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        type=MemoryType.PREFERENCE,
        content=content,
        importance=importance,
        user_confirmed=user_confirmed,
        created_at=created_at or datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=expires_at,
        metadata=metadata or {},
    )


@pytest.mark.parametrize(
    "secret_text",
    [
        "api_key = 'abcd1234efgh5678'",
        "-----BEGIN PRIVATE KEY-----\nredacted\n-----END PRIVATE KEY-----",
        "token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.VeryLongSignaturePart",
        "OpenAI key sk-proj-abcdefghijklmnopqrstuvwxyz1234567890",
        "AWS key AKIAIOSFODNN7EXAMPLE",
    ],
)
def test_policy_rejects_known_secret_patterns(secret_text: str) -> None:
    policy = MemoryPolicy()

    result = policy.evaluate(_record(secret_text))

    assert result.decision == PolicyDecision.REJECT
    assert result.matched_secret is True


def test_policy_rejects_high_entropy_secret_token() -> None:
    token = "aZ8qLm2P9xVbN4rTy6WcK1sDe7FgH3jQ"
    assert calculate_shannon_entropy(token) >= 4.2

    result = MemoryPolicy().evaluate(_record(f"session token {token}"))

    assert result.decision == PolicyDecision.REJECT
    assert result.matched_secret is True


def test_policy_rejects_secrets_nested_in_metadata() -> None:
    record = _record(
        "Preference metadata test",
        metadata={"profile": {"tokens": ["safe", "password=supersecret123"]}},
    )

    result = MemoryPolicy().evaluate(record)

    assert result.decision == PolicyDecision.REJECT
    assert result.matched_secret is True


def test_policy_accepts_benign_memory() -> None:
    record = _record(
        "User prefers Python examples when discussing backend implementation.",
        metadata={"topic": "coding", "source": "conversation"},
    )

    result = MemoryPolicy().evaluate(record)

    assert result.decision == PolicyDecision.ACCEPT
    assert result.matched_secret is False


@pytest.mark.parametrize(
    "importance",
    [MemoryImportance.IMPORTANT, MemoryImportance.CRITICAL],
)
def test_policy_requires_confirmation_for_high_importance(importance: MemoryImportance) -> None:
    result = MemoryPolicy().evaluate(
        _record("User wants this retained.", importance=importance, user_confirmed=False)
    )

    assert result.decision == PolicyDecision.REQUIRE_CONFIRMATION


def test_policy_accepts_confirmed_high_importance_memory() -> None:
    result = MemoryPolicy().evaluate(
        _record(
            "User confirmed this permanent preference.",
            importance=MemoryImportance.CRITICAL,
            user_confirmed=True,
        )
    )

    assert result.decision == PolicyDecision.ACCEPT


def test_policy_rejects_expiration_not_after_creation() -> None:
    created = datetime(2026, 1, 1, tzinfo=UTC)
    result = MemoryPolicy().evaluate(
        _record(
            "Task should expire in the future.",
            created_at=created,
            expires_at=created - timedelta(seconds=1),
        )
    )

    assert result.decision == PolicyDecision.REJECT
    assert result.matched_secret is False
