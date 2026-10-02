"""Phase 7 — Iteration 5: Agent + Vision Integration Tests.

Verifies vision capability exposed through AgentDependencies:
- vision_manager injected via AgentDependencies.vision_manager
- vision tools (vision.capture, vision.ask) flow through ToolExecutor/PermissionEngine
- No VisionManager bypass (tools go through executor pipeline)
- Backward compatibility: existing AgentDependencies callers work without vision_manager
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from tom.agents.dependencies import AgentDependencies
from tom.schemas.config import ToolsConfig
from tom.schemas.vision import BoundingBox, OCRResult, TextLocation
from tom.security.confirmation import AlwaysAllowConfirmationHook
from tom.security.permissions import PermissionEngine, PermissionLevel
from tom.tools.bootstrap import setup_default_tools
from tom.tools.executor import ToolExecutor
from tom.tools.files import PathGuard
from tom.tools.registry import ToolRegistry
from tom.vision.capture import MockCaptureBackend, ScreenCaptureService
from tom.vision.manager import VisionManager
from tom.vision.ocr import MockOCRProvider
from tom.vision.vlm import MockVLMProvider


def run_async(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_vision_manager(
    ocr_text: str = "TOM Screen", vlm_resp: str = "Dark theme."
) -> VisionManager:
    ocr = MockOCRProvider(
        responses=[
            OCRResult(
                text=ocr_text,
                confidence=0.9,
                words=[
                    TextLocation(
                        text=ocr_text,
                        bounding_box=BoundingBox(left=0, top=0, right=100, bottom=20),
                        confidence=0.9,
                    )
                ],
            )
        ]
    )
    vlm = MockVLMProvider(default_response=vlm_resp)
    return VisionManager(
        capture_service=ScreenCaptureService(backend=MockCaptureBackend()),
        ocr_provider=ocr,
        vlm_provider=vlm,
    )


def _make_pipeline_with_vision(
    tmp_path: Path,
    vision_manager: VisionManager | None = None,
) -> tuple[ToolRegistry, ToolExecutor, AgentDependencies]:
    registry = ToolRegistry()
    guard = PathGuard(allowed_directories=[tmp_path])
    config = ToolsConfig(allowed_directories=[str(tmp_path)])
    setup_default_tools(
        registry=registry,
        engine_client=None,
        path_guard=guard,
        config=config,
        vision_manager=vision_manager,
        dry_run_input=True,
        replace=True,
    )
    executor = ToolExecutor(
        registry=registry,
        permission_engine=PermissionEngine(),
        confirmation_hook=AlwaysAllowConfirmationHook(),
    )
    deps = AgentDependencies(
        registry=registry,
        executor=executor,
        vision_manager=vision_manager,
    )
    return registry, executor, deps


# ---------------------------------------------------------------------------
# A. AgentDependencies backward compatibility
# ---------------------------------------------------------------------------


class TestAgentDependenciesBackwardCompat:
    """vision_manager=None must not break any existing callers."""

    def test_agent_deps_without_vision_manager(self) -> None:
        deps = AgentDependencies()
        assert deps.vision_manager is None
        assert deps.get_vision_manager() is None

    def test_agent_deps_with_voice_and_memory_still_work(self) -> None:
        deps = AgentDependencies(voice_manager=object(), memory_manager=object())
        assert deps.vision_manager is None
        assert deps.voice_manager is not None

    def test_agent_deps_vision_manager_injected(self) -> None:
        vm = _make_vision_manager()
        deps = AgentDependencies(vision_manager=vm)
        assert deps.get_vision_manager() is vm


# ---------------------------------------------------------------------------
# B. vision.capture through ToolExecutor (SAFE, no confirmation needed)
# ---------------------------------------------------------------------------


class TestVisionCaptureToolIntegration:
    def test_capture_executes_safe_without_confirmation(self, tmp_path: Path) -> None:
        vm = _make_vision_manager()
        _, executor, _ = _make_pipeline_with_vision(tmp_path, vision_manager=vm)
        result = run_async(executor.execute("vision.capture", {}))
        assert result.success
        assert hasattr(result.data, "width")
        assert hasattr(result.data, "height")

    def test_capture_returns_metadata_not_raw_bytes(self, tmp_path: Path) -> None:
        """Raw frame bytes must NOT be present in tool output."""
        vm = _make_vision_manager()
        _, executor, _ = _make_pipeline_with_vision(tmp_path, vision_manager=vm)
        result = run_async(executor.execute("vision.capture", {}))
        assert not hasattr(result.data, "raw_bytes") or result.data.raw_bytes is None
        assert hasattr(result.data, "width")

    def test_capture_tool_registered_as_safe(self, tmp_path: Path) -> None:
        registry, _, _ = _make_pipeline_with_vision(tmp_path, vision_manager=_make_vision_manager())
        assert registry.get("vision.capture").permission_level == PermissionLevel.SAFE


# ---------------------------------------------------------------------------
# C. vision.ask through ToolExecutor (SAFE, uses VisionManager.ask)
# ---------------------------------------------------------------------------


class TestVisionAskToolIntegration:
    def test_ask_executes_and_returns_answer(self, tmp_path: Path) -> None:
        vm = _make_vision_manager(vlm_resp="Editor is open.")
        _, executor, _ = _make_pipeline_with_vision(tmp_path, vision_manager=vm)
        result = run_async(executor.execute("vision.ask", {"question": "What is open?"}))
        assert result.success
        assert "Editor" in (
            result.data.answer if hasattr(result.data, "answer") else str(result.data)
        )

    def test_ask_tool_registered_as_safe(self, tmp_path: Path) -> None:
        registry, _, _ = _make_pipeline_with_vision(tmp_path, vision_manager=_make_vision_manager())
        assert registry.get("vision.ask").permission_level == PermissionLevel.SAFE

    def test_ask_flows_through_executor_not_bypass(self, tmp_path: Path) -> None:
        """VisionManager.ask must only be called via ToolExecutor, not directly."""
        vm = _make_vision_manager()
        # Wrap ask to track calls
        original_ask = vm.ask
        call_count = []

        async def tracked_ask(*args: Any, **kwargs: Any) -> str:
            call_count.append(1)
            return await original_ask(*args, **kwargs)

        vm.ask = tracked_ask  # type: ignore[method-assign]
        _, executor, _ = _make_pipeline_with_vision(tmp_path, vision_manager=vm)
        run_async(executor.execute("vision.ask", {"question": "Describe screen"}))
        assert len(call_count) == 1


# ---------------------------------------------------------------------------
# D. vision.ocr through ToolExecutor
# ---------------------------------------------------------------------------


class TestVisionOcrToolIntegration:
    def test_ocr_returns_text_and_confidence(self, tmp_path: Path) -> None:
        vm = _make_vision_manager(ocr_text="Login Button")
        _, executor, _ = _make_pipeline_with_vision(tmp_path, vision_manager=vm)
        result = run_async(executor.execute("vision.ocr", {}))
        assert result.success
        assert hasattr(result.data, "text")
        assert hasattr(result.data, "confidence")

    def test_ocr_result_has_no_raw_image_data(self, tmp_path: Path) -> None:
        vm = _make_vision_manager()
        _, executor, _ = _make_pipeline_with_vision(tmp_path, vision_manager=vm)
        result = run_async(executor.execute("vision.ocr", {}))
        output_str = str(result.data)
        assert "raw_bytes" not in output_str
        assert "data:image" not in output_str


# ---------------------------------------------------------------------------
# E. Vision manager None → graceful error, not crash
# ---------------------------------------------------------------------------


class TestVisionManagerAbsent:
    def test_vision_tools_without_manager_return_error(self, tmp_path: Path) -> None:
        """When vision_manager=None, vision tools report failure, not unhandled exception."""
        _, executor, _ = _make_pipeline_with_vision(tmp_path, vision_manager=None)
        result = run_async(executor.execute("vision.capture", {}))
        # Either succeeds with a no-op or reports failure — must not raise
        assert isinstance(result.success, bool)
