"""TaskExecutor — deterministic execution engine for structured task plans.

Adheres to:
- Phase 8 Architecture (Execution Engine & Checkpointing)
- Decision 035: Step-Bounded Execution Loop & Cooperative Cancellation
- Decision 051: Task Lifecycle State Machine & Step Decomposition
"""

from __future__ import annotations

import time

from tom.agents.loop_detector import LoopDetector
from tom.agents.revalidator import VisualRevalidator
from tom.agents.task_manager import TaskManager
from tom.core.context import CancellationToken
from tom.schemas.planner import CycleDetectedError, Plan, PlanStep
from tom.schemas.task import StepResult, TaskResult, TaskState
from tom.security.permissions import PermissionDeniedError
from tom.telemetry.logging import get_logger
from tom.tools.executor import ToolExecutor
from tom.tools.registry import ToolResult

logger = get_logger(__name__, component="agents.executor")


def resolve_execution_order(plan: Plan) -> list[PlanStep]:
    """Compute a deterministic topological execution order for plan steps."""
    step_map = {step.step_id: step for step in plan.steps}
    in_degree: dict[str, int] = {step.step_id: 0 for step in plan.steps}
    adj: dict[str, list[str]] = {step.step_id: [] for step in plan.steps}

    for step in plan.steps:
        for dep in step.dependencies:
            if dep.step_id in step_map:
                adj[dep.step_id].append(step.step_id)
                in_degree[step.step_id] += 1

    index_map = {step.step_id: idx for idx, step in enumerate(plan.steps)}
    ready: list[str] = [sid for sid, deg in in_degree.items() if deg == 0]
    ready.sort(key=lambda sid: index_map[sid])

    ordered: list[PlanStep] = []
    while ready:
        curr_id = ready.pop(0)
        ordered.append(step_map[curr_id])

        for dependent_id in adj[curr_id]:
            in_degree[dependent_id] -= 1
            if in_degree[dependent_id] == 0:
                ready.append(dependent_id)
                ready.sort(key=lambda sid: index_map[sid])

    if len(ordered) != len(plan.steps):
        cycle_nodes = [sid for sid, deg in in_degree.items() if deg > 0]
        raise CycleDetectedError(
            f"Cyclic dependency detected among steps: {', '.join(cycle_nodes)}"
        )

    return ordered


