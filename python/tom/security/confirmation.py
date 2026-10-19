"""TOM Security Subsystem — Confirmation Hook Interface and Concrete Implementations.

This module provides the interactive user confirmation interface required before executing
any tool classified as ASK_USER. Implementations remain completely decoupled from CLI/UI/LLM
runtimes and default to denial (False) on timeout or failure.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import time
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tom.security.audit import SecurityAuditLogger
from tom.security.permissions import SecurityError
from tom.security.secrets import SecretScrubber, get_default_scrubber


class ConfirmationRequest(BaseModel):
    """Structured request payload presented to the user for confirmation."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    tool_name: str = Field(
        min_length=1,
        description="Name of the tool requiring confirmation",
    )
    description: str = Field(
        default="",
        description="Description of what the tool will do",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments to be supplied to the tool",
    )
    timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description="Maximum time allowed for user confirmation before defaulting to denial",
    )


class ConfirmationHook(ABC):
    """Abstract interface for interactive confirmation of ASK_USER operations."""

    @abstractmethod
    async def request_confirmation(self, request: ConfirmationRequest) -> bool:
        """Prompt user or downstream handler for confirmation.

        Args:
            request: The structured ConfirmationRequest details.

        Returns:
            True if confirmation was explicitly granted, False if denied or timed out.
        """


class AlwaysAllowConfirmationHook(ConfirmationHook):
    """Testing hook that unconditionally approves all confirmation requests."""

    async def request_confirmation(self, request: ConfirmationRequest) -> bool:
        return True


class AlwaysDenyConfirmationHook(ConfirmationHook):
    """Testing hook that unconditionally rejects all confirmation requests."""

    async def request_confirmation(self, request: ConfirmationRequest) -> bool:
        return False


class CallbackConfirmationHook(ConfirmationHook):
    """Confirmation hook that delegates confirmation decisions to a callable.

    Supports both asynchronous and synchronous callbacks. Enforces timeout limits
    and safely catches any exceptions, defaulting to denial (False).
    """

    def __init__(
        self,
        callback: Callable[[ConfirmationRequest], bool | Awaitable[bool]],
        timeout_seconds: float | None = None,
    ) -> None:
        self.callback = callback
        self.timeout_seconds = timeout_seconds

    async def request_confirmation(self, request: ConfirmationRequest) -> bool:
        effective_timeout = (
            self.timeout_seconds if self.timeout_seconds is not None else request.timeout_seconds
        )

        async def _execute() -> bool:
            if inspect.iscoroutinefunction(self.callback):
                res = await self.callback(request)
            else:
                res = self.callback(request)
                if inspect.isawaitable(res):
                    res = await res
            return bool(res)

        try:
            return await asyncio.wait_for(_execute(), timeout=effective_timeout)
        except (TimeoutError, Exception):
            # Safe failure state: any timeout or unexpected error defaults to denial
            return False


