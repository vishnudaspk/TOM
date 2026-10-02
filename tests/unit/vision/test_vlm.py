"""Unit tests for VLM providers and coordinate scaling — Phase 7 Iteration 3.

Adheres to:
- Offline determinism: zero network calls, zero GPU, zero disk persistence.
- Local-only constraint enforcement: Cloud endpoints must raise CloudVLMRejectedError.
- Coordinate re-scaling validation across resolution downscaling.
- Strict-markers convention: run_async() helper for coroutines, no pytest.mark.asyncio.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from PIL import Image
from tom.schemas.vision import (
    BoundingBox,
    CapturedFrame,
    VLMRequest,
)
from tom.vision.vlm import (
    CloudVLMRejectedError,
    LocalVLMProvider,
    MockVLMProvider,
    VLMConnectionError,
    VLMResponseError,
    VLMTimeoutError,
    _normalize_and_scale_box,
    _parse_bounding_boxes,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def _make_test_image(width: int = 2560, height: int = 1440) -> Image.Image:
    """Create an in-memory test image without disk I/O."""
    return Image.new("RGB", (width, height), color=(100, 150, 200))


def _make_mock_client(
    response_content: str = "Analysis result",
    status_code: int = 200,
    side_effect: Exception | None = None,
) -> httpx.AsyncClient:
    """Create a mock httpx.AsyncClient returning controlled responses."""
    client = MagicMock(spec=httpx.AsyncClient)
    client.is_closed = False

    if side_effect:
        client.post = AsyncMock(side_effect=side_effect)
    else:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        resp.text = json.dumps(
            {
                "choices": [{"message": {"content": response_content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 15, "completion_tokens": 25, "total_tokens": 40},
                "model": "local-vlm",
            }
        )
        resp.json.return_value = {
            "choices": [{"message": {"content": response_content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 15, "completion_tokens": 25, "total_tokens": 40},
            "model": "local-vlm",
        }
        client.post = AsyncMock(return_value=resp)

    return client


# ---------------------------------------------------------------------------
# MockVLMProvider tests
# ---------------------------------------------------------------------------


class TestMockVLMProvider:
    """Tests for MockVLMProvider offline behavior."""

    def test_analyze_image_default(self) -> None:
        provider = MockVLMProvider(default_response="Mock scene description")
        img = _make_test_image(800, 600)
        req = VLMRequest(prompt="What is on screen?", image=img)

        resp = run_async(provider.analyze_image(req))
        assert resp.content == "Mock scene description"
        assert resp.model == "mock-vlm"
        assert len(provider.call_history) == 1

    def test_analyze_image_canned_boxes(self) -> None:
        canned = [BoundingBox(left=50.0, top=60.0, right=150.0, bottom=120.0)]
        provider = MockVLMProvider(canned_boxes=canned)
        img = _make_test_image(800, 600)
        req = VLMRequest(prompt="Find button", image=img)

        resp = run_async(provider.analyze_image(req))
        assert len(resp.bounding_boxes) == 1
        assert resp.bounding_boxes[0].left == 50.0

    def test_locate_element(self) -> None:
        provider = MockVLMProvider()
        img = _make_test_image(1000, 800)
        boxes = run_async(provider.locate_element(img, "Submit button"))

        assert len(boxes) == 1
        assert boxes[0].width > 0
        assert boxes[0].height > 0

    def test_should_raise_injection(self) -> None:
        provider = MockVLMProvider(should_raise=VLMConnectionError("Simulated failure"))
        img = _make_test_image(800, 600)
        req = VLMRequest(prompt="Test", image=img)

        with pytest.raises(VLMConnectionError, match="Simulated failure"):
            run_async(provider.analyze_image(req))


# ---------------------------------------------------------------------------
# LocalVLMProvider local-only policy & initialization
# ---------------------------------------------------------------------------


class TestLocalVLMProviderSecurity:
    """Verify strict local-only runtime enforcement."""

    def test_local_endpoints_allowed(self) -> None:
        for url in [
            "http://localhost:1234/v1",
            "http://127.0.0.1:1234/v1",
            "http://[::1]:1234/v1",
            "http://127.0.0.1:11434/v1",
        ]:
            provider = LocalVLMProvider(base_url=url)
            assert provider.base_url.startswith("http")

    def test_cloud_endpoints_strictly_rejected(self) -> None:
        cloud_urls = [
            "https://api.openai.com/v1",
            "https://api.anthropic.com/v1",
            "http://remote-server.com:1234/v1",
            "https://my-vlm.azurewebsites.net/v1",
            "http://192.168.1.50:1234/v1",
        ]
        for url in cloud_urls:
            with pytest.raises(CloudVLMRejectedError, match="strictly forbidden"):
                LocalVLMProvider(base_url=url)


# ---------------------------------------------------------------------------
# Coordinate scaling & parsing tests
# ---------------------------------------------------------------------------


class TestCoordinateScaling:
    """Test scaling from resized VLM coordinate space back to canonical screen space."""

    def test_normalize_and_scale_normalized_unit(self) -> None:
        # Normalized [0.0..1.0] coordinates on a 2560x1440 display
        box = _normalize_and_scale_box(
            v0=0.1,
            v1=0.2,
            v2=0.3,
            v3=0.4,
            orig_w=2560,
            orig_h=1440,
            scale_x=2.0,
            scale_y=2.0,
        )
        assert box is not None
        assert box.left == 256.0
        assert box.top == 288.0
        assert box.right == 768.0
        assert box.bottom == 576.0

    def test_normalize_and_scale_1000_range(self) -> None:
        # 0..1000 coordinates (Qwen-VL style)
        box = _normalize_and_scale_box(
            v0=100.0,
            v1=200.0,
            v2=500.0,
            v3=600.0,
            orig_w=2000,
            orig_h=1000,
            scale_x=2.0,
            scale_y=2.0,
        )
        assert box is not None
        assert box.left == 200.0
        assert box.top == 200.0
        assert box.right == 1000.0
        assert box.bottom == 600.0

    def test_scale_resized_pixels(self) -> None:
        # Original 2560x1440 downscaled to 1280x720 (scale_x=2.0, scale_y=2.0)
        box = _normalize_and_scale_box(
            v0=100.0,
            v1=50.0,
            v2=200.0,
            v3=150.0,
            orig_w=2560,
            orig_h=1440,
            scale_x=2.0,
            scale_y=2.0,
        )
        assert box is not None
        assert box.left == 200.0
        assert box.top == 100.0
        assert box.right == 400.0
        assert box.bottom == 300.0

    def test_parse_bounding_boxes_json_dict(self) -> None:
        text = 'The button is at [{"left": 100, "top": 50, "right": 200, "bottom": 80}].'
        boxes = _parse_bounding_boxes(text, orig_w=2560, orig_h=1440, scale_x=2.0, scale_y=2.0)
        assert len(boxes) == 1
        assert boxes[0].left == 200.0
        assert boxes[0].top == 100.0
        assert boxes[0].right == 400.0
        assert boxes[0].bottom == 160.0

    def test_parse_bounding_boxes_json_array(self) -> None:
        text = "Identified element at [[100, 50, 300, 150]]."
        boxes = _parse_bounding_boxes(text, orig_w=1920, orig_h=1080, scale_x=1.5, scale_y=1.5)
        assert len(boxes) == 1
        assert boxes[0].left == 150.0
        assert boxes[0].top == 75.0


# ---------------------------------------------------------------------------
# LocalVLMProvider request assembly & error handling
# ---------------------------------------------------------------------------


class TestLocalVLMProviderExecution:
    """Test LocalVLMProvider HTTP payloads and network error handling."""

    def test_analyze_image_payload_and_resize(self) -> None:
        # 2560x1440 image -> downscaled to 1280x720
        img = _make_test_image(2560, 1440)
        mock_client = _make_mock_client("Found the submit button at [100, 100, 300, 200].")

        provider = LocalVLMProvider(
            base_url="http://127.0.0.1:1234/v1",
            client=mock_client,
            max_image_dim=1280,
        )

        req = VLMRequest(
            prompt="Locate submit button",
            image=img,
            system_prompt="You are a GUI grounding assistant.",
        )
        resp = run_async(provider.analyze_image(req))

        assert resp.content.startswith("Found the submit button")
        assert len(resp.bounding_boxes) == 1
        # Bounding box must be scaled back up by 2x
        box = resp.bounding_boxes[0]
        assert box.left == 200.0
        assert box.top == 200.0
        assert box.right == 600.0
        assert box.bottom == 400.0

        # Verify payload sent to client
        mock_client.post.assert_awaited_once()
        call_kwargs = mock_client.post.call_args.kwargs
        payload = call_kwargs["json"]
        assert payload["model"] == "local-vlm"
        assert len(payload["messages"]) == 2
        assert payload["messages"][0]["role"] == "system"
        user_msg = payload["messages"][1]["content"]
        assert user_msg[0]["type"] == "text"
        assert user_msg[1]["type"] == "image_url"
        assert user_msg[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    def test_captured_frame_input(self) -> None:
        img = _make_test_image(800, 600)
        frame = CapturedFrame.from_pil(img)
        mock_client = _make_mock_client("Screen inspected successfully.")

        provider = LocalVLMProvider(client=mock_client)
        req = VLMRequest(prompt="Inspect screen", image=frame)

        resp = run_async(provider.analyze_image(req))
        assert resp.content == "Screen inspected successfully."

    def test_connection_error_raises_vlm_connection_error(self) -> None:
        mock_client = _make_mock_client(side_effect=httpx.ConnectError("Failed to connect"))
        provider = LocalVLMProvider(client=mock_client)
        req = VLMRequest(prompt="test", image=_make_test_image(100, 100))

        with pytest.raises(VLMConnectionError, match="Could not connect to local VLM"):
            run_async(provider.analyze_image(req))

    def test_timeout_raises_vlm_timeout_error(self) -> None:
        mock_client = _make_mock_client(side_effect=httpx.TimeoutException("Timed out"))
        provider = LocalVLMProvider(client=mock_client, timeout=5.0)
        req = VLMRequest(prompt="test", image=_make_test_image(100, 100))

        with pytest.raises(VLMTimeoutError, match="timed out"):
            run_async(provider.analyze_image(req))

    def test_http_error_raises_vlm_response_error(self) -> None:
        mock_client = _make_mock_client("Internal Server Error", status_code=500)
        provider = LocalVLMProvider(client=mock_client)
        req = VLMRequest(prompt="test", image=_make_test_image(100, 100))

        with pytest.raises(VLMResponseError) as exc_info:
            run_async(provider.analyze_image(req))
        assert exc_info.value.status_code == 500

    def test_locate_element(self) -> None:
        mock_client = _make_mock_client("Found target at [[50, 50, 150, 150]].")
        provider = LocalVLMProvider(client=mock_client)
        img = _make_test_image(1000, 1000)

        boxes = run_async(provider.locate_element(img, "search box"))
        assert len(boxes) == 1
        assert boxes[0].left == 50.0
