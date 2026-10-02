"""TOM Deterministic Vision Tools.

Provides typed, deterministic vision inspection tools integrating with VisionManager:
- vision.capture (SAFE): Captures screen/monitor; returns screen dimensions and active window metadata.
- vision.ocr (SAFE): Extracts on-screen text; returns sanitized text, bounding boxes, and confidence.
- vision.find_element (SAFE): Locates UI element by text or description; returns center coordinates (x, y).
- vision.ask (SAFE): Asks local VLM a question about screen or cropped region; returns text answer.

Adheres to:
- Phase 7 Vision Specification (Iteration 4)
- Decision 032: Centralized Tool Invocation Safety (Strict ToolExecutor Routing)
- Zero disk persistence: all frames processed ephemerally in RAM.
- All vision tools are classified as SAFE.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from tom.schemas.vision import BoundingBox, ScreenDimensions
from tom.security.permissions import PermissionLevel
from tom.tools.registry import ToolDefinition, ToolRegistry, default_registry

if TYPE_CHECKING:
    from tom.vision.manager import VisionManager


# ---------------------------------------------------------------------------
# Pydantic Input and Output Schemas
# ---------------------------------------------------------------------------


class VisionCaptureInput(BaseModel):
    """Input parameters for vision.capture tool."""

    model_config = ConfigDict(extra="forbid")

    monitor: int | None = Field(
        default=None,
        description="Monitor index to capture (None/0 for primary or virtual desktop, 1..N for specific display)",
    )
    region: list[int] | None = Field(
        default=None,
        description="Optional bounding box [left, top, right, bottom] in virtual desktop pixels",
    )


class VisionCaptureOutput(BaseModel):
    """Output result for vision.capture tool."""

    model_config = ConfigDict(extra="ignore")

    width: int = Field(description="Width of captured frame in pixels")
    height: int = Field(description="Height of captured frame in pixels")
    monitor_index: int | None = Field(default=None, description="Index of captured monitor")
    active_window_title: str | None = Field(
        default=None, description="Title of active foreground window if detected"
    )
    timestamp: float = Field(default_factory=time.time, description="Unix timestamp of capture")


class VisionOcrInput(BaseModel):
    """Input parameters for vision.ocr tool."""

    model_config = ConfigDict(extra="forbid")

    monitor: int | None = Field(
        default=None,
        description="Monitor index to capture and OCR (None/0 for primary)",
    )
    region: list[int] | None = Field(
        default=None,
        description="Optional crop region [left, top, right, bottom] to OCR",
    )


class VisionOcrOutput(BaseModel):
    """Output result for vision.ocr tool."""

    model_config = ConfigDict(extra="ignore")

    text: str = Field(description="Extracted and sanitized text from screen")
    confidence: float = Field(default=1.0, description="Overall OCR confidence score (0.0 to 1.0)")
    bounding_boxes: list[BoundingBox] = Field(
        default_factory=list, description="Structured bounding boxes for detected words"
    )
    count: int = Field(default=0, description="Number of detected text segments")


class VisionFindElementInput(BaseModel):
    """Input parameters for vision.find_element tool."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(
        ...,
        min_length=1,
        description="Label, text, or visual description of the element to locate",
    )
    monitor: int | None = Field(
        default=None,
        description="Monitor index to search (None/0 for primary)",
    )


class VisionFindElementOutput(BaseModel):
    """Output result for vision.find_element tool."""

    model_config = ConfigDict(extra="ignore")

    found: bool = Field(description="Whether a matching UI element was located")
    x: int | None = Field(
        default=None,
        description="Center X coordinate in virtual desktop pixels (ready for click)",
    )
    y: int | None = Field(
        default=None,
        description="Center Y coordinate in virtual desktop pixels (ready for click)",
    )
    description: str = Field(description="Target description searched for")
    confidence: float = Field(default=0.0, description="Detection confidence score (0.0 to 1.0)")
    bounding_box: BoundingBox | None = Field(
        default=None, description="Bounding box of the located element"
    )


