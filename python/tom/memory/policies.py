"""TOM Memory Policy Subsystem ? Deterministic Security & Expiration Policies.

Adheres to:
- PHASE5_IMPLEMENTATIONPLAN.md (Iteration 3)
- Decision 037: MemoryManager as Single Entry Point & SQLite Source of Truth
- Invariant: Secret detected -> REJECT -> Nothing reaches SQLite -> Nothing reaches Qdrant.
- LLM-independent deterministic regex and Shannon entropy validation.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tom.schemas.memory import MemoryImportance, MemoryRecord


class PolicyDecision(StrEnum):
    """Deterministic outcome of a memory policy evaluation."""

    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    REQUIRE_CONFIRMATION = "REQUIRE_CONFIRMATION"


class PolicyEvaluationResult(BaseModel):
    """Structured evaluation report returned by MemoryPolicy."""

    model_config = ConfigDict(extra="forbid")

    decision: PolicyDecision = Field(description="Deterministic policy verdict")
    reason: str = Field(default="", description="Explanation of why the decision was made")
    matched_secret: bool = Field(
        default=False, description="Whether a secret or credential was detected"
    )


class MemoryPolicyError(Exception):
    """Base exception for all memory policy violations."""


class SecretDetectedError(MemoryPolicyError):
    """Raised when memory content contains sensitive credentials, keys, or secrets."""


class MemoryPolicyViolationError(MemoryPolicyError):
    """Raised when memory record violates importance, expiration, or retention constraints."""


# Pre-compiled high-confidence secret patterns
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    # Private keys & PEM blocks
    re.compile(r"-----BEGIN\s+(?:[A-Z0-9_-]+\s+)?PRIVATE\s+KEY-----", re.IGNORECASE),
    re.compile(r"-----BEGIN\s+RSA\s+PRIVATE\s+KEY-----", re.IGNORECASE),
    re.compile(r"-----BEGIN\s+EC\s+PRIVATE\s+KEY-----", re.IGNORECASE),
    re.compile(r"-----BEGIN\s+DSA\s+PRIVATE\s+KEY-----", re.IGNORECASE),
    re.compile(r"-----BEGIN\s+OPENSSH\s+PRIVATE\s+KEY-----", re.IGNORECASE),
    re.compile(r"-----BEGIN\s+PGP\s+PRIVATE\s+KEY\s+BLOCK-----", re.IGNORECASE),
    re.compile(r"-----BEGIN\s+ENCRYPTED\s+PRIVATE\s+KEY-----", re.IGNORECASE),
    re.compile(r"-----BEGIN\s+CERTIFICATE-----", re.IGNORECASE),
    # Known API Key prefixes
    re.compile(r"\bsk-(?:proj-|ant-|svcacct-)?[a-zA-Z0-9_-]{20,}\b"),
    re.compile(r"\b(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
    re.compile(r"\bxox[baprs]-[0-9]{10,}-[0-9a-zA-Z]{10,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
    re.compile(r"\b(?:sk|rk)_(?:live|test)_[0-9a-zA-Z]{24,}\b"),
    re.compile(r"\bhf_[a-zA-Z0-9]{34,}\b"),
    # Generic credential assignments (e.g. api_key = "...", password = "...")
    re.compile(
        r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password|passwd|pwd|db_password)\b\s*[:=]\s*['\"]?[^\s'\"]{6,}['\"]?"
    ),
    # Bearer and JWT tokens
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.\~+/]{20,}=*\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
]

# Candidate token regex for Shannon entropy analysis
_HIGH_ENTROPY_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9+/=_\-\$#@!~^%&*]{24,}")


def calculate_shannon_entropy(data: str) -> float:
    """Calculate the Shannon entropy of a string."""
    if not data:
        return 0.0
    length = len(data)
    counts = Counter(data)
    entropy = 0.0
    for count in counts.values():
        p = count / length
        entropy -= p * math.log2(p)
    return entropy


class MemoryPolicy:
    """Deterministic, LLM-independent policy engine for memory safety, validation, and retention."""

    def __init__(
        self,
        entropy_threshold: float = 4.2,
        min_token_len_for_entropy: int = 24,
    ) -> None:
        self.entropy_threshold = entropy_threshold
        self.min_token_len_for_entropy = min_token_len_for_entropy

    @classmethod
    def default_scan_for_secrets(cls, text: str) -> bool:
        """Convenience method for quick secret scanning using default policy."""
        return cls().scan_for_secrets(text)

    def scan_for_secrets(self, text: str) -> bool:
        """Check if string contains credential patterns or high-entropy suspicious secrets."""
        if not text:
            return False

        # 1. Regex pattern matching
        for pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                return True

        # 2. Shannon entropy check on candidate tokens
        tokens = _HIGH_ENTROPY_TOKEN_PATTERN.findall(text)
        for token in tokens:
            if len(token) < self.min_token_len_for_entropy:
                continue

            has_digit = any(c.isdigit() for c in token)
            has_upper = any(c.isupper() for c in token)
            has_lower = any(c.islower() for c in token)
            has_special = any(not c.isalnum() for c in token)
            char_classes = sum([has_digit, has_upper, has_lower, has_special])

            # Must have at least 2 distinct character classes (e.g. letters + digits)
            if char_classes >= 2:
                ent = calculate_shannon_entropy(token)
                if ent >= self.entropy_threshold:
                    return True

        return False

    def scan_metadata_for_secrets(self, metadata: dict[str, Any]) -> bool:
        """Recursively scan memory metadata for secrets."""
        for v in metadata.values():
            if isinstance(v, str):
                if self.scan_for_secrets(v):
                    return True
            elif isinstance(v, dict):
                if self.scan_metadata_for_secrets(v):
                    return True
            elif isinstance(v, (list, tuple, set)):
                for item in v:
                    if isinstance(item, str) and self.scan_for_secrets(item):
                        return True
                    if isinstance(item, dict) and self.scan_metadata_for_secrets(item):
                        return True
        return False

    def evaluate(
        self,
        record: MemoryRecord,
        now: datetime | None = None,
    ) -> PolicyEvaluationResult:
        """Evaluate a memory record against security, importance, and expiration policies.

        Returns:
            PolicyEvaluationResult with verdict ACCEPT, REJECT, or REQUIRE_CONFIRMATION.
        """
        # 1. Secret scanning on content
        if self.scan_for_secrets(record.content):
            return PolicyEvaluationResult(
                decision=PolicyDecision.REJECT,
                reason="Memory content contains secret credentials or private keys",
                matched_secret=True,
            )

        # 2. Secret scanning on metadata
        if record.metadata and self.scan_metadata_for_secrets(record.metadata):
            return PolicyEvaluationResult(
                decision=PolicyDecision.REJECT,
                reason="Memory metadata contains secret credentials or private keys",
                matched_secret=True,
            )

        # 3. Expiration policy validation
        if record.expires_at is not None:
            # Expiration cannot be before or equal to creation timestamp
            if record.expires_at <= record.created_at:
                return PolicyEvaluationResult(
                    decision=PolicyDecision.REJECT,
                    reason=f"Invalid expiration: expires_at ({record.expires_at.isoformat()}) is before or equal to created_at ({record.created_at.isoformat()})",
                    matched_secret=False,
                )

        # 4. Importance policy
        if record.importance in (MemoryImportance.IMPORTANT, MemoryImportance.CRITICAL):
            if not record.user_confirmed:
                return PolicyEvaluationResult(
                    decision=PolicyDecision.REQUIRE_CONFIRMATION,
                    reason=f"Memory of importance {record.importance.value} requires user confirmation",
                    matched_secret=False,
                )

        return PolicyEvaluationResult(
            decision=PolicyDecision.ACCEPT,
            reason="Memory record satisfies all policy constraints",
            matched_secret=False,
        )


__all__ = [
    "MemoryPolicy",
    "MemoryPolicyError",
    "MemoryPolicyViolationError",
    "PolicyDecision",
    "PolicyEvaluationResult",
    "SecretDetectedError",
    "calculate_shannon_entropy",
]
