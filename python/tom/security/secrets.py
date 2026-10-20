"""TOM Security Subsystem — Automated Secret Detection and Scrubbing.

This module provides the SecretScrubber for high-confidence credential detection
and redaction across tool inputs, outputs, logs, and confirmation displays.

Detection Strategy:
1. Catalog Patterns: Compiled regexes for known credential formats (OpenAI, Anthropic,
   GitHub, AWS Access Key IDs, PEM private keys, JWT/bearer tokens).
2. Shannon Entropy Heuristic: Flags high-entropy strings (>= 24 chars, H >= 4.5
   bits/char, >= 3 character classes) as potential secrets, with conservative
   whitelists for benign high-entropy formats (UUIDs, SHA-256, ISO timestamps,
   data URLs, base64 padding patterns).

Known Limitations (False Negatives):
- AWS Secret Access Keys (40-char base64-like) cannot be reliably distinguished
  from other 40-char strings without the accompanying Access Key ID context.
- Short secrets (< 24 chars), dictionary-based passwords, and custom token formats
  lacking catalog prefixes or sufficient entropy will not be detected.
- Low-entropy secrets (e.g., "password123") are not caught by entropy scanning.

Fail-Safe Behavior:
- If scanning or entropy calculation raises an unexpected exception, the affected
  token is replaced with `***REDACTION_ERROR***` rather than leaking cleartext.
- Input objects are never mutated; new containers are created.
"""

from __future__ import annotations

import math
import re
from typing import Any

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Compiled Credential Patterns
# ---------------------------------------------------------------------------

# OpenAI API keys: sk-... (legacy) and sk-proj-... (project-scoped)
_OPENAI_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{32,}\b")
_OPENAI_PROJ_KEY_PATTERN = re.compile(r"\bsk-proj-[A-Za-z0-9_.-]{16,}\b")

# Anthropic API keys: sk-ant-...
_ANTHROPIC_KEY_PATTERN = re.compile(r"\bsk-ant-[A-Za-z0-9_-]{32,}\b")

# GitHub Personal Access Tokens: ghp_... (36 chars) and github_pat_... (82+ chars)
_GITHUB_PAT_PATTERN = re.compile(r"\bghp_[A-Za-z0-9]{36}\b")
_GITHUB_PAT_LONG_PATTERN = re.compile(r"\bgithub_pat_[A-Za-z0-9_]{82,}\b")

# AWS Access Key IDs: AKIA... (20 chars) — reliably identifiable prefix
_AWS_ACCESS_KEY_PATTERN = re.compile(r"\bAKIA[0-9A-Z]{16}\b")

# PEM Private Key Blocks: -----BEGIN ... PRIVATE KEY-----
# Matches RSA, EC, OPENSSH, and generic PRIVATE KEY variants
_PEM_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    re.MULTILINE,
)

# JWT Tokens: three base64url segments separated by dots
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")

# Generic Bearer Tokens: "Bearer " followed by token (captured separately)
# Token can include dots (for JWT), hyphens, underscores, alphanumerics
_BEARER_PREFIX_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9_.-]{20,}\b", re.IGNORECASE)

# Optimized catalog patterns: PEM separate (avoids backtracking with [\s\S]*?),
# remaining patterns combined without IGNORECASE (only Bearer needs it).
_CATALOG_PATTERN_CS = re.compile(
    "|".join(
        p.pattern
        for p in [
            _OPENAI_PROJ_KEY_PATTERN,
            _OPENAI_KEY_PATTERN,
            _ANTHROPIC_KEY_PATTERN,
            _GITHUB_PAT_LONG_PATTERN,
            _GITHUB_PAT_PATTERN,
            _AWS_ACCESS_KEY_PATTERN,
            _JWT_PATTERN,
        ]
    ),
    re.MULTILINE,
)
_CATALOG_PATTERN_CI = _BEARER_PREFIX_PATTERN

