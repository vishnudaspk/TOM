"""Adversarial Command Injection Tests Across All 34 Canonical Tools (Iteration 11.5).

Verifies that shell metacharacters (;, |, &, &&, ||, backticks, $()) supplied as
input arguments to any of the 34 registered tools remain inert data and never
trigger subprocess or shell execution.

Subprocess boundaries (subprocess.Popen, subprocess.run, subprocess.call,
os.system, asyncio.create_subprocess_exec, asyncio.create_subprocess_shell)
are strictly instrumented to fail the test if any process invocation occurs.
"""

from __future__ import annotations

import asyncio
import os
import platform
import subprocess
from pathlib import Path
from typing import Any

import pytest
from tom.security.confirmation import AlwaysAllowConfirmationHook
from tom.security.permissions import PermissionDeniedError, PermissionEngine
from tom.tools.bootstrap import setup_default_tools
from tom.tools.executor import ToolExecutor
from tom.tools.registry import ToolRegistry

# Warm up platform.uname so Python stdlib caches internal OS identification
platform.uname()
_real_subprocess_run = subprocess.run

INJECTION_PAYLOADS = [
    "; echo INJECTED",
    "| dir",
    "& whoami",
    "&& calc.exe",
    "|| exit 1",
    "`id`",
    "$(whoami)",
]


def run_async(coro: Any) -> Any:
    return asyncio.run(coro)


class SubprocessExecutionAttempted(BaseException):
    """Raised when a forbidden subprocess boundary is invoked."""


@pytest.fixture(autouse=True)
def guard_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Instrument and guard all subprocess execution boundaries against invocation."""

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        # Permit Python's stdlib internal platform._syscmd_ver if invoked
        if args == ("ver",) and kwargs.get("shell") is True:
            return _real_subprocess_run(*args, **kwargs)
        raise SubprocessExecutionAttempted(
            f"Forbidden subprocess execution attempted with args: {args}, kwargs: {kwargs}"
        )

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "call", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", forbidden)


@pytest.fixture
def canonical_executor(tmp_path: Path) -> ToolExecutor:
    registry = ToolRegistry()
    setup_default_tools(registry)
    return ToolExecutor(
        registry=registry,
        permission_engine=PermissionEngine(),
        confirmation_hook=AlwaysAllowConfirmationHook(),
    )


# ---------------------------------------------------------------------------
# Account for All 34 Registered Tools
# ---------------------------------------------------------------------------


class TestAll34ToolsAccounted:
    """Verify that the test suite accounts for exactly all 34 registered tools."""

    def test_canonical_34_tools_present(self, canonical_executor: ToolExecutor) -> None:
        tools = canonical_executor.registry.list_tools()
        assert len(tools) == 34, f"Expected 34 registered tools, got {len(tools)}"


# ---------------------------------------------------------------------------
# Parameterized Command Injection Tests Across Tool Categories
# ---------------------------------------------------------------------------


class TestCommandInjectionSafety:
    """Verify that command injection payloads remain inert across tool categories."""

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_web_search_query_injection(
        self, canonical_executor: ToolExecutor, payload: str
    ) -> None:
        # Mock web search provider call
        tool_def = canonical_executor.registry.get("web.search")
        assert tool_def is not None

        # Execute web.search with shell injection payload as query
        result = run_async(
            canonical_executor.execute("web.search", {"query": f"test {payload}", "max_results": 5})
        )
        # Result should either be an error or result, but NEVER execute shell command
        assert result is not None

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_file_tools_path_injection(
        self, canonical_executor: ToolExecutor, payload: str, tmp_path: Path
    ) -> None:
        # Path injection in file tools (read_file, file_info)
        result = run_async(
            canonical_executor.execute("files.read_file", {"path": f"sample{payload}.txt"})
        )
        assert result is not None
        # Should fail cleanly or be blocked by security policy, never execute shell
        assert result.success is False

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_memory_tools_content_injection(
        self, canonical_executor: ToolExecutor, payload: str
    ) -> None:
        # Memory tools: key or content with injection payloads
        result = run_async(
            canonical_executor.execute(
                "memory.remember",
                {"key": f"key_{payload}", "content": f"data_{payload}", "category": "general"},
            )
        )
        assert result is not None

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_os_input_text_injection(self, canonical_executor: ToolExecutor, payload: str) -> None:
        # os.input.type_text with shell payload
        try:
            result = run_async(
                canonical_executor.execute("os.input.type_text", {"text": f"hello {payload}"})
            )
            assert result is not None
        except PermissionDeniedError:
            pass  # Blocked or confirmation denied is also safe

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_personal_tools_injection(self, canonical_executor: ToolExecutor, payload: str) -> None:
        # personal.note with shell payload
        try:
            result = run_async(
                canonical_executor.execute(
                    "personal.note", {"action": "create", "title": f"Note {payload}"}
                )
            )
            assert result is not None
        except PermissionDeniedError:
            pass

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_voice_announce_injection(self, canonical_executor: ToolExecutor, payload: str) -> None:
        # voice.announce with shell payload
        result = run_async(
            canonical_executor.execute("voice.announce", {"message": f"Alert {payload}"})
        )
        assert result is not None

    def test_all_34_tools_inert_execution_matrix(
        self, canonical_executor: ToolExecutor, tmp_path: Path
    ) -> None:
        """Run each of the 34 tools with safe inputs containing shell metacharacters."""
        tools = canonical_executor.registry.list_tools()
        payload = "; echo INERT && whoami | dir"

        for tool_def in tools:
            # Construct minimal test params respecting tool schemas
            name = tool_def.name
            params: dict[str, Any] = {}

            if "query" in (tool_def.input_schema.model_fields if tool_def.input_schema else {}):
                params["query"] = f"test {payload}"
            if "url" in (tool_def.input_schema.model_fields if tool_def.input_schema else {}):
                params["url"] = "https://example.com"
            if "path" in (tool_def.input_schema.model_fields if tool_def.input_schema else {}):
                params["path"] = f"safe_path_{payload}.txt"
            if "key" in (tool_def.input_schema.model_fields if tool_def.input_schema else {}):
                params["key"] = f"key_{payload}"
            if "text" in (tool_def.input_schema.model_fields if tool_def.input_schema else {}):
                params["text"] = f"text_{payload}"
            if "message" in (tool_def.input_schema.model_fields if tool_def.input_schema else {}):
                params["message"] = f"msg_{payload}"
            if "action" in (tool_def.input_schema.model_fields if tool_def.input_schema else {}):
                params["action"] = "retrieve"

            try:
                # Execution should never invoke forbidden subprocess boundaries
                run_async(canonical_executor.execute(name, params))
            except PermissionDeniedError:
                # Permission denial is an intended security rejection
                pass
            except Exception:
                # Any application-level error (e.g. offline endpoint, file not found) is acceptable,
                # as long as no subprocess was launched
                pass
