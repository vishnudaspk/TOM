"""VisionManager — Central perception coordinator for TOM (Phase 7 Iteration 3).

Adheres to:
- Phase 7 Vision Architecture (ADRs 046-050)
- Capability-driven perception routing (cheapest sufficient capability first: OCR -> CV -> VLM).
- Resource manager seam integration: checks GPU VRAM telemetry before escalating to VLM.
- Ephemeral in-memory processing: zero disk persistence.
- Strict local-only enforcement.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from PIL import Image

from tom.schemas.vision import (
    BoundingBox,
    CapturedFrame,
    ElementType,
    OCRResult,
    Point2D,
    UIElement,
    VisionAnalysisRequest,
    VisionAnalysisResult,
    VisionCapability,
    VLMRequest,
)
from tom.telemetry.logging import get_logger
from tom.vision.capture import ScreenCaptureService
from tom.vision.cv import CVElementDetector
from tom.vision.ocr import MockOCRProvider, OCRProvider, WindowsMediaOCRProvider
from tom.vision.vlm import VLMError, VLMProvider

if TYPE_CHECKING:
    pass

logger = get_logger(__name__, component="vision.manager")

# VRAM budget defaults (8 GB RTX 4060 target)
_DEFAULT_MAX_VRAM_MB = 7000
_DEFAULT_MIN_FREE_VRAM_MB = 1200


class VisionManager:
    """Coordinates visual perception tasks using capability-driven tiering.

    Routing policy:
    1. Pure text extraction queries -> CPU OCRProvider (0 VRAM, < 100ms)
    2. Button / label / widget finding -> OCRProvider + CVElementDetector (0 VRAM, < 50ms)
    3. Visual reasoning / scene description -> Local VLM tier (VRAM checked first)
    """

    def __init__(
        self,
        capture_service: ScreenCaptureService | None = None,
        ocr_provider: OCRProvider | None = None,
        cv_detector: CVElementDetector | None = None,
        vlm_provider: VLMProvider | None = None,
        engine_client: Any | None = None,
        max_vram_mb: int = _DEFAULT_MAX_VRAM_MB,
        min_free_vram_mb: int = _DEFAULT_MIN_FREE_VRAM_MB,
    ) -> None:
        self._capture_service = capture_service or ScreenCaptureService()
        self._ocr_provider = ocr_provider or self._default_ocr()
        self._cv_detector = cv_detector or CVElementDetector()
        self._vlm_provider = vlm_provider
        self._engine_client = engine_client
        self._max_vram_mb = max_vram_mb
        self._min_free_vram_mb = min_free_vram_mb

    @staticmethod
    def _default_ocr() -> OCRProvider:
        """Instantiate best available OCR provider."""
        try:
            return WindowsMediaOCRProvider()
        except Exception:
            return MockOCRProvider()

    @property
    def capture_service(self) -> ScreenCaptureService:
        return self._capture_service

    @property
    def ocr_provider(self) -> OCRProvider:
        return self._ocr_provider

    @property
    def cv_detector(self) -> CVElementDetector:
        return self._cv_detector

    @property
    def vlm_provider(self) -> VLMProvider | None:
        return self._vlm_provider

    def classify_capability(self, query: str) -> VisionCapability:
        """Classify query to the cheapest sufficient capability tier."""
        q = query.lower()

        # Tier 1: Pure text extraction cues
        text_cues = (
            "extract text",
            "read text",
            "ocr",
            "read screen",
            "what text",
            "get text",
            "transcribe",
            "all text",
        )
        if any(cue in q for cue in text_cues):
            return VisionCapability.OCR_ONLY

        # Tier 2: Element location / button finding cues
        location_cues = (
            "find button",
            "locate button",
            "where is",
            "find",
            "locate",
            "click on",
            "button",
            "input field",
            "textbox",
            "search bar",
            "checkbox",
            "icon",
        )
        if any(cue in q for cue in location_cues):
            return VisionCapability.ELEMENT_LOCATION

        # Tier 3: Visual understanding / reasoning cues
        return VisionCapability.FAST_VLM

    async def check_vram_available(self) -> bool:
        """Check whether GPU has sufficient free VRAM for VLM inference.

        Delegates to the engine telemetry seam without forcing model unloads.
        """
        if self._engine_client is None:
            return True

        try:
            gpu = await self._engine_client.get_gpu()
            if not getattr(gpu, "available", False):
                return True

            total_bytes = getattr(gpu, "memory_total_bytes", None)
            used_bytes = getattr(gpu, "memory_used_bytes", None)

            if total_bytes and used_bytes is not None:
                total_mb = total_bytes / (1024 * 1024)
                used_mb = used_bytes / (1024 * 1024)
                free_mb = total_mb - used_mb

                if used_mb > self._max_vram_mb or free_mb < self._min_free_vram_mb:
                    logger.warning(
                        "VRAM threshold exceeded for VLM inference",
                        used_mb=used_mb,
                        free_mb=free_mb,
                        limit_mb=self._max_vram_mb,
                    )
                    return False
        except Exception as exc:
            logger.warning("Could not query GPU telemetry seam", error=str(exc))
            return True

        return True

    def _resolve_image(self, image: CapturedFrame | Image.Image | None) -> Image.Image:
        """Convert input frame/image to a PIL Image in RAM."""
        if image is None:
            frame = self._capture_service.capture_screen()
            return frame.to_pil()
        if isinstance(image, CapturedFrame):
            return image.to_pil()
        if isinstance(image, Image.Image):
            return image
        raise ValueError(f"Unsupported image type: {type(image)}")

    async def analyze(self, request: VisionAnalysisRequest) -> VisionAnalysisResult:
        """Perform visual analysis using capability-driven routing."""
        start_time = time.perf_counter()
        pil_image = self._resolve_image(request.image)

        capability = request.capability or self.classify_capability(request.query)

        # -----------------------------------------------------------------------
        # Tier 1: OCR only
        # -----------------------------------------------------------------------
        if capability == VisionCapability.OCR_ONLY:
            ocr_result = self._ocr_provider.extract_text(pil_image)
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return VisionAnalysisResult(
                text=ocr_result.text,
                capability_used=VisionCapability.OCR_ONLY,
                ocr_result=ocr_result,
                execution_time_ms=elapsed_ms,
            )

        # -----------------------------------------------------------------------
        # Tier 2: Element location via OCR + CV
        # -----------------------------------------------------------------------
        if capability == VisionCapability.ELEMENT_LOCATION:
            # 1. Try OCR text search (direct query, then keywords)
            text_locs = self._ocr_provider.find_text(pil_image, request.query)
            if not text_locs:
                noise_words = {
                    "find",
                    "locate",
                    "where",
                    "is",
                    "the",
                    "button",
                    "input",
                    "icon",
                    "click",
                    "on",
                }
                words_in_q = [
                    w for w in request.query.split() if w.lower() not in noise_words and len(w) >= 2
                ]
                for word in words_in_q:
                    matches = self._ocr_provider.find_text(pil_image, word)
                    if matches:
                        text_locs.extend(matches)

            cv_elements = self._cv_detector.detect(pil_image)

            bounding_boxes: list[BoundingBox] = [loc.bounding_box for loc in text_locs]
            matched_elements: list[UIElement] = []

            # Match OCR locations with detected CV bounding boxes
            for loc in text_locs:
                matched = False
                for el in cv_elements:
                    # Check if OCR word center is within the element bounding box
                    cx, cy = loc.bounding_box.center
                    if (
                        el.bounding_box.left <= cx <= el.bounding_box.right
                        and el.bounding_box.top <= cy <= el.bounding_box.bottom
                    ):
                        el.label = loc.text
                        matched_elements.append(el)
                        matched = True
                        break
                if not matched:
                    # Construct synthesized UI element from OCR location
                    cx, cy = loc.bounding_box.center
                    matched_elements.append(
                        UIElement(
                            element_type=ElementType.BUTTON,
                            bounding_box=loc.bounding_box,
                            center=Point2D(x=cx, y=cy),
                            confidence=loc.confidence,
                            label=loc.text,
                        )
                    )

            # If OCR+CV succeeded, return immediately without invoking VLM
            if matched_elements:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                return VisionAnalysisResult(
                    text=f"Found {len(matched_elements)} element(s) matching '{request.query}'.",
                    capability_used=VisionCapability.ELEMENT_LOCATION,
                    bounding_boxes=bounding_boxes,
                    elements=matched_elements,
                    execution_time_ms=elapsed_ms,
                )

            # If OCR+CV did not locate the element and VLM is available, escalate to VLM
            if self._vlm_provider is not None:
                vram_ok = await self.check_vram_available()
                if vram_ok:
                    vlm_boxes = await self._vlm_provider.locate_element(pil_image, request.query)
                    if vlm_boxes:
                        escalated_elements = [
                            UIElement(
                                element_type=ElementType.UNKNOWN,
                                bounding_box=b,
                                center=Point2D(x=b.center[0], y=b.center[1]),
                                confidence=0.8,
                                label=request.query,
                            )
                            for b in vlm_boxes
                        ]
                        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                        return VisionAnalysisResult(
                            text=f"VLM located {len(vlm_boxes)} element(s) for '{request.query}'.",
                            capability_used=VisionCapability.FAST_VLM,
                            bounding_boxes=vlm_boxes,
                            elements=escalated_elements,
                            execution_time_ms=elapsed_ms,
                        )

            # Element not found
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return VisionAnalysisResult(
                text=f"No element matching '{request.query}' was detected.",
                capability_used=VisionCapability.ELEMENT_LOCATION,
                bounding_boxes=[],
                elements=[],
                execution_time_ms=elapsed_ms,
            )

        # -----------------------------------------------------------------------
        # Tier 3+: VLM reasoning (FAST_VLM, PRIMARY_VLM, DEEP_VLM)
        # -----------------------------------------------------------------------
        if self._vlm_provider is None:
            # Graceful degradation to OCR if VLM is not configured
            logger.info("VLM not configured; degrading to OCR for query", query=request.query)
            ocr_result = self._ocr_provider.extract_text(pil_image)
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return VisionAnalysisResult(
                text=f"[VLM unconfigured, extracted text]: {ocr_result.text}",
                capability_used=VisionCapability.OCR_ONLY,
                ocr_result=ocr_result,
                execution_time_ms=elapsed_ms,
            )

        vram_ok = await self.check_vram_available()
        if not vram_ok:
            raise VLMError(
                f"VRAM pressure detected; memory exceeds budget ({self._max_vram_mb} MB). "
                f"VLM inference rejected to protect system stability."
            )

        vlm_req = VLMRequest(
            prompt=request.query,
            image=pil_image,
            system_prompt=request.system_prompt,
            max_tokens=request.max_tokens,
            capability=capability,
        )
        vlm_resp = await self._vlm_provider.analyze_image(vlm_req)
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        return VisionAnalysisResult(
            text=vlm_resp.content,
            capability_used=capability,
            bounding_boxes=vlm_resp.bounding_boxes,
            raw_vlm_response=vlm_resp,
            execution_time_ms=elapsed_ms,
        )

    # ---------------------------------------------------------------------------
    # Convenience APIs
    # ---------------------------------------------------------------------------

    async def extract_text(self, image: Image.Image | CapturedFrame | None = None) -> OCRResult:
        """Extract structured OCR text from screen or provided image."""
        pil_image = self._resolve_image(image)
        return self._ocr_provider.extract_text(pil_image)

    async def find_element(
        self, description: str, image: Image.Image | CapturedFrame | None = None
    ) -> list[BoundingBox]:
        """Locate element on screen matching description; returns canonical bounding boxes."""
        req = VisionAnalysisRequest(
            query=description,
            image=image,
            capability=VisionCapability.ELEMENT_LOCATION,
        )
        result = await self.analyze(req)
        return result.bounding_boxes

    async def ask(self, prompt: str, image: Image.Image | CapturedFrame | None = None) -> str:
        """Ask local VLM a question about the screen or image."""
        req = VisionAnalysisRequest(
            query=prompt,
            image=image,
            capability=VisionCapability.FAST_VLM,
        )
        result = await self.analyze(req)
        return result.text