class ConfirmationTracker:
    """Sliding-window confirmation prompt rate limiter with anti-flooding cooldown.

    Enforces at most max_prompts within a sliding window_seconds (default: 5 prompts
    in 30 seconds). The next request exceeding this triggers an automatic cooldown_seconds
    (default: 10 seconds), during which requests are rejected immediately without prompting.

    Locking & Concurrency:
    - State is protected by an asyncio.Lock.
    - The lock is acquired only to check and update window timestamps; it is NEVER
      held while waiting for interactive user input.
    - Monotonic clock is injectable for deterministic testing without real sleeps.
    """

    def __init__(
        self,
        max_prompts: int = 5,
        window_seconds: float = 30.0,
        cooldown_seconds: float = 10.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._max_prompts = max_prompts
        self._window_seconds = window_seconds
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock or time.monotonic
        self._lock = asyncio.Lock()
        self._history: deque[float] = deque()
        self._cooldown_until: float = 0.0

    @property
    def is_in_cooldown(self) -> bool:
        """Check if cooldown is currently active."""
        return self._clock() < self._cooldown_until

    async def acquire(self) -> bool:
        """Attempt to acquire permission to present a confirmation prompt.

        Returns:
            True if permitted, False if rate-limited or currently in cooldown.
        """
        async with self._lock:
            now = self._clock()

            # If currently in cooldown, reject immediately
            if now < self._cooldown_until:
                return False

            # Sliding window: evict timestamps outside the window
            cutoff = now - self._window_seconds
            while self._history and self._history[0] <= cutoff:
                self._history.popleft()

            # Check if limit reached
            if len(self._history) >= self._max_prompts:
                self._cooldown_until = now + self._cooldown_seconds
                self._history.clear()
                return False

            self._history.append(now)
            return True

    def reset(self) -> None:
        """Reset history and cooldown state."""
        self._history.clear()
        self._cooldown_until = 0.0


class ConsoleConfirmationHook(ConfirmationHook):
    """Interactive console prompt hook for standard terminal environments.

    Prompts the user via stdin and reads the answer asynchronously under a timeout deadline.
    Unconditionally defaults to False if the deadline expires, input stream errors, or
    non-interactive stdin is detected. Scrubbing is applied to displayed parameters.
    """

    def __init__(
        self,
        timeout_seconds: float | None = None,
        input_fn: Callable[[str], str] | None = None,
        *,
        tracker: ConfirmationTracker | None = None,
        audit_logger: SecurityAuditLogger | None = None,
        scrubber: SecretScrubber | None = None,
        check_stdin: bool | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self._input_fn = input_fn or input
        self._check_stdin = check_stdin if check_stdin is not None else (input_fn is None)
        self._tracker = tracker if tracker is not None else ConfirmationTracker()
        self._audit_logger = audit_logger or SecurityAuditLogger()
        self._scrubber = scrubber or get_default_scrubber()

    async def request_confirmation(self, request: ConfirmationRequest) -> bool:
        start_time = time.monotonic()
        effective_timeout = (
            self.timeout_seconds if self.timeout_seconds is not None else request.timeout_seconds
        )

        # 1. Non-interactive stdin check
        if self._check_stdin:
            stdin = sys.stdin
            if stdin is None or not getattr(stdin, "isatty", lambda: False)():
                self._audit_logger.log_decision(
                    tool_name=request.tool_name,
                    action="request_confirmation",
                    decision="DENIED",
                    duration_ms=0.0,
                    params=request.params,
                    extra={"reason": "non_interactive_stdin"},
                )
                return False

        # 2. Anti-flooding rate limit / cooldown check
        permitted = await self._tracker.acquire()
        if not permitted:
            self._audit_logger.log_decision(
                tool_name=request.tool_name,
                action="request_confirmation",
                decision="BLOCKED",
                duration_ms=0.0,
                params=request.params,
                extra={"reason": "confirmation_rate_limit_exceeded"},
            )
            return False

        # 3. Sanitize parameters before rendering prompt string
        scrubbed_params = self._scrubber.scrub_object(request.params)

        prompt = (
            f"\n[TOM SECURITY CONFIRMATION]\n"
            f"  Tool: {request.tool_name}\n"
            f"  Description: {request.description}\n"
            f"  Parameters: {scrubbed_params}\n"
            f"  Confirm execution? [y/N]: "
        )

        decision = "DENIED"
        try:
            user_input = await asyncio.wait_for(
                asyncio.to_thread(self._input_fn, prompt),
                timeout=effective_timeout,
            )
            clean = user_input.strip().lower()
            granted = clean in ("y", "yes")
            decision = "GRANTED" if granted else "DENIED"
            return granted
        except TimeoutError:
            decision = "TIMED_OUT"
            return False
        except SecurityError:
            raise
        except Exception:
            decision = "DENIED"
            return False
        finally:
            duration_ms = (time.monotonic() - start_time) * 1000.0
            self._audit_logger.log_decision(
                tool_name=request.tool_name,
                action="request_confirmation",
                decision=decision,
                duration_ms=duration_ms,
                params=request.params,
            )
