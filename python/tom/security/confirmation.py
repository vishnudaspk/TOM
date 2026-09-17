"""TOM Security Subsystem — Confirmation Hook Interface and Concrete Implementations.

This module provides the interactive user confirmation interface required before executing
any tool classified as ASK_USER. Implementations remain completely decoupled from CLI/UI/LLM
runtimes and default to denial (False) on timeout or failure.
"""

from __future__ import annotations

import asyncio
import inspect
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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


class ConsoleConfirmationHook(ConfirmationHook):
    """Interactive console prompt hook for standard terminal environments.

    Prompts the user via stdin and reads the answer asynchronously under a timeout deadline.
    Unconditionally defaults to False if the deadline expires or input stream errors.
    """

    def __init__(
        self,
        timeout_seconds: float | None = None,
        input_fn: Callable[[str], str] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self._input_fn = input_fn or input

    async def request_confirmation(self, request: ConfirmationRequest) -> bool:
        effective_timeout = (
            self.timeout_seconds if self.timeout_seconds is not None else request.timeout_seconds
        )

        prompt = (
            f"\n[TOM SECURITY CONFIRMATION]\n"
            f"  Tool: {request.tool_name}\n"
            f"  Description: {request.description}\n"
            f"  Parameters: {request.params}\n"
            f"  Confirm execution? [y/N]: "
        )

        try:
            user_input = await asyncio.wait_for(
                asyncio.to_thread(self._input_fn, prompt),
                timeout=effective_timeout,
            )
            clean = user_input.strip().lower()
            return clean in ("y", "yes")
        except (TimeoutError, Exception):
            # Safe failure state: timeout or read error defaults to denial
            return False
