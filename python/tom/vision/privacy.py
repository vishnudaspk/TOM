"""Privacy protection shield and ephemeral screenshot lifecycle for TOM.

Adheres to Phase 7 Iteration 1 requirements and skills/security/secrets:
- Pre-capture foreground window title inspection against sensitive blacklist
- Instant blocking with SensitiveScreenContentError
- Regex redaction of secrets, credit cards, SSNs, and credentials
- Ephemeral in-memory lifecycle enforcement (zero disk persistence, zero base64 in logs)
"""

from __future__ import annotations

import ctypes
import fnmatch
import re
import sys
from collections.abc import Callable
from typing import Any

from tom.schemas.vision import CapturedFrame, PrivacyCheckResult
from tom.telemetry.logging import get_logger

logger = get_logger(__name__, component="vision.privacy")

# Default sensitive window title patterns (case-insensitive glob/substring)
DEFAULT_SENSITIVE_WINDOW_PATTERNS: tuple[str, ...] = (
    "*password*",
    "*bitwarden*",
    "*1password*",
    "*keepass*",
    "*bank*",
    "*private browsing*",
    "*incognito*",
)

# Regex patterns for redacting extracted text
RE_CREDIT_CARD = re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b|\b\d{15,16}\b")
RE_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
RE_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+([A-Za-z0-9_\-\.]{20,})\b")
RE_WELL_KNOWN_KEY = re.compile(
    r"\b(?:sk|ghp|gho|glpat|xoxb|xoxp|hf)_[A-Za-z0-9_\-]{20,}\b|\b(?:AIza[0-9A-Za-z\-_]{35})\b"
)
RE_KEY_VALUE_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.]{16,})['\"]?"
)
RE_KEY_VALUE_PASSWORD = re.compile(
    r"(?i)\b(password|passwd|pwd)\s*[:=]\s*['\"]?([^\s'\"]{4,})['\"]?"
)

# Base64 data-URI or large raw base64 detector
RE_BASE64_IMAGE = re.compile(r"data:image\/[a-zA-Z]+;base64,[A-Za-z0-9+/=]{50,}")


class SensitiveScreenContentError(Exception):
    """Raised when a sensitive application or window is active and screen capture is blocked."""


def _get_windows_foreground_title() -> str | None:
    """Retrieve foreground window title via Win32 ctypes."""
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return None
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception as exc:
        logger.debug("foreground_window_query_failed", error=str(exc))
        return None


class PrivacyShield:
    """Enforces pre-capture privacy boundaries and in-memory frame safety."""

    def __init__(
        self,
        sensitive_patterns: list[str] | tuple[str, ...] | None = None,
        foreground_window_provider: Callable[[], str | None] | None = None,
    ) -> None:
        self.sensitive_patterns = tuple(
            sensitive_patterns
            if sensitive_patterns is not None
            else DEFAULT_SENSITIVE_WINDOW_PATTERNS
        )
        self._foreground_window_provider = (
            foreground_window_provider or _get_windows_foreground_title
        )

    def is_window_sensitive(self, title: str | None) -> bool:
        """Check whether a window title matches any sensitive pattern."""
        if not title:
            return False
        clean = title.strip().lower()
        for pat in self.sensitive_patterns:
            pat_lower = pat.lower()
            # Support both wildcard globbing and simple substring matching
            if fnmatch.fnmatch(clean, pat_lower) or pat_lower.strip("*") in clean:
                return True
        return False

    def get_foreground_window_title(self) -> str | None:
        """Retrieve the current foreground window title using the active provider."""
        return self._foreground_window_provider()

    def check_window(self, title: str | None) -> PrivacyCheckResult:
        """Check a specific window title against sensitive patterns."""
        if not title:
            return PrivacyCheckResult(is_safe=True)
        clean = title.strip().lower()
        for pat in self.sensitive_patterns:
            pat_lower = pat.lower()
            if fnmatch.fnmatch(clean, pat_lower) or pat_lower.strip("*") in clean:
                return PrivacyCheckResult(
                    is_safe=False,
                    reason="Sensitive window detected",
                    matched_pattern=pat,
                    matched_title=title,
                )
        return PrivacyCheckResult(is_safe=True)

    def check_foreground_window(self) -> PrivacyCheckResult:
        """Inspect the active foreground window title."""
        title = self.get_foreground_window_title()
        return self.check_window(title)

    def assert_capture_allowed(self) -> None:
        """Ensure screen capture is permitted. Raises SensitiveScreenContentError if blocked."""
        check = self.check_foreground_window()
        if not check.is_safe:
            msg = (
                f"Screen capture blocked by PrivacyShield: sensitive foreground window "
                f"'{check.matched_title}' matched pattern '{check.matched_pattern}'"
            )
            logger.warning(
                "screen_capture_blocked_sensitive_content",
                matched_pattern=check.matched_pattern,
            )
            raise SensitiveScreenContentError(msg)

    def filter_extracted_text(self, text: str) -> str:
        """Redact credit cards, SSNs, API keys, and passwords from extracted text."""
        if not text:
            return ""

        # Redact credit cards
        filtered = RE_CREDIT_CARD.sub("[REDACTED_CREDIT_CARD]", text)
        # Redact SSNs
        filtered = RE_SSN.sub("[REDACTED_SSN]", filtered)
        # Redact Bearer tokens
        filtered = RE_BEARER_TOKEN.sub(r"Bearer [REDACTED_TOKEN]", filtered)
        # Redact known API keys
        filtered = RE_WELL_KNOWN_KEY.sub("[REDACTED_API_KEY]", filtered)
        # Redact key-value secrets
        filtered = RE_KEY_VALUE_SECRET.sub(r"\1=[REDACTED_SECRET]", filtered)
        # Redact key-value passwords
        filtered = RE_KEY_VALUE_PASSWORD.sub(r"\1=[REDACTED_PASSWORD]", filtered)

        return filtered

    def enforce_ephemeral_lifecycle(self, frame: CapturedFrame) -> None:
        """Verify that a frame is strictly ephemeral and clean up buffer references."""
        if not frame.is_ephemeral:
            raise ValueError("CapturedFrame must have is_ephemeral=True")
        # Ensure raw bytes can be dropped when done
        frame.clear()

    @staticmethod
    def sanitize_log_metadata(data: dict[str, Any]) -> dict[str, Any]:
        """Strip raw byte buffers and base64 image strings from telemetry metadata."""
        sanitized: dict[str, Any] = {}
        for k, v in data.items():
            if isinstance(v, bytes):
                sanitized[k] = f"[BYTES_OMITTED length={len(v)}]"
            elif isinstance(v, str) and (
                RE_BASE64_IMAGE.search(v)
                or (len(v) > 200 and v.startswith("data:image"))
                or (len(v) > 500 and not v.startswith("http"))
            ):
                sanitized[k] = "[IMAGE_PAYLOAD_REDACTED]"
            elif isinstance(v, dict):
                sanitized[k] = PrivacyShield.sanitize_log_metadata(v)
            else:
                sanitized[k] = v
        return sanitized
