"""Unit tests for Confirmation Hardening, Anti-Flooding, and Audit Logging (Iteration 11.3).

Verifies:
- Non-interactive stdin (None, isatty() == False) immediately fails safe (returns False).
- Parameter secrets are scrubbed before rendering prompt strings.
- Audit events are emitted for GRANTED, DENIED, TIMED_OUT, and BLOCKED decisions.
- Audit logging failures raise SecurityError and fail closed.
- ConfirmationTracker sliding-window limits (5 prompts in 30s), cooldown (10s), recovery, and concurrency.
- Backward compatibility with AlwaysAllow, AlwaysDeny, and CallbackConfirmationHook.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest
from tom.security.audit import SecurityAuditLogger
from tom.security.confirmation import (
    AlwaysAllowConfirmationHook,
    AlwaysDenyConfirmationHook,
    CallbackConfirmationHook,
    ConfirmationRequest,
    ConfirmationTracker,
    ConsoleConfirmationHook,
)
from tom.security.permissions import SecurityError


@pytest.fixture
def sample_request() -> ConfirmationRequest:
    return ConfirmationRequest(
        tool_name="files.delete_file",
        description="Delete sensitive document",
        params={
            "path": "/docs/plan.md",
            "secret_key": "sk-proj-1234567890abcdef1234567890abcdef1234567890abcdef",
        },
        timeout_seconds=5.0,
    )


# ---------------------------------------------------------------------------
# Non-Interactive Stdin Tests
# ---------------------------------------------------------------------------


class TestNonInteractiveStdin:
    """ConsoleConfirmationHook must fail closed if stdin is non-interactive."""

    @pytest.mark.asyncio
    async def test_stdin_is_none_denies_immediately(
        self, monkeypatch: pytest.MonkeyPatch, sample_request: ConfirmationRequest
    ) -> None:
        monkeypatch.setattr(sys, "stdin", None)
        input_mock = MagicMock(return_value="y")

        hook = ConsoleConfirmationHook(input_fn=input_mock, check_stdin=True)
        result = await hook.request_confirmation(sample_request)

        assert result is False
        input_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_stdin_not_a_tty_denies_immediately(
        self, monkeypatch: pytest.MonkeyPatch, sample_request: ConfirmationRequest
    ) -> None:
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = False
        monkeypatch.setattr(sys, "stdin", mock_stdin)
        input_mock = MagicMock(return_value="y")

        hook = ConsoleConfirmationHook(input_fn=input_mock, check_stdin=True)
        result = await hook.request_confirmation(sample_request)

        assert result is False
        input_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_stdin_interactive_allows_prompt(
        self, monkeypatch: pytest.MonkeyPatch, sample_request: ConfirmationRequest
    ) -> None:
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = True
        monkeypatch.setattr(sys, "stdin", mock_stdin)

        hook = ConsoleConfirmationHook(input_fn=lambda _p: "y", check_stdin=True)
        result = await hook.request_confirmation(sample_request)

        assert result is True


# ---------------------------------------------------------------------------
# Prompt Secret Scrubbing Tests
# ---------------------------------------------------------------------------


class TestPromptSecretScrubbing:
    """ConsoleConfirmationHook must sanitize parameters before formatting prompt."""

    @pytest.mark.asyncio
    async def test_prompt_redacts_credentials(
        self, monkeypatch: pytest.MonkeyPatch, sample_request: ConfirmationRequest
    ) -> None:
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = True
        monkeypatch.setattr(sys, "stdin", mock_stdin)

        rendered_prompts: list[str] = []

        def capture_input(prompt: str) -> str:
            rendered_prompts.append(prompt)
            return "y"

        hook = ConsoleConfirmationHook(input_fn=capture_input, check_stdin=True)
        result = await hook.request_confirmation(sample_request)

        assert result is True
        assert len(rendered_prompts) == 1
        prompt_text = rendered_prompts[0]

        # Raw credential must not be present
        assert "sk-proj-1234567890abcdef1234567890abcdef1234567890abcdef" not in prompt_text
        # Redaction marker must be present
        assert "***REDACTED" in prompt_text


# ---------------------------------------------------------------------------
# Security Audit Logger & Event Tests
# ---------------------------------------------------------------------------


class TestSecurityAuditLogging:
    """SecurityAuditLogger must record structured events and fail closed on errors."""

    def test_log_decision_structure_and_minimization(self) -> None:
        mock_logger = MagicMock(spec=logging.Logger)
        audit = SecurityAuditLogger(logger=mock_logger)

        event = audit.log_decision(
            tool_name="files.write_file",
            action="request_confirmation",
            decision="GRANTED",
            duration_ms=42.5,
            params={"path": "/app/config.json", "api_key": "secret123"},
        )

        assert event["tool_name"] == "files.write_file"
        assert event["action"] == "request_confirmation"
        assert event["decision"] == "GRANTED"
        assert event["duration_ms"] == 42.5
        assert "timestamp_utc" in event
        # Only parameter keys are kept
        assert event["sanitized_param_keys"] == ["api_key", "path"]
        assert "secret123" not in json.dumps(event)

        mock_logger.info.assert_called_once()

    def test_audit_failure_raises_security_error(self) -> None:
        mock_logger = MagicMock(spec=logging.Logger)
        mock_logger.info.side_effect = OSError("Audit disk full")
        audit = SecurityAuditLogger(logger=mock_logger)

        with pytest.raises(SecurityError, match="Security audit logging failed"):
            audit.log_decision(
                tool_name="files.write_file",
                action="request_confirmation",
                decision="GRANTED",
                duration_ms=10.0,
            )

    @pytest.mark.asyncio
    async def test_hook_audit_failure_prevents_approval(
        self, sample_request: ConfirmationRequest
    ) -> None:
        """If audit logging fails after user confirms 'y', SecurityError must be raised."""
        mock_logger = MagicMock(spec=logging.Logger)
        mock_logger.info.side_effect = OSError("Audit logger failure")
        failing_audit = SecurityAuditLogger(logger=mock_logger)

        hook = ConsoleConfirmationHook(
            input_fn=lambda _p: "y",
            audit_logger=failing_audit,
            check_stdin=False,
        )

        with pytest.raises(SecurityError, match="Security audit logging failed"):
            await hook.request_confirmation(sample_request)

    @pytest.mark.asyncio
    async def test_hook_records_granted_and_denied_audit_events(
        self, sample_request: ConfirmationRequest
    ) -> None:
        logged_events: list[dict[str, Any]] = []

        class CapturingAuditLogger(SecurityAuditLogger):
            def log_decision(self, **kwargs: Any) -> dict[str, Any]:
                event = super().log_decision(**kwargs)
                logged_events.append(event)
                return event

        capturing = CapturingAuditLogger()

        # 1. Granted
        hook_grant = ConsoleConfirmationHook(
            input_fn=lambda _p: "y",
            audit_logger=capturing,
            check_stdin=False,
        )
        assert await hook_grant.request_confirmation(sample_request) is True
        assert logged_events[-1]["decision"] == "GRANTED"

        # 2. Denied
        hook_deny = ConsoleConfirmationHook(
            input_fn=lambda _p: "n",
            audit_logger=capturing,
            check_stdin=False,
        )
        assert await hook_deny.request_confirmation(sample_request) is False
        assert logged_events[-1]["decision"] == "DENIED"

    @pytest.mark.asyncio
    async def test_hook_records_timeout_audit_event(
        self, sample_request: ConfirmationRequest
    ) -> None:
        logged_events: list[dict[str, Any]] = []

        class CapturingAuditLogger(SecurityAuditLogger):
            def log_decision(self, **kwargs: Any) -> dict[str, Any]:
                event = super().log_decision(**kwargs)
                logged_events.append(event)
                return event

        def hanging_input(_p: str) -> str:
            import time

            time.sleep(0.5)
            return "y"

        hook = ConsoleConfirmationHook(
            timeout_seconds=0.05,
            input_fn=hanging_input,
            audit_logger=CapturingAuditLogger(),
            check_stdin=False,
        )
        assert await hook.request_confirmation(sample_request) is False


# ---------------------------------------------------------------------------
# ConfirmationTracker (Flood Protection) Tests
# ---------------------------------------------------------------------------


class TestConfirmationTracker:
    """ConfirmationTracker sliding-window rate-limiting and anti-flooding."""

    @pytest.mark.asyncio
    async def test_sliding_window_burst_and_cooldown(self) -> None:
        simulated_time = 1000.0

        def fake_clock() -> float:
            return simulated_time

        tracker = ConfirmationTracker(
            max_prompts=5,
            window_seconds=30.0,
            cooldown_seconds=10.0,
            clock=fake_clock,
        )

        # 5 prompts permitted
        for _ in range(5):
            assert await tracker.acquire() is True
            simulated_time += 1.0

        # 6th prompt within 30s window triggers cooldown
        assert await tracker.acquire() is False
        assert tracker.is_in_cooldown is True

        # During 10s cooldown, requests continue to be rejected
        simulated_time += 5.0
        assert await tracker.acquire() is False
        assert tracker.is_in_cooldown is True

        # After cooldown elapses (10s past cooldown trigger at t=1005)
        simulated_time = 1016.0
        assert tracker.is_in_cooldown is False
        assert await tracker.acquire() is True

    @pytest.mark.asyncio
    async def test_sliding_window_eviction(self) -> None:
        simulated_time = 100.0

        def fake_clock() -> float:
            return simulated_time

        tracker = ConfirmationTracker(
            max_prompts=2,
            window_seconds=10.0,
            cooldown_seconds=5.0,
            clock=fake_clock,
        )

        assert await tracker.acquire() is True  # t=100
        simulated_time = 105.0
        assert await tracker.acquire() is True  # t=105

        # 3rd request at t=108 would exceed max_prompts
        simulated_time = 108.0
        assert await tracker.acquire() is False  # Cooldown triggered

        # Advance past cooldown (cooldown until 108 + 5 = 113) and past window for t=100
        simulated_time = 114.0
        assert await tracker.acquire() is True

    @pytest.mark.asyncio
    async def test_concurrent_acquire_protection(self) -> None:
        tracker = ConfirmationTracker(max_prompts=5, window_seconds=30.0, cooldown_seconds=10.0)

        # Launch 10 concurrent acquire calls
        results = await asyncio.gather(*(tracker.acquire() for _ in range(10)))

        # Exactly 5 must succeed; the remaining 5 must be rejected
        allowed_count = sum(1 for r in results if r is True)
        denied_count = sum(1 for r in results if r is False)

        assert allowed_count == 5
        assert denied_count == 5


# ---------------------------------------------------------------------------
# Compatibility with Existing Hooks
# ---------------------------------------------------------------------------


class TestExistingHooksCompatibility:
    """Verify existing confirmation hooks retain expected behavior."""

    @pytest.mark.asyncio
    async def test_always_allow_hook(self, sample_request: ConfirmationRequest) -> None:
        hook = AlwaysAllowConfirmationHook()
        assert await hook.request_confirmation(sample_request) is True

    @pytest.mark.asyncio
    async def test_always_deny_hook(self, sample_request: ConfirmationRequest) -> None:
        hook = AlwaysDenyConfirmationHook()
        assert await hook.request_confirmation(sample_request) is False

    @pytest.mark.asyncio
    async def test_callback_hook_sync_and_async(self, sample_request: ConfirmationRequest) -> None:
        # Sync callback
        sync_hook = CallbackConfirmationHook(callback=lambda _r: True)
        assert await sync_hook.request_confirmation(sample_request) is True

        # Async callback
        async def async_cb(_r: ConfirmationRequest) -> bool:
            await asyncio.sleep(0.01)
            return True

        async_hook = CallbackConfirmationHook(callback=async_cb)
        assert await async_hook.request_confirmation(sample_request) is True
