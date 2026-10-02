"""Vision-Language Model (VLM) provider abstraction — Phase 7 Iteration 3.

Adheres to:
- Phase 7 Vision Architecture (ADRs 046-050)
- Strict local-only execution: Cloud providers and external endpoints are rejected.
- OpenAI-compatible multimodal adapter (/v1/chat/completions with image_url base64 data URI).
- Zero disk persistence: All images held and processed in RAM.
- Coordinate re-scaling: Bounding boxes from resized inference images are mapped
  back to original virtual desktop dimensions.
"""

from __future__ import annotations

import abc
import base64
import io
import json
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx
from PIL import Image

from tom.schemas.vision import (
    BoundingBox,
    CapturedFrame,
    VisionCapability,
    VLMRequest,
    VLMResponse,
)
from tom.telemetry.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__, component="vision.vlm")


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class VLMError(Exception):
    """Base exception for all VLM provider errors."""


class VLMConnectionError(VLMError):
    """Raised when the local VLM HTTP endpoint is unreachable."""


class VLMTimeoutError(VLMError):
    """Raised when VLM inference exceeds the configured timeout."""


class VLMResponseError(VLMError):
    """Raised when the VLM returns an HTTP error code or malformed JSON."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class CloudVLMRejectedError(VLMError):
    """Raised when an attempt is made to use a non-local/cloud VLM provider."""


# ---------------------------------------------------------------------------
# Local-only verification helper
# ---------------------------------------------------------------------------

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "testserver"})


def _assert_local_endpoint(url: str) -> None:
    """Validate that the target URL resolves strictly to the local machine.

    Raises:
        CloudVLMRejectedError: If the URL points to an external or cloud endpoint.
    """
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
    except Exception as exc:
        raise CloudVLMRejectedError(f"Invalid VLM endpoint URL: {url!r}") from exc

    if not hostname or hostname not in _LOCAL_HOSTS:
        raise CloudVLMRejectedError(
            f"Cloud/remote VLM endpoint {url!r} is strictly forbidden in Phase 7. "
            f"TOM only supports local runtimes (e.g. LM Studio on 127.0.0.1)."
        )


# ---------------------------------------------------------------------------
# Coordinate scaling & parsing helpers
# ---------------------------------------------------------------------------


def _extract_json_arrays(text: str) -> list[str]:
    """Extract top-level JSON array strings from text, properly matching balanced brackets."""
    results: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "[":
            start = i
            depth = 0
            while i < len(text):
                if text[i] == "[":
                    depth += 1
                elif text[i] == "]":
                    depth -= 1
                    if depth == 0:
                        results.append(text[start : i + 1])
                        break
                i += 1
        i += 1
    return results


def _parse_bounding_boxes(
    text: str,
    orig_w: int,
    orig_h: int,
    scale_x: float,
    scale_y: float,
) -> list[BoundingBox]:
    """Extract bounding boxes from VLM text and re-scale to original screen pixels.

    Handles:
    - JSON list of objects: [{"left": ..., "top": ..., "right": ..., "bottom": ...}]
    - JSON list of 4-element arrays: [[x1, y1, x2, y2], ...] or [[ymin, xmin, ymax, xmax], ...]
    - Normalized coordinates (0.0 to 1.0 or 0 to 1000)
    - Resized pixel coordinates mapped back via (scale_x, scale_y)
    """
    boxes: list[BoundingBox] = []

    json_matches = _extract_json_arrays(text)
    for match in json_matches:
        try:
            parsed = json.loads(match)
            if not isinstance(parsed, list):
                continue
            if len(parsed) == 4 and all(isinstance(v, (int, float)) for v in parsed):
                v0, v1, v2, v3 = (
                    float(parsed[0]),
                    float(parsed[1]),
                    float(parsed[2]),
                    float(parsed[3]),
                )
                box = _normalize_and_scale_box(v0, v1, v2, v3, orig_w, orig_h, scale_x, scale_y)
                if box:
                    boxes.append(box)
                continue
            for item in parsed:
                if isinstance(item, dict):
                    # Keys could be left/top/right/bottom or xmin/ymin/xmax/ymax or box_2d
                    if (
                        "box_2d" in item
                        and isinstance(item["box_2d"], list)
                        and len(item["box_2d"]) == 4
                    ):
                        item = item["box_2d"]
                    else:
                        bx1 = item.get("left", item.get("xmin", item.get("x1")))
                        by1 = item.get("top", item.get("ymin", item.get("y1")))
                        bx2 = item.get("right", item.get("xmax", item.get("x2")))
                        by2 = item.get("bottom", item.get("ymax", item.get("y2")))
                        if all(
                            v is not None and isinstance(v, (int, float))
                            for v in (bx1, by1, bx2, by2)
                        ):
                            box = _normalize_and_scale_box(
                                float(bx1),
                                float(by1),
                                float(bx2),
                                float(by2),
                                orig_w,
                                orig_h,
                                scale_x,
                                scale_y,
                            )
                            if box:
                                boxes.append(box)
                            continue

                if (
                    isinstance(item, list)
                    and len(item) == 4
                    and all(isinstance(v, (int, float)) for v in item)
                ):
                    v0, v1, v2, v3 = float(item[0]), float(item[1]), float(item[2]), float(item[3])
                    box = _normalize_and_scale_box(v0, v1, v2, v3, orig_w, orig_h, scale_x, scale_y)
                    if box:
                        boxes.append(box)
        except Exception:
            continue

    return boxes


def _normalize_and_scale_box(
    v0: float,
    v1: float,
    v2: float,
    v3: float,
    orig_w: int,
    orig_h: int,
    scale_x: float,
    scale_y: float,
) -> BoundingBox | None:
    """Normalize coordinate values and scale back to original screen coordinates."""
    # 1. Check if normalized [0.0, 1.0]
    if max(v0, v1, v2, v3) <= 1.0:
        bx1, by1, bx2, by2 = v0 * orig_w, v1 * orig_h, v2 * orig_w, v3 * orig_h
    else:
        # Check if coordinates fit in the resized pixel dimensions
        max_w = (orig_w / scale_x) if scale_x > 0 else float(orig_w)
        max_h = (orig_h / scale_y) if scale_y > 0 else float(orig_h)
        if max(v0, v2) <= max_w * 1.05 and max(v1, v3) <= max_h * 1.05:
            # Resized pixel coordinates -> scale back up to original screen pixels
            bx1, by1, bx2, by2 = v0 * scale_x, v1 * scale_y, v2 * scale_x, v3 * scale_y
        elif max(v0, v1, v2, v3) <= 1000.0 and orig_w > 1000:
            # Normalized [0..1000] range
            bx1, by1, bx2, by2 = (
                (v0 / 1000.0) * orig_w,
                (v1 / 1000.0) * orig_h,
                (v2 / 1000.0) * orig_w,
                (v3 / 1000.0) * orig_h,
            )
        else:
            # Fallback direct pixel scaling
            bx1, by1, bx2, by2 = v0 * scale_x, v1 * scale_y, v2 * scale_x, v3 * scale_y

    left = min(bx1, bx2)
    right = max(bx1, bx2)
    top = min(by1, by2)
    bottom = max(by1, by2)

    if right <= left or bottom <= top:
        return None

    return BoundingBox(
        left=round(left, 2),
        top=round(top, 2),
        right=round(right, 2),
        bottom=round(bottom, 2),
    )


# ---------------------------------------------------------------------------
# Abstract base provider
# ---------------------------------------------------------------------------


class VLMProvider(abc.ABC):
    """Abstract interface for local Vision-Language Model inference."""

    @abc.abstractmethod
    async def analyze_image(self, request: VLMRequest) -> VLMResponse:
        """Analyze an image with a prompt and return text + bounding boxes.

        Args:
            request: Structured VLMRequest containing image and prompt.

        Returns:
            VLMResponse with content and canonical coordinates.
        """

    @abc.abstractmethod
    async def locate_element(self, image: Image.Image, description: str) -> list[BoundingBox]:
        """Locate element(s) matching description in original screen coordinates.

        Args:
            image: In-memory PIL Image.
            description: Target visual element description.

        Returns:
            List of matching BoundingBox instances in original screen coordinates.
        """


# ---------------------------------------------------------------------------
# Mock provider for offline testing
# ---------------------------------------------------------------------------


class MockVLMProvider(VLMProvider):
    """Deterministic offline test double for VLM operations.

    Zero network, zero GPU, zero disk persistence.
    """

    def __init__(
        self,
        default_response: str = "Mock visual analysis result.",
        canned_boxes: list[BoundingBox] | None = None,
        should_raise: Exception | None = None,
    ) -> None:
        self.default_response = default_response
        self.canned_boxes = canned_boxes or []
        self.should_raise = should_raise
        self.call_history: list[VLMRequest] = []

    async def analyze_image(self, request: VLMRequest) -> VLMResponse:
        if self.should_raise:
            raise self.should_raise

        self.call_history.append(request)

        # Allow dynamic responses based on prompt keywords if not overridden
        boxes = list(self.canned_boxes)
        content = self.default_response

        if "button" in request.prompt.lower() and not boxes:
            boxes.append(BoundingBox(left=100.0, top=200.0, right=220.0, bottom=240.0))

        return VLMResponse(
            content=content,
            bounding_boxes=boxes,
            model="mock-vlm",
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            finish_reason="stop",
        )

    async def locate_element(self, image: Image.Image, description: str) -> list[BoundingBox]:
        if self.should_raise:
            raise self.should_raise

        if self.canned_boxes:
            return list(self.canned_boxes)

        # Deterministic mock element based on image size
        w, h = image.size
        cx, cy = w / 2.0, h / 2.0
        return [
            BoundingBox(
                left=max(0.0, cx - 50.0),
                top=max(0.0, cy - 20.0),
                right=min(float(w), cx + 50.0),
                bottom=min(float(h), cy + 20.0),
            )
        ]


# ---------------------------------------------------------------------------
# Local OpenAI-compatible VLM Provider
# ---------------------------------------------------------------------------


class LocalVLMProvider(VLMProvider):
    """OpenAI-compatible multimodal HTTP provider for local VLM runtimes.

    Compatible with LM Studio, Bionic, Ollama, and local vLLM instances.
    Enforces local-only execution and dynamic coordinate re-scaling.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1234/v1",
        model: str = "local-vlm",
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
        max_image_dim: int = 1280,
    ) -> None:
        _assert_local_endpoint(base_url)

        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._max_image_dim = max_image_dim
        self._client = client
        self._owns_client = client is None

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def model(self) -> str:
        return self._model

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
            self._owns_client = True
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client if owned."""
        if self._owns_client and self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _preprocess_image(
        self, image: Image.Image | CapturedFrame
    ) -> tuple[str, int, int, float, float]:
        """Convert image to base64 data URI and calculate coordinate re-scale ratios.

        Returns:
            Tuple of (data_uri, orig_width, orig_height, scale_x, scale_y)
        """
        if isinstance(image, CapturedFrame):
            pil_img = image.to_pil()
        elif isinstance(image, Image.Image):
            pil_img = image
        else:
            raise ValueError(f"Unsupported image type: {type(image)}")

        orig_w, orig_h = pil_img.size
        max_dim = max(orig_w, orig_h)

        if max_dim > self._max_image_dim and max_dim > 0:
            scale = self._max_image_dim / float(max_dim)
            new_w = max(1, int(orig_w * scale))
            new_h = max(1, int(orig_h * scale))
            resized = pil_img.resize((new_w, new_h), Image.Resampling.BILINEAR)
            scale_x = orig_w / float(new_w)
            scale_y = orig_h / float(new_h)
        else:
            resized = pil_img
            scale_x = 1.0
            scale_y = 1.0

        # Encode in memory as JPEG or PNG
        buf = io.BytesIO()
        if resized.mode in ("RGBA", "LA", "P"):
            resized = resized.convert("RGB")
        resized.save(buf, format="JPEG", quality=85)
        raw = buf.getvalue()
        b64_str = base64.b64encode(raw).decode("ascii")
        data_uri = f"data:image/jpeg;base64,{b64_str}"

        return data_uri, orig_w, orig_h, scale_x, scale_y

    def _build_payload(self, request: VLMRequest, data_uri: str) -> dict[str, Any]:
        """Construct standard OpenAI multimodal chat completion payload."""
        messages: list[dict[str, Any]] = []

        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})

        user_content: list[dict[str, Any]] = [
            {"type": "text", "text": request.prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": data_uri,
                    "detail": request.detail,
                },
            },
        ]

        messages.append({"role": "user", "content": user_content})

        return {
            "model": self._model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }

    async def analyze_image(self, request: VLMRequest) -> VLMResponse:
        """Execute multimodal completion against the local VLM endpoint."""
        if request.image is None:
            raise ValueError("VLMRequest requires an image for visual analysis.")

        data_uri, orig_w, orig_h, scale_x, scale_y = self._preprocess_image(request.image)
        payload = self._build_payload(request, data_uri)
        endpoint = f"{self._base_url}/chat/completions"

        client = self._get_client()

        try:
            resp = await client.post(
                endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
        except httpx.ConnectError as exc:
            logger.error(
                "Failed to connect to local VLM endpoint", endpoint=endpoint, error=str(exc)
            )
            raise VLMConnectionError(
                f"Could not connect to local VLM at {endpoint}: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            logger.error("VLM inference timed out", endpoint=endpoint, timeout=self._timeout)
            raise VLMTimeoutError(
                f"Local VLM inference timed out after {self._timeout}s: {exc}"
            ) from exc
        except Exception as exc:
            logger.error("Unexpected network failure during VLM call", error=str(exc))
            raise VLMConnectionError(f"VLM network error: {exc}") from exc

        if resp.status_code != 200:
            logger.error(
                "Local VLM returned error status",
                status_code=resp.status_code,
                body=resp.text[:200],
            )
            raise VLMResponseError(
                f"Local VLM returned HTTP {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
            )

        try:
            data = resp.json()
            choice = data["choices"][0]
            content = choice["message"]["content"] or ""
            finish_reason = choice.get("finish_reason", "stop")
            usage = data.get("usage", {})
            model_name = data.get("model", self._model)
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise VLMResponseError(f"Malformed JSON response from local VLM: {exc}") from exc

        # Parse and re-scale any bounding boxes embedded in the response
        boxes = _parse_bounding_boxes(content, orig_w, orig_h, scale_x, scale_y)

        return VLMResponse(
            content=content,
            bounding_boxes=boxes,
            model=model_name,
            usage=usage,
            finish_reason=finish_reason,
        )

    async def locate_element(self, image: Image.Image, description: str) -> list[BoundingBox]:
        """Locate element matching description, returning bounding boxes in original screen coords."""
        prompt = (
            f"Locate the UI element described as '{description}'. "
            f"Return only a JSON array with its bounding box coordinates: "
            f"[left, top, right, bottom]."
        )
        request = VLMRequest(
            prompt=prompt,
            image=image,
            capability=VisionCapability.FAST_VLM,
            max_tokens=256,
            temperature=0.1,
        )
        resp = await self.analyze_image(request)
        return resp.bounding_boxes