class VisionAskInput(BaseModel):
    """Input parameters for vision.ask tool."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        ...,
        min_length=1,
        description="Question or prompt about visual content on the screen",
    )
    monitor: int | None = Field(
        default=None,
        description="Monitor index to inspect (None/0 for primary)",
    )
    region: list[int] | None = Field(
        default=None,
        description="Optional crop region [left, top, right, bottom] to analyze",
    )


class VisionAskOutput(BaseModel):
    """Output result for vision.ask tool."""

    model_config = ConfigDict(extra="ignore")

    answer: str = Field(description="Perception answer or visual reasoning output")
    capability_used: str = Field(default="FAST_VLM", description="Perception tier utilized")


# ---------------------------------------------------------------------------
# Tool Factory & Registration
# ---------------------------------------------------------------------------


def _to_screen_dimensions(region: list[int]) -> ScreenDimensions:
    """Convert [left, top, right, bottom] to ScreenDimensions(left, top, width, height)."""
    left = region[0]
    top = region[1]
    width = max(region[2] - left, 1)
    height = max(region[3] - top, 1)
    return ScreenDimensions(left=left, top=top, width=width, height=height)


def create_vision_tools(
    vision_manager: VisionManager | Any | None = None,
) -> list[ToolDefinition]:
    """Create deterministic vision perception tools bound to a VisionManager.

    Args:
        vision_manager: Optional injected VisionManager instance. If None,
            a default VisionManager is constructed on first invocation.

    Returns:
        List of ToolDefinition instances (vision.capture, vision.ocr,
        vision.find_element, vision.ask).
    """
    _manager = vision_manager

    def _get_manager() -> Any:
        nonlocal _manager
        if _manager is None:
            from tom.vision.manager import VisionManager

            _manager = VisionManager()
        return _manager

    async def capture(params: VisionCaptureInput) -> VisionCaptureOutput:
        """Capture current screen or specific monitor/region in memory."""
        mgr = _get_manager()
        active_title: str | None = None
        if hasattr(mgr, "capture_service"):
            cs = mgr.capture_service
            if hasattr(cs, "privacy_shield") and hasattr(
                cs.privacy_shield, "get_active_window_title"
            ):
                active_title = cs.privacy_shield.get_active_window_title()

            if params.region:
                r = _to_screen_dimensions(params.region)
                frame = cs.capture_region(r)
            else:
                frame = cs.capture_screen(monitor_index=params.monitor)
            return VisionCaptureOutput(
                width=frame.width,
                height=frame.height,
                monitor_index=frame.monitor_index,
                active_window_title=active_title,
            )

        # Fallback if manager is a mock/stub without capture_service
        return VisionCaptureOutput(
            width=1920,
            height=1080,
            monitor_index=params.monitor or 0,
            active_window_title=active_title,
        )

    async def ocr(params: VisionOcrInput) -> VisionOcrOutput:
        """Extract structured OCR text from current screen or cropped region."""
        mgr = _get_manager()
        frame = None
        if hasattr(mgr, "capture_service"):
            if params.region:
                r = _to_screen_dimensions(params.region)
                frame = mgr.capture_service.capture_region(r)
            else:
                frame = mgr.capture_service.capture_screen(monitor_index=params.monitor)

        result = await mgr.extract_text(image=frame)
        boxes = [w.bounding_box for w in getattr(result, "words", [])]
        if not boxes and hasattr(result, "bounding_boxes"):
            boxes = result.bounding_boxes
        return VisionOcrOutput(
            text=result.text,
            confidence=result.confidence,
            bounding_boxes=boxes,
            count=len(boxes),
        )

    async def find_element(
        params: VisionFindElementInput,
    ) -> VisionFindElementOutput:
        """Locate UI element on screen; returns canonical center (x, y) coordinates."""
        mgr = _get_manager()
        frame = None
        if hasattr(mgr, "capture_service"):
            frame = mgr.capture_service.capture_screen(monitor_index=params.monitor)

        boxes = await mgr.find_element(params.description, image=frame)
        if boxes:
            best = boxes[0]
            cx, cy = best.center
            return VisionFindElementOutput(
                found=True,
                x=cx,
                y=cy,
                description=params.description,
                confidence=0.9,
                bounding_box=best,
            )

        return VisionFindElementOutput(
            found=False,
            description=params.description,
            confidence=0.0,
        )

    async def ask(params: VisionAskInput) -> VisionAskOutput:
        """Ask question about screen content via local VLM tier."""
        mgr = _get_manager()
        frame = None
        if hasattr(mgr, "capture_service"):
            if params.region:
                r = _to_screen_dimensions(params.region)
                frame = mgr.capture_service.capture_region(r)
            else:
                frame = mgr.capture_service.capture_screen(monitor_index=params.monitor)

        answer = await mgr.ask(params.question, image=frame)
        return VisionAskOutput(
            answer=answer,
            capability_used="FAST_VLM",
        )

    return [
        ToolDefinition(
            name="vision.capture",
            description="Capture the current screen or monitor into memory and inspect dimensions/active window.",
            category="vision",
            input_schema=VisionCaptureInput,
            output_schema=VisionCaptureOutput,
            permission_level=PermissionLevel.SAFE,
            handler=capture,
        ),
        ToolDefinition(
            name="vision.ocr",
            description="Extract text from the screen using CPU-first OCR, returning structured bounding boxes.",
            category="vision",
            input_schema=VisionOcrInput,
            output_schema=VisionOcrOutput,
            permission_level=PermissionLevel.SAFE,
            handler=ocr,
        ),
        ToolDefinition(
            name="vision.find_element",
            description="Locate a button, textbox, or visual element on screen and return its center coordinates (x, y).",
            category="vision",
            input_schema=VisionFindElementInput,
            output_schema=VisionFindElementOutput,
            permission_level=PermissionLevel.SAFE,
            handler=find_element,
        ),
        ToolDefinition(
            name="vision.ask",
            description="Query the local VLM with a question about visual screen content or a cropped region.",
            category="vision",
            input_schema=VisionAskInput,
            output_schema=VisionAskOutput,
            permission_level=PermissionLevel.SAFE,
            handler=ask,
        ),
    ]


def register_vision_tools(
    registry: ToolRegistry | None = None,
    vision_manager: Any | None = None,
    replace: bool = True,
) -> list[ToolDefinition]:
    """Register all deterministic vision perception tools into a ToolRegistry.

    Args:
        registry: Target ToolRegistry (defaults to default_registry).
        vision_manager: Injected VisionManager instance.
        replace: Whether to replace existing registrations.

    Returns:
        List of registered ToolDefinition instances.
    """
    target_registry = registry if registry is not None else default_registry
    tools = create_vision_tools(vision_manager=vision_manager)
    for tool in tools:
        target_registry.register(tool, replace=replace)
    return tools
