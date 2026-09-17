"""Unit tests for TOM Security Subsystem, PermissionEngine, and ConfirmationHooks."""

import asyncio
import time
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError
from tom.schemas.config import PermissionsConfig
from tom.security.confirmation import (
    AlwaysAllowConfirmationHook,
    AlwaysDenyConfirmationHook,
    CallbackConfirmationHook,
    ConfirmationRequest,
    ConsoleConfirmationHook,
)
from tom.security.permissions import (
    ConfirmationDeniedError,
    ConfirmationRequiredError,
    ConfirmationTimeoutError,
    PermissionDecision,
    PermissionDeniedError,
    PermissionEngine,
    PermissionLevel,
    SecurityError,
)
from tom.tools.registry import ToolDefinition


def run_async(coro: Any) -> Any:
    """Helper to run async coroutines in tests without pytest-asyncio marker conflicts."""
    return asyncio.run(coro)


class DummyInput(BaseModel):
    command: str = ""


# ---------------------------------------------------------------------------
# Test Decision Models and Exceptions
# ---------------------------------------------------------------------------


class TestPermissionDecision:
    """Test PermissionDecision structured outcome model."""

    def test_direct_creation(self) -> None:
        dec = PermissionDecision(
            allowed=True,
            requires_confirmation=False,
            reason="Safe query",
            permission_level=PermissionLevel.SAFE,
        )
        assert dec.allowed is True
        assert dec.requires_confirmation is False
        assert dec.reason == "Safe query"
        assert dec.permission_level is PermissionLevel.SAFE

    def test_allow_factory(self) -> None:
        dec = PermissionDecision.allow(reason="Allowed")
        assert dec.allowed is True
        assert dec.requires_confirmation is False
        assert dec.permission_level is PermissionLevel.SAFE
        assert dec.reason == "Allowed"

    def test_ask_user_factory(self) -> None:
        dec = PermissionDecision.ask_user(reason="Please confirm")
        assert dec.allowed is True
        assert dec.requires_confirmation is True
        assert dec.permission_level is PermissionLevel.ASK_USER
        assert dec.reason == "Please confirm"

    def test_deny_factory(self) -> None:
        dec = PermissionDecision.deny(reason="Blocked by admin")
        assert dec.allowed is False
        assert dec.requires_confirmation is False
        assert dec.permission_level is PermissionLevel.BLOCK
        assert dec.reason == "Blocked by admin"

    def test_serialization(self) -> None:
        dec = PermissionDecision.allow()
        dumped = dec.model_dump()
        assert dumped["allowed"] is True
        assert dumped["permission_level"] == "SAFE"

        json_str = dec.model_dump_json()
        assert '"allowed":true' in json_str
        assert '"permission_level":"SAFE"' in json_str

    def test_exception_hierarchy(self) -> None:
        assert issubclass(PermissionDeniedError, SecurityError)
        assert issubclass(ConfirmationRequiredError, SecurityError)
        assert issubclass(ConfirmationTimeoutError, SecurityError)
        assert issubclass(ConfirmationDeniedError, SecurityError)


# ---------------------------------------------------------------------------
# Test PermissionEngine
# ---------------------------------------------------------------------------