class TaskExecutor:
    """Coordinates execution of structured plans through deterministic boundaries.

    Ensures:
    1. Every tool call passes strictly through ToolExecutor -> PermissionEngine.
    2. Cooperative cancellation is checked before every action.
    3. Step budget and wall-clock execution timeouts are enforced.
    4. LoopDetector monitors tool repetitions and visual stagnation.
    5. VisualRevalidator verifies targets before physical OS actuation.
    6. Checkpoints are recorded to SQLite after each completed step.
    7. ASK_USER actions transition the task to WAITING_CONFIRMATION.
    """

    def __init__(
        self,
        tool_executor: ToolExecutor,
        task_manager: TaskManager | None = None,
        revalidator: VisualRevalidator | None = None,
        loop_detector: LoopDetector | None = None,
        max_steps: int = 15,
        timeout_seconds: float = 300.0,
    ) -> None:
        self.tool_executor = tool_executor
        self.task_manager = task_manager or TaskManager(db_path=":memory:")
        self.revalidator = revalidator or VisualRevalidator()
        self.loop_detector = loop_detector or LoopDetector()
        self.max_steps = max(1, max_steps)
        self.timeout_seconds = max(0.01, timeout_seconds)

    async def execute(
        self,
        plan: Plan,
        task_id: str | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> TaskResult:
        """Execute a validated Plan sequentially according to its DAG ordering."""
        # 1. Initialize or load task
        if task_id is None:
            task = self.task_manager.create(
                goal=plan.goal,
                max_steps=self.max_steps,
                timeout_seconds=self.timeout_seconds,
            )
            task_id = task.task_id
        else:
            task = self.task_manager.get(task_id)

        # 2. Reconcile cancellation token
        token = cancellation_token or self.task_manager.get_token(task_id)

        # 3. Transition to EXECUTING if not already
        if task.state == TaskState.CREATED:
            self.task_manager.transition(task_id, TaskState.ROUTING)
            self.task_manager.transition(task_id, TaskState.EXECUTING)
        elif task.state in (TaskState.ROUTING, TaskState.PLANNING):
            self.task_manager.transition(task_id, TaskState.EXECUTING)

        ordered_steps = resolve_execution_order(plan)
        step_results: list[StepResult] = []
        completed_success_steps: set[str] = set()

        start_time = time.monotonic()
        steps_executed = 0

        logger.info(
            "task_execution_started",
            task_id=task_id,
            total_plan_steps=len(ordered_steps),
            max_steps=self.max_steps,
            timeout_seconds=self.timeout_seconds,
        )

        for step in ordered_steps:
            # ------------------------------------------------------------------
            # Cooperative Cancellation Check
            # ------------------------------------------------------------------
            if token.is_cancelled():
                logger.info("task_execution_cancelled", task_id=task_id, step_id=step.step_id)
                self.task_manager.cancel(task_id)
                return TaskResult(
                    task_id=task_id,
                    state=TaskState.CANCELLED,
                    step_results=step_results,
                    total_steps=steps_executed,
                    error="Task execution cancelled",
                )

            # ------------------------------------------------------------------
            # Wall-Clock Timeout Check
            # ------------------------------------------------------------------
            elapsed = time.monotonic() - start_time
            if elapsed > self.timeout_seconds:
                error_msg = f"Task execution exceeded timeout limit ({self.timeout_seconds}s)"
                logger.warning("task_execution_timeout", task_id=task_id, elapsed=elapsed)
                self.task_manager.fail(task_id, error_msg)
                return TaskResult(
                    task_id=task_id,
                    state=TaskState.FAILED,
                    step_results=step_results,
                    total_steps=steps_executed,
                    error=error_msg,
                )

            # ------------------------------------------------------------------
            # Step Budget Check
            # ------------------------------------------------------------------
            if steps_executed >= self.max_steps:
                error_msg = f"Task execution exceeded step budget limit ({self.max_steps} steps)"
                logger.warning("task_step_budget_exhausted", task_id=task_id, steps=steps_executed)
                self.task_manager.fail(task_id, error_msg)
                return TaskResult(
                    task_id=task_id,
                    state=TaskState.FAILED,
                    step_results=step_results,
                    total_steps=steps_executed,
                    error=error_msg,
                )

            # ------------------------------------------------------------------
            # Prerequisite Dependency Validation
            # ------------------------------------------------------------------
            for dep in step.dependencies:
                if dep.step_id not in completed_success_steps:
                    error_msg = (
                        f"Step '{step.step_id}' prerequisite dependency '{dep.step_id}' "
                        f"was not satisfied or failed"
                    )
                    logger.error("step_dependency_unmet", task_id=task_id, error=error_msg)
                    failed_res = StepResult(
                        step_id=step.step_id,
                        step_index=steps_executed,
                        tool_name=step.tool_name,
                        success=False,
                        error=error_msg,
                    )
                    self.task_manager.record_step(task_id, failed_res)
                    step_results.append(failed_res)
                    self.task_manager.fail(task_id, error_msg)
                    return TaskResult(
                        task_id=task_id,
                        state=TaskState.FAILED,
                        step_results=step_results,
                        total_steps=steps_executed + 1,
                        error=error_msg,
                    )

            # ------------------------------------------------------------------
            # Closed-Loop Visual Revalidation Gate
            # ------------------------------------------------------------------
            if step.requires_revalidation:
                reval_ok = await self.revalidator.revalidate(step)
                if not reval_ok:
                    error_msg = (
                        f"Visual revalidation failed for step '{step.step_id}': "
                        f"target '{step.revalidation_target or step.description}' missing or moved"
                    )
                    logger.warning("step_revalidation_failed", task_id=task_id, error=error_msg)
                    failed_res = StepResult(
                        step_id=step.step_id,
                        step_index=steps_executed,
                        tool_name=step.tool_name,
                        success=False,
                        error=error_msg,
                    )
                    self.task_manager.record_step(task_id, failed_res)
                    step_results.append(failed_res)
                    self.task_manager.fail(task_id, error_msg)
                    return TaskResult(
                        task_id=task_id,
                        state=TaskState.FAILED,
                        step_results=step_results,
                        total_steps=steps_executed + 1,
                        error=error_msg,
                    )

            # ------------------------------------------------------------------
            # Permission Pre-Check & WAITING_CONFIRMATION Transition
            # ------------------------------------------------------------------
            tool_def = self.tool_executor.registry.get(step.tool_name)
            decision = self.tool_executor.permission_engine.evaluate(
                tool_def, params=step.parameters
            )

            needs_confirmation = decision.requires_confirmation
            if needs_confirmation:
                self.task_manager.transition(task_id, TaskState.WAITING_CONFIRMATION)

            try:
                tool_result: ToolResult = await self.tool_executor.execute(
                    step.tool_name,
                    params=step.parameters,
                )
            except PermissionDeniedError as p_err:
                tool_result = ToolResult.fail(error=str(p_err))
            except Exception as exc:
                tool_result = ToolResult.fail(error=f"Unexpected tool error: {exc}")
            finally:
                if (
                    needs_confirmation
                    and self.task_manager.get(task_id).state == TaskState.WAITING_CONFIRMATION
                ):
                    self.task_manager.transition(task_id, TaskState.EXECUTING)

            # ------------------------------------------------------------------
            # Loop Detection & Checkpointing
            # ------------------------------------------------------------------
            self.loop_detector.record_tool_call(
                tool_name=step.tool_name,
                parameters=step.parameters,
                outcome=tool_result.data if tool_result.success else tool_result.error,
                success=tool_result.success,
            )

            res = StepResult(
                step_id=step.step_id,
                step_index=steps_executed,
                tool_name=step.tool_name,
                success=tool_result.success,
                output=tool_result.data,
                error=tool_result.error,
                execution_time_ms=tool_result.execution_time_ms,
            )
            self.task_manager.record_step(task_id, res)
            step_results.append(res)
            steps_executed += 1

            if self.loop_detector.is_loop_detected():
                loop_reason = self.loop_detector.get_loop_reason() or "Execution loop detected"
                logger.error("task_loop_aborted", task_id=task_id, reason=loop_reason)
                self.task_manager.fail(task_id, loop_reason)
                return TaskResult(
                    task_id=task_id,
                    state=TaskState.FAILED,
                    step_results=step_results,
                    total_steps=steps_executed,
                    error=loop_reason,
                )

            # ------------------------------------------------------------------
            # Step Outcome Evaluation
            # ------------------------------------------------------------------
            if not tool_result.success:
                err_text = tool_result.error or "Step failed"
                logger.warning(
                    "step_execution_failed", task_id=task_id, step_id=step.step_id, error=err_text
                )
                self.task_manager.fail(task_id, err_text)
                return TaskResult(
                    task_id=task_id,
                    state=TaskState.FAILED,
                    step_results=step_results,
                    total_steps=steps_executed,
                    error=err_text,
                )

            completed_success_steps.add(step.step_id)

            if step.is_terminal:
                logger.info("terminal_step_reached", task_id=task_id, step_id=step.step_id)
                break

        # ----------------------------------------------------------------------
        # Task Completion
        # ----------------------------------------------------------------------
        if time.monotonic() - start_time > self.timeout_seconds:
            error_msg = f"Task execution exceeded timeout limit ({self.timeout_seconds}s)"
            logger.warning(
                "task_execution_timeout", task_id=task_id, elapsed=time.monotonic() - start_time
            )
            self.task_manager.fail(task_id, error_msg)
            return TaskResult(
                task_id=task_id,
                state=TaskState.FAILED,
                step_results=step_results,
                total_steps=steps_executed,
                error=error_msg,
            )

        final_result = TaskResult(
            task_id=task_id,
            state=TaskState.COMPLETED,
            summary=f"Task completed successfully with {steps_executed} steps executed",
            step_results=step_results,
            total_steps=steps_executed,
        )
        self.task_manager.complete(task_id, final_result)
        logger.info("task_execution_completed", task_id=task_id, total_steps=steps_executed)
        return final_result
