"""Phase 7 — Iteration 5: End-to-End Input Pipeline Integration Tests.

Exercises OS input tools through the full TOM security pipeline:
    ToolExecutor → PermissionEngine → ConfirmationHook → EngineClient (dry-run)

Safety contract:
- dry_run=True on all input tools: ZERO real cursor moves, clicks, typing, or hotkeys.
- Mocked EngineClient: no IPC to tom-engine.
- All physical desktop interactions are impossible in this test module.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from tom.schemas.config import ToolsConfig
from tom.security.confirmation import (
    AlwaysAllowConfirmationHook,
    AlwaysDenyConfirmationHook,
)
from tom.security.permissions import (
    ConfirmationDeniedError,
    ConfirmationRequiredError,
    PermissionDeniedError,
    PermissionEngine,
    PermissionLevel,
)
from tom.tools.bootstrap import setup_default_tools
from tom.tools.executor import ToolExecutor
from tom.tools.files import PathGuard
from tom.tools.registry import ToolRegistry


def run_async(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_pipeline(
    allow: bool = True,
    tmp_path: Path | None = None,
) -> tuple[ToolRegistry, ToolExecutor]:
    registry = ToolRegistry()
    guard = PathGuard(allowed_directories=[tmp_path or Path(".")])
    config = ToolsConfig(allowed_directories=[str(tmp_path or Path("."))])
    setup_default_tools(
        registry=registry,
        engine_client=None,  # no IPC; dry_run=True handles safe simulation
        path_guard=guard,
        config=config,
        dry_run_input=True,  # SAFE — never touches real desktop
        replace=True,
    )
    hook = AlwaysAllowConfirmationHook() if allow else AlwaysDenyConfirmationHook()
    executor = ToolExecutor(
        registry=registry,
        permission_engine=PermissionEngine(),
        confirmation_hook=hook,
    )
    return registry, executor


# ---------------------------------------------------------------------------
# A. Tool registration
# ---------------------------------------------------------------------------


class TestInputToolRegistration:
    """Input tools are registered with correct permission levels."""

    def test_click_registered_ask_user(self, tmp_path: Path) -> None:
        registry, _ = _make_pipeline(tmp_path=tmp_path)
        assert registry.get("os.input.click").permission_level == PermissionLevel.ASK_USER

    def test_type_text_registered_ask_user(self, tmp_path: Path) -> None:
        registry, _ = _make_pipeline(tmp_path=tmp_path)
        assert registry.get("os.input.type_text").permission_level == PermissionLevel.ASK_USER

    def test_hotkey_registered_ask_user(self, tmp_path: Path) -> None:
        registry, _ = _make_pipeline(tmp_path=tmp_path)
        assert registry.get("os.input.hotkey").permission_level == PermissionLevel.ASK_USER

    def test_get_cursor_pos_registered_safe(self, tmp_path: Path) -> None:
        registry, _ = _make_pipeline(tmp_path=tmp_path)
        assert registry.get("os.input.get_cursor_pos").permission_level == PermissionLevel.SAFE


# ---------------------------------------------------------------------------
# B. SAFE tool: get_cursor_pos executes without confirmation
# ---------------------------------------------------------------------------


class TestSafeInputTool:
    def test_get_cursor_pos_executes_without_confirmation(self, tmp_path: Path) -> None:
        """SAFE tool must run with AlwaysDeny hook — no confirmation needed."""
        registry = ToolRegistry()
        guard = PathGuard(allowed_directories=[tmp_path])
        config = ToolsConfig(allowed_directories=[str(tmp_path)])
        setup_default_tools(
            registry=registry,
            engine_client=None,
            path_guard=guard,
            config=config,
            dry_run_input=True,
            replace=True,
        )
        # Even with AlwaysDeny, SAFE tool runs fine
        executor = ToolExecutor(
            registry=registry,
            permission_engine=PermissionEngine(),
            confirmation_hook=AlwaysDenyConfirmationHook(),
        )
        result = run_async(executor.execute("os.input.get_cursor_pos", {}))
        assert result.success
        assert hasattr(result.data, "x")
        assert hasattr(result.data, "y")

    def test_get_cursor_pos_returns_simulated_in_dry_run(self, tmp_path: Path) -> None:
        _, executor = _make_pipeline(tmp_path=tmp_path)
        result = run_async(executor.execute("os.input.get_cursor_pos", {}))
        assert result.data.simulated is True


# ---------------------------------------------------------------------------
# C. ASK_USER tools: confirmed → execute; denied → reject
# ---------------------------------------------------------------------------


class TestAskUserInputTools:
    def test_click_confirmed_executes_dry_run(self, tmp_path: Path) -> None:
        _, executor = _make_pipeline(allow=True, tmp_path=tmp_path)
        result = run_async(executor.execute("os.input.click", {"x": 100, "y": 200}))
        assert result.success
        assert result.data.simulated is True  # dry_run=True, never real

    def test_click_denied_raises(self, tmp_path: Path) -> None:
        _, executor = _make_pipeline(allow=False, tmp_path=tmp_path)
        with pytest.raises(
            (ConfirmationRequiredError, ConfirmationDeniedError, PermissionDeniedError)
        ):
            run_async(executor.execute("os.input.click", {"x": 100, "y": 200}))

    def test_type_text_confirmed_executes(self, tmp_path: Path) -> None:
        _, executor = _make_pipeline(allow=True, tmp_path=tmp_path)
        result = run_async(executor.execute("os.input.type_text", {"text": "hello"}))
        assert result.success
        assert result.data.simulated is True

    def test_type_text_denied_raises(self, tmp_path: Path) -> None:
        _, executor = _make_pipeline(allow=False, tmp_path=tmp_path)
        with pytest.raises(
            (ConfirmationRequiredError, ConfirmationDeniedError, PermissionDeniedError)
        ):
            run_async(executor.execute("os.input.type_text", {"text": "hello"}))

    def test_hotkey_confirmed_executes(self, tmp_path: Path) -> None:
        _, executor = _make_pipeline(allow=True, tmp_path=tmp_path)
        result = run_async(executor.execute("os.input.hotkey", {"keys": ["ctrl", "c"]}))
        assert result.success
        assert result.data.simulated is True

    def test_hotkey_denied_raises(self, tmp_path: Path) -> None:
        _, executor = _make_pipeline(allow=False, tmp_path=tmp_path)
        with pytest.raises(
            (ConfirmationRequiredError, ConfirmationDeniedError, PermissionDeniedError)
        ):
            run_async(executor.execute("os.input.hotkey", {"keys": ["ctrl", "c"]}))


# ---------------------------------------------------------------------------
# D. Validation boundaries
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_click_negative_coordinates_rejected(self, tmp_path: Path) -> None:
        """Pydantic schema rejects negative coordinates; executor returns success=False."""
        _, executor = _make_pipeline(allow=True, tmp_path=tmp_path)
        result = run_async(executor.execute("os.input.click", {"x": -1, "y": 200}))
        assert not result.success
        assert result.error is not None

    def test_type_text_over_500_chars_rejected(self, tmp_path: Path) -> None:
        """Text longer than 500 characters: executor returns success=False."""
        _, executor = _make_pipeline(allow=True, tmp_path=tmp_path)
        result = run_async(executor.execute("os.input.type_text", {"text": "a" * 501}))
        assert not result.success

    def test_type_text_exactly_500_chars_accepted(self, tmp_path: Path) -> None:
        _, executor = _make_pipeline(allow=True, tmp_path=tmp_path)
        result = run_async(executor.execute("os.input.type_text", {"text": "a" * 500}))
        assert result.success

    def test_hotkey_empty_keys_rejected(self, tmp_path: Path) -> None:
        _, executor = _make_pipeline(allow=True, tmp_path=tmp_path)
        result = run_async(executor.execute("os.input.hotkey", {"keys": []}))
        assert not result.success


# ---------------------------------------------------------------------------
# E. Python never injects input directly
# ---------------------------------------------------------------------------


def test_no_pyautogui_import() -> None:
    """input.py must not import pyautogui, pywin32, or pynput."""
    import ast
    import pathlib

    src = pathlib.Path("python/tom/tools/input.py").read_text()
    tree = ast.parse(src)
    banned = {"pyautogui", "win32api", "win32con", "pynput"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else ([node.module] if node.module else [])
            )
            for name in names:
                root = (name or "").split(".")[0]
                assert root not in banned, f"Forbidden import: {name}"
