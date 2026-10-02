"""Integration tests — Confirmation Pipeline (Phase 8 Iteration 5).

Verifies the ASK_USER confirmation contract across the full stack:
    TaskExecutor -> ToolExecutor -> PermissionEngine -> ConfirmationHook

All tests are offline and deterministic.
"""

from __future__ import annotations

import pytest
from tom.agents.executor import TaskExecutor
from tom.agents.task_manager import TaskManager
from tom.schemas.planner import Plan, PlanStep, StepDependency
from tom.schemas.task import TaskState
from tom.security.confirmation import (
    AlwaysDenyConfirmationHook,
    CallbackConfirmationHook,
    ConfirmationRequest,
)
from tom.security.permissions import PermissionEngine, PermissionLevel
from tom.tools.executor import ToolExecutor
from tom.tools.registry import ToolDefinition, ToolRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_env_with_hook(hook):  # type: ignore[no-untyped-def]
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
            lambda path="": True,
            name="files.delete_file",
            permission_level=PermissionLevel.ASK_USER,
        )
    )
    registry.register(
        ToolDefinition.from_function(
            lambda x=0, y=0: True,
            name="os.input.click",
            permission_level=PermissionLevel.ASK_USER,
        )
    )
    tool_executor = ToolExecutor(
        registry=registry,
        permission_engine=PermissionEngine(),
        confirmation_hook=hook,
    )
    task_manager = TaskManager(db_path=":memory:")
    executor = TaskExecutor(
        tool_executor=tool_executor,
        task_manager=task_manager,
        max_steps=10,
        timeout_seconds=5.0,
    )
    return task_manager, executor


def _step(tool: str, *, step_id: str | None = None, terminal: bool = False) -> PlanStep:
    kw: dict = {
        "description": f"Execute {tool}",
        "tool_name": tool,
        "expected_outcome": "success",
        "is_terminal": terminal,
    }
    if step_id:
        kw["step_id"] = step_id
    return PlanStep(**kw)


def _plan(*steps: PlanStep, goal: str = "confirmation test") -> Plan:
    return Plan(goal=goal, steps=list(steps))


# ===========================================================================
# 1. ASK_USER approval path
# ===========================================================================


class TestConfirmationApproval:
    @pytest.mark.anyio
    async def test_ask_user_tool_allowed_completes_task(self) -> None:
        from tom.security.confirmation import AlwaysAllowConfirmationHook

        _, executor = _make_env_with_hook(AlwaysAllowConfirmationHook())
        plan = _plan(_step("files.delete_file", terminal=True))
        result = await executor.execute(plan)
        assert result.state == TaskState.COMPLETED

    @pytest.mark.anyio
    async def test_step_succeeds_with_approved_confirmation(self) -> None:
        from tom.security.confirmation import AlwaysAllowConfirmationHook

        _, executor = _make_env_with_hook(AlwaysAllowConfirmationHook())
        plan = _plan(_step("files.delete_file", terminal=True))
        result = await executor.execute(plan)
        assert len(result.step_results) == 1
        assert result.step_results[0].success is True

    @pytest.mark.anyio
    async def test_task_returns_to_executing_state_after_confirmation(self) -> None:
        """After the ASK_USER step completes, the task must not remain in
        WAITING_CONFIRMATION — it must transition back to EXECUTING and then COMPLETED."""
        from tom.security.confirmation import AlwaysAllowConfirmationHook

        task_manager, executor = _make_env_with_hook(AlwaysAllowConfirmationHook())
        # ping SAFE + delete ASK_USER → both succeed
        plan = _plan(_step("system.ping"), _step("files.delete_file", terminal=True))
        result = await executor.execute(plan)
        stored = task_manager.get(result.task_id)
        assert stored.state == TaskState.COMPLETED

    @pytest.mark.anyio
    async def test_callback_confirmation_receives_correct_tool_name(self) -> None:
        received: list[ConfirmationRequest] = []

        async def capture(req: ConfirmationRequest) -> bool:
            received.append(req)
            return True

        _, executor = _make_env_with_hook(CallbackConfirmationHook(capture))
        plan = _plan(_step("files.delete_file", terminal=True))
        result = await executor.execute(plan)
        assert result.state == TaskState.COMPLETED
        assert len(received) == 1
        assert received[0].tool_name == "files.delete_file"


# ===========================================================================
# 2. ASK_USER denial path
# ===========================================================================