# Redaction replacement
_REDACTED = "***REDACTED***"
_REDACTION_ERROR = "***REDACTION_ERROR***"


# ---------------------------------------------------------------------------
# Whitelist Patterns (Benign High-Entropy Formats)
# ---------------------------------------------------------------------------

# UUID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx (standard format)
_UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)

# SHA-256 hex: 64 hex chars
_SHA256_PATTERN = re.compile(r"\b[0-9a-fA-F]{64}\b")

# ISO 8601 timestamps: 2024-01-15T10:30:45Z or with milliseconds/offset
_ISO_TIMESTAMP_PATTERN = re.compile(
    r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)

# Data URLs: data:[mediatype][;base64],...
_DATA_URL_PATTERN = re.compile(r"^data:[^,]+,", re.IGNORECASE)

# Base64 with standard padding (often appears in benign contexts)
# Matches strings ending with = or == that are otherwise base64
_BASE64_PADDING_PATTERN = re.compile(r"^[A-Za-z0-9+/]+={1,2}$")

# Git commit SHAs (40 hex chars) - often appear in logs
_GIT_SHA_PATTERN = re.compile(r"\b[0-9a-f]{40}\b")


def _is_whitelisted(token: str) -> bool:
    """Check if a token matches known benign high-entropy formats."""
    length = len(token)
    if length == 36 and token.count("-") == 4 and _UUID_PATTERN.fullmatch(token):
        return True
    if length == 64 and _SHA256_PATTERN.fullmatch(token):
        return True
    if length == 40 and _GIT_SHA_PATTERN.fullmatch(token):
        return True
    if (
        19 <= length <= 35
        and ("T" in token or "t" in token)
        and _ISO_TIMESTAMP_PATTERN.fullmatch(token)
    ):
        return True
    if token.startswith(("data:", "DATA:")) and _DATA_URL_PATTERN.match(token):
        return True
    if token.endswith("=") and _BASE64_PADDING_PATTERN.fullmatch(token):
        return True
    return False


# ---------------------------------------------------------------------------
# Pre-compiled Patterns for Performance
# ---------------------------------------------------------------------------

# Candidate pattern for entropy heuristic: non-delimiter sequences >= 24 chars
# Path separators (/ and \\) split filesystem paths so individual
# directory components do not trigger the entropy heuristic.
_ENTROPY_TOKEN_PATTERN = re.compile(r"[^\s,;:|\(\)\[\]{}/\\]{24,}")

# Quick filter: tokens that are purely alphabetic or purely numeric are never secrets
_ALPHABETIC_ONLY_PATTERN = re.compile(r"^[A-Za-z]+$")
_NUMERIC_ONLY_PATTERN = re.compile(r"^[0-9]+$")

# Potential secret pattern: must have mixed case OR digits+symbols to be a candidate
_POTENTIAL_SECRET_PATTERN = re.compile(
    r"[A-Za-z].*[0-9]|[0-9].*[A-Za-z]|[A-Za-z].*[^A-Za-z0-9]|[^A-Za-z0-9].*[A-Za-z]"
)


# ---------------------------------------------------------------------------
# Shannon Entropy Calculation
# ---------------------------------------------------------------------------


def shannon_entropy(token: str) -> float:
    """Calculate Shannon entropy (bits per character) of a string.

    Args:
        token: Input string to analyze.

    Returns:
        Entropy in bits per character (0.0 to 8.0 for ASCII).
    """
    length = len(token)
    if length == 0:
        return 0.0

    # Count character frequencies using list for ASCII (faster than dict)
    freq = [0] * 256
    for ch in token:
        o = ord(ch)
        if o < 256:
            freq[o] += 1
        else:
            # Non-ASCII fallback to dict
            freq_dict: dict[str, int] = {}
            for c in token:
                freq_dict[c] = freq_dict.get(c, 0) + 1
            sum_c_log_c = 0.0
            for count in freq_dict.values():
                if count > 1:
                    sum_c_log_c += count * math.log2(count)
            return math.log2(length) - (sum_c_log_c / length)

    sum_c_log_c = 0.0
    for count in freq:
        if count > 1:
            sum_c_log_c += count * math.log2(count)

    return math.log2(length) - (sum_c_log_c / length)


