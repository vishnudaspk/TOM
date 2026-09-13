"""Structured logging and secret redaction for TOM.

Adheres to plan.md §44 and skills/coding/structured-logging:
- Structured JSON output format
- Automatic redaction of sensitive credentials and keys
- Pipeline timing and latency measurement
- Component, event, and task correlation
"""

import json
import logging
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

# Known sensitive field names that must never appear in cleartext in logs
SENSITIVE_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "api_key",
        "password",
        "token",
        "secret",
        "credential",
        "key",
        "auth",
        "authorization",
        "bearer",
        "access_token",
        "refresh_token",
        "private_key",
    }
)


def redact_secrets(data: Any) -> Any:
    """Recursively redact sensitive fields from dictionaries and lists."""
    if isinstance(data, dict):
        redacted: dict[str, Any] = {}
        for k, v in data.items():
            if isinstance(k, str) and k.lower() in SENSITIVE_FIELD_NAMES:
                redacted[k] = "***REDACTED***"
            else:
                redacted[k] = redact_secrets(v)
        return redacted
    elif isinstance(data, (list, tuple)):
        return [redact_secrets(item) for item in data]
    return data


class StructuredJsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects with redacted secrets."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.now(UTC).isoformat()

        log_obj: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "component": getattr(record, "component", record.name),
            "event": getattr(record, "event", record.getMessage()),
        }

        # Optional task correlation ID
        task_id = getattr(record, "task_id", None)
        if task_id:
            log_obj["task_id"] = task_id

        # Optional latency measurement
        latency_ms = getattr(record, "latency_ms", None)
        if latency_ms is not None:
            log_obj["latency_ms"] = latency_ms

        # Optional error field
        if record.exc_info:
            log_obj["error"] = self.formatException(record.exc_info)
        elif hasattr(record, "error") and record.error:
            log_obj["error"] = str(record.error)

        # Include additional contextual attributes passed in `extra`
        standard_attrs = {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "message",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "thread",
            "threadName",
            "component",
            "event",
            "task_id",
            "latency_ms",
            "error",
        }

        extra_fields = {
            k: v
            for k, v in record.__dict__.items()
            if k not in standard_attrs and not k.startswith("_")
        }

        if extra_fields:
            log_obj["context"] = extra_fields

        # Apply secrets filter to the entire log dictionary
        redacted_obj = redact_secrets(log_obj)

        return json.dumps(redacted_obj, default=str)


class TomLoggerAdapter(logging.LoggerAdapter):
    """Logger adapter providing ergonomic structured logging helper methods."""

    def __init__(self, logger: logging.Logger, component: str):
        super().__init__(logger, {"component": component})
        self.component = component

    def log_event(
        self,
        level: int,
        event: str,
        task_id: str | None = None,
        latency_ms: int | None = None,
        error: Any | None = None,
        **context: Any,
    ) -> None:
        """Emit a structured event with explicit metadata and extra context."""
        extra: dict[str, Any] = {
            "component": self.component,
            "event": event,
            "task_id": task_id,
            "latency_ms": latency_ms,
            "error": error,
            **context,
        }
        self.logger.log(level, event, extra=extra)

    def debug(self, event: str, **kwargs: Any) -> None:
        self.log_event(logging.DEBUG, event, **kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        self.log_event(logging.INFO, event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self.log_event(logging.WARNING, event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self.log_event(logging.ERROR, event, **kwargs)

    @contextmanager
    def timed_operation(
        self,
        operation_name: str,
        task_id: str | None = None,
        level: int = logging.INFO,
        **context: Any,
    ) -> Iterator[None]:
        """Context manager to time a pipeline stage and log completion latency."""
        start_time = time.monotonic()
        try:
            yield
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            self.log_event(
                level,
                f"{operation_name}_completed",
                task_id=task_id,
                latency_ms=elapsed_ms,
                **context,
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            self.log_event(
                logging.ERROR,
                f"{operation_name}_failed",
                task_id=task_id,
                latency_ms=elapsed_ms,
                error=str(exc),
                **context,
            )
            raise


_configured: bool = False


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    """Configure root logger for TOM with structured JSON formatting."""
    global _configured
    root_logger = logging.getLogger("tom")
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Clear existing handlers to prevent duplicate logs
    root_logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    if json_output:
        handler.setFormatter(StructuredJsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
        )

    root_logger.addHandler(handler)
    root_logger.propagate = False
    _configured = True


def get_logger(name: str, component: str | None = None) -> TomLoggerAdapter:
    """Obtain a structured TOM logger adapter.

    Args:
        name: Logger module name (typically __name__).
        component: Optional component label (e.g. 'core.orchestrator', 'tools.files').
    """
    if not _configured:
        configure_logging()

    comp = component or name.replace("tom.", "")
    logger = logging.getLogger(name)
    return TomLoggerAdapter(logger, component=comp)
