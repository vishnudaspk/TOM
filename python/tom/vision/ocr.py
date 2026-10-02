"""OCR provider abstraction for TOM — Phase 7 Iteration 2.

Design rules:
- ``OCRProvider`` is the sole abstraction boundary (mirrors STTProvider / TTSProvider patterns).
- ``MockOCRProvider``: deterministic offline test double; never used in production paths.
- ``WindowsMediaOCRProvider``: lazy-loaded Windows.Media.Ocr via ``winocr``; CPU-only; 0 VRAM.
- Production: raises ``OCRError`` if the configured provider is unavailable.
- OCR text is always passed through ``PrivacyShield.filter_extracted_text()`` before return.
- No disk writes; no raw image data retained after the call returns.
"""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING

from tom.schemas.vision import BoundingBox, OCRResult, ScreenDimensions, TextLocation
from tom.telemetry.logging import get_logger
from tom.vision.privacy import PrivacyShield

if TYPE_CHECKING:
    from PIL import Image

logger = get_logger(__name__, component="vision.ocr")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OCRError(Exception):
    """Raised when OCR fails or the provider is unavailable."""


class OCRProviderUnavailableError(OCRError):
    """Raised when a production OCR provider dependency is missing."""


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class OCRProvider(abc.ABC):
    """Abstract base for all OCR providers."""

    @abc.abstractmethod
    def extract_text(
        self,
        image: Image.Image,
        source_region: ScreenDimensions | None = None,
    ) -> OCRResult:
        """Extract all text from *image* and return a structured result.

        Args:
            image: A PIL Image held ephemerally in RAM.
            source_region: Optional screen coordinates the image was captured from.

        Returns:
            ``OCRResult`` with sanitised text and per-word bounding boxes.
        """

    def find_text(self, image: Image.Image, query: str) -> list[TextLocation]:
        """Find all occurrences of *query* in *image* and return their locations.

        Default implementation runs ``extract_text`` and filters words by substring match.
        Providers may override for more efficient search.
        """
        result = self.extract_text(image)
        q_lower = query.lower()
        return [w for w in result.words if q_lower in w.text.lower()]


# ---------------------------------------------------------------------------
# Mock provider — test double only
# ---------------------------------------------------------------------------


class MockOCRProvider(OCRProvider):
    """Deterministic offline OCR provider for unit tests.

    Returns synthetic text and bounding boxes from the preset ``responses`` list.
    Must never be used in production paths (enforced by callers, not here).
    """

    def __init__(
        self,
        responses: list[OCRResult] | None = None,
        privacy_shield: PrivacyShield | None = None,
    ) -> None:
        self._responses = list(responses or [])
        self._call_count = 0
        self._shield = privacy_shield or PrivacyShield()

    def extract_text(
        self,
        image: Image.Image,
        source_region: ScreenDimensions | None = None,
    ) -> OCRResult:
        if self._responses:
            idx = self._call_count % len(self._responses)
            result = self._responses[idx]
        else:
            # Synthetic default
            result = OCRResult(
                text="Sample OCR Text",
                confidence=0.99,
                words=[
                    TextLocation(
                        text="Sample",
                        bounding_box=BoundingBox(left=10.0, top=10.0, right=60.0, bottom=30.0),
                        confidence=0.99,
                    ),
                    TextLocation(
                        text="OCR",
                        bounding_box=BoundingBox(left=65.0, top=10.0, right=95.0, bottom=30.0),
                        confidence=0.99,
                    ),
                    TextLocation(
                        text="Text",
                        bounding_box=BoundingBox(left=100.0, top=10.0, right=140.0, bottom=30.0),
                        confidence=0.99,
                    ),
                ],
                source_region=source_region,
            )
        self._call_count += 1
        # Always sanitise through privacy shield
        sanitised = self._shield.filter_extracted_text(result.text)
        return OCRResult(
            text=sanitised,
            confidence=result.confidence,
            words=result.words,
            source_region=source_region or result.source_region,
        )


# ---------------------------------------------------------------------------
# Windows Media OCR provider
# ---------------------------------------------------------------------------


class WindowsMediaOCRProvider(OCRProvider):
    """Native Windows 10/11 OCR via ``winocr``.

    - Lazy-loaded: ``winocr`` is imported on first use.
    - Runs entirely on CPU; requires 0 VRAM and 0 external binaries.
    - Raises ``OCRProviderUnavailableError`` at construction if ``winocr`` is absent.
    - OCR text is always redacted through ``PrivacyShield.filter_extracted_text``.
    """

    def __init__(
        self,
        language: str = "en",
        privacy_shield: PrivacyShield | None = None,
    ) -> None:
        try:
            import winocr  # noqa: F401 — validate presence at construction
        except ImportError as exc:
            raise OCRProviderUnavailableError(
                "winocr is not installed. Install it with: pip install winocr"
            ) from exc
        self._language = language
        self._shield = privacy_shield or PrivacyShield()
        logger.debug("windows_media_ocr_provider_ready", language=language)

    def extract_text(
        self,
        image: Image.Image,
        source_region: ScreenDimensions | None = None,
    ) -> OCRResult:
        """Run Windows.Media.Ocr synchronously on *image* (CPU-only)."""
        import winocr

        try:
            raw = winocr.recognize_pil_sync(image, self._language)
        except Exception as exc:
            logger.error("windows_media_ocr_failed", error=str(exc))
            raise OCRError(f"Windows Media OCR failed: {exc}") from exc

        words: list[TextLocation] = []
        for line in raw.get("lines", []):
            for word_data in line.get("words", []):
                rect = word_data.get("bounding_rect", {})
                x = float(rect.get("x", 0.0))
                y = float(rect.get("y", 0.0))
                w = float(rect.get("width", 0.0))
                h = float(rect.get("height", 0.0))
                words.append(
                    TextLocation(
                        text=word_data.get("text", ""),
                        bounding_box=BoundingBox(
                            left=x,
                            top=y,
                            right=x + w,
                            bottom=y + h,
                        ),
                        confidence=1.0,  # winocr does not expose per-word confidence
                    )
                )

        raw_text: str = raw.get("text", "") or ""
        sanitised = self._shield.filter_extracted_text(raw_text)

        logger.debug(
            "windows_media_ocr_completed",
            word_count=len(words),
            text_length=len(sanitised),
        )
        return OCRResult(
            text=sanitised,
            confidence=1.0,
            words=words,
            source_region=source_region,
        )