def _character_class_diversity(token: str) -> int:
    """Count distinct character classes present in the token.

    Classes: lowercase, uppercase, digits, symbols (non-alphanumeric).
    """
    has_lower = False
    has_upper = False
    has_digit = False
    has_symbol = False

    for ch in token:
        if ch.islower():
            has_lower = True
        elif ch.isupper():
            has_upper = True
        elif ch.isdigit():
            has_digit = True
        else:
            has_symbol = True
        if has_lower and has_upper and has_digit and has_symbol:
            return 4

    return (
        (1 if has_lower else 0)
        + (1 if has_upper else 0)
        + (1 if has_digit else 0)
        + (1 if has_symbol else 0)
    )


# ---------------------------------------------------------------------------
# SecretScrubber
# ---------------------------------------------------------------------------


class SecretScrubber:
    """High-confidence credential detector and redactor.

    Combines catalog pattern matching with a conservative Shannon entropy
    heuristic to redact secrets from strings and nested data structures.

    Attributes:
        entropy_threshold: Minimum bits/char for entropy heuristic (default 4.5).
        min_entropy_length: Minimum string length for entropy scanning (default 24).
        min_char_classes: Minimum character class diversity for entropy scanning (default 3).
    """

    def __init__(
        self,
        *,
        entropy_threshold: float = 4.5,
        min_entropy_length: int = 24,
        min_char_classes: int = 3,
    ) -> None:
        self.entropy_threshold = entropy_threshold
        self.min_entropy_length = min_entropy_length
        self.min_char_classes = min_char_classes

    # -----------------------------------------------------------------------
    # String Scrubbing
    # -----------------------------------------------------------------------

    def scrub_string(self, text: str) -> str:
        """Redact secrets from a single string.

        Applies catalog patterns first, then entropy heuristic on remaining
        unmached segments. Whitelisted formats are preserved.

        Args:
            text: Input string potentially containing secrets.

        Returns:
            String with secrets replaced by `***REDACTED***`. If an unexpected
            error occurs during scanning, the problematic segment is replaced
            with `***REDACTION_ERROR***`.
        """
        if not text or not isinstance(text, str):
            return text

        try:
            return self._scrub_string_impl(text)
        except Exception:
            # Fail-safe: never leak cleartext on scanner failure
            return _REDACTION_ERROR

    def _scrub_string_impl(self, text: str) -> str:
        """Internal implementation without exception handling."""
        intermediate = text
        if "PRIVATE KEY" in intermediate:
            intermediate = _PEM_PRIVATE_KEY_PATTERN.sub(_REDACTED, intermediate)
        intermediate = _CATALOG_PATTERN_CS.sub(_REDACTED, intermediate)
        if "bearer" in intermediate or "Bearer" in intermediate or "BEARER" in intermediate:
            intermediate = _CATALOG_PATTERN_CI.sub(_REDACTED, intermediate)

        if _REDACTED in intermediate:
            segments = intermediate.split(_REDACTED)
            return _REDACTED.join(self._apply_entropy_heuristic(seg) for seg in segments)

        return self._apply_entropy_heuristic(intermediate)

    def _apply_entropy_heuristic(self, text: str) -> str:
        """Apply entropy-based detection to a text segment.

        Scans for candidate tokens that meet length preconditions, strips trailing
        punctuation (so sentence-ending tokens are correctly evaluated against
        whitelists and character diversity), and redacts high-entropy secrets.
        """
        if len(text) < self.min_entropy_length:
            return text

        pattern = (
            _ENTROPY_TOKEN_PATTERN
            if self.min_entropy_length == 24
            else re.compile(rf"[^\s,;:|\(\)\[\]{{}}]{{{self.min_entropy_length},}}")
        )
        if not pattern.search(text):
            return text

        def _replace_candidate(match: re.Match[str]) -> str:
            token = match.group(0)
            cleaned = token.rstrip(".,;:!?\"')")
            trailing = token[len(cleaned) :]
            if len(cleaned) < self.min_entropy_length:
                return token

            if _ALPHABETIC_ONLY_PATTERN.fullmatch(cleaned) or _NUMERIC_ONLY_PATTERN.fullmatch(
                cleaned
            ):
                return token

            if not _POTENTIAL_SECRET_PATTERN.search(cleaned):
                return token

            if _is_whitelisted(cleaned):
                return token

            if _character_class_diversity(cleaned) < self.min_char_classes:
                return token

            try:
                entropy = shannon_entropy(cleaned)
            except Exception:
                return _REDACTION_ERROR + trailing

            if entropy >= self.entropy_threshold:
                return _REDACTED + trailing
            return token

        return pattern.sub(_replace_candidate, text)

    # -----------------------------------------------------------------------
    # Recursive Object Scrubbing
    # -----------------------------------------------------------------------

    def scrub_object(self, obj: Any) -> Any:
        """Recursively scrub secrets from a data structure.

        Handles dicts, lists, tuples, sets, Pydantic models, and primitive types.
        Never mutates the input; creates new containers. Non-string values are
        preserved as-is (except for containers which are recreated).

        Args:
            obj: Any Python object (dict, list, tuple, set, Pydantic model, etc.).

        Returns:
            New object with string values scrubbed. Container types are preserved
            (list stays list, tuple stays tuple, dict stays dict).
            Pydantic models are converted to dicts via `model_dump()` then scrubbed.
        """
        try:
            return self._scrub_object_impl(obj)
        except Exception:
            return _REDACTION_ERROR

    def _scrub_object_impl(self, obj: Any) -> Any:
        """Internal implementation without exception handling."""
        # None and primitives (except str)
        if obj is None or not isinstance(obj, (str, dict, list, tuple, set, BaseModel)):
            return obj

        # Strings
        if isinstance(obj, str):
            return self.scrub_string(obj)

        # Pydantic models -> dump to dict then scrub
        if isinstance(obj, BaseModel):
            try:
                dumped = obj.model_dump()
            except Exception:
                return _REDACTION_ERROR
            return self._scrub_object_impl(dumped)

        # Dictionaries
        if isinstance(obj, dict):
            scrubbed: dict[Any, Any] = {}
            for k, v in obj.items():
                scrubbed_key = self._scrub_object_impl(k)
                scrubbed_val = self._scrub_object_impl(v)
                scrubbed[scrubbed_key] = scrubbed_val
            return scrubbed

        # Lists
        if isinstance(obj, list):
            return [self._scrub_object_impl(item) for item in obj]

        # Tuples (preserve as tuple)
        if isinstance(obj, tuple):
            return tuple(self._scrub_object_impl(item) for item in obj)

        # Sets (preserve as set)
        if isinstance(obj, set):
            return {self._scrub_object_impl(item) for item in obj}

        # Fallback for any other iterable (should not happen with above checks)
        return _REDACTION_ERROR


# ---------------------------------------------------------------------------
# Convenience Functions
# ---------------------------------------------------------------------------


_DEFAULT_SCRUBBER: SecretScrubber | None = None


def get_default_scrubber() -> SecretScrubber:
    """Get or create the default SecretScrubber instance."""
    global _DEFAULT_SCRUBBER
    if _DEFAULT_SCRUBBER is None:
        _DEFAULT_SCRUBBER = SecretScrubber()
    return _DEFAULT_SCRUBBER


def scrub_string(text: str) -> str:
    """Convenience function to scrub a string using the default scrubber."""
    return get_default_scrubber().scrub_string(text)


def scrub_object(obj: Any) -> Any:
    """Convenience function to scrub an object using the default scrubber."""
    return get_default_scrubber().scrub_object(obj)
