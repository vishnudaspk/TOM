"""TOM Security Subsystem — Centralized Permission Engine and Decision Models.

This module provides the authoritative 3-tier security classification engine for
deterministic tools in TOM. Permission checks occur strictly prior to execution.
Tools never evaluate their own safety, and permissions are completely decoupled from
LLMs and agent frameworks.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from tom.schemas.config import PermissionsConfig


class PermissionLevel(StrEnum):
    """Authoritative 3-tier security classification for deterministic tools.

    SAFE: Read-only, informational, non-destructive -> Auto-executed.
    ASK_USER: State-modifying, file modification, process kill -> Requires explicit confirmation.
    BLOCK: Arbitrary shell, disk formatting, credential extraction -> Always prohibited.
    """

    SAFE = "SAFE"
    ASK_USER = "ASK_USER"
    BLOCK = "BLOCK"

    @classmethod
    def from_str(cls, value: str) -> PermissionLevel:
        """Parse string to PermissionLevel, normalizing aliases."""
        clean = value.strip().upper()
        if clean == "ASK":
            return cls.ASK_USER
        return cls(clean)


if TYPE_CHECKING:
    from tom.tools.registry import ToolDefinition


# ---------------------------------------------------------------------------
# Security Exceptions
# ---------------------------------------------------------------------------


class SecurityError(Exception):
    """Base exception for all security and permission violations."""


class PermissionDeniedError(SecurityError):
    """Raised when an operation is blocked by security policy."""


class ConfirmationRequiredError(SecurityError):
    """Raised when an ASK_USER operation is attempted without user confirmation."""


class ConfirmationTimeoutError(SecurityError):
    """Raised when confirmation prompt expires without response."""


class ConfirmationDeniedError(SecurityError):
    """Raised when user explicitly denies confirmation for an ASK_USER operation."""


# ---------------------------------------------------------------------------
# Decision Model
# ---------------------------------------------------------------------------


class PermissionDecision(BaseModel):
    """Authoritative structured evaluation outcome from the PermissionEngine."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    allowed: bool = Field(
        description="Whether tool execution is permitted to proceed (subject to confirmation if required)",
    )
    requires_confirmation: bool = Field(
        default=False,
        description="Whether execution requires explicit interactive user confirmation before proceeding",
    )
    reason: str = Field(
        default="",
        description="Human-readable explanation of why this decision was reached",
    )
    permission_level: PermissionLevel = Field(
        default=PermissionLevel.SAFE,
        description="Effective permission level applied to this decision",
    )

    @classmethod
    def allow(
        cls,
        level: PermissionLevel = PermissionLevel.SAFE,
        reason: str = "Execution permitted by security policy",
    ) -> PermissionDecision:
        """Create an allowed decision that does not require interactive confirmation."""
        return cls(
            allowed=True,
            requires_confirmation=False,
            reason=reason,
            permission_level=level,
        )

    @classmethod
    def ask_user(
        cls,
        reason: str = "Explicit user confirmation required",
        level: PermissionLevel = PermissionLevel.ASK_USER,
    ) -> PermissionDecision:
        """Create an allowed decision that requires interactive user confirmation."""
        return cls(
            allowed=True,
            requires_confirmation=True,
            reason=reason,
            permission_level=level,
        )

    @classmethod
    def deny(
        cls,
        reason: str = "Execution permanently prohibited by security policy",
        level: PermissionLevel = PermissionLevel.BLOCK,
    ) -> PermissionDecision:
        """Create an unconditionally denied decision."""
        return cls(
            allowed=False,
            requires_confirmation=False,
            reason=reason,
            permission_level=level,
        )


# ---------------------------------------------------------------------------
# Central Permission Engine
# ---------------------------------------------------------------------------


