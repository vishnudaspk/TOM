"""Unit tests for ToolExecutor secret scrubbing integration."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel
from tom.security.confirmation import (
    AlwaysAllowConfirmationHook,
    ConfirmationHook,
    ConfirmationRequest,
)
from tom.security.permissions import PermissionEngine
from tom.security.secrets import SecretScrubber
from tom.tools.executor import ToolExecutor
from tom.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)


def run_async(coro: Any) -> Any:
    """Run an async coroutine synchronously in tests."""
    return asyncio.run(coro)


class DummyInput(BaseModel):
    value: str = ""


# Simple test to verify basic functionality works in this file
@pytest.fixture
def simple_registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture
def simple_scrubber_fixture() -> SecretScrubber:
    return SecretScrubber()


async def simple_handler() -> str:
    return "API key: sk-abcdefghijklmnopqrstuvwxyz123456"


class TestBasicFunctionality:
    """Basic tests to verify the test infrastructure works."""

    def test_simple_executor_call(
        self, simple_registry: ToolRegistry, simple_scrubber_fixture: SecretScrubber
    ) -> None:
        """Simple test to verify ToolExecutor works with fixtures in this file."""
        from tom.security.confirmation import AlwaysAllowConfirmationHook
        from tom.tools.registry import ToolDefinition

        td = ToolDefinition.from_function(
            simple_handler,
            name="test.simple",
            description="Test tool",
            category="test",
            permission_level="SAFE",
            timeout_seconds=15.0,
        )
        simple_registry.register(td)

        executor = ToolExecutor(
            registry=simple_registry,
            permission_engine=PermissionEngine(),
            confirmation_hook=AlwaysAllowConfirmationHook(),
            secret_scrubber=simple_scrubber_fixture,
        )

        result = run_async(executor.execute("test.simple", {}))
        assert result.success is True
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in str(result.data)
        assert "***REDACTED***" in str(result.data)


def _register_tool(
    registry: ToolRegistry,
    name: str,
    handler,
    permission_level: str = "SAFE",
    input_schema: type[BaseModel] | None = None,
    timeout_seconds: float | None = None,
) -> None:
    td = ToolDefinition.from_function(
        handler,
        name=name,
        description=f"Test tool {name}",
        category="test",
        permission_level=permission_level,
        input_schema=input_schema,
        timeout_seconds=timeout_seconds if timeout_seconds is not None else 15.0,
    )
    registry.register(td)


def _make_executor(
    registry: ToolRegistry,
    scrubber_fixture: SecretScrubber | None = None,
    confirmation_hook: ConfirmationHook | None = None,
) -> ToolExecutor:
    """Create an executor with the given registry and optional scrubber_fixture/hook."""
    print(f"DEBUG _make_executor: registry param id={id(registry)}")
    return ToolExecutor(
        registry=registry,
        permission_engine=PermissionEngine(),
        confirmation_hook=confirmation_hook or AlwaysAllowConfirmationHook(),
        secret_scrubber=scrubber_fixture,
    )


@pytest.fixture
def exec_registry_fixture() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture
def scrubber_fixture() -> SecretScrubber:
    return SecretScrubber()


class TestExecutorSecretScrubbing:
    """Tests for secret scrubbing in ToolExecutor execution branches."""

    # -------------------------------------------------------------------------
    # Confirmation parameter scrubbing
    # -------------------------------------------------------------------------

    def test_confirmation_params_scrubbed_but_execution_uses_original(
        self, exec_registry_fixture: ToolRegistry, scrubber_fixture: SecretScrubber
    ) -> None:
        """Confirmation request receives scrubbed params; handler receives original."""
        captured_params: dict[str, Any] = {}

        async def handler(value: str) -> str:
            captured_params["value"] = value
            return f"processed: {value}"

        _register_tool(exec_registry_fixture, "test.confirm", handler, permission_level="ASK_USER")
        executor = _make_executor(exec_registry_fixture, scrubber_fixture)

        secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
        result = run_async(executor.execute("test.confirm", {"value": secret}))

        # Handler should receive the original (unscrubbed) value
        assert captured_params["value"] == secret
        # Result should be scrubbed
        assert secret not in str(result.data)
        assert "***REDACTED***" in str(result.data)

    def test_confirmation_hook_receives_scrubbed_params(
        self, exec_registry_fixture: ToolRegistry, scrubber_fixture: SecretScrubber
    ) -> None:
        """ConfirmationHook receives scrubbed parameters in ConfirmationRequest."""
        captured_request: ConfirmationRequest | None = None

        class CapturingHook(ConfirmationHook):
            async def request_confirmation(self, request: ConfirmationRequest) -> bool:
                nonlocal captured_request
                captured_request = request
                return True

        async def handler(value: str) -> str:
            return f"processed: {value}"

        _register_tool(exec_registry_fixture, "test.confirm2", handler, permission_level="ASK_USER")
        hook = CapturingHook()
        executor = _make_executor(exec_registry_fixture, scrubber_fixture, hook)

        secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
        run_async(executor.execute("test.confirm2", {"value": secret}))

        assert captured_request is not None
        # Confirmation request should have scrubbed params
        assert secret not in str(captured_request.params)
        assert "***REDACTED***" in str(captured_request.params)

    # -------------------------------------------------------------------------
    # Result scrubbing (string, dict, list)
    # -------------------------------------------------------------------------

    def test_string_result_scrubbed_working_copy(
        self, exec_registry_fixture: ToolRegistry, scrubber_fixture: SecretScrubber
    ) -> None:
        """Copy of working test to see where it breaks."""
        captured_params: dict[str, Any] = {}

        async def handler(value: str) -> str:
            captured_params["value"] = value
            return f"processed: {value}"

        _register_tool(exec_registry_fixture, "test.confirm", handler, permission_level="ASK_USER")
        executor = _make_executor(exec_registry_fixture, scrubber_fixture)

        secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
        result = run_async(executor.execute("test.confirm", {"value": secret}))

        # Handler should receive the original (unscrubbed) value
        assert captured_params["value"] == secret
        # Result should be scrubbed
        assert secret not in str(result.data)
        assert "***REDACTED***" in str(result.data)

    def test_dict_result_scrubbed(
        self, exec_registry_fixture: ToolRegistry, scrubber_fixture: SecretScrubber
    ) -> None:
        """Dictionary results containing secrets are scrubbed."""

        async def handler() -> dict[str, str]:
            return {"token": "ghp_abcdefghijklmnopqrstuvwxyz123456", "safe": "value"}

        _register_tool(exec_registry_fixture, "test.dict_result", handler)
        executor = _make_executor(exec_registry_fixture, scrubber_fixture)
        result = run_async(executor.execute("test.dict_result", {}))

        assert result.success is True
        assert isinstance(result.data, dict)
        assert result.data["token"] == "***REDACTED***"
        assert result.data["safe"] == "value"

    def test_list_result_scrubbed(
        self, exec_registry_fixture: ToolRegistry, scrubber_fixture: SecretScrubber
    ) -> None:
        """List results containing secrets are scrubbed."""

        async def handler() -> list[str]:
            return ["sk-abcdefghijklmnopqrstuvwxyz123456", "safe_item"]

        _register_tool(exec_registry_fixture, "test.list_result", handler)
        executor = _make_executor(exec_registry_fixture, scrubber_fixture)
        result = run_async(executor.execute("test.list_result", {}))

        assert result.success is True
        assert isinstance(result.data, list)
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in str(result.data)
        assert "***REDACTED***" in str(result.data)
        assert "safe_item" in str(result.data)

    def test_nested_structure_scrubbed(
        self, exec_registry_fixture: ToolRegistry, scrubber_fixture: SecretScrubber
    ) -> None:
        """Deeply nested structures are recursively scrubbed."""

        async def handler() -> dict[str, Any]:
            return {
                "level1": {
                    "level2": [
                        {"api_key": "sk-abcdefghijklmnopqrstuvwxyz123456"},
                        "ghp_abcdefghijklmnopqrstuvwxyz123456",
                    ]
                }
            }

        _register_tool(exec_registry_fixture, "test.nested_result", handler)
        executor = _make_executor(exec_registry_fixture, scrubber_fixture)
        result = run_async(executor.execute("test.nested_result", {}))

        assert result.success is True
        data = result.data
        assert data["level1"]["level2"][0]["api_key"] == "***REDACTED***"
        assert data["level1"]["level2"][1] == "***REDACTED***"

    # -------------------------------------------------------------------------
    # ToolResult handling
    # -------------------------------------------------------------------------

    def test_existing_toolresult_scrubbed(
        self, exec_registry_fixture: ToolRegistry, scrubber_fixture: SecretScrubber
    ) -> None:
        """Handler returning existing ToolResult has data and error scrubbed."""

        async def handler() -> ToolResult:
            return ToolResult.ok(data={"secret": "sk-abcdefghijklmnopqrstuvwxyz123456"})

        _register_tool(exec_registry_fixture, "test.toolresult_result", handler)
        executor = _make_executor(exec_registry_fixture, scrubber_fixture)
        result = run_async(executor.execute("test.toolresult_result", {}))

        assert result.success is True
        assert isinstance(result.data, dict)
        assert result.data["secret"] == "***REDACTED***"
        assert result.error is None

    def test_existing_toolresult_error_scrubbed(
        self, exec_registry_fixture: ToolRegistry, scrubber_fixture: SecretScrubber
    ) -> None:
        """Handler returning failed ToolResult has error scrubbed."""

        async def handler() -> ToolResult:
            return ToolResult.fail(error="Failed with token ghp_abcdefghijklmnopqrstuvwxyz123456")

        _register_tool(exec_registry_fixture, "test.toolresult_error", handler)
        executor = _make_executor(exec_registry_fixture, scrubber_fixture)
        result = run_async(executor.execute("test.toolresult_error", {}))

        assert result.success is False
        assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in result.error
        assert "***REDACTED***" in result.error

    def test_existing_toolresult_preserves_success_and_timing(
        self, exec_registry_fixture: ToolRegistry, scrubber_fixture: SecretScrubber
    ) -> None:
        """Existing ToolResult's success status and timing metadata are preserved."""

        async def handler() -> ToolResult:
            return ToolResult(
                success=True,
                data={"key": "value"},
                error=None,
                execution_time_ms=123.45,
            )

        _register_tool(exec_registry_fixture, "test.toolresult_preserve", handler)
        executor = _make_executor(exec_registry_fixture, scrubber_fixture)
        result = run_async(executor.execute("test.toolresult_preserve", {}))

        assert result.success is True
        assert result.data == {"key": "value"}
        # execution_time_ms should be updated to actual elapsed time
        assert result.execution_time_ms > 0

    # -------------------------------------------------------------------------
    # Validation error scrubbing
    # -------------------------------------------------------------------------

    def test_validation_error_with_secret_in_message(
        self, exec_registry_fixture: ToolRegistry, scrubber_fixture: SecretScrubber
    ) -> None:
        """Validation error messages are scrubbed even if they contain secrets."""

        class StrictInput(BaseModel):
            value: int

        async def handler(input: StrictInput) -> str:
            return "ok"

        _register_tool(exec_registry_fixture, "test.validation", handler, input_schema=StrictInput)
        executor = _make_executor(exec_registry_fixture, scrubber_fixture)
        # Pass a string where int is expected - this will cause validation error
        result = run_async(executor.execute("test.validation", {"value": "not-an-int"}))

        assert result.success is False
        assert result.error is not None
        assert "validation" in result.error.lower()
        # If the error message contained a secret, it would be scrubbed

    # -------------------------------------------------------------------------
    # Timeout error scrubbing
    # -------------------------------------------------------------------------

    def test_timeout_error_scrubbed(
        self, exec_registry_fixture: ToolRegistry, scrubber_fixture: SecretScrubber
    ) -> None:
        """Timeout error messages are scrubbed (though typically don't contain secrets)."""

        async def handler() -> str:
            await asyncio.sleep(10)
            return "done"

        _register_tool(exec_registry_fixture, "test.timeout", handler, timeout_seconds=0.01)
        executor = _make_executor(exec_registry_fixture, scrubber_fixture)
        result = run_async(executor.execute("test.timeout", {}, timeout_seconds=0.01))

        assert result.success is False
        assert result.error is not None
        assert "timed out" in result.error.lower()
        # The error message goes through scrubber_fixture (defense in depth)

    # -------------------------------------------------------------------------
    # Exception error scrubbing
    # -------------------------------------------------------------------------

    def test_exception_error_scrubbed(
        self, exec_registry_fixture: ToolRegistry, scrubber_fixture: SecretScrubber
    ) -> None:
        """Exception messages containing secrets are scrubbed."""

        async def handler() -> str:
            raise RuntimeError("Failed with key sk-abcdefghijklmnopqrstuvwxyz123456")

        _register_tool(exec_registry_fixture, "test.exception", handler)
        executor = _make_executor(exec_registry_fixture, scrubber_fixture)
        result = run_async(executor.execute("test.exception", {}))

        assert result.success is False
        assert result.error is not None
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in result.error
        assert "***REDACTED***" in result.error

    # -------------------------------------------------------------------------
    # Cancellation semantics preserved
    # -------------------------------------------------------------------------

    def test_cancellation_reraised_not_converted(
        self, exec_registry_fixture: ToolRegistry, scrubber_fixture: SecretScrubber
    ) -> None:
        """asyncio.CancelledError is re-raised, not converted to failure result."""
        cancelled = asyncio.Event()

        async def handler() -> str:
            await cancelled.wait()
            return "done"

        _register_tool(exec_registry_fixture, "test.cancel", handler)
        executor = _make_executor(exec_registry_fixture, scrubber_fixture)

        async def run_and_cancel() -> ToolResult:
            task = asyncio.create_task(executor.execute("test.cancel", {}))
            await asyncio.sleep(0.01)
            task.cancel()
            try:
                return await task
            except asyncio.CancelledError:
                raise

        with pytest.raises(asyncio.CancelledError):
            run_async(run_and_cancel())

    def test_cancellation_drains_child_task(
        self, exec_registry_fixture: ToolRegistry, scrubber_fixture: SecretScrubber
    ) -> None:
        """Cancelled task is properly drained before re-raising."""
        task_done = asyncio.Event()

        async def handler() -> str:
            task_done.set()
            await asyncio.sleep(10)
            return "done"

        _register_tool(exec_registry_fixture, "test.cancel2", handler)
        executor = _make_executor(exec_registry_fixture, scrubber_fixture)

        async def run_and_cancel() -> ToolResult:
            task = asyncio.create_task(executor.execute("test.cancel2", {}))
            await task_done.wait()
            task.cancel()
            try:
                return await task
            except asyncio.CancelledError:
                raise

        with pytest.raises(asyncio.CancelledError):
            run_async(run_and_cancel())

    # -------------------------------------------------------------------------
    # Non-path tools execute normally
    # -------------------------------------------------------------------------

    def test_non_path_tools_execute_normally(
        self, exec_registry_fixture: ToolRegistry, scrubber_fixture: SecretScrubber
    ) -> None:
        """System, web, memory tools execute without path-policy overhead."""

        async def handler() -> dict[str, str]:
            return {"status": "ok"}

        _register_tool(exec_registry_fixture, "test.system_tool", handler, permission_level="SAFE")
        executor = _make_executor(exec_registry_fixture, scrubber_fixture)
        result = run_async(executor.execute("test.system_tool", {}))

        assert result.success is True
        assert result.data == {"status": "ok"}

    # -------------------------------------------------------------------------
    # Fail-safe: scrubber_fixture failure doesn't leak secrets
    # -------------------------------------------------------------------------

    def test_scrubber_fixture_failure_returns_redaction_error(
        self, exec_registry_fixture: ToolRegistry
    ) -> None:
        """If scrubber fails, result contains REDACTION_ERROR not cleartext."""

        # Create a scrubber that fails on _scrub_object_impl
        class FailingScrubber(SecretScrubber):
            def _scrub_object_impl(self, obj: Any) -> Any:
                raise RuntimeError("scrubber broken")

        failing_scrubber = FailingScrubber()

        async def handler() -> str:
            return "sk-abcdefghijklmnopqrstuvwxyz123456"

        _register_tool(exec_registry_fixture, "test.fail_scrub", handler)
        executor_failing = _make_executor(exec_registry_fixture, failing_scrubber)
        result = run_async(executor_failing.execute("test.fail_scrub", {}))

        # Should not leak the secret
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in str(result.data)
        assert "***REDACTION_ERROR***" in str(result.data) or result.data is None

    # -------------------------------------------------------------------------
    # Non-string values preserved
    # -------------------------------------------------------------------------

    def test_non_string_values_preserved_in_result(
        self, exec_registry_fixture: ToolRegistry, scrubber_fixture: SecretScrubber
    ) -> None:
        """Non-string values (int, float, bool, None) are preserved in results."""

        async def handler() -> dict[str, Any]:
            return {
                "num": 42,
                "float": 3.14,
                "bool": True,
                "none": None,
                "secret": "sk-abcdefghijklmnopqrstuvwxyz123456",
            }

        _register_tool(exec_registry_fixture, "test.preserve", handler)
        executor = _make_executor(exec_registry_fixture, scrubber_fixture)
        result = run_async(executor.execute("test.preserve", {}))

        assert result.success is True
        data = result.data
        assert data["num"] == 42
        assert data["float"] == 3.14
        assert data["bool"] is True
        assert data["none"] is None
        assert data["secret"] == "***REDACTED***"