class TestPermissionEngine:
    """Test authoritative 3-tier security policy evaluator."""

    @pytest.fixture
    def engine(self) -> PermissionEngine:
        return PermissionEngine()

    @pytest.fixture
    def safe_tool(self) -> ToolDefinition:
        return ToolDefinition(
            name="system.cpu_info",
            description="Query CPU",
            category="system",
            input_schema=DummyInput,
            permission_level=PermissionLevel.SAFE,
        )

    @pytest.fixture
    def ask_tool(self) -> ToolDefinition:
        return ToolDefinition(
            name="files.delete_file",
            description="Delete a file",
            category="files",
            input_schema=DummyInput,
            permission_level=PermissionLevel.ASK_USER,
        )

    @pytest.fixture
    def block_tool(self) -> ToolDefinition:
        return ToolDefinition(
            name="shell.exec",
            description="Run arbitrary shell",
            category="shell",
            input_schema=DummyInput,
            permission_level=PermissionLevel.BLOCK,
        )

    def test_evaluate_safe_tool(self, engine: PermissionEngine, safe_tool: ToolDefinition) -> None:
        decision = engine.evaluate(safe_tool)
        assert decision.allowed is True
        assert decision.requires_confirmation is False
        assert decision.permission_level is PermissionLevel.SAFE

    def test_evaluate_ask_user_tool(
        self, engine: PermissionEngine, ask_tool: ToolDefinition
    ) -> None:
        decision = engine.evaluate(ask_tool)
        assert decision.allowed is True
        assert decision.requires_confirmation is True
        assert decision.permission_level is PermissionLevel.ASK_USER

    def test_evaluate_ask_user_when_confirmation_disabled(self, ask_tool: ToolDefinition) -> None:
        config = PermissionsConfig(require_confirmation_for_ask=False)
        engine = PermissionEngine(config=config)
        decision = engine.evaluate(ask_tool)
        assert decision.allowed is True
        assert decision.requires_confirmation is False
        assert decision.permission_level is PermissionLevel.ASK_USER

    def test_evaluate_block_tool(
        self, engine: PermissionEngine, block_tool: ToolDefinition
    ) -> None:
        decision = engine.evaluate(block_tool)
        assert decision.allowed is False
        assert decision.requires_confirmation is False
        assert decision.permission_level is PermissionLevel.BLOCK
        assert "permanently prohibited" in decision.reason

    def test_evaluate_by_string_tool_name(self, engine: PermissionEngine) -> None:
        safe_dec = engine.evaluate("system.ping", default_level=PermissionLevel.SAFE)
        assert safe_dec.allowed is True
        assert safe_dec.requires_confirmation is False

        ask_dec = engine.evaluate("system.reboot", default_level=PermissionLevel.ASK_USER)
        assert ask_dec.allowed is True
        assert ask_dec.requires_confirmation is True

        block_dec = engine.evaluate("shell.cmd", default_level=PermissionLevel.BLOCK)
        assert block_dec.allowed is False
        assert block_dec.permission_level is PermissionLevel.BLOCK

    def test_empty_or_whitespace_tool_name_rejected(self, engine: PermissionEngine) -> None:
        dec = engine.evaluate("   ")
        assert dec.allowed is False
        assert dec.permission_level is PermissionLevel.BLOCK
        assert "cannot be empty" in dec.reason

    def test_invalid_tool_object_rejected(self, engine: PermissionEngine) -> None:
        dec = engine.evaluate(12345)  # type: ignore[arg-type]
        assert dec.allowed is False
        assert dec.permission_level is PermissionLevel.BLOCK

    def test_blocked_command_in_params_flat_dict(
        self, engine: PermissionEngine, safe_tool: ToolDefinition
    ) -> None:
        params = {"command": "rmdir /s /q test"}
        decision = engine.evaluate(safe_tool, params=params)
        assert decision.allowed is False
        assert decision.permission_level is PermissionLevel.BLOCK
        assert "rmdir /s" in decision.reason

    def test_blocked_command_case_insensitive_and_nested(
        self, engine: PermissionEngine, safe_tool: ToolDefinition
    ) -> None:
        params = {
            "options": {
                "script": "echo hello && FoRmAt D: /fs:ntfs",
            }
        }
        decision = engine.evaluate(safe_tool, params=params)
        assert decision.allowed is False
        assert decision.permission_level is PermissionLevel.BLOCK
        assert "format" in decision.reason

    def test_blocked_command_in_list_parameter(
        self, engine: PermissionEngine, safe_tool: ToolDefinition
    ) -> None:
        params = {"actions": ["status", "diskpart clean", "exit"]}
        decision = engine.evaluate(safe_tool, params=params)
        assert decision.allowed is False
        assert decision.permission_level is PermissionLevel.BLOCK
        assert "diskpart" in decision.reason

    def test_other_default_blocked_commands(
        self, engine: PermissionEngine, safe_tool: ToolDefinition
    ) -> None:
        for cmd in ["reg delete", "shutdown", "drop database"]:
            decision = engine.evaluate(safe_tool, params={"arg": f"run {cmd} now"})
            assert decision.allowed is False
            assert decision.permission_level is PermissionLevel.BLOCK

    def test_is_blocked_command_helper(self, engine: PermissionEngine) -> None:
        assert engine.is_blocked_command("rmdir /s") is True
        assert engine.is_blocked_command("DIR C:") is False
        assert engine.is_blocked_command("DROP DATABASE users") is True

    def test_policy_override_elevates_safe_to_block(
        self, engine: PermissionEngine, safe_tool: ToolDefinition
    ) -> None:
        engine.set_override(safe_tool.name, PermissionLevel.BLOCK)
        assert engine.get_override(safe_tool.name) is PermissionLevel.BLOCK

        decision = engine.evaluate(safe_tool)
        assert decision.allowed is False
        assert decision.permission_level is PermissionLevel.BLOCK

    def test_policy_override_downgrades_ask_to_safe(
        self, engine: PermissionEngine, ask_tool: ToolDefinition
    ) -> None:
        engine.set_override(ask_tool.name, PermissionLevel.SAFE)
        decision = engine.evaluate(ask_tool)
        assert decision.allowed is True
        assert decision.requires_confirmation is False
        assert decision.permission_level is PermissionLevel.SAFE

    def test_remove_override(self, engine: PermissionEngine, ask_tool: ToolDefinition) -> None:
        engine.set_override(ask_tool.name, PermissionLevel.SAFE)
        assert engine.evaluate(ask_tool).requires_confirmation is False

        engine.remove_override(ask_tool.name)
        assert engine.get_override(ask_tool.name) is None
        assert engine.evaluate(ask_tool).requires_confirmation is True

    def test_clear_overrides(
        self, engine: PermissionEngine, safe_tool: ToolDefinition, ask_tool: ToolDefinition
    ) -> None:
        engine.set_override(safe_tool.name, PermissionLevel.BLOCK)
        engine.set_override(ask_tool.name, PermissionLevel.SAFE)
        engine.clear_overrides()

        assert engine.evaluate(safe_tool).allowed is True
        assert engine.evaluate(ask_tool).requires_confirmation is True


