"""Classical computer vision element detector — Phase 7 Iteration 2.

Design rules:
- Pure CPU; 0 VRAM; 0 model downloads; targets < 50 ms on 1080p.
- OpenCV contour analysis with greyscale + Canny edges.
- Returns ``list[UIElement]`` with normalised bounding boxes and element classifications.
- Clean extension seam: override ``_classify_contour`` or register a custom detector hook
  for YOLO/SAM-style replacements without touching the caller interface.
- No disk writes; no persistent image data.
"""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING

from tom.schemas.vision import BoundingBox, ElementType, Point2D, UIElement
from tom.telemetry.logging import get_logger

if TYPE_CHECKING:
    from PIL import Image

logger = get_logger(__name__, component="vision.cv")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CVError(Exception):
    """Raised for fatal CV processing failures."""


# ---------------------------------------------------------------------------
# Extension seam — specialised detector hook
# ---------------------------------------------------------------------------


class ElementDetectorHook(abc.ABC):
    """Extension point for specialised detectors (e.g. YOLO, SAM).

    Register via ``CVElementDetector.add_hook(hook)`` to layer additional
    detection on top of classical contour analysis.
    """

    @abc.abstractmethod
    def detect(self, image: Image.Image) -> list[UIElement]:
        """Detect elements in *image* and return a list of ``UIElement``."""


# ---------------------------------------------------------------------------
# Classical detector
# ---------------------------------------------------------------------------

# Aspect-ratio and area thresholds for heuristic classification
_MIN_AREA_PX = 200  # ignore tiny noise contours
_BUTTON_MAX_ASPECT = 8.0  # wider buttons are reclassified as TEXT_BLOCK
_INPUT_ASPECT_MIN = 3.0  # wide and short rectangles → INPUT_FIELD
_INPUT_HEIGHT_MAX = 60  # pixels; taller is a CONTAINER or TEXT_BLOCK
_ICON_MAX_DIM = 64  # pixels on longest side
_CONTAINER_MIN_AREA = 40_000  # pixels²


def _classify_contour(left: float, top: float, w: float, h: float) -> ElementType:
    """Heuristic classification of a rectangular contour."""
    area = w * h
    aspect = w / h if h > 0 else 1.0

    if area >= _CONTAINER_MIN_AREA:
        return ElementType.CONTAINER
    if max(w, h) <= _ICON_MAX_DIM:
        return ElementType.ICON
    if aspect >= _INPUT_ASPECT_MIN and h <= _INPUT_HEIGHT_MAX:
        return ElementType.INPUT_FIELD
    if aspect <= _BUTTON_MAX_ASPECT and h <= _INPUT_HEIGHT_MAX:
        return ElementType.BUTTON
    return ElementType.TEXT_BLOCK


class CVElementDetector:
    """Detects UI elements in a PIL Image using classical OpenCV contour analysis.

    Usage::

        detector = CVElementDetector()
        elements = detector.detect(pil_image)

    Extend without changing the interface::

        detector.add_hook(MyYoloHook())
    """

    def __init__(
        self,
        canny_low: int = 50,
        canny_high: int = 150,
        min_area: int = _MIN_AREA_PX,
        max_elements: int = 200,
    ) -> None:
        self._canny_low = canny_low
        self._canny_high = canny_high
        self._min_area = min_area
        self._max_elements = max_elements
        self._hooks: list[ElementDetectorHook] = []

    def add_hook(self, hook: ElementDetectorHook) -> None:
        """Register a specialised detector hook (e.g. YOLO/SAM)."""
        self._hooks.append(hook)

    def detect(self, image: Image.Image) -> list[UIElement]:
        """Run contour detection on *image* and return classified UI elements.

        The image is processed entirely in RAM; no disk writes occur.

        Args:
            image: PIL Image in any mode; converted to greyscale internally.

        Returns:
            List of detected ``UIElement`` objects sorted top-left to bottom-right.

        Raises:
            CVError: If OpenCV is unavailable or image processing fails critically.
        """
        try:
            import cv2
            import numpy as np
        except ImportError as exc:
            raise CVError(
                "opencv-python and numpy are required: pip install opencv-python numpy"
            ) from exc

        img_w, img_h = image.size
        if img_w == 0 or img_h == 0:
            return []

        try:
            # Convert PIL → greyscale numpy array
            grey = np.array(image.convert("L"))

            # Canny edge detection
            edges = cv2.Canny(grey, self._canny_low, self._canny_high)

            # Morphological close to connect nearby edges
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)

            # Find external contours
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        except Exception as exc:
            logger.error("cv_detection_failed", error=str(exc))
            raise CVError(f"CV detection failed: {exc}") from exc

        elements: list[UIElement] = []
        for contour in contours:
            x, y, cw, ch = cv2.boundingRect(contour)
            area = cw * ch
            if area < self._min_area:
                continue

            bbox = BoundingBox(
                left=float(x),
                top=float(y),
                right=float(x + cw),
                bottom=float(y + ch),
            )
            element_type = _classify_contour(float(x), float(y), float(cw), float(ch))
            cx, cy = bbox.center
            elements.append(
                UIElement(
                    element_type=element_type,
                    bounding_box=bbox,
                    center=Point2D(x=cx, y=cy),
                    confidence=1.0,
                )
            )

        # Sort top-left → bottom-right (by top then left)
        elements.sort(key=lambda e: (e.bounding_box.top, e.bounding_box.left))

        # Cap results
        if len(elements) > self._max_elements:
            elements = elements[: self._max_elements]

        # Run registered hooks (future YOLO/SAM extensions)
        for hook in self._hooks:
            try:
                hook_results = hook.detect(image)
                elements.extend(hook_results)
            except Exception as exc:
                logger.warning("cv_hook_failed", hook=type(hook).__name__, error=str(exc))

        logger.debug("cv_detection_complete", element_count=len(elements))
        return elements
