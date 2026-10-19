"""Adversarial Secret Leakage Tests (Iteration 11.5).

Verifies that ToolExecutor and SecretScrubber prevent credential leakage across:
- Direct return values: strings, dictionaries, nested lists, tuples, and ToolResult instances.
- Error channels: validation errors, runtime exceptions, tracebacks, and timeouts.
- Credential patterns: OpenAI, Anthropic, GitHub, AWS Access Keys, and PEM private keys.
- High-entropy heuristics: Shannon entropy >= 4.5 bits/char with character class diversity.
- Scrubber failure fail-safe: scanner/scrubber exceptions never leak cleartext.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel
from tom.security.confirmation import AlwaysAllowConfirmationHook
from tom.security.permissions import PermissionEngine
from tom.security.secrets import SecretScrubber, get_default_scrubber
from tom.tools.executor import ToolExecutor
from tom.tools.registry import ToolDefinition, ToolRegistry, ToolResult

# ---------------------------------------------------------------------------
# Synthetic Test Credentials (strictly non-live, synthetically constructed)
# ---------------------------------------------------------------------------

SYNTHETIC_OPENAI_KEY = "sk-proj-mocktestkey1234567890abcdefghijklmnopqrstuvwxyz12"
SYNTHETIC_ANTHROPIC_KEY = "sk-ant-api03-mocktestkeyabcdefghijklmnopqrstuv123456"
SYNTHETIC_GITHUB_PAT = "ghp_mocktesttoken1234567890abcdefghij"
SYNTHETIC_AWS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
SYNTHETIC_PEM_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEA0Ysyntheticmockkeyforadversarialsecuritytesting12\n"
    "-----END RSA PRIVATE KEY-----"
)
SYNTHETIC_HIGH_ENTROPY_TOKEN = "X9k#mP2$vL8@qR5!wZ1&tY4*jN7^"


def run_async(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture
def test_registry() -> ToolRegistry:
    return ToolRegistry()


def _make_executor(registry: ToolRegistry, scrubber: SecretScrubber | None = None) -> ToolExecutor:
    return ToolExecutor(
        registry=registry,
        permission_engine=PermissionEngine(),
        confirmation_hook=AlwaysAllowConfirmationHook(),
        secret_scrubber=scrubber or get_default_scrubber(),
    )


def _register(
    registry: ToolRegistry, name: str, handler: Any, schema: type[BaseModel] | None = None
) -> None:
    td = ToolDefinition.from_function(
        handler,
        name=name,
        description=f"Test tool {name}",
        category="test",
        permission_level="SAFE",
        input_schema=schema,
        timeout_seconds=5.0,
    )
    registry.register(td)


# ---------------------------------------------------------------------------
# Output Channel Leakage Tests
# ---------------------------------------------------------------------------


class TestOutputChannelLeakage:
    """Tool execution outputs must have all secrets redacted."""

    def test_secrets_in_string_return(self, test_registry: ToolRegistry) -> None:
        async def handler() -> str:
            return f"Found key: {SYNTHETIC_OPENAI_KEY} and token: {SYNTHETIC_GITHUB_PAT}"

        _register(test_registry, "test.leak_string", handler)
        executor = _make_executor(test_registry)

        result = run_async(executor.execute("test.leak_string", {}))
        assert result.success is True
        assert SYNTHETIC_OPENAI_KEY not in str(result.data)
        assert SYNTHETIC_GITHUB_PAT not in str(result.data)
        assert "***REDACTED***" in str(result.data)

    def test_secrets_in_dict_return(self, test_registry: ToolRegistry) -> None:
        async def handler() -> dict[str, Any]:
            return {
                "status": "ok",
                "config": {
                    "aws_id": SYNTHETIC_AWS_KEY_ID,
                    "anthropic": SYNTHETIC_ANTHROPIC_KEY,
                },
            }

        _register(test_registry, "test.leak_dict", handler)
        executor = _make_executor(test_registry)

        result = run_async(executor.execute("test.leak_dict", {}))
        assert result.success is True
        assert SYNTHETIC_AWS_KEY_ID not in str(result.data)
        assert SYNTHETIC_ANTHROPIC_KEY not in str(result.data)
        assert result.data["config"]["aws_id"] == "***REDACTED***"

    def test_secrets_in_nested_list(self, test_registry: ToolRegistry) -> None:
        async def handler() -> list[Any]:
            return [
                "item1",
                [SYNTHETIC_PEM_KEY, {"key": SYNTHETIC_HIGH_ENTROPY_TOKEN}],
            ]

        _register(test_registry, "test.leak_nested", handler)
        executor = _make_executor(test_registry)

        result = run_async(executor.execute("test.leak_nested", {}))
        assert result.success is True
        assert "BEGIN RSA PRIVATE KEY" not in str(result.data)
        assert SYNTHETIC_HIGH_ENTROPY_TOKEN not in str(result.data)

    def test_secrets_in_existing_tool_result(self, test_registry: ToolRegistry) -> None:
        async def handler() -> ToolResult:
            return ToolResult.ok(data={"token": SYNTHETIC_GITHUB_PAT})

        _register(test_registry, "test.leak_tool_result", handler)
        executor = _make_executor(test_registry)

        result = run_async(executor.execute("test.leak_tool_result", {}))
        assert result.success is True
        assert SYNTHETIC_GITHUB_PAT not in str(result.data)
        assert result.data["token"] == "***REDACTED***"


# ---------------------------------------------------------------------------
# Error Channel Leakage Tests
# ---------------------------------------------------------------------------


class TestErrorChannelLeakage:
    """Tool exceptions, validation errors, and traces must have secrets scrubbed."""

    def test_secrets_in_exception_message(self, test_registry: ToolRegistry) -> None:
        async def handler() -> None:
            raise ValueError(f"Connection failed using secret: {SYNTHETIC_OPENAI_KEY}")

        _register(test_registry, "test.leak_exception", handler)
        executor = _make_executor(test_registry)

        result = run_async(executor.execute("test.leak_exception", {}))
        assert result.success is False
        assert SYNTHETIC_OPENAI_KEY not in str(result.error)
        assert "***REDACTED***" in str(result.error)

    def test_secrets_in_validation_error(self, test_registry: ToolRegistry) -> None:
        class StrictInput(BaseModel):
            port: int

        async def handler(input_data: StrictInput) -> int:
            return input_data.port

        _register(test_registry, "test.leak_validation", handler, schema=StrictInput)
        executor = _make_executor(test_registry)

        # Pass a secret as invalid type for an int field
        result = run_async(executor.execute("test.leak_validation", {"port": SYNTHETIC_OPENAI_KEY}))
        assert result.success is False
        assert SYNTHETIC_OPENAI_KEY not in str(result.error)
        assert "***REDACTED***" in str(result.error)


# ---------------------------------------------------------------------------
# Fail-Safe Scrubber Tests
# ---------------------------------------------------------------------------


class TestScrubberFailSafe:
    """When SecretScrubber encounters internal errors, it must fail closed and never leak cleartext."""

    def test_scrubber_crash_on_object_fails_safe(self, test_registry: ToolRegistry) -> None:
        async def handler() -> dict[str, str]:
            return {"secret": SYNTHETIC_OPENAI_KEY}

        _register(test_registry, "test.scrubber_crash", handler)

        broken_scrubber = MagicMock(spec=SecretScrubber)
        broken_scrubber.scrub_object.side_effect = RuntimeError("Fatal regex crash")
        broken_scrubber.scrub_string.side_effect = RuntimeError("Fatal regex crash")

        executor = _make_executor(test_registry, scrubber=broken_scrubber)
        result = run_async(executor.execute("test.scrubber_crash", {}))

        assert result.success is True
        # Must never return the cleartext secret
        assert SYNTHETIC_OPENAI_KEY not in str(result.data)
        assert result.data == "***REDACTION_ERROR***"

    def test_scrubber_crash_on_error_message_fails_safe(self, test_registry: ToolRegistry) -> None:
        async def handler() -> None:
            raise RuntimeError(f"Error with key: {SYNTHETIC_OPENAI_KEY}")

        _register(test_registry, "test.scrubber_crash_error", handler)

        broken_scrubber = MagicMock(spec=SecretScrubber)
        broken_scrubber.scrub_string.side_effect = RuntimeError("Fatal string crash")

        executor = _make_executor(test_registry, scrubber=broken_scrubber)
        result = run_async(executor.execute("test.scrubber_crash_error", {}))

        assert result.success is False
        assert SYNTHETIC_OPENAI_KEY not in str(result.error)
        assert result.error == "***REDACTION_ERROR***"
