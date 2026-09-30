"""Unit tests for VoiceInteractionManager (Phase 6 Iteration 4).

All tests are deterministic and offline.
No microphone, speakers, network, GPU, CUDA, or external services required.

Convention matches existing TOM tests:
- Synchronous def test_ methods
- Async coroutines run via run_async() helper
- No pytest.mark.asyncio (strict-markers mode)
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from tom.agents.dependencies import AgentDependencies
from tom.core.engine import EngineClient
from tom.ipc.protocol import AudioOperationResponse, AudioSpeechResponse, AudioStatusResponse
from tom.schemas.agent import ConversationHistory, Message, Role
from tom.schemas.voice import VoicePipelineState, VoiceTurnResult
from tom.voice.formatter import SpeechFormatter
from tom.voice.interaction import VoiceInteractionManager
from tom.voice.pipeline import VoicePipelineError, VoicePipelineManager
from tom.voice.stt import MockSTTProvider, STTUnavailableError
from tom.voice.tts import MockTTSProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


DUMMY_AUDIO = [0.05, -0.05, 0.1, -0.1] * 400  # 1600 samples


def make_mock_engine(
    *,
    capture_active: bool = False,
    playback_active: bool = False,
    audio_status_error: Exception | None = None,
) -> MagicMock:
    engine = MagicMock(spec=EngineClient)
    engine.start_audio_capture = AsyncMock(
        return_value=AudioOperationResponse(success=True, message="capture started")
    )
    engine.stop_audio_capture = AsyncMock(
        return_value=AudioOperationResponse(success=True, message="capture stopped")
    )
    engine.get_captured_speech = AsyncMock(
        return_value=AudioSpeechResponse(
            samples=DUMMY_AUDIO,
            sample_rate=16000,
            channels=1,
            sample_count=len(DUMMY_AUDIO),
            duration_ms=100,
        )
    )
    engine.play_audio_buffer = AsyncMock(
        return_value=AudioOperationResponse(success=True, message="audio playing")
    )
    engine.stop_audio_playback = AsyncMock(
        return_value=AudioOperationResponse(success=True, message="audio stopped")
    )
    if audio_status_error:
        engine.get_audio_status = AsyncMock(side_effect=audio_status_error)
    else:
        engine.get_audio_status = AsyncMock(
            return_value=AudioStatusResponse(
                capture_active=capture_active,
                playback_active=playback_active,
                capture_sample_rate=16000,
                capture_channels=1,
                captured_samples=0,
            )
        )
    return engine


def make_pipeline(
    engine: EngineClient | None = None,
    agent_handler: Any = None,
) -> VoicePipelineManager:
    stt = MockSTTProvider(transcript="hello from mock")
    tts = MockTTSProvider()
    eng = engine or make_mock_engine()
    return VoicePipelineManager(
        stt_provider=stt,
        tts_provider=tts,
        engine_client=eng,
        formatter=SpeechFormatter(),
        agent_handler=agent_handler,
    )


def make_manager(
    engine: EngineClient | None = None,
    agent: Any = None,
    history: ConversationHistory | None = None,
    min_announce_interval: float = 0.0,
) -> tuple[VoiceInteractionManager, VoicePipelineManager]:
    pipeline = make_pipeline(engine=engine)
    mgr = VoiceInteractionManager(
        pipeline_manager=pipeline,
        agent=agent,
        history=history,
        min_announce_interval_seconds=min_announce_interval,
    )
    return mgr, pipeline


# ---------------------------------------------------------------------------
# Construction & Properties
# ---------------------------------------------------------------------------


class TestVoiceInteractionManagerConstruction:
    def test_default_construction(self) -> None:
        mgr, pipeline = make_manager()
        assert mgr.pipeline is pipeline
        assert isinstance(mgr.history, ConversationHistory)
        assert len(mgr.history) == 0

    def test_custom_history_injected(self) -> None:
        history = ConversationHistory(max_messages=50)
        history.append(Message(role=Role.USER, content="prior message"))
        mgr, _ = make_manager(history=history)
        assert len(mgr.history) == 1

    def test_initial_state_is_idle(self) -> None:
        mgr, _ = make_manager()
        assert mgr.state == VoicePipelineState.IDLE

    def test_pipeline_property_returns_same_instance(self) -> None:
        mgr, pipeline = make_manager()
        assert mgr.pipeline is pipeline


# ---------------------------------------------------------------------------
# get_status()
# ---------------------------------------------------------------------------


class TestVoiceInteractionManagerGetStatus:
    def test_status_idle_pipeline(self) -> None:
        mgr, _ = make_manager()
        st = run_async(mgr.get_status())
        assert st["pipeline_state"] == VoicePipelineState.IDLE.value
        assert st["is_idle"] is True
        assert st["is_speaking"] is False
        assert st["is_listening"] is False
        assert st["history_message_count"] == 0

    def test_status_includes_audio_engine_info(self) -> None:
        engine = make_mock_engine(capture_active=False, playback_active=False)
        mgr, _ = make_manager(engine=engine)
        st = run_async(mgr.get_status())
        assert "audio_engine" in st
        assert "capture_active" in st["audio_engine"]

    def test_status_reports_history_count(self) -> None:
        mgr, _ = make_manager()
        mgr.history.append(Message(role=Role.USER, content="Hi"))
        mgr.history.append(Message(role=Role.ASSISTANT, content="Hello"))
        st = run_async(mgr.get_status())
        assert st["history_message_count"] == 2

    def test_status_handles_engine_error_gracefully(self) -> None:
        engine = make_mock_engine(audio_status_error=RuntimeError("IPC offline"))
        mgr, _ = make_manager(engine=engine)
        st = run_async(mgr.get_status())
        assert "error" in st["audio_engine"]


# ---------------------------------------------------------------------------
# announce()
# ---------------------------------------------------------------------------


class TestVoiceInteractionManagerAnnounce:
    def test_announce_idle_pipeline_succeeds(self) -> None:
        mgr, pipeline = make_manager(min_announce_interval=0.0)
        result = run_async(mgr.announce("Meeting in 5 minutes"))
        assert result is True
        # Pipeline should return to IDLE after announcement
        assert pipeline.state == VoicePipelineState.IDLE

    def test_announce_empty_message_returns_false(self) -> None:
        mgr, _ = make_manager(min_announce_interval=0.0)
        result = run_async(mgr.announce("   "))
        assert result is False

    def test_announce_respects_rate_limit(self) -> None:
        mgr, _ = make_manager(min_announce_interval=60.0)
        run_async(mgr.announce("First announcement"))  # succeeds
        with pytest.raises(VoicePipelineError, match="rate limit"):
            run_async(mgr.announce("Second announcement"))

    def test_announce_pipeline_returns_to_idle_on_tts_error(self) -> None:
        """Pipeline state must be restored to IDLE even when TTS fails."""
        mgr, pipeline = make_manager(min_announce_interval=0.0)
        # Patch TTS to raise
        pipeline._tts = MagicMock()
        pipeline._tts.synthesize = AsyncMock(side_effect=RuntimeError("TTS down"))
        with pytest.raises(RuntimeError):
            run_async(mgr.announce("This will fail"))
        assert pipeline.state == VoicePipelineState.IDLE


# ---------------------------------------------------------------------------
# run_turn() — history recording
# ---------------------------------------------------------------------------


class TestVoiceInteractionManagerRunTurn:
    def test_run_turn_records_user_and_assistant_messages(self) -> None:
        """A successful turn must add user transcript + assistant response to history."""

        async def fake_agent(transcript: str) -> str:
            return f"You said: {transcript}"

        mgr, pipeline = make_manager(agent=fake_agent, min_announce_interval=0.0)
        result = run_async(mgr.run_turn(audio_samples=DUMMY_AUDIO, sample_rate=16000))
        assert not result.error
        messages = mgr.history.get_messages()
        # At least user + assistant
        roles = [m.role for m in messages]
        assert Role.USER in roles
        assert Role.ASSISTANT in roles

    def test_run_turn_no_agent_still_completes(self) -> None:
        mgr, _ = make_manager(agent=None, min_announce_interval=0.0)
        # Pipeline has MockSTTProvider which returns a transcript
        result = run_async(mgr.run_turn(audio_samples=DUMMY_AUDIO, sample_rate=16000))
        # Should not raise; pipeline returns VoiceTurnResult
        assert isinstance(result, VoiceTurnResult)

    def test_run_turn_does_not_duplicate_history_messages(self) -> None:
        """Calling run_turn twice must not double-insert messages for the same turn."""
        call_count = 0

        async def counting_agent(transcript: str) -> str:
            nonlocal call_count
            call_count += 1
            return "response"

        mgr, _ = make_manager(agent=counting_agent, min_announce_interval=0.0)
        run_async(mgr.run_turn(audio_samples=DUMMY_AUDIO, sample_rate=16000))
        run_async(mgr.run_turn(audio_samples=DUMMY_AUDIO, sample_rate=16000))
        assert call_count == 2
        # Two full turns = at most 4 messages (user+assistant x2)
        assert len(mgr.history) <= 4

    def test_run_turn_error_does_not_pollute_history(self) -> None:
        """If the STT provider raises, we should not record empty/garbage history entries."""

        stt = MagicMock()
        stt.transcribe = AsyncMock(side_effect=STTUnavailableError("offline"))
        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=MockTTSProvider(),
            engine_client=make_mock_engine(),
        )
        mgr = VoiceInteractionManager(pipeline_manager=pipeline, min_announce_interval_seconds=0.0)
        result = run_async(mgr.run_turn(audio_samples=DUMMY_AUDIO, raise_on_error=False))
        assert result.error is not None
        # No user/assistant messages should have been written for a failed turn
        assert len(mgr.history) == 0


# ---------------------------------------------------------------------------
# History bounding
# ---------------------------------------------------------------------------


class TestVoiceInteractionManagerHistoryBounding:
    def test_history_respects_max_messages(self) -> None:
        """ConversationHistory must trim when max_messages is exceeded."""
        history = ConversationHistory(max_messages=4)
        mgr, _ = make_manager(history=history)
        for i in range(6):
            mgr.history.append(Message(role=Role.USER, content=f"msg {i}"))
        assert len(mgr.history) <= 4

    def test_default_history_max_is_bounded(self) -> None:
        mgr, _ = make_manager()
        assert mgr.history.max_messages is not None
        assert mgr.history.max_messages > 0


# ---------------------------------------------------------------------------
# AgentDependencies integration
# ---------------------------------------------------------------------------


class TestAgentDependenciesVoiceManager:
    def test_voice_manager_field_accepts_interaction_manager(self) -> None:
        mgr, _ = make_manager()
        deps = AgentDependencies(voice_manager=mgr)
        assert deps.get_voice_manager() is mgr

    def test_voice_manager_defaults_to_none(self) -> None:
        deps = AgentDependencies()
        assert deps.get_voice_manager() is None

    def test_voice_manager_accepts_none(self) -> None:
        deps = AgentDependencies(voice_manager=None)
        assert deps.get_voice_manager() is None
