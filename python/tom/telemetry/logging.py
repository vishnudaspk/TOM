"""Structured logging and secret redaction for TOM.

Adheres to plan.md §44 and skills/coding/structured-logging:
- Structured JSON output format
- Automatic redaction of sensitive credentials and keys
- Pipeline timing and latency measurement
- Component, event, and task correlation
- Session ID correlation for test isolation
- Utterance ID correlation for voice pipeline tracing
- Normal vs Diagnostic logging modes
- Rate limiting for repetitive polling logs
"""

import json
import logging
import os
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tom.security.secrets import SecretScrubber, get_default_scrubber

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

# Module-level scrubber instance for logging integration
_LOG_SCRUBBER: SecretScrubber = get_default_scrubber()

# Session ID for test isolation - set at startup
_SESSION_ID: str = os.environ.get("TOM_SESSION_ID", uuid.uuid4().hex[:8])

# Utterance ID for voice pipeline correlation (context variable)
_UTTERANCE_ID: ContextVar[str | None] = ContextVar("utterance_id", default=None)

# Diagnostic mode flag (set via --debug or TOM_DIAGNOSTIC_LOG=1)
_DIAGNOSTIC_MODE: bool = False

# Rate limiting for repetitive logs
_RATE_LIMITER: dict[str, tuple[float, int]] = {}  # key -> (last_time, count)
_RATE_LIMIT_WINDOW: float = 1.0  # seconds
_RATE_LIMIT_MAX_COUNT: int = 10  # max logs per window


def get_session_id() -> str:
    """Return the current session ID."""
    return _SESSION_ID


def set_session_id(session_id: str) -> None:
    """Set the session ID for the current process."""
    global _SESSION_ID
    _SESSION_ID = session_id


def get_utterance_id() -> str | None:
    """Return the current utterance ID for voice pipeline correlation."""
    return _UTTERANCE_ID.get()


def set_utterance_id(utterance_id: str | None) -> None:
    """Set the utterance ID for the current context."""
    _UTTERANCE_ID.set(utterance_id)


def is_diagnostic_mode() -> bool:
    """Check if diagnostic (verbose) logging mode is enabled."""
    return _DIAGNOSTIC_MODE


def set_diagnostic_mode(enabled: bool) -> None:
    """Enable or disable diagnostic logging mode."""
    global _DIAGNOSTIC_MODE
    _DIAGNOSTIC_MODE = enabled


def should_log_rate_limited(key: str) -> bool:
    """Check if a rate-limited log should be emitted.
    
    Returns True if the log should be emitted, False if rate-limited.
    """
    now = time.monotonic()
    last_time, count = _RATE_LIMITER.get(key, (0.0, 0))
    
    if now - last_time > _RATE_LIMIT_WINDOW:
        # Window reset
        _RATE_LIMITER[key] = (now, 1)
        return True
    
    if count >= _RATE_LIMIT_MAX_COUNT:
        return False
    
    _RATE_LIMITER[key] = (last_time, count + 1)
    return True


def log_rate_limited(logger: logging.Logger, level: int, event: str, key: str, **context: Any) -> None:
    """Emit a rate-limited log event."""
    if should_log_rate_limited(key):
        extra = {"component": context.pop("component", ""), "event": event, **context}
        logger.log(level, event, extra=extra)


# Module-level scrubber instance for logging integration
_LOG_SCRUBBER: SecretScrubber = get_default_scrubber()

# Session ID for test isolation - set at startup
_SESSION_ID: str = os.environ.get("TOM_SESSION_ID", uuid.uuid4().hex[:8])


def get_session_id() -> str:
    """Return the current session ID."""
    return _SESSION_ID


def set_session_id(session_id: str) -> None:
    """Set the session ID for the current process."""
    global _SESSION_ID
    _SESSION_ID = session_id


