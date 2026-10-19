"""TOM Security Subsystem — Structured Security Audit Logging.

Provides append-only structured logging for security events (tool confirmation,
permission decisions, rate-limiting, and policy evaluation).

Design & Constraints:
- Uses standard Python logging via logging.getLogger("tom.security.audit").
- Emits structured JSON events with:
  timestamp_utc, event_type, tool_name, action, decision, duration_ms, sanitized_param_keys.
- Data minimization: never logs raw parameter values, secrets, or prompt texts.
  Only sanitized parameter keys are recorded.
- Fail-Closed: if audit recording raises an exception during confirmation,
  raises SecurityError to ensure protected actions never proceed un-audited.
- Durability note: uses configured Python logging handlers; does not claim
  tamper-proof hardware storage or write-ahead disk guarantees beyond standard logging.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from tom.security.permissions import SecurityError

AUDIT_LOGGER_NAME = "tom.security.audit"


class SecurityAuditLogger:
    """Structured security audit logger emitting minimized, JSON-formatted audit events."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        """Initialize with a Python logger.

        Args:
            logger: Optional logging.Logger instance. Defaults to logging.getLogger("tom.security.audit").
        """
        self._logger = logger or logging.getLogger(AUDIT_LOGGER_NAME)

    @property
    def logger(self) -> logging.Logger:
        """Return the underlying logger instance."""
        return self._logger

    def log_decision(
        self,
        *,
        event_type: str = "confirmation",
        tool_name: str,
        action: str,
        decision: str,  # "GRANTED", "DENIED", "TIMED_OUT", "BLOCKED"
        duration_ms: float,
        params: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a structured security decision event.

        Args:
            event_type: Category of security event (e.g., 'confirmation', 'permission').
            tool_name: Identifier of the tool involved.
            action: Action being evaluated (e.g., 'request_confirmation').
            decision: Outcome ('GRANTED', 'DENIED', 'TIMED_OUT', 'BLOCKED').
            duration_ms: Duration of evaluation in milliseconds.
            params: Parameters provided to the tool (only keys are recorded).
            extra: Optional additional minimized metadata.

        Returns:
            The structured event dictionary that was emitted.

        Raises:
            SecurityError: If logging emission raises an exception (fail-closed).
        """
        # Data minimization: record keys only, never values
        sanitized_keys = sorted(params.keys()) if params else []

        event: dict[str, Any] = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "event_type": event_type,
            "tool_name": tool_name,
            "action": action,
            "decision": decision,
            "duration_ms": round(float(duration_ms), 2),
            "sanitized_param_keys": sanitized_keys,
        }

        if extra:
            event["extra"] = extra

        try:
            msg = json.dumps(event, ensure_ascii=True, default=str)
            self._logger.info(msg)
        except Exception as exc:
            raise SecurityError(f"Security audit logging failed: {exc}") from exc

        return event
