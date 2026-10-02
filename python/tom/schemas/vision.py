"""Vision and screen capture schemas for TOM.

Adheres to Phase 7 Iteration 1 + Iteration 2 requirements:
- Strict Pydantic v2 schemas
- In-memory ephemeral frame representation (no disk caching, no raw bytes in logs)
- Multi-monitor geometry and DPI-aware coordinates
- Structured OCR result types (OCRResult, TextLocation, BoundingBox)
- CV element detection types (UIElement, ElementType)
"""

from __future__ import annotations

import io
import time
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from PIL import Image


class ScreenDimensions(BaseModel):
    """Bounding dimensions for screen regions and virtual desktop coordinates."""

    left: int = Field(default=0, description="Virtual desktop left pixel coordinate")
    top: int = Field(default=0, description="Virtual desktop top pixel coordinate")
    width: int = Field(gt=0, description="Region width in pixels")
    height: int = Field(gt=0, description="Region height in pixels")


class MonitorInfo(BaseModel):
    """Information and geometry for a connected monitor."""

    index: int = Field(description="Monitor index (0: virtual desktop, 1..N: physical displays)")
    name: str = Field(default="", description="Monitor device name or identifier")
    x: int = Field(default=0, description="Left coordinate in virtual desktop space")
    y: int = Field(default=0, description="Top coordinate in virtual desktop space")
    width: int = Field(gt=0, description="Monitor pixel width")
    height: int = Field(gt=0, description="Monitor pixel height")
    is_primary: bool = Field(default=False, description="True if primary display")
    scale_factor: float = Field(
        default=1.0, gt=0.0, description="DPI scaling factor (e.g. 1.0, 1.25, 1.5, 2.0)"
    )


class WindowInfo(BaseModel):
    """Metadata and bounds of a desktop window."""

    handle: int = Field(description="Native window handle (HWND)")
    title: str = Field(description="Window title text")
    process_name: str | None = Field(default=None, description="Owning executable name")
    process_id: int | None = Field(default=None, description="Owning process PID")
    left: int = Field(default=0)
    top: int = Field(default=0)
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)
    is_minimized: bool = Field(default=False)
    is_visible: bool = Field(default=True)


class PrivacyCheckResult(BaseModel):
    """Outcome of privacy checks on window titles or screen regions."""

    is_safe: bool = Field(description="True if screen capture is permitted")
    reason: str | None = Field(default=None, description="Explanation if blocked")
    matched_pattern: str | None = Field(default=None, description="Pattern triggered if blocked")
    matched_title: str | None = Field(default=None, description="Title detected if blocked")
    timestamp: float = Field(default_factory=time.time)