# ---------------------------------------------------------------------------
# Test Confirmation Hooks
# ---------------------------------------------------------------------------


class TestConfirmationHooks:
    """Test interactive confirmation hook abstractions and concrete implementations."""

    @pytest.fixture
    def req(self) -> ConfirmationRequest:
        return ConfirmationRequest(
            tool_name="files.delete",
            description="Delete file",
            params={"path": "temp.txt"},
            timeout_seconds=5.0,
        )

    def test_confirmation_request_validation(self) -> None:
        with pytest.raises(ValidationError):
            ConfirmationRequest(tool_name="", timeout_seconds=5.0)

        with pytest.raises(ValidationError):
            ConfirmationRequest(tool_name="files.delete", timeout_seconds=-1.0)

    def test_always_allow_hook(self, req: ConfirmationRequest) -> None:
        hook = AlwaysAllowConfirmationHook()
        assert run_async(hook.request_confirmation(req)) is True

    def test_always_deny_hook(self, req: ConfirmationRequest) -> None:
        hook = AlwaysDenyConfirmationHook()
        assert run_async(hook.request_confirmation(req)) is False

    def test_callback_hook_sync_callable(self, req: ConfirmationRequest) -> None:
        called = False

        def on_confirm(r: ConfirmationRequest) -> bool:
            nonlocal called
            called = True
            return r.tool_name == "files.delete"

        hook = CallbackConfirmationHook(callback=on_confirm)
        res = run_async(hook.request_confirmation(req))
        assert res is True
        assert called is True

    def test_callback_hook_async_callable(self, req: ConfirmationRequest) -> None:
        async def on_confirm(r: ConfirmationRequest) -> bool:
            await asyncio.sleep(0.01)
            return True

        hook = CallbackConfirmationHook(callback=on_confirm)
        assert run_async(hook.request_confirmation(req)) is True

    def test_callback_hook_timeout_defaults_to_denial(self, req: ConfirmationRequest) -> None:
        async def hanging_callback(_r: ConfirmationRequest) -> bool:
            await asyncio.sleep(1.0)
            return True

        hook = CallbackConfirmationHook(callback=hanging_callback, timeout_seconds=0.05)
        start = time.monotonic()
        res = run_async(hook.request_confirmation(req))
        duration = time.monotonic() - start

        assert res is False  # Safe failure on timeout
        assert duration < 0.5

    def test_callback_hook_exception_defaults_to_denial(self, req: ConfirmationRequest) -> None:
        def error_callback(_r: ConfirmationRequest) -> bool:
            raise RuntimeError("UI service unavailable")

        hook = CallbackConfirmationHook(callback=error_callback)
        assert run_async(hook.request_confirmation(req)) is False

    def test_console_hook_affirmative_inputs(self, req: ConfirmationRequest) -> None:
        for user_text in ["y", "Y", "yes", "YES", "  y  "]:
            hook = ConsoleConfirmationHook(input_fn=lambda _prompt, t=user_text: t)
            assert run_async(hook.request_confirmation(req)) is True

    def test_console_hook_negative_inputs(self, req: ConfirmationRequest) -> None:
        for user_text in ["n", "no", "", "cancel", "quit"]:
            hook = ConsoleConfirmationHook(input_fn=lambda _prompt, t=user_text: t)
            assert run_async(hook.request_confirmation(req)) is False

    def test_console_hook_timeout_defaults_to_denial(self, req: ConfirmationRequest) -> None:
        def hanging_input(_prompt: str) -> str:
            time.sleep(1.0)
            return "y"

        hook = ConsoleConfirmationHook(timeout_seconds=0.05, input_fn=hanging_input)
        assert run_async(hook.request_confirmation(req)) is False

    def test_console_hook_exception_defaults_to_denial(self, req: ConfirmationRequest) -> None:
        def error_input(_prompt: str) -> str:
            raise EOFError("stdin closed")

        hook = ConsoleConfirmationHook(input_fn=error_input)
        assert run_async(hook.request_confirmation(req)) is False
