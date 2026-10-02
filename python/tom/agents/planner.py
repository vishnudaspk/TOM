"""TaskPlanner — structured JSON task plan generation, DAG validation, and cycle detection.

Adheres to:
- Phase 8 Architecture (Task Planning & DAG Decomposition)
- Decision 033: Decoupled Model Provider Protocol
- Decision 035: Step-Bounded Execution Loop & Cooperative Cancellation
- Decision 051: Task Lifecycle State Machine & Step Decomposition
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from tom.core.context import CancellationToken
from tom.models.base import LLMProvider
from tom.schemas.models import Message, ModelRequest, Role
from tom.schemas.planner import (
    CycleDetectedError,
    DuplicateStepIdError,
    MissingDependencyError,
    Plan,
    PlannerValidationError,
    PlanStep,
    SelfDependencyError,
    UnknownToolError,
)
from tom.telemetry.logging import get_logger
from tom.tools.registry import ToolRegistry, default_registry

logger = get_logger(__name__)


def extract_json_payload(raw_content: str) -> dict[str, Any]:
    """Extract and parse a JSON object from raw model completion text.

    Supports raw JSON and markdown code block fences (```json ... ```).
    """
    content = raw_content.strip()
    if not content:
        raise PlannerValidationError("Model response is empty")

    # Strip code block fences if present
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if match:
        content = match.group(1).strip()
    elif content.startswith("```"):
        lines = content.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
            content = "\n".join(lines[1:-1]).strip()

    # If still not starting with {, search for first { to last }
    if not content.startswith("{"):
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            content = content[start : end + 1]

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise PlannerValidationError(f"Failed to parse JSON from model output: {exc}") from exc

    if not isinstance(data, dict):
        raise PlannerValidationError("Parsed JSON is not a JSON object")

    return data


class TaskPlanner:
    """Decomposes user tasks into validated, dependency-ordered execution plans.

    Uses an abstract LLMProvider to generate structured JSON plans, validates
    all referenced tools against a ToolRegistry, and verifies DAG dependency validity
    via topological sorting.
    """

    def __init__(
        self,
        model_provider: LLMProvider,
        tool_registry: ToolRegistry | None = None,
        model_name: str = "qwen3-8b",
        max_steps: int = 20,
    ) -> None:
        self.model_provider = model_provider
        self.tool_registry = tool_registry or default_registry
        self.model_name = model_name
        self.max_steps = max_steps

    def _build_system_prompt(self) -> str:
        """Construct deterministic system instructions including tool signatures."""
        tools = self.tool_registry.list_tools()
        tool_docs = [
            f"- {t.name}: {t.description.strip()} (category: {t.category}, permission: {t.permission_level})"
            for t in tools
        ]
        tools_str = "\n".join(tool_docs)

        return (
            "You are TOM's Task Planner. Your job is to decompose user goals into a bounded, "
            "executable plan of discrete steps.\n\n"
            "AVAILABLE TOOLS:\n"
            f"{tools_str}\n\n"
            "REQUIREMENTS:\n"
            "1. Output ONLY a valid JSON object matching the Plan schema.\n"
            "2. Each step must use a valid tool from the available tools list.\n"
            "3. Dependencies must reference prior step_ids.\n"
            "4. Do NOT create circular dependencies.\n"
            "5. Mark the final concluding step with 'is_terminal': true.\n"
            "6. Keep the plan strictly bounded and minimal.\n"
        )

    async def plan(
        self,
        goal: str,
        context: dict[str, Any] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> Plan:
        """Generate and validate a structured execution plan for a goal."""
        if cancellation_token and cancellation_token.is_cancelled():
            raise asyncio.CancelledError("Planning cancelled before model request")

        user_content = f"Goal: {goal}"
        if context:
            user_content += f"\nContext: {json.dumps(context, default=str)}"

        request = ModelRequest(
            model=self.model_name,
            messages=[
                Message(role=Role.SYSTEM, content=self._build_system_prompt()),
                Message(role=Role.USER, content=user_content),
            ],
            temperature=0.2,
            max_tokens=2048,
        )

        logger.info("task_planning_started", goal=goal, model=self.model_name)
        response = await self.model_provider.generate(request)

        if cancellation_token and cancellation_token.is_cancelled():
            raise asyncio.CancelledError("Planning cancelled after model response")

        parsed_data = extract_json_payload(response.content)

        try:
            plan = Plan.model_validate(parsed_data)
        except Exception as exc:
            raise PlannerValidationError(f"Plan schema validation failed: {exc}") from exc

        self.validate_plan(plan)
        logger.info("task_planning_succeeded", plan_id=plan.plan_id, steps_count=len(plan.steps))
        return plan

    async def replan(
        self,
        goal: str,
        failed_step: PlanStep,
        failure_reason: str,
        context: dict[str, Any] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> Plan:
        """Generate an alternative plan when a step fails during execution."""
        if cancellation_token and cancellation_token.is_cancelled():
            raise asyncio.CancelledError("Replanning cancelled before model request")

        replan_context = dict(context or {})
        replan_context["failed_step"] = {
            "step_id": failed_step.step_id,
            "tool_name": failed_step.tool_name,
            "description": failed_step.description,
            "failure_reason": failure_reason,
        }

        user_content = (
            f"Original Goal: {goal}\n"
            f"Step '{failed_step.step_id}' ({failed_step.tool_name}) failed with reason: {failure_reason}\n"
            "Generate a new or revised plan to achieve the goal avoiding the failed approach."
        )

        request = ModelRequest(
            model=self.model_name,
            messages=[
                Message(role=Role.SYSTEM, content=self._build_system_prompt()),
                Message(role=Role.USER, content=user_content),
            ],
            temperature=0.2,
            max_tokens=2048,
        )

        logger.info("task_replanning_started", goal=goal, failed_step=failed_step.step_id)
        response = await self.model_provider.generate(request)

        if cancellation_token and cancellation_token.is_cancelled():
            raise asyncio.CancelledError("Replanning cancelled after model response")

        parsed_data = extract_json_payload(response.content)

        try:
            plan = Plan.model_validate(parsed_data)
        except Exception as exc:
            raise PlannerValidationError(f"Replanned schema validation failed: {exc}") from exc

        self.validate_plan(plan)
        logger.info("task_replanning_succeeded", plan_id=plan.plan_id, steps_count=len(plan.steps))
        return plan

    def validate_plan(self, plan: Plan) -> None:
        """Validate all invariants: bounded size, tool existence, and DAG acyclicity."""
        if len(plan.steps) > self.max_steps:
            raise PlannerValidationError(
                f"Plan exceeds maximum allowable step limit ({len(plan.steps)} > {self.max_steps})"
            )

        step_ids: set[str] = set()
        for step in plan.steps:
            # Check unique step IDs
            if step.step_id in step_ids:
                raise DuplicateStepIdError(f"Duplicate step_id '{step.step_id}' in plan")
            step_ids.add(step.step_id)

            # Check tool registration
            if not self.tool_registry.has(step.tool_name):
                raise UnknownToolError(
                    f"Tool '{step.tool_name}' referenced in step '{step.step_id}' is not registered"
                )

        # Dependency references and cycle check
        for step in plan.steps:
            for dep in step.dependencies:
                if dep.step_id == step.step_id:
                    raise SelfDependencyError(
                        f"Step '{step.step_id}' cannot declare a dependency on itself"
                    )
                if dep.step_id not in step_ids:
                    raise MissingDependencyError(
                        f"Step '{step.step_id}' depends on nonexistent step '{dep.step_id}'"
                    )

        # Check for cycles via topological ordering
        self.get_execution_order(plan)

    def get_execution_order(self, plan: Plan) -> list[PlanStep]:
        """Compute a deterministic topological execution order for plan steps.

        Raises:
            CycleDetectedError: If the step dependency graph contains a cycle.
        """
        step_map = {step.step_id: step for step in plan.steps}
        # In-degree count: number of prerequisites step must wait for
        in_degree: dict[str, int] = {step.step_id: 0 for step in plan.steps}
        # Adjacency list: prereq_id -> list of dependent step_ids
        adj: dict[str, list[str]] = {step.step_id: [] for step in plan.steps}

        for step in plan.steps:
            for dep in step.dependencies:
                if dep.step_id in step_map:
                    adj[dep.step_id].append(step.step_id)
                    in_degree[step.step_id] += 1

        # Preserve deterministic ordering using original plan index
        index_map = {step.step_id: idx for idx, step in enumerate(plan.steps)}

        # Queue nodes with in_degree == 0
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
