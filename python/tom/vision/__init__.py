"""TOM vision subsystem.

Phase 7 Iteration 1: In-memory screen capture and privacy shield.
Phase 7 Iteration 2: Structured OCR and classical CV element detection.
"""

from tom.schemas.vision import (
    BoundingBox,
    CapturedFrame,
    ElementType,
    MonitorInfo,
    OCRResult,
    Point2D,
    PrivacyCheckResult,
    ScreenDimensions,
    TextLocation,
    UIElement,
    VisionAnalysisRequest,
    VisionAnalysisResult,
    VisionCapability,
    VLMRequest,
    VLMResponse,
    WindowInfo,
)
from tom.vision.capture import (
    CaptureBackend,
    MockCaptureBackend,
    MSSCaptureBackend,
    PillowCaptureBackend,
    ScreenCaptureError,
    ScreenCaptureService,
)
from tom.vision.cv import CVElementDetector, CVError, ElementDetectorHook
from tom.vision.manager import VisionManager
from tom.vision.ocr import (
    MockOCRProvider,
    OCRError,
    OCRProvider,
    OCRProviderUnavailableError,
    WindowsMediaOCRProvider,
)
from tom.vision.privacy import (
    DEFAULT_SENSITIVE_WINDOW_PATTERNS,
    PrivacyShield,
    SensitiveScreenContentError,
)
from tom.vision.vlm import (
    CloudVLMRejectedError,
    LocalVLMProvider,
    MockVLMProvider,
    VLMConnectionError,
    VLMError,
    VLMProvider,
    VLMResponseError,
    VLMTimeoutError,
)

__all__ = [
    # Schemas — Iteration 1
    "CapturedFrame",
    "MonitorInfo",
    "ScreenDimensions",
    "WindowInfo",
    "PrivacyCheckResult",
    # Schemas — Iteration 2
    "BoundingBox",
    "Point2D",
    "TextLocation",
    "OCRResult",
    "ElementType",
    "UIElement",
    # Capture — Iteration 1
    "ScreenCaptureService",
    "ScreenCaptureError",
    "CaptureBackend",
    "MSSCaptureBackend",
    "PillowCaptureBackend",
    "MockCaptureBackend",
    # Privacy — Iteration 1
    "PrivacyShield",
    "SensitiveScreenContentError",
    "DEFAULT_SENSITIVE_WINDOW_PATTERNS",
    # OCR — Iteration 2
    "OCRProvider",
    "OCRError",
    "OCRProviderUnavailableError",
    "MockOCRProvider",
    "WindowsMediaOCRProvider",
    # CV — Iteration 2
    "CVElementDetector",
    "CVError",
    "ElementDetectorHook",
    # Schemas — Iteration 3
    "VisionCapability",
    "VLMRequest",
    "VLMResponse",
    "VisionAnalysisRequest",
    "VisionAnalysisResult",
    # VLM — Iteration 3
    "VLMProvider",
    "MockVLMProvider",
    "LocalVLMProvider",
    "VLMError",
    "VLMConnectionError",
    "VLMTimeoutError",
    "VLMResponseError",
    "CloudVLMRejectedError",
    # Manager — Iteration 3
    "VisionManager",
]