def redact_secrets(data: Any) -> Any:
    """Recursively redact sensitive fields from dictionaries and lists.

    Applies both key-name filtering (for known sensitive field names) and
    string-value scanning via SecretScrubber to catch credentials appearing
    as values (e.g., in tool outputs, error messages, log context).

    Args:
        data: Any data structure (dict, list, tuple, primitive, Pydantic model).

    Returns:
        New data structure with secrets redacted. Original is not mutated.
    """
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
    elif isinstance(data, str):
        # Apply string-level secret scrubbing for credential values
        return _LOG_SCRUBBER.scrub_string(data)
    return data


# Standard event names for voice pipeline (stable, searchable)
class VoiceEventNames:
    """Stable event names for voice pipeline stages."""
    
    # Session lifecycle
    SESSION_STARTED = "voice.session.started"
    SESSION_STOPPING = "voice.session.stopping"
    SESSION_STOPPED = "voice.session.stopped"
    SESSION_EXCEPTION = "voice.session.exception"
    
    # Capture
    CAPTURE_STARTED = "voice.capture.started"
    CAPTURE_COMPLETED = "voice.capture.completed"
    CAPTURE_FAILED = "voice.capture.failed"
    
    # Turn detection
    TURN_DECISION = "voice.turn.decision"
    
    # STT
    STT_STARTED = "voice.stt.started"
    STT_COMPLETED = "voice.stt.completed"
    STT_EMPTY = "voice.stt.empty"
    STT_FAILED = "voice.stt.failed"
    
    # LLM
    LLM_STARTED = "voice.llm.started"
    LLM_COMPLETED = "voice.llm.completed"
    LLM_EMPTY = "voice.llm.empty"
    LLM_FAILED = "voice.llm.failed"
    
    # TTS
    TTS_STARTED = "voice.tts.started"
    TTS_CHUNK = "voice.tts.chunk"
    TTS_COMPLETED = "voice.tts.completed"
    TTS_EMPTY = "voice.tts.empty"
    TTS_FAILED = "voice.tts.failed"
    
    # Playback
    PLAYBACK_SUBMITTED = "voice.playback.submitted"
    PLAYBACK_STARTED = "voice.playback.started"
    PLAYBACK_COMPLETED = "voice.playback.completed"
    PLAYBACK_FAILED = "voice.playback.failed"
    
    # Pipeline
    PIPELINE_CANCELLED = "voice.pipeline.cancelled"
    PIPELINE_TIMEOUT = "voice.pipeline.timeout"
    
    # IPC
    IPC_REQUEST_SENT = "ipc.request.sent"
    IPC_RESPONSE_RECEIVED = "ipc.response.received"
    IPC_REQUEST_TIMEOUT = "ipc.request.timeout"
    IPC_RESPONSE_UNKNOWN = "ipc.response.unknown_id"
    IPC_RESPONSE_DUPLICATE = "ipc.response.duplicate_or_late"
    IPC_RECONNECTING = "ipc.reconnecting"
    IPC_RECONNECTED = "ipc.reconnected"
    IPC_DISCONNECTED = "ipc.disconnected"

    # Startup greeting
    STARTUP_GREETING_STARTED = "startup_greeting.started"
    STARTUP_GREETING_TIME_RECEIVED = "startup_greeting.time_received"
    STARTUP_GREETING_LLM_STARTED = "startup_greeting.llm_started"
    STARTUP_GREETING_LLM_COMPLETED = "startup_greeting.llm_completed"
    STARTUP_GREETING_TTS_STARTED = "startup_greeting.tts_started"
    STARTUP_GREETING_TTS_COMPLETED = "startup_greeting.tts_completed"
    STARTUP_GREETING_PLAYBACK_STARTED = "startup_greeting.playback_started"
    STARTUP_GREETING_PLAYBACK_COMPLETED = "startup_greeting.playback_completed"
    STARTUP_GREETING_FAILED = "startup_greeting.failed"


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
            "session_id": _SESSION_ID,
        }

        # Add utterance ID if available
        utterance_id = _UTTERANCE_ID.get()
        if utterance_id:
            log_obj["utterance_id"] = utterance_id

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

    def _build_extra(
        self,
        event: str,
        task_id: str | None = None,
        latency_ms: int | None = None,
        error: Any | None = None,
        **context: Any,
    ) -> dict[str, Any]:
        """Build extra dict with standard fields."""
        extra: dict[str, Any] = {
            "component": self.component,
            "event": event,
            "task_id": task_id,
            "latency_ms": latency_ms,
            "error": error,
            **context,
        }
        # Add utterance ID if available
        utterance_id = get_utterance_id()
        if utterance_id:
            extra["utterance_id"] = utterance_id
        return extra

    def log_event(
        self,
        level: int,
        event: str,
        task_id: str | None = None,
        latency_ms: int | None = None,
        error: Any | None = None,
        rate_limit_key: str | None = None,
        **context: Any,
    ) -> None:
        """Emit a structured event with explicit metadata and extra context."""
        if rate_limit_key and not should_log_rate_limited(rate_limit_key):
            return
        
        extra = self._build_extra(event, task_id, latency_ms, error, **context)
        self.logger.log(level, event, extra=extra)

    def debug(self, event: str, **kwargs: Any) -> None:
        self.log_event(logging.DEBUG, event, **kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        self.log_event(logging.INFO, event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self.log_event(logging.WARNING, event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self.log_event(logging.ERROR, event, **kwargs)

    def critical(self, event: str, **kwargs: Any) -> None:
        self.log_event(logging.CRITICAL, event, **kwargs)

    def log_voice_event(
        self,
        level: int,
        event_name: str,
        utterance_id: str | None = None,
        **context: Any,
    ) -> None:
        """Log a voice pipeline event with standard fields."""
        if utterance_id:
            # Temporarily set utterance ID for this log
            token = set_utterance_id(utterance_id)
            try:
                self.log_event(level, event_name, **context)
            finally:
                set_utterance_id(token)
        else:
            self.log_event(level, event_name, **context)

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


def configure_logging(
    level: str = "INFO",
    json_output: bool = True,
    verbose: bool = False,
    log_file: str | None = None,
    diagnostic: bool = False,
) -> None:
    """Configure root logger for TOM with structured JSON formatting.

    Non-verbose: all logs → logs/tom.log (RotatingFileHandler, 5MB x 3)
    Verbose: INFO/DEBUG → stderr, WARNING+ → stderr, all → logs/tom.log
    stdout = chat output ONLY (never logs)

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        json_output: Use structured JSON format
        verbose: Enable console output for INFO/DEBUG
        log_file: Optional custom log file path
        diagnostic: Enable diagnostic mode (detailed per-stage logging)
    """
    global _configured, _DIAGNOSTIC_MODE
    
    _DIAGNOSTIC_MODE = diagnostic or os.environ.get("TOM_DIAGNOSTIC_LOG") == "1"
    
    root_logger = logging.getLogger("tom")
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Clear existing handlers to prevent duplicate logs
    root_logger.handlers.clear()

    # Always log to file (RotatingFileHandler, 5MB x 3)
    from logging.handlers import RotatingFileHandler
    if log_file is None:
        log_file = os.environ.get("TOM_LOG_FILE", "logs/tom.log")
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    if json_output:
        file_handler.setFormatter(StructuredJsonFormatter())
    else:
        file_handler.setFormatter(
            logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
        )
    root_logger.addHandler(file_handler)

    if verbose:
        # INFO/DEBUG → stderr (not stdout)
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.DEBUG)
        if json_output:
            stderr_handler.setFormatter(StructuredJsonFormatter())
        else:
            stderr_handler.setFormatter(
                logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
            )
        root_logger.addHandler(stderr_handler)
    else:
        # Non-verbose: WARNING+ → stderr
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.WARNING)
        if json_output:
            stderr_handler.setFormatter(StructuredJsonFormatter())
        else:
            stderr_handler.setFormatter(
                logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
            )
        root_logger.addHandler(stderr_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("pipecat").setLevel(logging.INFO if not _DIAGNOSTIC_MODE else logging.DEBUG)
    logging.getLogger("faster_whisper").setLevel(logging.INFO if not _DIAGNOSTIC_MODE else logging.DEBUG)

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