class CapturedFrame(BaseModel):
    """Ephemeral in-memory captured screen frame.

    Security & Privacy Guarantee:
    - Lives in volatile RAM during request lifetime.
    - Excludes raw bytes from serialization/repr to prevent accidental log leakage.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    width: int = Field(gt=0, description="Frame pixel width")
    height: int = Field(gt=0, description="Frame pixel height")
    channels: int = Field(default=4, ge=1, le=4, description="Channel count (3: RGB, 4: RGBA)")
    format: str = Field(default="RGBA", description="Pixel encoding format")
    timestamp: float = Field(default_factory=time.time)
    monitor_index: int | None = Field(default=None)
    source_region: ScreenDimensions | None = Field(default=None)
    is_ephemeral: bool = Field(default=True, description="Strict RAM-only ephemeral indicator")
    raw_bytes: bytes = Field(
        default=b"",
        repr=False,
        exclude=True,
        description="Raw pixel bytes held in RAM (never dumped to logs or JSON)",
    )

    def to_pil(self) -> Image.Image:
        """Convert in-memory pixel bytes to a PIL Image without writing to disk."""
        from PIL import Image

        if (
            self.format in ("RGBA", "RGB")
            and len(self.raw_bytes) == self.width * self.height * self.channels
        ):
            return Image.frombytes(self.format, (self.width, self.height), self.raw_bytes)
        # Fallback if compressed bytes (e.g. PNG buffer in RAM)
        return Image.open(io.BytesIO(self.raw_bytes))

    @classmethod
    def from_pil(
        cls,
        image: Image.Image,
        monitor_index: int | None = None,
        source_region: ScreenDimensions | None = None,
    ) -> CapturedFrame:
        """Construct an ephemeral CapturedFrame from a PIL Image."""
        img_rgba = image.convert("RGBA")
        raw = img_rgba.tobytes()
        return cls(
            width=img_rgba.width,
            height=img_rgba.height,
            channels=4,
            format="RGBA",
            raw_bytes=raw,
            monitor_index=monitor_index,
            source_region=source_region
            or ScreenDimensions(
                left=0,
                top=0,
                width=img_rgba.width,
                height=img_rgba.height,
            ),
            is_ephemeral=True,
        )

    def clear(self) -> None:
        """Explicitly clear the raw frame buffer from RAM."""
        self.raw_bytes = b""

    def __repr__(self) -> str:
        return (
            f"<CapturedFrame {self.width}x{self.height} {self.format} "
            f"monitor={self.monitor_index} ephemeral={self.is_ephemeral}>"
        )


# ---------------------------------------------------------------------------
# Iteration 2: OCR structured types
# ---------------------------------------------------------------------------


class BoundingBox(BaseModel):
    """Normalised bounding box in pixel coordinates [left, top, right, bottom]."""

    left: float = Field(ge=0.0, description="Left pixel coordinate")
    top: float = Field(ge=0.0, description="Top pixel coordinate")
    right: float = Field(ge=0.0, description="Right pixel coordinate")
    bottom: float = Field(ge=0.0, description="Bottom pixel coordinate")

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)

    @property
    def center(self) -> tuple[float, float]:
        return (self.left + self.width / 2, self.top + self.height / 2)

    def scale(self, scale_x: float, scale_y: float) -> BoundingBox:
        """Return a new BoundingBox scaled by (scale_x, scale_y)."""
        return BoundingBox(
            left=self.left * scale_x,
            top=self.top * scale_y,
            right=self.right * scale_x,
            bottom=self.bottom * scale_y,
        )


class Point2D(BaseModel):
    """2-D pixel coordinate."""

    x: float = Field(description="Horizontal pixel position")
    y: float = Field(description="Vertical pixel position")


class TextLocation(BaseModel):
    """A recognised text word/phrase with its bounding box and confidence."""

    text: str = Field(description="Recognised text string")
    bounding_box: BoundingBox = Field(description="Bounding box in screen coordinates")
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Recogniser confidence score (0–1)"
    )


class OCRResult(BaseModel):
    """Structured result returned by an OCRProvider.

    Full text is sanitised by PrivacyShield before reaching this model.
    No raw image data is stored here.
    """

    text: str = Field(description="Full sanitised text extracted from the image")
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Overall confidence estimate (0–1)"
    )
    words: list[TextLocation] = Field(
        default_factory=list, description="Per-word locations with individual bounding boxes"
    )
    source_region: ScreenDimensions | None = Field(
        default=None, description="Screen region this result was extracted from"
    )


# ---------------------------------------------------------------------------
# Iteration 2: CV element detection types
# ---------------------------------------------------------------------------


class ElementType(StrEnum):
    """Classification of detected UI element."""

    BUTTON = "button"
    INPUT_FIELD = "input_field"
    TEXT_BLOCK = "text_block"
    ICON = "icon"
    CONTAINER = "container"
    UNKNOWN = "unknown"


class UIElement(BaseModel):
    """A UI element detected by the CVElementDetector."""

    element_type: ElementType = Field(description="Classified element type")
    bounding_box: BoundingBox = Field(description="Element bounding box in screen pixels")
    center: Point2D = Field(description="Element center in screen pixels")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Detection confidence (0–1)")
    label: str | None = Field(default=None, description="Optional text label from OCR")


# ---------------------------------------------------------------------------
# Iteration 3: VLM & VisionManager types
# ---------------------------------------------------------------------------


class VisionCapability(StrEnum):
    """Execution tiers for visual perception tasks."""

    OCR_ONLY = "ocr_only"
    ELEMENT_LOCATION = "element_location"
    FAST_VLM = "fast_vlm"
    PRIMARY_VLM = "primary_vlm"
    DEEP_VLM = "deep_vlm"


class VLMRequest(BaseModel):
    """Input payload for Vision-Language Model inference."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    prompt: str = Field(description="User prompt or visual question")
    image: CapturedFrame | Any = Field(
        default=None, description="Image to analyze (PIL Image or CapturedFrame)"
    )
    system_prompt: str | None = Field(default=None, description="Optional system instruction")
    max_tokens: int = Field(default=512, ge=1, le=4096, description="Max tokens for response")
    temperature: float = Field(default=0.2, ge=0.0, le=2.0, description="Sampling temperature")
    capability: VisionCapability = Field(
        default=VisionCapability.FAST_VLM, description="Target capability tier"
    )
    detail: str = Field(default="auto", description="Image resolution detail (low, high, auto)")


class VLMResponse(BaseModel):
    """Structured response from a VLMProvider."""

    content: str = Field(description="Generated textual answer or reasoning")
    bounding_boxes: list[BoundingBox] = Field(
        default_factory=list, description="Extracted bounding boxes in canonical coordinates"
    )
    model: str = Field(default="", description="Model name that served the request")
    usage: dict[str, int] = Field(default_factory=dict, description="Token usage statistics")
    finish_reason: str = Field(default="stop", description="Generation termination reason")


class VisionAnalysisRequest(BaseModel):
    """High-level perception request dispatched to VisionManager."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    query: str = Field(description="Natural language query, task description, or target label")
    image: CapturedFrame | Any = Field(
        default=None,
        description="Optional image to analyze; if None, current screen is captured",
    )
    capability: VisionCapability | None = Field(
        default=None,
        description="Explicit capability tier; if None, VisionManager auto-routes based on query",
    )
    system_prompt: str | None = Field(default=None)
    max_tokens: int = Field(default=512, ge=1, le=4096)


class VisionAnalysisResult(BaseModel):
    """Consolidated outcome from VisionManager analysis."""

    text: str = Field(description="Textual outcome (OCR transcript, element label, or VLM answer)")
    capability_used: VisionCapability = Field(
        description="Capability tier that processed this query"
    )
    bounding_boxes: list[BoundingBox] = Field(
        default_factory=list, description="Target bounding boxes if any"
    )
    elements: list[UIElement] = Field(
        default_factory=list, description="Detected UI elements if any"
    )
    ocr_result: OCRResult | None = Field(
        default=None, description="Structured OCR result if OCR was performed"
    )
    raw_vlm_response: VLMResponse | None = Field(
        default=None, description="Raw VLM response if VLM was invoked"
    )
    execution_time_ms: float = Field(
        default=0.0, ge=0.0, description="Total execution time in milliseconds"
    )


VLMRequest.model_rebuild()
VisionAnalysisRequest.model_rebuild()
