"""Unit tests for TOM deterministic vision tools.

Adheres to:
- Phase 7 Vision Specification (Iteration 4)
- Decision 032: Centralized Tool Invocation Safety
- All vision tools are classified as SAFE (auto-executed without confirmation)
- Ephemeral in-memory processing: zero disk persistence
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError
from tom.schemas.vision import (
    BoundingBox,
    CapturedFrame,
    OCRResult,
    ScreenDimensions,
    TextLocation,
)
from tom.security.permissions import PermissionLevel
from tom.tools.executor import ToolExecutor
from tom.tools.registry import ToolRegistry
from tom.tools.vision import (
    VisionAskInput,
    VisionAskOutput,
    VisionCaptureInput,
    VisionCaptureOutput,
    VisionFindElementInput,
    VisionFindElementOutput,
    VisionOcrInput,
    VisionOcrOutput,
    create_vision_tools,
    register_vision_tools,
)


def run_async(coro: Any) -> Any:
    """Helper to run async coroutines synchronously in pytest."""
    return asyncio.run(coro)


EXPECTED_VISION_TOOLS = {
    "vision.capture",
    "vision.ocr",
    "vision.find_element",
    "vision.ask",
}


# ---------------------------------------------------------------------------
# Fake Stubs for VisionManager
# ---------------------------------------------------------------------------


class FakePrivacyShield:
    def get_active_window_title(self) -> str | None:
        return "Editor - Test Window"


class FakeScreenCaptureService:
    def __init__(self) -> None:
        self.privacy_shield = FakePrivacyShield()
        self.captured_screens: list[int | None] = []
        self.captured_regions: list[ScreenDimensions] = []

    def capture_screen(self, monitor_index: int | None = None) -> CapturedFrame:
        self.captured_screens.append(monitor_index)
        return CapturedFrame(
            width=100,
            height=50,
            channels=4,
            format="RGBA",
            raw_bytes=b"\x00" * (100 * 50 * 4),
            monitor_index=monitor_index or 1,
        )

    def capture_region(self, region: ScreenDimensions) -> CapturedFrame:
        self.captured_regions.append(region)
        return CapturedFrame(
            width=region.width,
            height=region.height,
            channels=4,
            format="RGBA",
            raw_bytes=b"\x00" * (region.width * region.height * 4),
            source_region=region,
        )


class FakeVisionManager:
    """Deterministic stub for VisionManager during unit tests."""

    def __init__(self) -> None:
        self.capture_service = FakeScreenCaptureService()
        self.ocr_calls: list[Any] = []
        self.find_element_calls: list[str] = []
        self.ask_calls: list[str] = []

    async def extract_text(self, image: Any = None) -> OCRResult:
        self.ocr_calls.append(image)
        return OCRResult(
            text="Submit Button",
            confidence=0.98,
            words=[
                TextLocation(
                    text="Submit Button",
                    bounding_box=BoundingBox(left=10, top=20, right=80, bottom=40),
                    confidence=0.98,
                )
            ],
        )

    async def find_element(self, description: str, image: Any = None) -> list[BoundingBox]:
        self.find_element_calls.append(description)
        if "submit" in description.lower() or "button" in description.lower():
            return [BoundingBox(left=50, top=100, right=150, bottom=140)]
        return []

    async def ask(self, prompt: str, image: Any = None) -> str:
        self.ask_calls.append(prompt)
        return f"Perception response for: {prompt}"


# ---------------------------------------------------------------------------
# Test Registration & Definitions
# ---------------------------------------------------------------------------


class TestVisionToolRegistration:
    def test_create_vision_tools_returns_four_definitions(self) -> None:
        tools = create_vision_tools()
        assert len(tools) == 4
        names = {t.name for t in tools}
        assert names == EXPECTED_VISION_TOOLS

    def test_register_vision_tools_populates_registry(self) -> None:
        reg = ToolRegistry()
        defs = register_vision_tools(registry=reg)
        assert len(defs) == 4
        assert {t.name for t in reg.list_tools(category="vision")} == EXPECTED_VISION_TOOLS

    def test_all_vision_tools_are_safe_permission(self) -> None:
        tools = create_vision_tools()
        for t in tools:
            assert t.permission_level == PermissionLevel.SAFE, (
                f"{t.name} should be SAFE, got {t.permission_level}"
            )

    def test_all_vision_tools_have_vision_category(self) -> None:
        tools = create_vision_tools()
        for t in tools:
            assert t.category == "vision"

    def test_register_replace_is_idempotent(self) -> None:
        reg = ToolRegistry()
        register_vision_tools(registry=reg, replace=True)
        register_vision_tools(registry=reg, replace=True)
        assert len(reg.list_tools(category="vision")) == 4


# ---------------------------------------------------------------------------
# Test Parameter Validation
# ---------------------------------------------------------------------------


class TestVisionInputValidation:
    def test_find_element_requires_description(self) -> None:
        with pytest.raises(ValidationError):
            VisionFindElementInput.model_validate({})

        with pytest.raises(ValidationError):
            VisionFindElementInput.model_validate({"description": ""})

    def test_ask_requires_question(self) -> None:
        with pytest.raises(ValidationError):
            VisionAskInput.model_validate({})

        with pytest.raises(ValidationError):
            VisionAskInput.model_validate({"question": ""})

    def test_capture_forbids_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            VisionCaptureInput.model_validate({"extra_field": "disallowed"})

    def test_ocr_forbids_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            VisionOcrInput.model_validate({"extra_field": "disallowed"})


# ---------------------------------------------------------------------------
# Test Execution Through ToolExecutor
# ---------------------------------------------------------------------------


class TestVisionToolExecution:
    def test_vision_capture_executes_safely(self) -> None:
        reg = ToolRegistry()
        fake_mgr = FakeVisionManager()
        register_vision_tools(registry=reg, vision_manager=fake_mgr)
        executor = ToolExecutor(registry=reg)

        result = run_async(executor.execute("vision.capture", {"monitor": 1}))
        assert result.success is True
        assert isinstance(result.data, VisionCaptureOutput)
        assert result.data.width == 100
        assert result.data.height == 50
        assert result.data.monitor_index == 1
        assert result.data.active_window_title == "Editor - Test Window"

    def test_vision_capture_region_executes_safely(self) -> None:
        reg = ToolRegistry()
        fake_mgr = FakeVisionManager()
        register_vision_tools(registry=reg, vision_manager=fake_mgr)
        executor = ToolExecutor(registry=reg)

        result = run_async(executor.execute("vision.capture", {"region": [0, 0, 50, 25]}))
        assert result.success is True
        assert isinstance(result.data, VisionCaptureOutput)
        assert result.data.width == 50
        assert result.data.height == 25
        assert len(fake_mgr.capture_service.captured_regions) == 1

    def test_vision_ocr_executes_safely(self) -> None:
        reg = ToolRegistry()
        fake_mgr = FakeVisionManager()
        register_vision_tools(registry=reg, vision_manager=fake_mgr)
        executor = ToolExecutor(registry=reg)

        result = run_async(executor.execute("vision.ocr", {}))
        assert result.success is True
        assert isinstance(result.data, VisionOcrOutput)
        assert result.data.text == "Submit Button"
        assert result.data.confidence == 0.98
        assert result.data.count == 1
        assert len(fake_mgr.ocr_calls) == 1

    def test_vision_find_element_located(self) -> None:
        reg = ToolRegistry()
        fake_mgr = FakeVisionManager()
        register_vision_tools(registry=reg, vision_manager=fake_mgr)
        executor = ToolExecutor(registry=reg)

        result = run_async(
            executor.execute("vision.find_element", {"description": "submit button"})
        )
        assert result.success is True
        assert isinstance(result.data, VisionFindElementOutput)
        assert result.data.found is True
        assert result.data.x == 100  # center of 50..150
        assert result.data.y == 120  # center of 100..140
        assert result.data.confidence == 0.9

    def test_vision_find_element_not_found(self) -> None:
        reg = ToolRegistry()
        fake_mgr = FakeVisionManager()
        register_vision_tools(registry=reg, vision_manager=fake_mgr)
        executor = ToolExecutor(registry=reg)

        result = run_async(
            executor.execute("vision.find_element", {"description": "nonexistent icon"})
        )
        assert result.success is True
        assert isinstance(result.data, VisionFindElementOutput)
        assert result.data.found is False
        assert result.data.x is None
        assert result.data.y is None

    def test_vision_ask_executes_safely(self) -> None:
        reg = ToolRegistry()
        fake_mgr = FakeVisionManager()
        register_vision_tools(registry=reg, vision_manager=fake_mgr)
        executor = ToolExecutor(registry=reg)

        result = run_async(executor.execute("vision.ask", {"question": "What is open?"}))
        assert result.success is True
        assert isinstance(result.data, VisionAskOutput)
        assert "Perception response for: What is open?" in result.data.answer
        assert result.data.capability_used == "FAST_VLM"
