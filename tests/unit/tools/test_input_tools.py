"""Unit tests for TOM deterministic OS input automation tools.

Adheres to:
- Phase 7 Vision & OS Automation Specification (Iteration 4)
- Decision 032: Centralized Tool Invocation Safety
- os.input.click, os.input.type_text, os.input.hotkey require ASK_USER confirmation
- os.input.get_cursor_pos is SAFE
- Python never directly injects input: routes via EngineClient IPC
- 500-character limit and coordinate validation strictly enforced
- Offline dry-run and mock testing prevents real mouse/keyboard actuation
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError
from tom.security.confirmation import (
    AlwaysAllowConfirmationHook,
    AlwaysDenyConfirmationHook,
)
from tom.security.permissions import (
    PermissionDeniedError,
    PermissionEngine,
    PermissionLevel,
)
from tom.tools.executor import ToolExecutor
from tom.tools.input import (
    InputClickInput,
    InputClickOutput,
    InputGetCursorPosOutput,
    InputHotkeyInput,
    InputHotkeyOutput,
    InputTypeTextInput,
    InputTypeTextOutput,
    create_input_tools,
    register_input_tools,
)
from tom.tools.registry import ToolRegistry


def run_async(coro: Any) -> Any:
    """Helper to run async coroutines synchronously in pytest."""
    return asyncio.run(coro)


EXPECTED_INPUT_TOOLS = {
    "os.input.click",
    "os.input.type_text",
    "os.input.hotkey",
    "os.input.get_cursor_pos",
}


# ---------------------------------------------------------------------------
# Mock EngineClient for IPC Input Delegation
# ---------------------------------------------------------------------------


class MockEngineClient:
    """Deterministic mock for EngineClient input endpoints."""

    def __init__(self) -> None:
        self.clicks: list[dict[str, Any]] = []
        self.typed_texts: list[str] = []
        self.hotkeys: list[list[str]] = []
        self.cursor_pos = {"x": 640, "y": 480}

    async def mouse_click(
        self,
        x: int,
        y: int,
        button: str = "left",
        click_type: str = "single",
    ) -> dict[str, Any]:
        call = {"x": x, "y": y, "button": button, "click_type": click_type}
        self.clicks.append(call)
        return {"dispatched": True, **call}

    async def type_text(self, text: str) -> dict[str, Any]:
        self.typed_texts.append(text)
        return {"dispatched": True, "characters": len(text)}

    async def send_hotkey(self, keys: list[str]) -> dict[str, Any]:
        self.hotkeys.append(keys)
        return {"dispatched": True, "keys": keys}

    async def get_cursor_pos(self) -> dict[str, int]:
        return dict(self.cursor_pos)


# ---------------------------------------------------------------------------
# Test Registration & Permission Tiers
# ---------------------------------------------------------------------------


class TestInputToolRegistration:
    def test_create_input_tools_returns_four_definitions(self) -> None:
        tools = create_input_tools()
        assert len(tools) == 4
        names = {t.name for t in tools}
        assert names == EXPECTED_INPUT_TOOLS

    def test_register_input_tools_populates_registry(self) -> None:
        reg = ToolRegistry()
        defs = register_input_tools(registry=reg)
        assert len(defs) == 4
        assert {t.name for t in reg.list_tools(category="os.input")} == EXPECTED_INPUT_TOOLS

    def test_permission_tiers_are_correct(self) -> None:
        tools = {t.name: t for t in create_input_tools()}
        assert tools["os.input.get_cursor_pos"].permission_level == PermissionLevel.SAFE
        assert tools["os.input.click"].permission_level == PermissionLevel.ASK_USER
        assert tools["os.input.type_text"].permission_level == PermissionLevel.ASK_USER
        assert tools["os.input.hotkey"].permission_level == PermissionLevel.ASK_USER

    def test_all_input_tools_have_input_category(self) -> None:
        tools = create_input_tools()
        for t in tools:
            assert t.category == "os.input"


# ---------------------------------------------------------------------------
# Test Parameter Validation
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_click_coordinates_must_be_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            InputClickInput.model_validate({"x": -1, "y": 100})

        with pytest.raises(ValidationError):
            InputClickInput.model_validate({"x": 100, "y": -5})

    def test_type_text_enforces_500_char_limit(self) -> None:
        # Valid 500 characters
        valid = InputTypeTextInput.model_validate({"text": "a" * 500})
        assert len(valid.text) == 500

        # Invalid 501 characters
        with pytest.raises(ValidationError):
            InputTypeTextInput.model_validate({"text": "a" * 501})

    def test_type_text_rejects_empty_or_whitespace(self) -> None:
        with pytest.raises(ValidationError):
            InputTypeTextInput.model_validate({"text": ""})

        with pytest.raises(ValidationError):
            InputTypeTextInput.model_validate({"text": "   "})

    def test_hotkey_requires_keys(self) -> None:
        with pytest.raises(ValidationError):
            InputHotkeyInput.model_validate({"keys": []})

        valid = InputHotkeyInput.model_validate({"keys": ["ctrl", "c"]})
        assert valid.keys == ["ctrl", "c"]


# ---------------------------------------------------------------------------
# Test Execution Through ToolExecutor & Permission Enforcement
# ---------------------------------------------------------------------------


class TestInputToolExecution:
    def test_get_cursor_pos_executes_safely_without_confirmation(self) -> None:
        reg = ToolRegistry()
        mock_client = MockEngineClient()
        register_input_tools(registry=reg, client=mock_client)
        executor = ToolExecutor(registry=reg)

        result = run_async(executor.execute("os.input.get_cursor_pos", {}))
        assert result.success is True
        assert isinstance(result.data, InputGetCursorPosOutput)
        assert result.data.x == 640
        assert result.data.y == 480
        assert result.data.simulated is False

    def test_unconfirmed_click_requires_confirmation(self) -> None:
        reg = ToolRegistry()
        register_input_tools(registry=reg)
        engine = PermissionEngine()
        tool_def = reg.get("os.input.click")
        decision = engine.evaluate(tool_def, {"x": 100, "y": 200})
        assert decision.requires_confirmation is True
        assert decision.permission_level == PermissionLevel.ASK_USER

        executor = ToolExecutor(registry=reg, confirmation_hook=AlwaysDenyConfirmationHook())
        with pytest.raises(PermissionDeniedError) as exc_info:
            run_async(executor.execute("os.input.click", {"x": 100, "y": 200}))
        assert "confirmation denied" in str(exc_info.value).lower()

    def test_unconfirmed_type_text_requires_confirmation(self) -> None:
        reg = ToolRegistry()
        register_input_tools(registry=reg)
        engine = PermissionEngine()
        tool_def = reg.get("os.input.type_text")
        decision = engine.evaluate(tool_def, {"text": "Hello"})
        assert decision.requires_confirmation is True
        assert decision.permission_level == PermissionLevel.ASK_USER

        executor = ToolExecutor(registry=reg, confirmation_hook=AlwaysDenyConfirmationHook())
        with pytest.raises(PermissionDeniedError) as exc_info:
            run_async(executor.execute("os.input.type_text", {"text": "Hello"}))
        assert "confirmation denied" in str(exc_info.value).lower()

    def test_unconfirmed_hotkey_requires_confirmation(self) -> None:
        reg = ToolRegistry()
        register_input_tools(registry=reg)
        engine = PermissionEngine()
        tool_def = reg.get("os.input.hotkey")
        decision = engine.evaluate(tool_def, {"keys": ["ctrl", "s"]})
        assert decision.requires_confirmation is True
        assert decision.permission_level == PermissionLevel.ASK_USER

        executor = ToolExecutor(registry=reg, confirmation_hook=AlwaysDenyConfirmationHook())
        with pytest.raises(PermissionDeniedError) as exc_info:
            run_async(executor.execute("os.input.hotkey", {"keys": ["ctrl", "s"]}))
        assert "confirmation denied" in str(exc_info.value).lower()

    def test_denied_confirmation_blocks_execution(self) -> None:
        reg = ToolRegistry()
        mock_client = MockEngineClient()
        register_input_tools(registry=reg, client=mock_client)
        executor = ToolExecutor(registry=reg, confirmation_hook=AlwaysDenyConfirmationHook())

        with pytest.raises(PermissionDeniedError):
            run_async(executor.execute("os.input.click", {"x": 50, "y": 50}))
        assert len(mock_client.clicks) == 0  # No action dispatched

    def test_confirmed_click_delegates_to_client(self) -> None:
        reg = ToolRegistry()
        mock_client = MockEngineClient()
        register_input_tools(registry=reg, client=mock_client)
        executor = ToolExecutor(registry=reg, confirmation_hook=AlwaysAllowConfirmationHook())

        result = run_async(
            executor.execute("os.input.click", {"x": 300, "y": 400, "button": "left"})
        )
        assert result.success is True
        assert isinstance(result.data, InputClickOutput)
        assert result.data.x == 300
        assert result.data.y == 400
        assert len(mock_client.clicks) == 1
        assert mock_client.clicks[0]["x"] == 300
        assert mock_client.clicks[0]["y"] == 400

    def test_confirmed_type_text_delegates_to_client(self) -> None:
        reg = ToolRegistry()
        mock_client = MockEngineClient()
        register_input_tools(registry=reg, client=mock_client)
        executor = ToolExecutor(registry=reg, confirmation_hook=AlwaysAllowConfirmationHook())

        result = run_async(executor.execute("os.input.type_text", {"text": "automated input"}))
        assert result.success is True
        assert isinstance(result.data, InputTypeTextOutput)
        assert result.data.characters_typed == 15
        assert len(mock_client.typed_texts) == 1
        assert mock_client.typed_texts[0] == "automated input"

    def test_confirmed_hotkey_delegates_to_client(self) -> None:
        reg = ToolRegistry()
        mock_client = MockEngineClient()
        register_input_tools(registry=reg, client=mock_client)
        executor = ToolExecutor(registry=reg, confirmation_hook=AlwaysAllowConfirmationHook())

        result = run_async(executor.execute("os.input.hotkey", {"keys": ["ctrl", "alt", "del"]}))
        assert result.success is True
        assert isinstance(result.data, InputHotkeyOutput)
        assert result.data.keys == ["ctrl", "alt", "del"]
        assert len(mock_client.hotkeys) == 1

    def test_dry_run_mode_does_not_call_client(self) -> None:
        reg = ToolRegistry()
        mock_client = MockEngineClient()
        register_input_tools(registry=reg, client=mock_client, dry_run=True)
        executor = ToolExecutor(registry=reg, confirmation_hook=AlwaysAllowConfirmationHook())

        result = run_async(executor.execute("os.input.click", {"x": 10, "y": 20}))
        assert result.success is True
        assert isinstance(result.data, InputClickOutput)
        assert result.data.simulated is True
        assert len(mock_client.clicks) == 0  # No IPC call made!

    def test_blocked_command_in_type_text_is_rejected(self) -> None:
        reg = ToolRegistry()
        register_input_tools(registry=reg)
        engine = PermissionEngine()
        executor = ToolExecutor(
            registry=reg,
            permission_engine=engine,
            confirmation_hook=AlwaysAllowConfirmationHook(),
        )

        # Dangerous command substring must be blocked unconditionally
        with pytest.raises(PermissionDeniedError):
            run_async(executor.execute("os.input.type_text", {"text": "format c: /q"}))
