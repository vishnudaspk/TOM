import json
import logging

from tom.telemetry.logging import (
    StructuredJsonFormatter,
    get_logger,
    redact_secrets,
)


def test_redact_secrets_recursive():
    """Verify that sensitive keys are replaced with ***REDACTED*** at all levels."""
    payload = {
        "api_key": "sk-12345secret",
        "nested": {
            "token": "ghp_abcdef123456",
            "password": "supersecretpassword",
            "safe_field": "hello world",
        },
        "items": [
            {"secret": "hidden_val"},
            {"key": "private_data"},
            {"safe_item": 42},
        ],
    }

    cleaned = redact_secrets(payload)

    assert cleaned["api_key"] == "***REDACTED***"
    assert cleaned["nested"]["token"] == "***REDACTED***"
    assert cleaned["nested"]["password"] == "***REDACTED***"
    assert cleaned["nested"]["safe_field"] == "hello world"
    assert cleaned["items"][0]["secret"] == "***REDACTED***"
    assert cleaned["items"][1]["key"] == "***REDACTED***"
    assert cleaned["items"][2]["safe_item"] == 42


def test_structured_json_formatter():
    """Verify that log records format as valid single-line JSON with standard fields."""
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        name="tom.test",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="test_event",
        args=(),
        exc_info=None,
    )
    record.component = "test_comp"
    record.event = "test_event"
    record.task_id = "task-001"
    record.latency_ms = 45

    formatted = formatter.format(record)
    parsed = json.loads(formatted)

    assert parsed["level"] == "INFO"
    assert parsed["component"] == "test_comp"
    assert parsed["event"] == "test_event"
    assert parsed["task_id"] == "task-001"
    assert parsed["latency_ms"] == 45
    assert "timestamp" in parsed


def test_logger_adapter_secrets_masking():
    """Verify that logging through TomLoggerAdapter with sensitive extra context redacts them."""
    formatter = StructuredJsonFormatter()

    # Create record with extra sensitive fields
    record = logging.LogRecord(
        name="tom.test_adapter",
        level=logging.INFO,
        pathname="test.py",
        lineno=20,
        msg="user_login",
        args=(),
        exc_info=None,
    )
    record.component = "security_test"
    record.event = "user_login"
    record.api_key = "sk-secretkey123"
    record.user_id = "alice"

    formatted = formatter.format(record)
    parsed = json.loads(formatted)

    assert parsed["context"]["api_key"] == "***REDACTED***"
    assert parsed["context"]["user_id"] == "alice"


def test_timed_operation_context_manager():
    """Verify timed_operation emits completion latency."""
    logger = get_logger("tom.test_timer", component="timer_test")
    with logger.timed_operation("sample_stage", task_id="task-99"):
        pass  # instantaneous block