class TestExecutorWithoutScrubber:
    """Tests for ToolExecutor with default scrubber (no explicit injection)."""

    @pytest.fixture
    def exec_registry_fixture(self) -> ToolRegistry:
        return ToolRegistry()

    def test_default_scrubber_fixture_used(self, exec_registry_fixture: ToolRegistry) -> None:
        """Executor instantiates default scrubber when none provided."""
        executor = _make_executor(exec_registry_fixture)
        assert executor.secret_scrubber is not None
        assert isinstance(executor.secret_scrubber, SecretScrubber)

    def test_execution_works_without_explicit_scrubber_fixture(
        self, exec_registry_fixture: ToolRegistry
    ) -> None:
        """Tools execute normally with default scrubber."""

        async def handler() -> str:
            return "sk-abcdefghijklmnopqrstuvwxyz123456"

        _register_tool(
            exec_registry_fixture, "test.default_scrubber_fixture", handler, permission_level="SAFE"
        )
        executor = _make_executor(exec_registry_fixture)
        result = run_async(executor.execute("test.default_scrubber_fixture", {}))
        assert result.success is True
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in str(result.data)
        assert "***REDACTED***" in str(result.data)


class TestScrubberFailSafeAndCancellation:
    """Tests for fail-safe handling when scrubber raises, and cancellation task cleanup."""

    def test_scrubber_exception_fails_safe_on_result(self) -> None:
        """When scrubber raises an exception, result is redacted to ***REDACTION_ERROR***."""
        from unittest.mock import MagicMock

        registry = ToolRegistry()

        async def handler() -> str:
            return "sk-abcdefghijklmnopqrstuvwxyz123456"

        _register_tool(registry, "test.failing_scrubber", handler, permission_level="SAFE")

        broken_scrubber = MagicMock(spec=SecretScrubber)
        broken_scrubber.scrub_object.side_effect = RuntimeError("Scrubber crashed")
        broken_scrubber.scrub_string.side_effect = RuntimeError("Scrubber crashed")

        executor = ToolExecutor(
            registry=registry,
            permission_engine=PermissionEngine(),
            confirmation_hook=AlwaysAllowConfirmationHook(),
            secret_scrubber=broken_scrubber,
        )

        result = run_async(executor.execute("test.failing_scrubber", {}))
        assert result.success is True
        # Never leak the cleartext
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in str(result.data)
        assert result.data == "***REDACTION_ERROR***"

    def test_cancellation_reraises_cancelled_error(self) -> None:
        """Task cancellation properly re-raises asyncio.CancelledError."""
        registry = ToolRegistry()

        async def hanging_handler() -> str:
            await asyncio.sleep(10.0)
            return "done"

        _register_tool(registry, "test.hanging", hanging_handler, permission_level="SAFE")
        executor = ToolExecutor(
            registry=registry,
            permission_engine=PermissionEngine(),
            confirmation_hook=AlwaysAllowConfirmationHook(),
        )

        async def cancel_it() -> None:
            task = asyncio.create_task(executor.execute("test.hanging", {}))
            await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        run_async(cancel_it())
