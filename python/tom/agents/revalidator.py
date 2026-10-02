"""Closed-loop visual target revalidator for GUI safety.

Adheres to:
- Phase 8 Architecture (Closed-Loop Visual Target Revalidation)
- Decision 046-050: Vision Manager & Ephemeral Perception
- Decision 051: Task Lifecycle State Machine
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tom.schemas.planner import PlanStep
from tom.telemetry.logging import get_logger

if TYPE_CHECKING:
    from tom.vision.manager import VisionManager

logger = get_logger(__name__, component="agents.revalidator")


class VisualRevalidator:
    """Safety gate that revalidates visual UI targets before physical input actuation.

    Prevents the stale-observation GUI problem by checking that the intended target
    element is still present and that target coordinates remain valid before
    dispatching OS input tools (e.g. click, type_text, hotkey).
    """

    def __init__(self, vision_manager: VisionManager | None = None) -> None:
        self.vision_manager = vision_manager

    async def revalidate(self, step: PlanStep) -> bool:
        """Verify that the target element for an action step is present and current.

        Args:
            step: The PlanStep about to be executed.

        Returns:
            True if the target is verified or revalidation is not required.
            False if the target is missing, moved, or observation is stale.
        """
        if not step.requires_revalidation:
            return True

        if self.vision_manager is None:
            logger.warning(
                "visual_revalidation_failed_no_vision_manager",
                step_id=step.step_id,
            )
            return False

        target = step.revalidation_target or step.description
        if not target:
            logger.warning(
                "visual_revalidation_failed_empty_target",
                step_id=step.step_id,
            )
            return False

        # Query VisionManager for element presence (ephemeral in RAM)
        try:
            boxes = await self.vision_manager.find_element(target)
        except Exception as exc:
            logger.warning(
                "visual_revalidation_query_error",
                target=target,
                error=str(exc),
                step_id=step.step_id,
            )
            return False

        if not boxes:
            logger.warning(
                "visual_revalidation_target_missing",
                target=target,
                step_id=step.step_id,
            )
            return False

        # Check if coordinates (x, y) were specified in parameters
        params = step.parameters or {}
        x = params.get("x")
        y = params.get("y")
        if x is None and "coords" in params and isinstance(params["coords"], (list, tuple)):
            if len(params["coords"]) >= 2:
                x, y = params["coords"][0], params["coords"][1]

        if x is not None and y is not None:
            coords_match = any(
                box.left <= x <= box.right and box.top <= y <= box.bottom for box in boxes
            )
            if not coords_match:
                logger.warning(
                    "visual_revalidation_coordinates_stale",
                    target=target,
                    coords=(x, y),
                    step_id=step.step_id,
                )
                return False

        logger.info("visual_revalidation_succeeded", target=target, step_id=step.step_id)
        return True