class TestConfirmationDenial:
    @pytest.mark.anyio
    async def test_ask_user_tool_denied_fails_step(self) -> None:
        _, executor = _make_env_with_hook(AlwaysDenyConfirmationHook())
        plan = _plan(_step("files.delete_file", terminal=True))
        result = await executor.execute(plan)
        # Denial → ToolExecutor returns failure → TaskExecutor marks FAILED
        assert result.state == TaskState.FAILED

    @pytest.mark.anyio
    async def test_denied_step_result_recorded_as_failure(self) -> None:
        _, executor = _make_env_with_hook(AlwaysDenyConfirmationHook())
        plan = _plan(_step("files.delete_file"))
        result = await executor.execute(plan)
        assert len(result.step_results) == 1
        assert result.step_results[0].success is False

    @pytest.mark.anyio
    async def test_subsequent_steps_not_executed_after_denial(self) -> None:
        """If ASK_USER step is denied and fails, dependent subsequent steps
        must NOT execute."""
        _, executor = _make_env_with_hook(AlwaysDenyConfirmationHook())
        s1 = PlanStep(
            step_id="delete",
            description="delete file",
            tool_name="files.delete_file",
            expected_outcome="deleted",
        )
        s2 = PlanStep(
            step_id="ping",
            description="ping after",
            tool_name="system.ping",
            expected_outcome="ok",
            dependencies=[StepDependency(step_id="delete")],
        )
        plan = Plan(goal="test", steps=[s1, s2])
        result = await executor.execute(plan)
        assert result.state == TaskState.FAILED
        # Only 1 step recorded (the failed delete), not the ping
        assert result.total_steps == 1

    @pytest.mark.anyio
    async def test_safe_steps_before_ask_user_complete_normally(self) -> None:
        """SAFE steps that precede an ASK_USER step must run successfully
        regardless of what happens at the confirmation boundary."""
        from tom.security.confirmation import AlwaysAllowConfirmationHook

        task_manager, executor = _make_env_with_hook(AlwaysAllowConfirmationHook())
        plan = _plan(
            _step("system.ping"), _step("system.ping"), _step("files.delete_file", terminal=True)
        )
        result = await executor.execute(plan)
        assert result.state == TaskState.COMPLETED
        assert result.total_steps == 3


# ===========================================================================
# 3. PermissionEngine cannot be bypassed
# ===========================================================================


class TestPermissionCannotBeBypassed:
    @pytest.mark.anyio
    async def test_blocked_tool_fails_even_with_allow_hook(self) -> None:
        """A BLOCK-level tool must fail even if the confirmation hook says yes."""
        from tom.security.confirmation import AlwaysAllowConfirmationHook

        registry = ToolRegistry()
        registry.register(
            ToolDefinition.from_function(
                lambda: True,
                name="system.blocked_op",
                permission_level=PermissionLevel.BLOCK,
            )
        )
        tool_executor = ToolExecutor(
            registry=registry,
            permission_engine=PermissionEngine(),
            confirmation_hook=AlwaysAllowConfirmationHook(),
        )
        task_manager = TaskManager(db_path=":memory:")
        executor = TaskExecutor(tool_executor=tool_executor, task_manager=task_manager)
        plan = _plan(_step("system.blocked_op"))
        result = await executor.execute(plan)
        assert result.state == TaskState.FAILED

    @pytest.mark.anyio
    async def test_permission_engine_is_authoritative_for_ask_user(self) -> None:
        """ASK_USER must always route through PermissionEngine, not be executed
        directly. Prove by showing a BLOCK re-classified tool is still blocked."""
        # If the tool were silently bypassing PermissionEngine, a BLOCK tool would succeed.
        from tom.security.confirmation import AlwaysAllowConfirmationHook

        registry = ToolRegistry()
        registry.register(
            ToolDefinition.from_function(
                lambda: "payload",
                name="files.write_file",
                permission_level=PermissionLevel.BLOCK,  # classified BLOCK explicitly
            )
        )
        tool_executor = ToolExecutor(
            registry=registry,
            permission_engine=PermissionEngine(),
            confirmation_hook=AlwaysAllowConfirmationHook(),
        )
        executor = TaskExecutor(
            tool_executor=tool_executor,
            task_manager=TaskManager(db_path=":memory:"),
        )
        result = await executor.execute(_plan(_step("files.write_file")))
        assert result.state == TaskState.FAILED
