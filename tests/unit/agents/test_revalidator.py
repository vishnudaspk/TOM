"""Unit tests for VisualRevalidator (Phase 8 Iteration 2).

Adheres to:
- Phase 8 Architecture (Closed-Loop Visual Target Revalidation)
- Decision 046-050: Vision Manager & Ephemeral Perception
- Decision 051: Task Lifecycle State Machine
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from tom.agents.revalidator import VisualRevalidator
from tom.schemas.planner import PlanStep
from tom.schemas.vision import BoundingBox

# ---------------------------------------------------------------------------
# Test Doubles
# ---------------------------------------------------------------------------


class FakeVisionManager:
    """Deterministic mock of VisionManager for offline visual revalidation."""

    def __init__(self, elements_map: dict[str, list[BoundingBox]] | None = None) -> None:
        self.elements_map = elements_map or {}
        self.call_count = 0

    async def find_element(self, description: str) -> list[BoundingBox]:
        self.call_count += 1
        return self.elements_map.get(description, [])


# ---------------------------------------------------------------------------
# VisualRevalidator Unit Tests
# ---------------------------------------------------------------------------


class TestVisualRevalidator:
    @pytest.mark.anyio
    async def test_non_revalidation_step_always_succeeds(self) -> None:
        revalidator = VisualRevalidator(vision_manager=None)
        step = PlanStep(
            description="read a file",
            tool_name="files.read_file",
            expected_outcome="file read",
            requires_revalidation=False,
        )
        assert await revalidator.revalidate(step) is True

    @pytest.mark.anyio
    async def test_missing_vision_manager_fails_revalidation(self) -> None:
        revalidator = VisualRevalidator(vision_manager=None)
        step = PlanStep(
            description="click submit",
            tool_name="os.input.click",
            revalidation_target="Submit Button",
            requires_revalidation=True,
            expected_outcome="clicked",
        )
        assert await revalidator.revalidate(step) is False

    @pytest.mark.anyio
    async def test_target_present_succeeds(self) -> None:
        fake_vm = FakeVisionManager(
            {"Submit Button": [BoundingBox(left=100.0, top=100.0, right=160.0, bottom=130.0)]}
        )
        revalidator = VisualRevalidator(vision_manager=fake_vm)  # type: ignore[arg-type]

        step = PlanStep(
            description="click submit",
            tool_name="os.input.click",
            revalidation_target="Submit Button",
            parameters={"x": 120, "y": 115},
            requires_revalidation=True,
            expected_outcome="clicked",
        )
        assert await revalidator.revalidate(step) is True
        assert fake_vm.call_count == 1

    @pytest.mark.anyio
    async def test_target_missing_fails(self) -> None:
        fake_vm = FakeVisionManager({})
        revalidator = VisualRevalidator(vision_manager=fake_vm)  # type: ignore[arg-type]

        step = PlanStep(
            description="click submit",
            tool_name="os.input.click",
            revalidation_target="Nonexistent Button",
            requires_revalidation=True,
            expected_outcome="clicked",
        )
        assert await revalidator.revalidate(step) is False

    @pytest.mark.anyio
    async def test_stale_coordinates_fail_revalidation(self) -> None:
        # Button is located at (100..160, 100..130), but click is targeted at (500, 500)
        fake_vm = FakeVisionManager(
            {"Submit Button": [BoundingBox(left=100.0, top=100.0, right=160.0, bottom=130.0)]}
        )
        revalidator = VisualRevalidator(vision_manager=fake_vm)  # type: ignore[arg-type]

        step = PlanStep(
            description="click submit",
            tool_name="os.input.click",
            revalidation_target="Submit Button",
            parameters={"x": 500, "y": 500},
            requires_revalidation=True,
            expected_outcome="clicked",
        )
        assert await revalidator.revalidate(step) is False

    @pytest.mark.anyio
    async def test_coordinates_tuple_format(self) -> None:
        fake_vm = FakeVisionManager(
            {"Icon": [BoundingBox(left=50.0, top=50.0, right=70.0, bottom=70.0)]}
        )
        revalidator = VisualRevalidator(vision_manager=fake_vm)  # type: ignore[arg-type]

        step_valid = PlanStep(
            description="click icon",
            tool_name="os.input.click",
            revalidation_target="Icon",
            parameters={"coords": [60, 60]},
            requires_revalidation=True,
            expected_outcome="clicked",
        )
        assert await revalidator.revalidate(step_valid) is True

        step_invalid = PlanStep(
            description="click icon",
            tool_name="os.input.click",
            revalidation_target="Icon",
            parameters={"coords": [10, 10]},
            requires_revalidation=True,
            expected_outcome="clicked",
        )
        assert await revalidator.revalidate(step_invalid) is False

    @pytest.mark.anyio
    async def test_vision_manager_exception_handled_safely(self) -> None:
        mock_vm = AsyncMock()
        mock_vm.find_element.side_effect = RuntimeError("Capture device failed")
        revalidator = VisualRevalidator(vision_manager=mock_vm)

        step = PlanStep(
            description="click button",
            tool_name="os.input.click",
            revalidation_target="Button",
            requires_revalidation=True,
            expected_outcome="clicked",
        )
        assert await revalidator.revalidate(step) is False
