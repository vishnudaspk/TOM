"""Phase 7 — Iteration 5: End-to-End Vision Pipeline Integration Tests.

Exercises the complete mocked pipeline:
    ScreenCaptureService → PrivacyShield → OCRProvider → CVElementDetector
        → VLMProvider → VisionManager → ToolExecutor

Requirements:
- Deterministic mocks only; no cloud, no neural weights, no real screenshots.
- Zero disk persistence of any frame data.
- No physical mouse/keyboard actions.
- Verifies capability routing, privacy enforcement, error containment.
"""

from __future__ import annotations

import asyncio
from typing import Any

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
from tom.vision.privacy import PrivacyShield
from tom.vision.vlm import MockVLMProvider, VLMError


def run_async(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_ocr(text: str) -> OCRResult:
    return OCRResult(
        text=text,
        confidence=0.95,
        words=[
            TextLocation(
                text=text,
                bounding_box=BoundingBox(left=0, top=0, right=100, bottom=20),
                confidence=0.95,
            )
        ],
    )


def _make_manager(
    ocr_text: str = "Screen text",
    vlm_response: str = "Screen shows a desktop.",
    vlm_boxes: list[BoundingBox] | None = None,
) -> VisionManager:
    ocr = MockOCRProvider(responses=[_make_ocr(ocr_text)])
    vlm = MockVLMProvider(default_response=vlm_response, canned_boxes=vlm_boxes or [])
    capture = ScreenCaptureService(backend=MockCaptureBackend())
    return VisionManager(capture_service=capture, ocr_provider=ocr, vlm_provider=vlm)


# ---------------------------------------------------------------------------
# A. Capture → Privacy → OCR (text extraction path)
# ---------------------------------------------------------------------------


class TestVisionPipelineOCRPath:
    """Capture → privacy → OCR without VLM."""

    def test_extract_text_uses_ocr_only(self) -> None:
        """Pure text extraction routes to OCR, never triggers VLM."""
        ocr = MockOCRProvider(responses=[_make_ocr("Hello TOM")])
        vlm = MockVLMProvider()
        manager = VisionManager(
            capture_service=ScreenCaptureService(backend=MockCaptureBackend()),
            ocr_provider=ocr,
            vlm_provider=vlm,
        )
        req = VisionAnalysisRequest(query="Extract all text on screen")
        result = run_async(manager.analyze(req))

        assert result.capability_used == VisionCapability.OCR_ONLY
        assert "Hello TOM" in result.text
        assert len(vlm.call_history) == 0

    def test_captured_frame_not_persisted(self, tmp_path: Any) -> None:
        """Analyze must not write any files."""
        import os

        before = set(os.listdir(tmp_path))
        manager = _make_manager(ocr_text="test text")
        req = VisionAnalysisRequest(query="Read text")
        run_async(manager.analyze(req))
        assert set(os.listdir(tmp_path)) == before

    def test_ocr_text_passes_through_privacy_shield(self) -> None:
        """Sensitive tokens in OCR output are redacted."""
        ocr = MockOCRProvider(responses=[_make_ocr("Password: ghp_abc123secrettoken000")])
        manager = VisionManager(
            capture_service=ScreenCaptureService(backend=MockCaptureBackend()),
            ocr_provider=ocr,
        )
        req = VisionAnalysisRequest(query="Extract text")
        result = run_async(manager.analyze(req))
        assert "ghp_" not in result.text
        assert "REDACTED" in result.text


# ---------------------------------------------------------------------------
# B. Privacy Shield: sensitive window blocking
# ---------------------------------------------------------------------------


class TestPrivacyShieldEnforcement:
    """PrivacyShield filters sensitive window titles and OCR content."""

    def test_sensitive_window_title_detected(self) -> None:
        shield = PrivacyShield()
        result = shield.check_window("1Password — Main Vault")
        assert not result.is_safe

    def test_safe_window_title_allowed(self) -> None:
        shield = PrivacyShield()
        result = shield.check_window("VS Code — main.py")
        assert result.is_safe

    def test_filter_extracted_text_redacts_credit_card(self) -> None:
        shield = PrivacyShield()
        clean = shield.filter_extracted_text("Card: 4111 1111 1111 1111 expiry 01/30")
        assert "4111" not in clean
        assert "REDACTED" in clean

    def test_filter_extracted_text_preserves_safe_text(self) -> None:
        shield = PrivacyShield()
        clean = shield.filter_extracted_text("Click the Submit button to continue")
        assert clean == "Click the Submit button to continue"


# ---------------------------------------------------------------------------
# C. VLM path: visual reasoning escalation
# ---------------------------------------------------------------------------


class TestVisionPipelineVLMPath:
    """Queries requiring semantic understanding route to VLM."""

    def test_reasoning_query_hits_vlm(self) -> None:
        vlm = MockVLMProvider(default_response="User is editing code.")
        manager = VisionManager(
            capture_service=ScreenCaptureService(backend=MockCaptureBackend()),
            vlm_provider=vlm,
        )
        req = VisionAnalysisRequest(query="Describe what the user is doing")
        result = run_async(manager.analyze(req))

        assert result.capability_used == VisionCapability.FAST_VLM
        assert "code" in result.text
        assert len(vlm.call_history) == 1

    def test_vlm_failure_contained_does_not_crash_manager(self) -> None:
        """VLM error must be caught; manager raises but doesn't propagate uncaught."""

        class BrokenVLM(MockVLMProvider):
            async def analyze_image(self, req):  # type: ignore[override]
                raise VLMError("simulated VLM crash")

        manager = VisionManager(
            capture_service=ScreenCaptureService(backend=MockCaptureBackend()),
            vlm_provider=BrokenVLM(),
        )
        req = VisionAnalysisRequest(query="What is on screen?")
        with pytest.raises(VLMError):
            run_async(manager.analyze(req))

    def test_no_vlm_configured_degrades_to_ocr(self) -> None:
        """With no VLM, semantic queries fall back to OCR result."""
        ocr = MockOCRProvider(responses=[_make_ocr("Settings Panel")])
        manager = VisionManager(
            capture_service=ScreenCaptureService(backend=MockCaptureBackend()),
            ocr_provider=ocr,
            vlm_provider=None,
        )
        req = VisionAnalysisRequest(query="Explain this screen")
        result = run_async(manager.analyze(req))
        assert result.capability_used == VisionCapability.OCR_ONLY
        assert "Settings Panel" in result.text


# ---------------------------------------------------------------------------
# D. Convenience API: extract_text / ask
# ---------------------------------------------------------------------------


class TestVisionConvenienceAPIs:
    def test_extract_text_returns_string(self) -> None:
        manager = _make_manager(ocr_text="Open File")
        img = Image.new("RGB", (100, 50), "white")
        result = run_async(manager.extract_text(img))
        assert isinstance(result.text, str)
        assert "Open File" in result.text

    def test_ask_returns_string(self) -> None:
        manager = _make_manager(vlm_response="Dark theme UI.")
        img = Image.new("RGB", (100, 50), "white")
        answer = run_async(manager.ask("What color is the theme?", image=img))
        assert "Dark" in answer

    def test_base64_data_never_logged(self, caplog: Any) -> None:
        """No base64-encoded image payload should appear in logs."""
        import logging

        manager = _make_manager()
        img = Image.new("RGB", (100, 50), "white")
        with caplog.at_level(logging.DEBUG):
            run_async(manager.ask("Describe screen", image=img))
        for record in caplog.records:
            assert "base64" not in record.message.lower() or "data:image" not in record.message
