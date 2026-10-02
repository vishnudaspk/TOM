"""Unit tests for TaskPlanner and Plan schemas (Phase 8 Iteration 1).

Adheres to:
- Phase 8 Architecture (Task Planning & DAG Decomposition)
- Decision 033: Decoupled Model Provider Protocol
- Decision 035: Step-Bounded Execution Loop & Cooperative Cancellation
- Decision 051: Task Lifecycle State Machine & Step Decomposition
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError
from tom.agents.planner import TaskPlanner, extract_json_payload
from tom.core.context import CancellationToken
from tom.models.base import ModelProviderError
from tom.models.providers.mock import MockModelProvider
from tom.schemas.planner import (
    CycleDetectedError,
    DuplicateStepIdError,
    MissingDependencyError,
    Plan,
    PlannerValidationError,
    PlanStep,
    SelfDependencyError,
    StepDependency,
    UnknownToolError,
)
from tom.tools.registry import ToolDefinition, ToolRegistry

# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------


def make_test_registry() -> ToolRegistry:
    """Create an isolated registry populated with standard tools for testing."""
    reg = ToolRegistry()
    reg.register(
        ToolDefinition.from_function(
            lambda path: "content", name="files.read_file", category="files"
        )
    )
    reg.register(
        ToolDefinition.from_function(
            lambda path, content: True, name="files.write_file", category="files"
        )
    )
    reg.register(
        ToolDefinition.from_function(lambda: {"pong": True}, name="system.ping", category="system")
    )
    reg.register(
        ToolDefinition.from_function(lambda text: True, name="voice.announce", category="voice")
    )
    return reg


# ---------------------------------------------------------------------------
# JSON Extraction Tests
# ---------------------------------------------------------------------------


class TestExtractJsonPayload:
    def test_extract_plain_json(self) -> None:
        raw = '{"goal": "test", "steps": []}'
        data = extract_json_payload(raw)
        assert data["goal"] == "test"

    def test_extract_markdown_fenced_json(self) -> None:
        raw = """```json
{"goal": "fenced", "steps": []}
```"""
        data = extract_json_payload(raw)
        assert data["goal"] == "fenced"

    def test_extract_json_with_surrounding_commentary(self) -> None:
        raw = """Here is the execution plan for your request:
{"goal": "embedded", "steps": []}
Hope this helps!"""
        data = extract_json_payload(raw)
        assert data["goal"] == "embedded"

    def test_extract_empty_string_raises(self) -> None:
        with pytest.raises(PlannerValidationError, match="empty"):
            extract_json_payload("")

    def test_extract_invalid_json_raises(self) -> None:
        with pytest.raises(PlannerValidationError, match="Failed to parse JSON"):
            extract_json_payload("not json at all")


# ---------------------------------------------------------------------------
# Schema Model Validation Tests
# ---------------------------------------------------------------------------


class TestPlanSchemas:
    def test_plan_step_defaults(self) -> None:
        step = PlanStep(
            description="read a file",
            tool_name="files.read_file",
            expected_outcome="file content received",
        )
        assert step.step_id.startswith("step_")
        assert not step.requires_revalidation
        assert not step.is_terminal
        assert step.parameters == {}
        assert step.dependencies == []

    def test_empty_goal_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Plan(goal="", steps=[PlanStep(description="d", tool_name="t", expected_outcome="o")])

    def test_empty_steps_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Plan(goal="valid", steps=[])

    def test_exceeding_max_steps_rejected_by_schema(self) -> None:
        steps = [
            PlanStep(description=f"step {i}", tool_name="system.ping", expected_outcome="ok")
            for i in range(21)
        ]
        with pytest.raises(ValidationError):
            Plan(goal="too long", steps=steps)


# ---------------------------------------------------------------------------
# TaskPlanner Generation & Validation Tests
# ---------------------------------------------------------------------------


class TestTaskPlannerValidation:
    def test_validate_plan_accepts_valid_plan(self) -> None:
        registry = make_test_registry()
        planner = TaskPlanner(model_provider=MockModelProvider([]), tool_registry=registry)

        plan = Plan(
            goal="Ping system and report",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="Ping system",
                    tool_name="system.ping",
                    expected_outcome="ping ok",
                ),
                PlanStep(
                    step_id="s2",
                    description="Announce ping",
                    tool_name="voice.announce",
                    parameters={"text": "pong"},
                    dependencies=[StepDependency(step_id="s1")],
                    expected_outcome="announced",
                    is_terminal=True,
                ),
            ],
        )

        planner.validate_plan(plan)
        order = planner.get_execution_order(plan)
        assert [s.step_id for s in order] == ["s1", "s2"]

    def test_unknown_tool_rejected(self) -> None:
        registry = make_test_registry()
        planner = TaskPlanner(model_provider=MockModelProvider([]), tool_registry=registry)

        plan = Plan(
            goal="Use unknown tool",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="hack",
                    tool_name="unregistered.dangerous_tool",
                    expected_outcome="hacked",
                )
            ],
        )
        with pytest.raises(UnknownToolError, match="unregistered.dangerous_tool"):
            planner.validate_plan(plan)

    def test_duplicate_step_id_rejected(self) -> None:
        registry = make_test_registry()
        planner = TaskPlanner(model_provider=MockModelProvider([]), tool_registry=registry)

        plan = Plan(
            goal="Duplicate steps",
            steps=[
                PlanStep(
                    step_id="step_dup",
                    description="first",
                    tool_name="system.ping",
                    expected_outcome="ok",
                ),
                PlanStep(
                    step_id="step_dup",
                    description="second",
                    tool_name="system.ping",
                    expected_outcome="ok",
                ),
            ],
        )
        with pytest.raises(DuplicateStepIdError, match="step_dup"):
            planner.validate_plan(plan)

    def test_self_dependency_rejected(self) -> None:
        registry = make_test_registry()
        planner = TaskPlanner(model_provider=MockModelProvider([]), tool_registry=registry)

        plan = Plan(
            goal="Self dep",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="self loop",
                    tool_name="system.ping",
                    dependencies=[StepDependency(step_id="s1")],
                    expected_outcome="ok",
                )
            ],
        )
        with pytest.raises(SelfDependencyError, match="cannot declare a dependency on itself"):
            planner.validate_plan(plan)

    def test_missing_dependency_rejected(self) -> None:
        registry = make_test_registry()
        planner = TaskPlanner(model_provider=MockModelProvider([]), tool_registry=registry)

        plan = Plan(
            goal="Missing dep",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="wait for ghost",
                    tool_name="system.ping",
                    dependencies=[StepDependency(step_id="ghost_step")],
                    expected_outcome="ok",
                )
            ],
        )
        with pytest.raises(MissingDependencyError, match="ghost_step"):
            planner.validate_plan(plan)

    def test_cyclical_dependency_rejected(self) -> None:
        registry = make_test_registry()
        planner = TaskPlanner(model_provider=MockModelProvider([]), tool_registry=registry)

        plan = Plan(
            goal="Cycle",
            steps=[
                PlanStep(
                    step_id="A",
                    description="step A",
                    tool_name="system.ping",
                    dependencies=[StepDependency(step_id="B")],
                    expected_outcome="ok",
                ),
                PlanStep(
                    step_id="B",
                    description="step B",
                    tool_name="system.ping",
                    dependencies=[StepDependency(step_id="A")],
                    expected_outcome="ok",
                ),
            ],
        )
        with pytest.raises(CycleDetectedError, match="Cyclic dependency detected"):
            planner.validate_plan(plan)

    def test_topological_sort_preserves_deterministic_order(self) -> None:
        registry = make_test_registry()
        planner = TaskPlanner(model_provider=MockModelProvider([]), tool_registry=registry)

        # DAG: s3 depends on s1 and s2; s1 and s2 are independent
        plan = Plan(
            goal="Multi-step DAG",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="step 1",
                    tool_name="system.ping",
                    expected_outcome="ok",
                ),
                PlanStep(
                    step_id="s2",
                    description="step 2",
                    tool_name="files.read_file",
                    parameters={"path": "a.txt"},
                    expected_outcome="ok",
                ),
                PlanStep(
                    step_id="s3",
                    description="step 3",
                    tool_name="voice.announce",
                    dependencies=[StepDependency(step_id="s1"), StepDependency(step_id="s2")],
                    expected_outcome="ok",
                ),
            ],
        )
        order = planner.get_execution_order(plan)
        step_ids = [s.step_id for s in order]
        assert step_ids == ["s1", "s2", "s3"]


# ---------------------------------------------------------------------------
# TaskPlanner Async Execution Tests
# ---------------------------------------------------------------------------


class TestTaskPlannerAsync:
    @pytest.mark.anyio
    async def test_plan_successful_generation(self) -> None:
        registry = make_test_registry()
        scripted_plan = {
            "goal": "Read and announce config",
            "steps": [
                {
                    "step_id": "step_1",
                    "description": "Read config",
                    "tool_name": "files.read_file",
                    "parameters": {"path": "config.yaml"},
                    "expected_outcome": "config read",
                },
                {
                    "step_id": "step_2",
                    "description": "Announce config loaded",
                    "tool_name": "voice.announce",
                    "parameters": {"text": "Config loaded"},
                    "dependencies": [{"step_id": "step_1"}],
                    "expected_outcome": "announced",
                    "is_terminal": True,
                },
            ],
            "rationale": "Read file first then announce.",
        }

        mock_provider = MockModelProvider([json.dumps(scripted_plan)])
        planner = TaskPlanner(model_provider=mock_provider, tool_registry=registry)

        plan = await planner.plan("Read config and announce")
        assert plan.goal == "Read and announce config"
        assert len(plan.steps) == 2
        assert plan.steps[0].tool_name == "files.read_file"
        assert plan.steps[1].tool_name == "voice.announce"

    @pytest.mark.anyio
    async def test_cancellation_before_model_call(self) -> None:
        registry = make_test_registry()
        mock_provider = MockModelProvider(["{}"])
        planner = TaskPlanner(model_provider=mock_provider, tool_registry=registry)

        token = CancellationToken()
        token.cancel()

        with pytest.raises(asyncio.CancelledError, match="cancelled before model request"):
            await planner.plan("goal", cancellation_token=token)

    @pytest.mark.anyio
    async def test_model_provider_error_propagates(self) -> None:
        registry = make_test_registry()
        mock_provider = MockModelProvider([ModelProviderError("Endpoint unavailable")])
        planner = TaskPlanner(model_provider=mock_provider, tool_registry=registry)

        with pytest.raises(ModelProviderError, match="Endpoint unavailable"):
            await planner.plan("goal")

    @pytest.mark.anyio
    async def test_replan_flow(self) -> None:
        registry = make_test_registry()
        replan_json = {
            "goal": "Read alternative config",
            "steps": [
                {
                    "step_id": "step_alt",
                    "description": "Read fallback config",
                    "tool_name": "files.read_file",
                    "parameters": {"path": "default_config.yaml"},
                    "expected_outcome": "fallback loaded",
                    "is_terminal": True,
                }
            ],
            "rationale": "Original config missing, using fallback",
        }

        mock_provider = MockModelProvider([json.dumps(replan_json)])
        planner = TaskPlanner(model_provider=mock_provider, tool_registry=registry)

        failed_step = PlanStep(
            step_id="step_fail",
            description="Read config",
            tool_name="files.read_file",
            parameters={"path": "config.yaml"},
            expected_outcome="config read",
        )

        plan = await planner.replan(
            goal="Read config",
            failed_step=failed_step,
            failure_reason="FileNotFoundError: config.yaml does not exist",
        )
        assert len(plan.steps) == 1
        assert plan.steps[0].step_id == "step_alt"
        assert plan.steps[0].parameters["path"] == "default_config.yaml"
