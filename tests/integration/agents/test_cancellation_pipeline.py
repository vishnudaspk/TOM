"""Integration tests — Cancellation Pipeline (Phase 8 Iteration 5).

Verifies cooperative cancellation via the existing CancellationToken across
the full Task execution stack.

All tests are offline, deterministic, and side-effect free.
"""

from __future__ import annotations

import pytest
from tom.agents.executor import TaskExecutor
from tom.agents.task_manager import TaskManager
from tom.core.context import CancellationToken
from tom.schemas.planner import Plan, PlanStep, StepDependency
from tom.schemas.task import TaskState
from tom.security.confirmation import AlwaysAllowConfirmationHook
from tom.security.permissions import PermissionEngine, PermissionLevel
from tom.tools.executor import ToolExecutor
from tom.tools.registry import ToolDefinition, ToolRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_env(
    *,
    max_steps: int = 10,
    timeout_seconds: float = 5.0,
) -> tuple[TaskManager, TaskExecutor]:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition.from_function(
            lambda: {"status": "ok"},
            name="system.ping",
            permission_level=PermissionLevel.SAFE,
        )
    )
    registry.register(
        ToolDefinition.from_function(
            lambda path="": f"read:{path}",
            name="files.read_file",
            permission_level=PermissionLevel.SAFE,
        )
    )
    tool_executor = ToolExecutor(
        registry=registry,
        permission_engine=PermissionEngine(),
        confirmation_hook=AlwaysAllowConfirmationHook(),
    )
    task_manager = TaskManager(db_path=":memory:")
    executor = TaskExecutor(
        tool_executor=tool_executor,
        task_manager=task_manager,
        max_steps=max_steps,
        timeout_seconds=timeout_seconds,
    )
    return task_manager, executor


def _step(tool: str, *, step_id: str | None = None, deps: list[str] | None = None) -> PlanStep:
    kw: dict = {
        "description": f"Execute {tool}",
        "tool_name": tool,
        "expected_outcome": "success",
    }
    if step_id:
        kw["step_id"] = step_id
    if deps:
        kw["dependencies"] = [StepDependency(step_id=d) for d in deps]
    return PlanStep(**kw)


def _plan(*steps: PlanStep, goal: str = "cancellation test") -> Plan:
    return Plan(goal=goal, steps=list(steps))


# ===========================================================================
# 1. Cancellation before execution starts
# ===========================================================================


class TestCancellationBeforeExecution:
    @pytest.mark.anyio
    async def test_pre_cancelled_token_aborts_immediately(self) -> None:
        """A token cancelled before execute() is called must abort on the
        first cooperative check — no steps must be executed."""
        task_manager, executor = _make_env()
        token = CancellationToken()
        token.cancel()

        plan = _plan(_step("system.ping"), _step("files.read_file"))
        result = await executor.execute(plan, cancellation_token=token)

        assert result.state == TaskState.CANCELLED
        # No steps should have executed
        assert result.total_steps == 0

    @pytest.mark.anyio
    async def test_pre_cancelled_task_stored_as_cancelled(self) -> None:
        task_manager, executor = _make_env()
        token = CancellationToken()
        token.cancel()

        plan = _plan(_step("system.ping"))
        result = await executor.execute(plan, cancellation_token=token)
        stored = task_manager.get(result.task_id)
        assert stored.state == TaskState.CANCELLED


# ===========================================================================
# 2. Cancellation does not accidentally become success
# ===========================================================================


class TestCancellationIsNotSuccess:
    @pytest.mark.anyio
    async def test_cancelled_result_state_is_cancelled_not_completed(self) -> None:
        task_manager, executor = _make_env()
        token = CancellationToken()
        token.cancel()
        result = await executor.execute(_plan(_step("system.ping")), cancellation_token=token)
        assert result.state != TaskState.COMPLETED
        assert result.state == TaskState.CANCELLED

    @pytest.mark.anyio
    async def test_cancellation_carries_error_message(self) -> None:
        _, executor = _make_env()
        token = CancellationToken()
        token.cancel()
        result = await executor.execute(_plan(_step("system.ping")), cancellation_token=token)
        assert result.error is not None
        assert len(result.error) > 0


# ===========================================================================
# 3. CancellationToken is the only mechanism
# ===========================================================================


class TestCancellationTokenIsAuthoritative:
    def test_cancellation_token_initial_state(self) -> None:
        token = CancellationToken()
        assert token.is_cancelled() is False

    def test_cancellation_token_after_cancel(self) -> None:
        token = CancellationToken()
        token.cancel()
        assert token.is_cancelled() is True

    def test_cancellation_token_idempotent(self) -> None:
        """Multiple calls to cancel() must not raise."""
        token = CancellationToken()
        token.cancel()
        token.cancel()
        assert token.is_cancelled() is True

    @pytest.mark.anyio
    async def test_uncancelled_token_allows_full_execution(self) -> None:
        """A fresh, uncancelled token must not interfere with normal execution."""
        _, executor = _make_env()
        token = CancellationToken()
        plan = _plan(_step("system.ping"), _step("files.read_file"))
        result = await executor.execute(plan, cancellation_token=token)
        assert result.state == TaskState.COMPLETED
        assert result.total_steps == 2


# ===========================================================================
# 4. No subsequent steps execute after cancellation
# ===========================================================================


class TestNoOrphanStepsAfterCancellation:
    @pytest.mark.anyio
    async def test_no_steps_run_when_pre_cancelled(self) -> None:
        _, executor = _make_env()
        token = CancellationToken()
        token.cancel()
        plan = _plan(
            _step("system.ping", step_id="s1"),
            _step("system.ping", step_id="s2", deps=["s1"]),
            _step("files.read_file", step_id="s3", deps=["s2"]),
        )
        result = await executor.execute(plan, cancellation_token=token)
        assert result.state == TaskState.CANCELLED
        # No steps should have run
        assert result.total_steps == 0
        assert all(r.step_id != "s2" for r in result.step_results)
        assert all(r.step_id != "s3" for r in result.step_results)


# ===========================================================================
# 5. TaskManager token wiring
# ===========================================================================


class TestTaskManagerTokenWiring:
    @pytest.mark.anyio
    async def test_task_manager_provides_cancellation_token(self) -> None:
        """TaskManager must provide a valid CancellationToken for every created task."""
        task_manager, _ = _make_env()
        task = task_manager.create(goal="token test")
        token = task_manager.get_token(task.task_id)
        assert token is not None
        assert not token.is_cancelled()

    @pytest.mark.anyio
    async def test_task_manager_cancel_sets_token(self) -> None:
        task_manager, _ = _make_env()
        task = task_manager.create(goal="token cancel test")
        task_manager.transition(task.task_id, TaskState.ROUTING)
        task_manager.transition(task.task_id, TaskState.EXECUTING)
        token = task_manager.get_token(task.task_id)
        task_manager.cancel(task.task_id)
        assert token.is_cancelled()
