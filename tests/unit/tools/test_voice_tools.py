"""Unit tests for TOM deterministic voice tools (voice.announce, voice.status).

Adheres to:
- Phase 6 Voice Specification (Iteration 4)
- Decision 032: Centralized Tool Invocation Safety
- Decision 045: Voice as Outer Interaction Layer vs. LLM Tools
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError
from tom.security.permissions import PermissionLevel
from tom.tools.registry import ToolRegistry
from tom.tools.voice import (
    VoiceAnnounceInput,
    VoiceAnnounceOutput,
    VoiceStatusInput,
    VoiceStatusOutput,
    create_voice_tools,
    register_voice_tools,
)


def run_async(coro: Any) -> Any:
    return asyncio.run(coro)


EXPECTED_VOICE_TOOLS = {"voice.announce", "voice.status"}


# ---------------------------------------------------------------------------
# Fake VoiceInteractionManager for tests
# ---------------------------------------------------------------------------


class FakeVoiceManager:
    """Minimal stub exposing announce() and get_status() like VoiceInteractionManager."""

    def __init__(
        self, *, is_idle: bool = True, is_speaking: bool = False, is_listening: bool = False
    ) -> None:
        self._is_idle = is_idle
        self._is_speaking = is_speaking
        self._is_listening = is_listening
        self.announced_messages: list[str] = []
        self.announce_result = True

    async def announce(self, message: str, priority: bool = False) -> bool:
        self.announced_messages.append(message)
        return self.announce_result

    async def get_status(self) -> dict[str, Any]:
        return {
            "pipeline_state": "IDLE" if self._is_idle else "SPEAKING",
            "is_idle": self._is_idle,
            "is_speaking": self._is_speaking,
            "is_listening": self._is_listening,
            "history_message_count": 3,
            "audio_engine": {"capture_active": False, "playback_active": False},
        }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestVoiceToolRegistration:
    def test_create_voice_tools_returns_two_definitions(self) -> None:
        tools = create_voice_tools()
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == EXPECTED_VOICE_TOOLS

    def test_register_voice_tools_populates_registry(self) -> None:
        reg = ToolRegistry()
        defs = register_voice_tools(registry=reg)
        assert len(defs) == 2
        assert {t.name for t in reg.list_tools(category="voice")} == EXPECTED_VOICE_TOOLS

    def test_voice_tools_are_safe_permission(self) -> None:
        tools = create_voice_tools()
        for t in tools:
            assert t.permission_level == PermissionLevel.SAFE, (
                f"{t.name} should be SAFE, got {t.permission_level}"
            )

    def test_voice_tools_have_correct_category(self) -> None:
        tools = create_voice_tools()
        for t in tools:
            assert t.category == "voice"

    def test_register_replace_is_idempotent(self) -> None:
        reg = ToolRegistry()
        register_voice_tools(registry=reg, replace=True)
        register_voice_tools(registry=reg, replace=True)  # must not raise
        assert len(reg.list_tools(category="voice")) == 2


# ---------------------------------------------------------------------------
# voice.announce — no voice manager
# ---------------------------------------------------------------------------


class TestVoiceAnnounceNoManager:
    def test_announce_returns_not_announced_when_no_manager(self) -> None:
        tools = create_voice_tools(voice_manager=None)
        announce_tool = next(t for t in tools if t.name == "voice.announce")

        result: VoiceAnnounceOutput = run_async(
            announce_tool.handler(VoiceAnnounceInput(message="Hello TOM"))
        )
        assert result.announced is False
        assert result.message == "Hello TOM"

    def test_announce_input_schema_rejects_empty_message(self) -> None:
        with pytest.raises(ValidationError):
            VoiceAnnounceInput(message="")


# ---------------------------------------------------------------------------
# voice.announce — with fake manager
# ---------------------------------------------------------------------------


class TestVoiceAnnounceWithManager:
    def _make_tool(self, manager: FakeVoiceManager) -> Any:
        tools = create_voice_tools(voice_manager=manager)
        return next(t for t in tools if t.name == "voice.announce")

    def test_announce_calls_manager_and_returns_true(self) -> None:
        mgr = FakeVoiceManager()
        tool = self._make_tool(mgr)
        result: VoiceAnnounceOutput = run_async(
            tool.handler(VoiceAnnounceInput(message="Alert! Low battery."))
        )
        assert result.announced is True
        assert result.message == "Alert! Low battery."
        assert mgr.announced_messages == ["Alert! Low battery."]

    def test_announce_priority_flag_forwarded(self) -> None:
        mgr = FakeVoiceManager()
        tool = self._make_tool(mgr)
        run_async(tool.handler(VoiceAnnounceInput(message="Urgent!", priority=True)))
        assert mgr.announced_messages == ["Urgent!"]

    def test_announce_returns_false_when_manager_returns_false(self) -> None:
        mgr = FakeVoiceManager()
        mgr.announce_result = False
        tool = self._make_tool(mgr)
        result: VoiceAnnounceOutput = run_async(
            tool.handler(VoiceAnnounceInput(message="Quiet message"))
        )
        assert result.announced is False


# ---------------------------------------------------------------------------
# voice.status — no voice manager
# ---------------------------------------------------------------------------


class TestVoiceStatusNoManager:
    def test_status_returns_unavailable_when_no_manager(self) -> None:
        tools = create_voice_tools(voice_manager=None)
        status_tool = next(t for t in tools if t.name == "voice.status")

        result: VoiceStatusOutput = run_async(status_tool.handler(VoiceStatusInput()))
        assert result.pipeline_state == "UNAVAILABLE"
        assert result.is_idle is False
        assert result.audio_engine.get("available") is False


# ---------------------------------------------------------------------------
# voice.status — with fake manager
# ---------------------------------------------------------------------------


class TestVoiceStatusWithManager:
    def _make_tool(self, manager: FakeVoiceManager) -> Any:
        tools = create_voice_tools(voice_manager=manager)
        return next(t for t in tools if t.name == "voice.status")

    def test_status_returns_idle_state(self) -> None:
        mgr = FakeVoiceManager(is_idle=True)
        tool = self._make_tool(mgr)
        result: VoiceStatusOutput = run_async(tool.handler(VoiceStatusInput()))
        assert result.pipeline_state == "IDLE"
        assert result.is_idle is True
        assert result.is_speaking is False
        assert result.is_listening is False

    def test_status_returns_history_count(self) -> None:
        mgr = FakeVoiceManager()
        tool = self._make_tool(mgr)
        result: VoiceStatusOutput = run_async(tool.handler(VoiceStatusInput()))
        assert result.history_message_count == 3

    def test_status_returns_audio_engine_info(self) -> None:
        mgr = FakeVoiceManager()
        tool = self._make_tool(mgr)
        result: VoiceStatusOutput = run_async(tool.handler(VoiceStatusInput()))
        assert isinstance(result.audio_engine, dict)

    def test_status_speaking_state(self) -> None:
        mgr = FakeVoiceManager(is_idle=False, is_speaking=True)
        tool = self._make_tool(mgr)
        result: VoiceStatusOutput = run_async(tool.handler(VoiceStatusInput()))
        assert result.pipeline_state == "SPEAKING"
        assert result.is_idle is False
        assert result.is_speaking is True


# ---------------------------------------------------------------------------
# Input / output schema validation
# ---------------------------------------------------------------------------


class TestVoiceToolSchemas:
    def test_announce_output_model(self) -> None:
        out = VoiceAnnounceOutput(announced=True, message="Test")
        assert out.announced is True

    def test_status_output_defaults(self) -> None:
        out = VoiceStatusOutput(
            pipeline_state="IDLE",
            is_idle=True,
            is_speaking=False,
            is_listening=False,
        )
        assert out.history_message_count == 0
        assert out.audio_engine == {}

    def test_announce_input_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            VoiceAnnounceInput(message="x", unknown_field="bad")  # type: ignore[call-arg]

    def test_status_input_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            VoiceStatusInput(unknown_field="bad")  # type: ignore[call-arg]
