"""Unit tests for VisionManager — Phase 7 Iteration 3.

Adheres to:
- Capability-driven perception routing (cheapest sufficient tier first: OCR -> CV -> VLM).
- Resource manager seam integration: checks GPU VRAM before issuing VLM inference.
- Offline determinism: zero network, zero GPU, zero disk writes.
- Strict-markers convention: run_async() helper for coroutines, no pytest.mark.asyncio.
"""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import Coroutine
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image
from tom.schemas.vision import (
    BoundingBox,
    OCRResult,
    TextLocation,
    VisionAnalysisRequest,
    VisionCapability,
)
from tom.vision.capture import MockCaptureBackend, ScreenCaptureService
from tom.vision.manager import VisionManager
from tom.vision.ocr import MockOCRProvider
from tom.vision.vlm import MockVLMProvider, VLMError

_cv2_available = importlib.util.find_spec("cv2") is not None
_requires_cv2 = pytest.mark.skipif(
    not _cv2_available, reason="opencv-python optional dep not installed"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def _make_test_image(width: int = 800, height: int = 600) -> Image.Image:
    return Image.new("RGB", (width, height), color=(240, 240, 240))


def _make_ocr_result(text: str = "Submit Application") -> OCRResult:
    words = []
    x = 50.0
    for w in text.split():
        words.append(
            TextLocation(
                text=w,
                bounding_box=BoundingBox(left=x, top=100.0, right=x + len(w) * 15.0, bottom=130.0),
                confidence=0.98,
            )
        )
        x += len(w) * 15.0 + 10.0
    return OCRResult(
        text=text,
        confidence=0.98,
        words=words,
    )


def _make_mock_engine(
    gpu_available: bool = True,
    used_mb: int = 2000,
    total_mb: int = 8192,
) -> MagicMock:
    """Mock EngineClient providing GPU telemetry."""
    engine = MagicMock()
    gpu = MagicMock()
    gpu.available = gpu_available
    gpu.memory_used_bytes = used_mb * 1024 * 1024
    gpu.memory_total_bytes = total_mb * 1024 * 1024
    engine.get_gpu = AsyncMock(return_value=gpu)
    return engine


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------


class TestVisionManagerRouting:
    """Test capability-driven routing in VisionManager."""

    def test_classify_capability(self) -> None:
        manager = VisionManager()

        # Pure text queries -> OCR_ONLY
        assert (
            manager.classify_capability("Extract text from this window")
            == VisionCapability.OCR_ONLY
        )
        assert manager.classify_capability("read screen") == VisionCapability.OCR_ONLY
        assert manager.classify_capability("Perform OCR") == VisionCapability.OCR_ONLY

        # Element location queries -> ELEMENT_LOCATION
        assert (
            manager.classify_capability("Find the Submit button")
            == VisionCapability.ELEMENT_LOCATION
        )
        assert (
            manager.classify_capability("Where is the search bar")
            == VisionCapability.ELEMENT_LOCATION
        )
        assert (
            manager.classify_capability("Locate button on screen")
            == VisionCapability.ELEMENT_LOCATION
        )

        # Semantic understanding queries -> FAST_VLM
        assert (
            manager.classify_capability("Describe what is happening on screen")
            == VisionCapability.FAST_VLM
        )
        assert (
            manager.classify_capability("Explain this error message") == VisionCapability.FAST_VLM
        )

    def test_ocr_only_bypasses_vlm(self) -> None:
        """Text queries must be answered entirely by OCR; VLM is never invoked."""
        ocr = MockOCRProvider(responses=[_make_ocr_result("Welcome to TOM")])
        vlm = MockVLMProvider()
        capture = ScreenCaptureService(backend=MockCaptureBackend())

        manager = VisionManager(
            capture_service=capture,
            ocr_provider=ocr,
            vlm_provider=vlm,
        )

        req = VisionAnalysisRequest(query="Extract all text from the screen")
        result = run_async(manager.analyze(req))

        assert result.capability_used == VisionCapability.OCR_ONLY
        assert result.text == "Welcome to TOM"
        assert len(vlm.call_history) == 0  # VLM completely bypassed

    @_requires_cv2
    def test_element_location_via_ocr_and_cv_bypasses_vlm(self) -> None:
        """Known buttons found via OCR/CV do not trigger VLM calls."""
        ocr = MockOCRProvider(responses=[_make_ocr_result("Submit Application")])
        vlm = MockVLMProvider()
        capture = ScreenCaptureService(backend=MockCaptureBackend())

        manager = VisionManager(
            capture_service=capture,
            ocr_provider=ocr,
            vlm_provider=vlm,
        )

        req = VisionAnalysisRequest(query="Find Submit button")
        result = run_async(manager.analyze(req))

        assert result.capability_used == VisionCapability.ELEMENT_LOCATION
        assert len(result.elements) >= 1
        assert result.elements[0].label == "Submit"
        assert len(vlm.call_history) == 0  # VLM completely bypassed

    @_requires_cv2
    def test_element_location_escalates_to_vlm_if_not_found(self) -> None:
        """When OCR/CV cannot find an element, manager escalates to VLM."""
        ocr = MockOCRProvider(responses=[_make_ocr_result("No matching text here")])
        vlm_boxes = [BoundingBox(left=300.0, top=400.0, right=450.0, bottom=460.0)]
        vlm = MockVLMProvider(canned_boxes=vlm_boxes)
        capture = ScreenCaptureService(backend=MockCaptureBackend())

        manager = VisionManager(
            capture_service=capture,
            ocr_provider=ocr,
            vlm_provider=vlm,
        )

        req = VisionAnalysisRequest(query="Find the hidden blue circle icon")
        result = run_async(manager.analyze(req))

        assert result.capability_used == VisionCapability.FAST_VLM
        assert len(result.bounding_boxes) == 1
        assert result.bounding_boxes[0].left == 300.0

    def test_vlm_reasoning_query(self) -> None:
        """Visual reasoning queries route directly to VLM."""
        vlm = MockVLMProvider(default_response="User is editing Python code in VS Code.")
        capture = ScreenCaptureService(backend=MockCaptureBackend())

        manager = VisionManager(
            capture_service=capture,
            vlm_provider=vlm,
        )

        req = VisionAnalysisRequest(query="Describe what the user is doing")
        result = run_async(manager.analyze(req))

        assert result.capability_used == VisionCapability.FAST_VLM
        assert "VS Code" in result.text
        assert len(vlm.call_history) == 1


class TestVisionManagerResourceManagement:
    """Test VRAM telemetry seam integration and budget enforcement."""

    def test_vram_within_budget_allows_vlm(self) -> None:
        engine = _make_mock_engine(gpu_available=True, used_mb=3000, total_mb=8192)
        vlm = MockVLMProvider(default_response="VLM analysis passed.")
        manager = VisionManager(
            engine_client=engine,
            vlm_provider=vlm,
            max_vram_mb=7000,
        )

        img = _make_test_image()
        req = VisionAnalysisRequest(
            query="Analyze image", image=img, capability=VisionCapability.FAST_VLM
        )
        result = run_async(manager.analyze(req))

        assert result.text == "VLM analysis passed."

    def test_vram_threshold_tripped_rejects_vlm(self) -> None:
        """When VRAM usage exceeds budget (> 7000 MB), VLM inference is blocked."""
        engine = _make_mock_engine(gpu_available=True, used_mb=7500, total_mb=8192)
        vlm = MockVLMProvider()
        manager = VisionManager(
            engine_client=engine,
            vlm_provider=vlm,
            max_vram_mb=7000,
            min_free_vram_mb=1200,
        )

        img = _make_test_image()
        req = VisionAnalysisRequest(
            query="Analyze image", image=img, capability=VisionCapability.FAST_VLM
        )

        with pytest.raises(VLMError, match="VRAM pressure detected"):
            run_async(manager.analyze(req))


class TestVisionManagerConvenienceAPIs:
    """Test direct convenience methods on VisionManager."""

    def test_extract_text(self) -> None:
        ocr = MockOCRProvider(responses=[_make_ocr_result("Quick OCR")])
        manager = VisionManager(ocr_provider=ocr)

        img = _make_test_image()
        res = run_async(manager.extract_text(img))
        assert res.text == "Quick OCR"

    @_requires_cv2
    def test_find_element(self) -> None:
        ocr = MockOCRProvider(responses=[_make_ocr_result("Login")])
        manager = VisionManager(ocr_provider=ocr)

        img = _make_test_image()
        boxes = run_async(manager.find_element("Login", image=img))
        assert len(boxes) >= 1

    def test_ask(self) -> None:
        vlm = MockVLMProvider(default_response="Blue theme detected.")
        manager = VisionManager(vlm_provider=vlm)

        img = _make_test_image()
        ans = run_async(manager.ask("What color is the theme?", image=img))
        assert ans == "Blue theme detected."

    def test_unconfigured_vlm_graceful_degradation(self) -> None:
        """When no VLM is provided, queries degrade to OCR without crashing."""
        ocr = MockOCRProvider(responses=[_make_ocr_result("Page title: Settings")])
        manager = VisionManager(ocr_provider=ocr, vlm_provider=None)

        img = _make_test_image()
        req = VisionAnalysisRequest(query="Explain this screen", image=img)
        result = run_async(manager.analyze(req))

        assert result.capability_used == VisionCapability.OCR_ONLY
        assert "Settings" in result.text
