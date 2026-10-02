"""Unit tests for TaskExecutor (Phase 8 Iteration 2).

Adheres to:
- Phase 8 Architecture (Execution Engine & Checkpointing)
- Decision 035: Step-Bounded Execution Loop & Cooperative Cancellation
- Decision 051: Task Lifecycle State Machine & Step Decomposition
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from tom.agents.executor import TaskExecutor
from tom.agents.loop_detector import LoopDetector
from tom.agents.revalidator import VisualRevalidator
from tom.agents.task_manager import TaskManager
from tom.core.context import CancellationToken
from tom.schemas.planner import Plan, PlanStep, StepDependency
from tom.schemas.task import TaskState
from tom.schemas.vision import BoundingBox
from tom.security.confirmation import AlwaysAllowConfirmationHook, CallbackConfirmationHook
from tom.security.permissions import PermissionEngine, PermissionLevel
from tom.tools.executor import ToolExecutor
from tom.tools.registry import ToolDefinition, ToolRegistry

# ---------------------------------------------------------------------------
# Test Setup & Fixtures
# ---------------------------------------------------------------------------


def make_test_environment() -> tuple[ToolRegistry, ToolExecutor, TaskManager, TaskExecutor]:
    """Build a deterministic testing environment for TaskExecutor."""
    registry = ToolRegistry()

    # SAFE tools
    registry.register(
        ToolDefinition.from_function(
            lambda: {"status": "ok"},
            name="system.ping",
            permission_level=PermissionLevel.SAFE,
        )
    )
    registry.register(
        ToolDefinition.from_function(
            lambda path: f"content of {path}",
            name="files.read_file",
            permission_level=PermissionLevel.SAFE,
        )
    )
    registry.register(
        ToolDefinition.from_function(
            lambda: (_ for _ in ()).throw(RuntimeError("disk read failure")),
            name="system.failing_tool",
            permission_level=PermissionLevel.SAFE,
        )
    )

    # ASK_USER mutating tool
    registry.register(
        ToolDefinition.from_function(
            lambda path: True,
            name="files.delete_file",
            permission_level=PermissionLevel.ASK_USER,
        )
    )

    # Simulated OS input click tool
    registry.register(
        ToolDefinition.from_function(
            lambda x, y: True,
            name="os.input.click",
            permission_level=PermissionLevel.ASK_USER,
        )
    )

    perm_engine = PermissionEngine()
    confirmation_hook = AlwaysAllowConfirmationHook()
    tool_executor = ToolExecutor(
        registry=registry,
        permission_engine=perm_engine,
        confirmation_hook=confirmation_hook,
    )

    task_manager = TaskManager(db_path=":memory:")
    executor = TaskExecutor(
        tool_executor=tool_executor,
        task_manager=task_manager,
        max_steps=10,
        timeout_seconds=5.0,
    )

    return registry, tool_executor, task_manager, executor


# ---------------------------------------------------------------------------
# Execution & Sequencing Tests
# ---------------------------------------------------------------------------


class TestTaskExecutorSequencing:
    @pytest.mark.anyio
    async def test_successful_linear_step_execution(self) -> None:
        _, _, tm, executor = make_test_environment()

        plan = Plan(
            goal="Ping system and read file",
            steps=[
                PlanStep(
                    step_id="step_1",
                    description="Ping system",
                    tool_name="system.ping",
                    expected_outcome="ping ok",
                ),
                PlanStep(
                    step_id="step_2",
                    description="Read file",
                    tool_name="files.read_file",
                    parameters={"path": "test.txt"},
                    dependencies=[StepDependency(step_id="step_1")],
                    expected_outcome="file read",
                    is_terminal=True,
                ),
            ],
        )

        result = await executor.execute(plan)

        assert result.state == TaskState.COMPLETED
        assert result.total_steps == 2
        assert len(result.step_results) == 2
        assert result.step_results[0].success is True
        assert result.step_results[0].output == {"status": "ok"}
        assert result.step_results[1].success is True
        assert result.step_results[1].output == "content of test.txt"

        # Verify task is recorded as COMPLETED in TaskManager
        persisted_task = tm.get(result.task_id)
        assert persisted_task.state == TaskState.COMPLETED

    @pytest.mark.anyio
    async def test_dag_dependency_ordering(self) -> None:
        _, _, _, executor = make_test_environment()

        # Step 2 is declared first in the list, but depends on Step 1
        plan = Plan(
            goal="Out-of-order declaration",
            steps=[
                PlanStep(
                    step_id="step_2",
                    description="Read file",
                    tool_name="files.read_file",
                    parameters={"path": "a.txt"},
                    dependencies=[StepDependency(step_id="step_1")],
                    expected_outcome="file read",
                ),
                PlanStep(
                    step_id="step_1",
                    description="Ping system",
                    tool_name="system.ping",
                    expected_outcome="ping ok",
                ),
            ],
        )

        result = await executor.execute(plan)
        assert result.state == TaskState.COMPLETED
        assert [r.step_id for r in result.step_results] == ["step_1", "step_2"]

    @pytest.mark.anyio
    async def test_failed_step_aborts_task(self) -> None:
        _, _, tm, executor = make_test_environment()

        plan = Plan(
            goal="Run failing tool",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="Failing step",
                    tool_name="system.failing_tool",
                    expected_outcome="fail",
                ),
                PlanStep(
                    step_id="s2",
                    description="Unreachable step",
                    tool_name="system.ping",
                    expected_outcome="ping",
                ),
            ],
        )

        result = await executor.execute(plan)

        assert result.state == TaskState.FAILED
        assert "disk read failure" in (result.error or "")
        assert len(result.step_results) == 1
        assert result.step_results[0].success is False

        # Verify state in TaskManager
        persisted = tm.get(result.task_id)
        assert persisted.state == TaskState.FAILED
        assert "disk read failure" in (persisted.error or "")

    @pytest.mark.anyio
    async def test_step_dependency_failure_prevents_execution(self) -> None:
        _, _, _, executor = make_test_environment()

        plan = Plan(
            goal="Dependency failure",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="Fails",
                    tool_name="system.failing_tool",
                    expected_outcome="fail",
                ),
                PlanStep(
                    step_id="s2",
                    description="Depends on s1",
                    tool_name="system.ping",
                    dependencies=[StepDependency(step_id="s1")],
                    expected_outcome="ping",
                ),
            ],
        )

        result = await executor.execute(plan)
        assert result.state == TaskState.FAILED
        # Second step was never executed because s1 failed
        assert len(result.step_results) == 1
        assert result.step_results[0].step_id == "s1"


# ---------------------------------------------------------------------------
# Budget, Timeout & Cancellation Tests
# ---------------------------------------------------------------------------


class TestTaskExecutorBudgetsAndCancellation:
    @pytest.mark.anyio
    async def test_step_budget_exhaustion(self) -> None:
        _, _, _, executor = make_test_environment()
        executor.max_steps = 2  # Budget limited to 2 steps

        plan = Plan(
            goal="Exceed budget",
            steps=[
                PlanStep(
                    step_id=f"step_{i}",
                    description="ping",
                    tool_name="system.ping",
                    expected_outcome="ok",
                )
                for i in range(5)
            ],
        )

        result = await executor.execute(plan)
        assert result.state == TaskState.FAILED
        assert "step budget limit" in (result.error or "")
        assert len(result.step_results) == 2

    @pytest.mark.anyio
    async def test_wall_clock_timeout(self) -> None:
        registry, _, _, _ = make_test_environment()

        async def slow_handler() -> str:
            await asyncio.sleep(0.5)
            return "done"

        registry.register(
            ToolDefinition.from_function(
                slow_handler, name="system.slow_tool", permission_level=PermissionLevel.SAFE
            )
        )

        tool_executor = ToolExecutor(registry=registry)
        executor = TaskExecutor(
            tool_executor=tool_executor,
            timeout_seconds=0.2,  # 200ms timeout
        )

        plan = Plan(
            goal="Slow plan",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="slow",
                    tool_name="system.slow_tool",
                    expected_outcome="ok",
                ),
                PlanStep(
                    step_id="s2",
                    description="slow",
                    tool_name="system.slow_tool",
                    expected_outcome="ok",
                ),
            ],
        )

        result = await executor.execute(plan)
        assert result.state == TaskState.FAILED
        assert "timeout limit" in (result.error or "")

    @pytest.mark.anyio
    async def test_cooperative_cancellation(self) -> None:
        _, _, tm, executor = make_test_environment()

        token = CancellationToken()
        token.cancel()

        plan = Plan(
            goal="Cancelled plan",
            steps=[
                PlanStep(
                    step_id="s1", description="ping", tool_name="system.ping", expected_outcome="ok"
                )
            ],
        )

        result = await executor.execute(plan, cancellation_token=token)
        assert result.state == TaskState.CANCELLED
        assert "cancelled" in (result.error or "").lower()

        persisted = tm.get(result.task_id)
        assert persisted.state == TaskState.CANCELLED


# ---------------------------------------------------------------------------
# Loop Detection Tests
# ---------------------------------------------------------------------------


class TestTaskExecutorLoopDetection:
    @pytest.mark.anyio
    async def test_repetitive_ineffective_tools_trigger_loop_abort(self) -> None:
        registry = ToolRegistry()
        call_count = 0

        def stuck_tool() -> dict[str, str]:
            nonlocal call_count
            call_count += 1
            return {"status": "unchanged_stagnant"}

        registry.register(
            ToolDefinition.from_function(
                stuck_tool, name="system.stuck_tool", permission_level=PermissionLevel.SAFE
            )
        )

        tool_executor = ToolExecutor(registry=registry)
        detector = LoopDetector(tool_threshold=3)
        executor = TaskExecutor(
            tool_executor=tool_executor,
            loop_detector=detector,
            max_steps=10,
        )

        # Plan with 5 identical calls to stuck_tool with same parameters
        plan = Plan(
            goal="Loop plan",
            steps=[
                PlanStep(
                    step_id=f"step_{i}",
                    description="stuck",
                    tool_name="system.stuck_tool",
                    expected_outcome="ok",
                )
                for i in range(5)
            ],
        )

        result = await executor.execute(plan)
        assert result.state == TaskState.FAILED
        assert "Tool repetition loop detected" in (result.error or "")
        # Should abort after the 3rd repetition
        assert len(result.step_results) == 3


# ---------------------------------------------------------------------------
# Confirmation & Revalidation Tests
# ---------------------------------------------------------------------------


class TestTaskExecutorConfirmationAndRevalidation:
    @pytest.mark.anyio
    async def test_ask_user_transitions_to_waiting_confirmation(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition.from_function(
                lambda path: True,
                name="files.delete_file",
                permission_level=PermissionLevel.ASK_USER,
            )
        )

        state_history: list[TaskState] = []
        tm = TaskManager(db_path=":memory:")

        # Hook that observes task state during confirmation
        async def observing_confirmation(req: Any) -> bool:
            task = tm.get(current_task_id)
            state_history.append(task.state)
            return True

        perm_engine = PermissionEngine()
        hook = CallbackConfirmationHook(observing_confirmation)
        tool_executor = ToolExecutor(
            registry=registry, permission_engine=perm_engine, confirmation_hook=hook
        )

        executor = TaskExecutor(tool_executor=tool_executor, task_manager=tm)

        plan = Plan(
            goal="Delete file",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="Delete",
                    tool_name="files.delete_file",
                    parameters={"path": "bad.txt"},
                    expected_outcome="deleted",
                )
            ],
        )

        # Pre-create task so we can record its ID for the hook
        task = tm.create("Delete file")
        current_task_id = task.task_id

        result = await executor.execute(plan, task_id=current_task_id)

        assert result.state == TaskState.COMPLETED
        # Must have observed WAITING_CONFIRMATION during confirmation hook execution
        assert TaskState.WAITING_CONFIRMATION in state_history

    @pytest.mark.anyio
    async def test_denied_confirmation_fails_step(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition.from_function(
                lambda path: True,
                name="files.delete_file",
                permission_level=PermissionLevel.ASK_USER,
            )
        )

        perm_engine = PermissionEngine()
        # Always deny
        hook = CallbackConfirmationHook(lambda req: False)
        tool_executor = ToolExecutor(
            registry=registry, permission_engine=perm_engine, confirmation_hook=hook
        )
        executor = TaskExecutor(tool_executor=tool_executor)

        plan = Plan(
            goal="Delete file with denial",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="Delete",
                    tool_name="files.delete_file",
                    parameters={"path": "secret.txt"},
                    expected_outcome="deleted",
                )
            ],
        )

        result = await executor.execute(plan)
        assert result.state == TaskState.FAILED
        assert "User confirmation denied" in (result.error or "")

    @pytest.mark.anyio
    async def test_visual_revalidation_failure_aborts_actuation(self) -> None:
        registry, tool_executor, _, _ = make_test_environment()

        class MissingElementVisionManager:
            async def find_element(self, description: str) -> list[BoundingBox]:
                return []  # Element not found

        revalidator = VisualRevalidator(vision_manager=MissingElementVisionManager())  # type: ignore[arg-type]
        executor = TaskExecutor(tool_executor=tool_executor, revalidator=revalidator)

        plan = Plan(
            goal="Click button with revalidation",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="Click Submit Button",
                    tool_name="os.input.click",
                    parameters={"x": 100, "y": 100},
                    revalidation_target="Submit Button",
                    requires_revalidation=True,
                    expected_outcome="clicked",
                )
            ],
        )

        result = await executor.execute(plan)
        assert result.state == TaskState.FAILED
        assert "Visual revalidation failed" in (result.error or "")
        assert len(result.step_results) == 1
        assert result.step_results[0].success is False
