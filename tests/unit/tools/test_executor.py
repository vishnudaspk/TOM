"""Unit tests for TOM Tool Execution Engine (ToolExecutor)."""

import asyncio
import time
from typing import Any

import pytest
from pydantic import BaseModel
from tom.schemas.config import ToolsConfig
from tom.security.confirmation import (
    AlwaysAllowConfirmationHook,
    AlwaysDenyConfirmationHook,
    CallbackConfirmationHook,
    ConfirmationRequest,
)
from tom.security.permissions import (
    PermissionDeniedError,
    PermissionEngine,
    PermissionLevel,
)
from tom.tools.executor import ToolExecutor
from tom.tools.registry import (
    ToolDefinition,
    ToolNotFoundError,
    ToolRegistry,
    ToolResult,
    ToolValidationError,
)


def run_async(coro: Any) -> Any:
    """Helper to run async coroutines in tests without pytest-asyncio marker conflicts."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Test Schemas
# ---------------------------------------------------------------------------


class AddInput(BaseModel):
    a: int
    b: int = 1


class AddOutput(BaseModel):
    result: int


class DangerousInput(BaseModel):
    target: str
    force: bool = False


# ---------------------------------------------------------------------------
# Test Suites
# ---------------------------------------------------------------------------


class TestToolExecutorRegistryLookup:
    """Test executor interaction with ToolRegistry."""

    def test_execute_registered_tool_success(self) -> None:
        registry = ToolRegistry()

        @registry.tool(name="math.add", input_schema=AddInput, output_schema=AddOutput)
        def add(params: AddInput) -> AddOutput:
            return AddOutput(result=params.a + params.b)

        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("math.add", {"a": 5, "b": 10}))
        assert res.success is True
        assert res.data == AddOutput(result=15)
        assert res.execution_time_ms > 0.0

    def test_execute_unknown_tool_raises_tool_not_found(self) -> None:
        registry = ToolRegistry()
        executor = ToolExecutor(registry=registry)

        with pytest.raises(ToolNotFoundError) as exc_info:
            run_async(executor.execute("unknown.tool", {}))
        assert "unknown.tool" in str(exc_info.value)

    def test_execute_tool_with_none_handler_raises_validation_error(self) -> None:
        registry = ToolRegistry()
        tool_def = ToolDefinition(
            name="broken.tool",
            description="No handler",
            category="broken",
            input_schema=AddInput,
            handler=None,
        )
        registry.register(tool_def)
        executor = ToolExecutor(registry=registry)

        with pytest.raises(ToolValidationError):
            run_async(executor.execute("broken.tool", {"a": 1}))


class TestToolExecutorSecurityBoundary:
    """Test that the centralized security boundary is strictly enforced before execution."""

    def test_safe_tool_bypasses_confirmation(self) -> None:
        registry = ToolRegistry()
        called_hook = False

        def hook_cb(_r: ConfirmationRequest) -> bool:
            nonlocal called_hook
            called_hook = True
            return True

        hook = CallbackConfirmationHook(callback=hook_cb)

        @registry.tool(
            name="system.info",
            permission_level=PermissionLevel.SAFE,
        )
        def get_info() -> dict[str, str]:
            return {"status": "ok"}

        executor = ToolExecutor(registry=registry, confirmation_hook=hook)
        res = run_async(executor.execute("system.info", {}))
        assert res.success is True
        assert called_hook is False  # SAFE tools never invoke confirmation hook

    def test_ask_user_tool_with_approval_executes(self) -> None:
        registry = ToolRegistry()
        hook = AlwaysAllowConfirmationHook()

        @registry.tool(
            name="files.delete",
            permission_level=PermissionLevel.ASK_USER,
            input_schema=DangerousInput,
        )
        def delete_file(params: DangerousInput) -> str:
            return f"Deleted {params.target}"

        executor = ToolExecutor(registry=registry, confirmation_hook=hook)
        res = run_async(executor.execute("files.delete", {"target": "data.txt"}))
        assert res.success is True
        assert res.data == "Deleted data.txt"

    def test_ask_user_tool_with_denial_raises_permission_denied_and_does_not_execute(
        self,
    ) -> None:
        registry = ToolRegistry()
        tool_executed = False

        @registry.tool(
            name="files.delete",
            permission_level=PermissionLevel.ASK_USER,
            input_schema=DangerousInput,
        )
        def delete_file(_params: DangerousInput) -> str:
            nonlocal tool_executed
            tool_executed = True
            return "Should not run"

        hook = AlwaysDenyConfirmationHook()
        executor = ToolExecutor(registry=registry, confirmation_hook=hook)

        with pytest.raises(PermissionDeniedError) as exc_info:
            run_async(executor.execute("files.delete", {"target": "data.txt"}))

        assert tool_executed is False  # Underlying tool was NEVER called
        assert "confirmation denied" in str(exc_info.value).lower()

    def test_ask_user_tool_with_timeout_denial_does_not_execute(self) -> None:
        registry = ToolRegistry()
        tool_executed = False

        @registry.tool(
            name="files.delete",
            permission_level=PermissionLevel.ASK_USER,
            input_schema=DangerousInput,
        )
        def delete_file(_params: DangerousInput) -> str:
            nonlocal tool_executed
            tool_executed = True
            return "Should not run"

        async def hanging_hook(_r: ConfirmationRequest) -> bool:
            await asyncio.sleep(1.0)
            return True

        hook = CallbackConfirmationHook(callback=hanging_hook, timeout_seconds=0.05)
        executor = ToolExecutor(registry=registry, confirmation_hook=hook)

        with pytest.raises(PermissionDeniedError):
            run_async(executor.execute("files.delete", {"target": "data.txt"}))

        assert tool_executed is False

    def test_block_tool_never_executes_and_raises_permission_denied(self) -> None:
        registry = ToolRegistry()
        tool_executed = False

        @registry.tool(
            name="shell.exec",
            permission_level=PermissionLevel.BLOCK,
        )
        def shell_exec() -> str:
            nonlocal tool_executed
            tool_executed = True
            return "Exploit"

        hook = AlwaysAllowConfirmationHook()
        executor = ToolExecutor(registry=registry, confirmation_hook=hook)

        with pytest.raises(PermissionDeniedError) as exc_info:
            run_async(executor.execute("shell.exec", {}))

        assert tool_executed is False
        assert "prohibited" in str(exc_info.value).lower()

    def test_blocked_command_in_params_halts_immediately(self) -> None:
        registry = ToolRegistry()
        tool_executed = False

        @registry.tool(
            name="system.run",
            permission_level=PermissionLevel.SAFE,
        )
        def run_cmd(_params: dict[str, Any]) -> str:
            nonlocal tool_executed
            tool_executed = True
            return "Ran"

        executor = ToolExecutor(registry=registry)
        with pytest.raises(PermissionDeniedError) as exc_info:
            run_async(executor.execute("system.run", {"command": "rmdir /s /q c:"}))

        assert tool_executed is False
        assert "rmdir /s" in str(exc_info.value).lower()

    def test_policy_override_in_permission_engine(self) -> None:
        registry = ToolRegistry()
        tool_executed = False

        @registry.tool(name="test.safe_to_block", permission_level=PermissionLevel.SAFE)
        def safe_tool() -> str:
            nonlocal tool_executed
            tool_executed = True
            return "Ran"

        permission_engine = PermissionEngine()
        permission_engine.set_override("test.safe_to_block", PermissionLevel.BLOCK)
        executor = ToolExecutor(registry=registry, permission_engine=permission_engine)

        with pytest.raises(PermissionDeniedError):
            run_async(executor.execute("test.safe_to_block", {}))

        assert tool_executed is False


class TestToolExecutorArgumentValidation:
    """Test Pydantic argument validation prior to execution."""

    def test_valid_arguments_pass_to_handler(self) -> None:
        registry = ToolRegistry()

        @registry.tool(name="math.add", input_schema=AddInput)
        def add(params: AddInput) -> int:
            return params.a + params.b

        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("math.add", {"a": 20, "b": 22}))
        assert res.success is True
        assert res.data == 42

    def test_default_values_respected(self) -> None:
        registry = ToolRegistry()

        @registry.tool(name="math.add", input_schema=AddInput)
        def add(params: AddInput) -> int:
            return params.a + params.b

        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("math.add", {"a": 10}))
        assert res.success is True
        assert res.data == 11  # b defaulted to 1

    def test_invalid_arguments_prevent_invocation_and_return_structured_failure(
        self,
    ) -> None:
        registry = ToolRegistry()
        tool_executed = False

        @registry.tool(name="math.add", input_schema=AddInput)
        def add(_params: AddInput) -> int:
            nonlocal tool_executed
            tool_executed = True
            return 999

        executor = ToolExecutor(registry=registry)
        # Missing required parameter 'a'
        res = run_async(executor.execute("math.add", {"b": 5}))
        assert res.success is False
        assert tool_executed is False  # Handler never ran
        assert "argument validation failed" in res.error.lower()
        assert "Field required" in res.error or "missing" in res.error.lower()

    def test_invalid_type_prevent_invocation(self) -> None:
        registry = ToolRegistry()
        tool_executed = False

        @registry.tool(name="math.add", input_schema=AddInput)
        def add(_params: AddInput) -> int:
            nonlocal tool_executed
            tool_executed = True
            return 999

        executor = ToolExecutor(registry=registry)
        # 'a' should be int, not non-numeric string
        res = run_async(executor.execute("math.add", {"a": "not-an-integer"}))
        assert res.success is False
        assert tool_executed is False
        assert "validation failed" in res.error.lower()


class TestToolExecutorTimeout:
    """Test timeout bounding and termination of hanging tools."""

    def test_fast_tool_succeeds_under_timeout(self) -> None:
        registry = ToolRegistry()

        @registry.tool(name="async.fast")
        async def fast_tool() -> str:
            await asyncio.sleep(0.01)
            return "done"

        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("async.fast", {}, timeout_seconds=1.0))
        assert res.success is True
        assert res.data == "done"

    def test_hanging_async_tool_times_out_cleanly_without_leak(self) -> None:
        registry = ToolRegistry()
        tool_cleaned_up = False

        @registry.tool(name="async.hang")
        async def hang_tool() -> str:
            nonlocal tool_cleaned_up
            try:
                await asyncio.sleep(100.0)
            except asyncio.CancelledError:
                tool_cleaned_up = True
                raise
            return "never"

        executor = ToolExecutor(registry=registry)
        start = time.monotonic()
        res = run_async(executor.execute("async.hang", {}, timeout_seconds=0.05))
        duration = time.monotonic() - start

        assert res.success is False
        assert "timed out after 0.05 seconds" in res.error
        assert duration < 0.5
        assert tool_cleaned_up is True  # Task was cleanly cancelled on timeout

    def test_default_timeout_used_from_config(self) -> None:
        registry = ToolRegistry()

        @registry.tool(name="async.hang")
        async def hang_tool() -> str:
            await asyncio.sleep(50.0)
            return "never"

        config = ToolsConfig(default_timeout_seconds=0.05)
        executor = ToolExecutor(registry=registry, config=config)
        res = run_async(executor.execute("async.hang", {}))
        assert res.success is False
        assert "timed out after 0.05 seconds" in res.error


class TestToolExecutorCancellation:
    """Test that caller cancellation propagates and does not leak tasks."""

    def test_caller_cancellation_propagates_and_cleans_up_task(self) -> None:
        registry = ToolRegistry()
        tool_cancelled = False

        @registry.tool(name="async.cancel_target")
        async def target_tool() -> str:
            nonlocal tool_cancelled
            try:
                await asyncio.sleep(100.0)
            except asyncio.CancelledError:
                tool_cancelled = True
                raise
            return "never"

        executor = ToolExecutor(registry=registry)

        async def run_and_cancel() -> None:
            exec_task = asyncio.create_task(
                executor.execute("async.cancel_target", {}, timeout_seconds=10.0)
            )
            await asyncio.sleep(0.02)
            exec_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await exec_task

        run_async(run_and_cancel())
        assert tool_cancelled is True


class TestToolExecutorSyncAsyncSupport:
    """Test seamless execution of both sync and async tools."""

    def test_sync_tool_executed_without_blocking(self) -> None:
        registry = ToolRegistry()

        @registry.tool(name="sync.compute")
        def sync_compute(n: int) -> int:
            return n * 2

        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("sync.compute", {"n": 21}))
        assert res.success is True
        assert res.data == 42

    def test_async_tool_executed_directly(self) -> None:
        registry = ToolRegistry()

        @registry.tool(name="async.compute")
        async def async_compute(n: int) -> int:
            await asyncio.sleep(0.01)
            return n * 3

        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("async.compute", {"n": 10}))
        assert res.success is True
        assert res.data == 30


class TestToolExecutorErrorHandlingAndTiming:
    """Test runtime error capture and monotonic timing."""

    def test_tool_raising_exception_produces_structured_failure(self) -> None:
        registry = ToolRegistry()

        @registry.tool(name="math.divide")
        def divide(a: int, b: int) -> float:
            return a / b

        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("math.divide", {"a": 10, "b": 0}))
        assert res.success is False
        assert "division by zero" in res.error
        assert res.execution_time_ms > 0.0

    def test_tool_returning_tool_result_preserves_result_and_updates_time(self) -> None:
        registry = ToolRegistry()

        @registry.tool(name="custom.result")
        def custom_tool() -> ToolResult:
            return ToolResult.ok(data={"custom": True})

        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("custom.result", {}))
        assert res.success is True
        assert res.data == {"custom": True}
        assert res.execution_time_ms > 0.0


class TestFabricatedToolResultsPrevention:
    """Regression tests ensuring missing/denied/failed tools cannot be represented as successful with invented output."""

    def test_missing_tool_raises_not_found_not_fake_success(self) -> None:
        """Executing an unregistered tool must raise ToolNotFoundError, never return fake success."""
        registry = ToolRegistry()
        executor = ToolExecutor(registry=registry)

        with pytest.raises(ToolNotFoundError):
            run_async(executor.execute("nonexistent.tool", {}))

    def test_denied_tool_raises_permission_denied_not_fake_success(self) -> None:
        """A tool denied by PermissionEngine must raise PermissionDeniedError, never return fake success."""
        registry = ToolRegistry()

        @registry.tool(
            name="test.denied",
            permission_level=PermissionLevel.BLOCK,
        )
        def denied_tool() -> str:
            return "should not run"

        # Use a permission engine that blocks everything
        class DenyAllPermissionEngine(PermissionEngine):
            def evaluate(self, tool_def, params=None):
                from tom.security.permissions import PermissionDecision, PermissionLevel
                return PermissionDecision(
                    allowed=False,
                    permission_level=PermissionLevel.BLOCK,
                    reason="Blocked by test engine",
                )

        executor = ToolExecutor(
            registry=registry,
            permission_engine=DenyAllPermissionEngine(),
        )

        with pytest.raises(PermissionDeniedError):
            run_async(executor.execute("test.denied", {}))

    def test_failed_tool_returns_error_not_fake_success(self) -> None:
        """A tool that fails at runtime must return ToolResult.fail, never fake success data."""
        registry = ToolRegistry()

        @registry.tool(name="test.fail")
        def failing_tool() -> str:
            raise RuntimeError("intentional failure")

        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("test.fail", {}))

        assert res.success is False
        assert res.error is not None
        assert "intentional failure" in res.error
        assert res.data is None  # No fake data

    def test_tool_timeout_returns_error_not_fake_success(self) -> None:
        """A tool that times out must return ToolResult.fail with timeout error, never fake success data."""
        registry = ToolRegistry()

        @registry.tool(name="test.timeout", timeout_seconds=0.01)
        async def slow_tool() -> str:
            await asyncio.sleep(1.0)
            return "too slow"

        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("test.timeout", {}))

        assert res.success is False
        assert res.error is not None
        assert "timed out" in res.error.lower()
        assert res.data is None  # No fake data

    def test_missing_tool_via_agent_executor_raises_not_found(self) -> None:
        """Agent.execute_tool must return ToolResult.fail for missing tools, not fake success."""
        from tom.agents.base import Agent
        from tom.agents.dependencies import AgentDependencies
        from tom.schemas.agent import AgentConfig

        registry = ToolRegistry()
        deps = AgentDependencies(executor=ToolExecutor(registry=registry))
        agent = Agent(config=AgentConfig(), dependencies=deps)

        res = run_async(agent.execute_tool("nonexistent.tool"))
        assert res.success is False
        assert res.error is not None
        assert "not registered" in res.error
        assert res.data is None

    def test_denied_tool_via_agent_executor_raises_permission_denied(self) -> None:
        """Agent.execute_tool must return ToolResult.fail for denied tools, not fake success."""
        from tom.agents.base import Agent
        from tom.agents.dependencies import AgentDependencies
        from tom.schemas.agent import AgentConfig
        from tom.security.permissions import PermissionDecision, PermissionEngine, PermissionLevel

        registry = ToolRegistry()

        @registry.tool(
            name="test.denied",
            permission_level=PermissionLevel.BLOCK,
        )
        def denied_tool() -> str:
            return "should not run"

        class DenyAllPermissionEngine(PermissionEngine):
            def evaluate(self, tool_def, params=None):
                return PermissionDecision(
                    allowed=False,
                    permission_level=PermissionLevel.BLOCK,
                    reason="Blocked by test engine",
                )

        deps = AgentDependencies(executor=ToolExecutor(
            registry=registry,
            permission_engine=DenyAllPermissionEngine(),
        ))
        agent = Agent(config=AgentConfig(), dependencies=deps)

        res = run_async(agent.execute_tool("test.denied"))
        assert res.success is False
        assert res.error is not None
        assert "denied" in res.error.lower() or "prohibited" in res.error.lower()
        assert res.data is None

    def test_failed_tool_via_agent_executor_returns_error_result(self) -> None:
        """Agent.execute_tool must return ToolResult.fail for runtime tool failures, not fake success."""
        from tom.agents.base import Agent
        from tom.agents.dependencies import AgentDependencies
        from tom.schemas.agent import AgentConfig

        registry = ToolRegistry()

        @registry.tool(name="test.fail")
        def failing_tool() -> str:
            raise RuntimeError("intentional failure")

        deps = AgentDependencies(executor=ToolExecutor(registry=registry))
        agent = Agent(config=AgentConfig(), dependencies=deps)

        res = run_async(agent.execute_tool("test.fail"))

        assert res.success is False
        assert res.error is not None
        assert "intentional failure" in res.error
        assert res.data is None

    def test_tool_result_never_contains_unverified_factual_claims(self) -> None:
        """ToolResult data must only contain actual tool output, never synthesized claims."""
        registry = ToolRegistry()

        @registry.tool(name="test.gpu")
        def gpu_tool() -> dict:
            return {"name": "RTX 4060", "vram_mb": 8192}

        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("test.gpu", {}))

        # Verify the result contains actual tool output
        assert res.success is True
        assert res.data == {"name": "RTX 4060", "vram_mb": 8192}
        # The result should NOT contain fabricated data like "GTX 1650" or "4 GB"
        data_str = str(res.data)
        assert "GTX 1650" not in data_str
        assert "4 GB" not in data_str
        assert "4096" not in data_str  # 4GB in MB