class PermissionEngine:
    """Authoritative pre-execution security policy evaluator for deterministic tools.

    Evaluates:
    1. Dangerous blocked commands and strings inside input parameters.
    2. Tool-specific policy overrides.
    3. Tool declared permission tiers (SAFE, ASK_USER, BLOCK).
    4. Configuration-level confirmation requirements.
    """

    def __init__(
        self,
        config: PermissionsConfig | None = None,
        policy_overrides: dict[str, PermissionLevel] | None = None,
    ) -> None:
        self.config = config or PermissionsConfig()
        self._overrides: dict[str, PermissionLevel] = dict(policy_overrides or {})

    def is_blocked_command(self, text: str) -> bool:
        """Check whether a string contains any blocked command substrings."""
        text_lower = text.lower()
        for blocked in self.config.blocked_commands:
            if blocked.lower() in text_lower:
                return True
        return False

    def find_blocked_command(self, payload: Any) -> str | None:
        """Recursively scan an argument structure for blocked command substrings."""
        if isinstance(payload, str):
            payload_lower = payload.lower()
            for blocked in self.config.blocked_commands:
                if blocked.lower() in payload_lower:
                    return blocked
        elif isinstance(payload, dict):
            for v in payload.values():
                found = self.find_blocked_command(v)
                if found is not None:
                    return found
        elif isinstance(payload, (list, tuple, set)):
            for item in payload:
                found = self.find_blocked_command(item)
                if found is not None:
                    return found
        return None

    def evaluate(
        self,
        tool: ToolDefinition | str,
        params: dict[str, Any] | None = None,
        default_level: PermissionLevel | str = PermissionLevel.SAFE,
    ) -> PermissionDecision:
        """Evaluate whether a tool invocation is permitted, requires confirmation, or is blocked.

        Args:
            tool: A ToolDefinition instance or tool name string.
            params: Dictionary of arguments to be passed to the tool.
            default_level: Default permission level to apply if tool is a string.

        Returns:
            A structured PermissionDecision.
        """
        # 1. Resolve tool name and base permission level
        if hasattr(tool, "name") and hasattr(tool, "permission_level"):
            tool_name = tool.name
            base_level = tool.permission_level
        elif isinstance(tool, str):
            tool_name = tool.strip()
            if not tool_name:
                return PermissionDecision.deny(
                    reason="Tool name cannot be empty or whitespace",
                    level=PermissionLevel.BLOCK,
                )
            base_level = (
                PermissionLevel.from_str(default_level)
                if isinstance(default_level, str)
                else default_level
            )
        else:
            return PermissionDecision.deny(
                reason=f"Expected ToolDefinition or str, got {type(tool).__name__}",
                level=PermissionLevel.BLOCK,
            )

        # 2. Inspect params for blocked commands/substrings (Unconditional BLOCK)
        if params:
            blocked_match = self.find_blocked_command(params)
            if blocked_match:
                return PermissionDecision.deny(
                    reason=f"Operation contains blocked command substring: '{blocked_match}'",
                    level=PermissionLevel.BLOCK,
                )

        # 3. Check administrative policy overrides
        if tool_name in self._overrides:
            effective_level = self._overrides[tool_name]
        else:
            effective_level = base_level

        # 4. Evaluate effective permission level
        if effective_level == PermissionLevel.BLOCK:
            return PermissionDecision.deny(
                reason=f"Tool '{tool_name}' is permanently prohibited by security policy",
                level=PermissionLevel.BLOCK,
            )

        if effective_level == PermissionLevel.ASK_USER:
            if self.config.require_confirmation_for_ask:
                return PermissionDecision.ask_user(
                    reason=f"Tool '{tool_name}' requires explicit user confirmation prior to execution",
                    level=PermissionLevel.ASK_USER,
                )
            return PermissionDecision.allow(
                level=PermissionLevel.ASK_USER,
                reason=f"Tool '{tool_name}' is classified as ASK_USER but auto-confirmed by configuration",
            )

        if effective_level == PermissionLevel.SAFE:
            return PermissionDecision.allow(
                level=PermissionLevel.SAFE,
                reason=f"Tool '{tool_name}' is safe for automatic execution",
            )

        return PermissionDecision.deny(
            reason=f"Unrecognized permission level '{effective_level}' for tool '{tool_name}'",
            level=PermissionLevel.BLOCK,
        )

    def set_override(self, tool_name: str, level: PermissionLevel | str) -> None:
        """Set or update a policy override for a specific tool."""
        resolved = PermissionLevel.from_str(level) if isinstance(level, str) else level
        self._overrides[tool_name] = resolved

    def remove_override(self, tool_name: str) -> None:
        """Remove a policy override for a specific tool."""
        self._overrides.pop(tool_name, None)

    def get_override(self, tool_name: str) -> PermissionLevel | None:
        """Get the current policy override for a tool, if any."""
        return self._overrides.get(tool_name)

    def clear_overrides(self) -> None:
        """Remove all administrative policy overrides."""
        self._overrides.clear()
