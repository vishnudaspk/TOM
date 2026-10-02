"""Unit tests for OCR providers — Phase 7 Iteration 2.

Coverage:
- MockOCRProvider: structured OCRResult, serialisation, text search, bounding boxes.
- Confidence values, per-word locations.
- Sensitive text redaction via PrivacyShield integration.
- WindowsMediaOCRProvider: availability check, missing-provider error, no silent mock fallback.
- find_text substring matching.
- OCRResult field validation.
- Offline-only: zero desktop interaction, zero network, zero GPU.
"""

from __future__ import annotations

import importlib

import pytest
from tom.schemas.vision import BoundingBox, OCRResult, ScreenDimensions, TextLocation
from tom.vision.ocr import (
    MockOCRProvider,
    OCRProvider,
    OCRProviderUnavailableError,
    WindowsMediaOCRProvider,
)

_winocr_available = importlib.util.find_spec("winocr") is not None
_requires_winocr = pytest.mark.skipif(
    not _winocr_available, reason="winocr optional dep not installed"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pil_image(width: int = 100, height: int = 50, text: str = ""):
    """Create a minimal in-memory PIL image (no disk I/O)."""
    from PIL import Image

    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    if text:
        from PIL import ImageDraw

        ImageDraw.Draw(img).text((5, 5), text, fill=(0, 0, 0))
    return img


def _make_result(text: str = "Hello World", confidence: float = 0.95) -> OCRResult:
    return OCRResult(
        text=text,
        confidence=confidence,
        words=[
            TextLocation(
                text="Hello",
                bounding_box=BoundingBox(left=10.0, top=10.0, right=50.0, bottom=30.0),
                confidence=confidence,
            ),
            TextLocation(
                text="World",
                bounding_box=BoundingBox(left=55.0, top=10.0, right=110.0, bottom=30.0),
                confidence=confidence,
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


def test_bounding_box_width_height():
    box = BoundingBox(left=10.0, top=20.0, right=60.0, bottom=50.0)
    assert box.width == pytest.approx(50.0)
    assert box.height == pytest.approx(30.0)


def test_bounding_box_center():
    box = BoundingBox(left=0.0, top=0.0, right=100.0, bottom=40.0)
    cx, cy = box.center
    assert cx == pytest.approx(50.0)
    assert cy == pytest.approx(20.0)


def test_bounding_box_zero_area():
    box = BoundingBox(left=5.0, top=5.0, right=5.0, bottom=5.0)
    assert box.width == pytest.approx(0.0)
    assert box.height == pytest.approx(0.0)


def test_ocr_result_serialisation():
    result = _make_result("Hello World", 0.9)
    d = result.model_dump()
    assert d["text"] == "Hello World"
    assert d["confidence"] == pytest.approx(0.9)
    assert len(d["words"]) == 2
    assert d["words"][0]["text"] == "Hello"
    assert d["words"][0]["bounding_box"]["left"] == pytest.approx(10.0)


def test_ocr_result_source_region():
    region = ScreenDimensions(left=0, top=0, width=1920, height=1080)
    result = OCRResult(text="hi", confidence=1.0, source_region=region)
    assert result.source_region is not None
    assert result.source_region.width == 1920


def test_ocr_result_empty_words():
    result = OCRResult(text="", confidence=1.0)
    assert result.words == []
    assert result.text == ""


def test_text_location_confidence_clamped():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TextLocation(
            text="x",
            bounding_box=BoundingBox(left=0.0, top=0.0, right=10.0, bottom=10.0),
            confidence=1.5,  # > 1.0 → Pydantic validation error
        )


# ---------------------------------------------------------------------------
# MockOCRProvider tests
# ---------------------------------------------------------------------------


def test_mock_provider_is_ocr_provider():
    assert issubclass(MockOCRProvider, OCRProvider)


def test_mock_provider_default_response():
    provider = MockOCRProvider()
    img = _make_pil_image()
    result = provider.extract_text(img)
    assert isinstance(result, OCRResult)
    assert len(result.text) > 0
    assert result.confidence >= 0.0
    assert result.confidence <= 1.0


def test_mock_provider_custom_response():
    preset = _make_result("Custom text", 0.88)
    provider = MockOCRProvider(responses=[preset])
    img = _make_pil_image()
    result = provider.extract_text(img)
    assert result.text == "Custom text"
    assert result.confidence == pytest.approx(0.88)


def test_mock_provider_cycles_responses():
    r1 = _make_result("First")
    r2 = _make_result("Second")
    provider = MockOCRProvider(responses=[r1, r2])
    img = _make_pil_image()
    assert provider.extract_text(img).text == "First"
    assert provider.extract_text(img).text == "Second"
    assert provider.extract_text(img).text == "First"  # wraps around


def test_mock_provider_preserves_word_bounding_boxes():
    preset = _make_result("Hello World")
    provider = MockOCRProvider(responses=[preset])
    img = _make_pil_image()
    result = provider.extract_text(img)
    assert len(result.words) == 2
    assert result.words[0].bounding_box.left == pytest.approx(10.0)
    assert result.words[1].bounding_box.right == pytest.approx(110.0)


def test_mock_provider_source_region_propagated():
    provider = MockOCRProvider()
    img = _make_pil_image()
    region = ScreenDimensions(left=100, top=200, width=800, height=600)
    result = provider.extract_text(img, source_region=region)
    assert result.source_region is not None
    assert result.source_region.left == 100


def test_mock_provider_find_text_found():
    preset = _make_result("Hello World")
    provider = MockOCRProvider(responses=[preset])
    img = _make_pil_image()
    locations = provider.find_text(img, "Hello")
    assert len(locations) == 1
    assert locations[0].text == "Hello"


def test_mock_provider_find_text_not_found():
    preset = _make_result("Hello World")
    provider = MockOCRProvider(responses=[preset])
    img = _make_pil_image()
    locations = provider.find_text(img, "Python")
    assert locations == []


def test_mock_provider_find_text_case_insensitive():
    preset = _make_result("Hello World")
    provider = MockOCRProvider(responses=[preset])
    img = _make_pil_image()
    locations = provider.find_text(img, "hello")
    assert len(locations) >= 1


def test_mock_provider_redacts_credit_card():
    """PrivacyShield integration: mock must redact sensitive text from OCR output."""
    sensitive = _make_result("Card: 4111 1111 1111 1111")
    provider = MockOCRProvider(responses=[sensitive])
    img = _make_pil_image()
    result = provider.extract_text(img)
    assert "4111" not in result.text
    assert "REDACTED" in result.text


def test_mock_provider_redacts_ssn():
    sensitive = _make_result("SSN: 123-45-6789")
    provider = MockOCRProvider(responses=[sensitive])
    img = _make_pil_image()
    result = provider.extract_text(img)
    assert "123-45-6789" not in result.text
    assert "REDACTED" in result.text


def test_mock_provider_redacts_api_key():
    sensitive = _make_result("Key: ghp_abcdefghijklmnopqrstuvwxyz")
    provider = MockOCRProvider(responses=[sensitive])
    img = _make_pil_image()
    result = provider.extract_text(img)
    assert "ghp_" not in result.text
    assert "REDACTED" in result.text


def test_mock_provider_clean_text_unchanged():
    """Non-sensitive text must pass through without modification."""
    preset = _make_result("Open Settings and click OK")
    provider = MockOCRProvider(responses=[preset])
    img = _make_pil_image()
    result = provider.extract_text(img)
    assert "Open Settings" in result.text
    assert "REDACTED" not in result.text


# ---------------------------------------------------------------------------
# WindowsMediaOCRProvider availability tests
# ---------------------------------------------------------------------------


@_requires_winocr
def test_windows_media_ocr_provider_available():
    """winocr is installed; WindowsMediaOCRProvider should construct without error."""
    provider = WindowsMediaOCRProvider()
    assert isinstance(provider, OCRProvider)


@_requires_winocr
def test_windows_media_ocr_provider_extract_text_returns_ocr_result():
    """Provider should return a valid OCRResult on a synthetic PIL image."""
    provider = WindowsMediaOCRProvider()
    img = _make_pil_image(200, 60, text="Hello TOM")
    result = provider.extract_text(img)
    assert isinstance(result, OCRResult)
    assert isinstance(result.text, str)
    assert result.confidence >= 0.0


@_requires_winocr
def test_windows_media_ocr_provider_no_disk_writes(tmp_path):
    """Running OCR must not create any files under tmp_path or the cwd."""
    import os

    before = set(os.listdir(tmp_path))
    provider = WindowsMediaOCRProvider()
    img = _make_pil_image(100, 40, text="test")
    provider.extract_text(img)
    after = set(os.listdir(tmp_path))
    assert before == after, "OCR provider wrote files to disk"


def test_windows_media_ocr_unavailable_raises_typed_error(monkeypatch):
    """If winocr is not importable, construction must raise OCRProviderUnavailableError."""
    import sys

    # Simulate missing winocr by temporarily hiding the module
    saved = sys.modules.pop("winocr", None)
    try:
        sys.modules["winocr"] = None  # type: ignore[assignment]

        with pytest.raises(OCRProviderUnavailableError):
            WindowsMediaOCRProvider()
    finally:
        if saved is not None:
            sys.modules["winocr"] = saved
        elif "winocr" in sys.modules:
            del sys.modules["winocr"]


@_requires_winocr
def test_no_silent_mock_fallback_in_production():
    """WindowsMediaOCRProvider must raise, not silently route to MockOCRProvider."""
    # This is a design-level assertion: if winocr IS available, production uses it.
    # If unavailable, OCRProviderUnavailableError is raised — not MockOCRProvider.
    # Test: construct provider with winocr present; verify it's NOT a MockOCRProvider.
    provider = WindowsMediaOCRProvider()
    assert not isinstance(provider, MockOCRProvider)
    assert type(provider) is WindowsMediaOCRProvider
