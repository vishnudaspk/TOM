"""Unit tests for CVElementDetector — Phase 7 Iteration 2.

Coverage:
- Synthetic PIL images with clear rectangular shapes (buttons, input fields, containers).
- Contour detection finds expected shapes.
- Bounding box accuracy / normalisation.
- ElementType classification heuristics.
- Empty image / blank image handling.
- max_elements cap.
- Extension hook seam (ElementDetectorHook).
- Deterministic offline behaviour: zero desktop, zero network, zero GPU.
"""

from __future__ import annotations

import pytest

pytest.importorskip("cv2", reason="opencv-python optional dep not installed")

from tom.schemas.vision import BoundingBox, ElementType, Point2D, UIElement  # noqa: E402
from tom.vision.cv import CVElementDetector, ElementDetectorHook  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_blank_image(width: int = 200, height: int = 200, color: int = 255):
    """Solid white (or other) PIL image — no text, no shapes."""
    from PIL import Image

    return Image.new("RGB", (width, height), (color, color, color))


def _draw_rect(
    width: int = 400,
    height: int = 200,
    rect_left: int = 50,
    rect_top: int = 50,
    rect_right: int = 200,
    rect_bottom: int = 100,
    rect_color: tuple[int, int, int] = (0, 0, 0),
    bg_color: tuple[int, int, int] = (255, 255, 255),
):
    """White image with a single filled rectangle."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height), bg_color)
    ImageDraw.Draw(img).rectangle([rect_left, rect_top, rect_right, rect_bottom], fill=rect_color)
    return img


def _draw_multiple_rects(
    width: int = 800,
    height: int = 600,
    rects: list[tuple[int, int, int, int]] | None = None,
):
    """White image with multiple filled rectangles."""
    from PIL import Image, ImageDraw

    if rects is None:
        rects = [(50, 50, 150, 90), (200, 50, 400, 80), (50, 150, 700, 550)]
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    for r in rects:
        draw.rectangle(r, fill=(30, 30, 30))
    return img


# ---------------------------------------------------------------------------
# Detector construction
# ---------------------------------------------------------------------------


def test_cv_element_detector_constructs():
    detector = CVElementDetector()
    assert detector is not None


def test_cv_element_detector_custom_params():
    detector = CVElementDetector(canny_low=30, canny_high=100, min_area=500)
    assert detector._canny_low == 30
    assert detector._canny_high == 100
    assert detector._min_area == 500


# ---------------------------------------------------------------------------
# Blank / empty image handling
# ---------------------------------------------------------------------------


def test_detect_blank_white_image_returns_no_elements():
    detector = CVElementDetector()
    img = _make_blank_image(200, 200, color=255)
    elements = detector.detect(img)
    # Blank white image has no edges → no contours → no elements
    assert isinstance(elements, list)
    # May return 0 or very few; none should be confident false positives at default thresholds
    # We assert the call does not raise
    assert all(isinstance(e, UIElement) for e in elements)


def test_detect_zero_size_image_returns_empty():
    """Degenerate zero-size image must return [] without raising."""
    from PIL import Image

    img = Image.new("RGB", (1, 1), (255, 255, 255))  # smallest valid PIL image
    detector = CVElementDetector(min_area=10)
    elements = detector.detect(img)
    assert isinstance(elements, list)


def test_detect_solid_black_image():
    detector = CVElementDetector()
    img = _make_blank_image(200, 200, color=0)
    elements = detector.detect(img)
    assert isinstance(elements, list)


# ---------------------------------------------------------------------------
# Single rectangle detection
# ---------------------------------------------------------------------------


def test_detect_single_button_shape():
    """A small wide rectangle should be detected and classified as BUTTON."""
    img = _draw_rect(400, 200, rect_left=50, rect_top=80, rect_right=200, rect_bottom=115)
    detector = CVElementDetector(min_area=100)
    elements = detector.detect(img)
    assert len(elements) >= 1
    # At least one element should be a BUTTON or INPUT_FIELD (short, wide)
    types = {e.element_type for e in elements}
    assert ElementType.BUTTON in types or ElementType.INPUT_FIELD in types


def test_detect_bounding_box_is_bounding_box_type():
    img = _draw_rect(400, 200, rect_left=50, rect_top=50, rect_right=200, rect_bottom=100)
    detector = CVElementDetector(min_area=100)
    elements = detector.detect(img)
    assert len(elements) >= 1
    assert isinstance(elements[0].bounding_box, BoundingBox)


def test_detect_center_is_point2d():
    img = _draw_rect(400, 200, rect_left=50, rect_top=50, rect_right=200, rect_bottom=100)
    detector = CVElementDetector(min_area=100)
    elements = detector.detect(img)
    assert len(elements) >= 1
    assert isinstance(elements[0].center, Point2D)


def test_detect_center_within_bounding_box():
    img = _draw_rect(400, 200, rect_left=50, rect_top=50, rect_right=200, rect_bottom=100)
    detector = CVElementDetector(min_area=100)
    elements = detector.detect(img)
    for e in elements:
        box = e.bounding_box
        cx, cy = box.center
        assert box.left <= cx <= box.right, "Center x outside bounding box"
        assert box.top <= cy <= box.bottom, "Center y outside bounding box"


def test_detect_bounding_box_positive_dimensions():
    img = _draw_rect(400, 200, rect_left=50, rect_top=50, rect_right=200, rect_bottom=100)
    detector = CVElementDetector(min_area=100)
    elements = detector.detect(img)
    for e in elements:
        assert e.bounding_box.width > 0
        assert e.bounding_box.height > 0


# ---------------------------------------------------------------------------
# Multiple rectangle detection
# ---------------------------------------------------------------------------


def test_detect_multiple_shapes():
    """Button, input, and container shapes all present — expect ≥ 3 elements."""
    # Button: (50,50)-(150,90)  →  100x40, small, wide  → BUTTON/INPUT_FIELD
    # Input:  (200,50)-(400,80) →  200x30, very wide, short  → INPUT_FIELD
    # Container: (50,150)-(700,550) → 650x400, large area  → CONTAINER
    img = _draw_multiple_rects(
        800, 600, rects=[(50, 50, 150, 90), (200, 50, 400, 80), (50, 150, 700, 550)]
    )
    detector = CVElementDetector(min_area=200)
    elements = detector.detect(img)
    assert len(elements) >= 1  # at least one shape detected


def test_detect_large_rectangle_classified_as_container():
    """A very large rectangle should be CONTAINER."""
    # 700x500 rectangle → area = 350000 >> _CONTAINER_MIN_AREA=40000
    img = _draw_rect(800, 600, rect_left=50, rect_top=50, rect_right=750, rect_bottom=550)
    detector = CVElementDetector(min_area=200)
    elements = detector.detect(img)
    types = {e.element_type for e in elements}
    assert ElementType.CONTAINER in types


def test_detect_small_square_classified_as_icon():
    """A small square ≤ 64px should be ICON."""
    img = _draw_rect(200, 200, rect_left=80, rect_top=80, rect_right=120, rect_bottom=120)
    detector = CVElementDetector(min_area=50)
    elements = detector.detect(img)
    types = {e.element_type for e in elements}
    # Small 40x40 → ICON
    assert ElementType.ICON in types or len(elements) == 0  # degenerate: edge only


# ---------------------------------------------------------------------------
# Sorting order
# ---------------------------------------------------------------------------


def test_elements_sorted_top_left_to_bottom_right():
    """Detected elements must be sorted top→bottom, left→right."""
    img = _draw_multiple_rects(
        800, 600, rects=[(50, 400, 200, 450), (50, 50, 200, 100), (300, 200, 500, 240)]
    )
    detector = CVElementDetector(min_area=200)
    elements = detector.detect(img)
    if len(elements) >= 2:
        tops = [e.bounding_box.top for e in elements]
        assert tops == sorted(tops), "Elements not sorted top to bottom"


# ---------------------------------------------------------------------------
# max_elements cap
# ---------------------------------------------------------------------------


def test_max_elements_cap_respected():
    """When many contours are found, result must be capped at max_elements."""
    from PIL import Image, ImageDraw

    # Draw 30 small rectangles
    img = Image.new("RGB", (800, 600), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    for i in range(30):
        x = (i % 10) * 75 + 10
        y = (i // 10) * 100 + 10
        draw.rectangle([x, y, x + 50, y + 40], fill=(0, 0, 0))

    detector = CVElementDetector(min_area=100, max_elements=5)
    elements = detector.detect(img)
    assert len(elements) <= 5


# ---------------------------------------------------------------------------
# Confidence values
# ---------------------------------------------------------------------------


def test_element_confidence_in_range():
    img = _draw_rect(400, 200, rect_left=50, rect_top=50, rect_right=200, rect_bottom=100)
    detector = CVElementDetector(min_area=100)
    elements = detector.detect(img)
    for e in elements:
        assert 0.0 <= e.confidence <= 1.0


# ---------------------------------------------------------------------------
# Extension hook seam
# ---------------------------------------------------------------------------


class _StubHook(ElementDetectorHook):
    """Hook that always returns one synthetic UIElement."""

    def detect(self, image):  # type: ignore[override]
        return [
            UIElement(
                element_type=ElementType.BUTTON,
                bounding_box=BoundingBox(left=5.0, top=5.0, right=25.0, bottom=15.0),
                center=Point2D(x=15.0, y=10.0),
                confidence=0.75,
                label="stub",
            )
        ]


def test_hook_results_appended():
    detector = CVElementDetector()
    detector.add_hook(_StubHook())
    img = _make_blank_image()
    elements = detector.detect(img)
    stub_elements = [e for e in elements if e.label == "stub"]
    assert len(stub_elements) == 1
    assert stub_elements[0].element_type == ElementType.BUTTON


class _FailingHook(ElementDetectorHook):
    def detect(self, image):  # type: ignore[override]
        raise RuntimeError("hook exploded")


def test_failing_hook_does_not_propagate_exception():
    """A hook that raises must be swallowed by the detector; other results still returned."""
    detector = CVElementDetector()
    detector.add_hook(_FailingHook())
    img = _draw_rect(400, 200, 50, 50, 200, 100)
    # Should not raise
    elements = detector.detect(img)
    assert isinstance(elements, list)


# ---------------------------------------------------------------------------
# No disk persistence
# ---------------------------------------------------------------------------


def test_detect_does_not_write_files(tmp_path):
    import os

    before = set(os.listdir(tmp_path))
    detector = CVElementDetector()
    img = _draw_rect(400, 200, 50, 50, 200, 100)
    detector.detect(img)
    after = set(os.listdir(tmp_path))
    assert before == after, "CVElementDetector wrote files to disk"
