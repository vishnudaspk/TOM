"""Unit tests for Phase 6 Iteration 3: VoicePipelineManager State Machine & Barge-In.

All tests are deterministic and offline.
No microphone, speakers, network, GPU, CUDA, or external services required.

Convention matches existing TOM tests:
- Synchronous def test_ methods
- Async coroutines run with run_async(asyncio.run(...)) helper
- No pytest.mark.asyncio (strict-markers mode)
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from tom.core.engine import EngineClient
from tom.ipc.protocol import AudioOperationResponse, AudioSpeechResponse
from tom.schemas.voice import (
    SynthesisRequest,
    SynthesisResult,
    VoicePipelineState,
    VoiceTurnResult,
)
from tom.voice.formatter import SpeechFormatter
from tom.voice.pipeline import (
    InvalidStateTransitionError,
    VoicePipelineBusyError,
    VoicePipelineManager,
)
from tom.voice.stt import MockSTTProvider, STTTranscriptionError, STTUnavailableError
from tom.voice.tts import MockTTSProvider, TTSSynthesisError

# ---------------------------------------------------------------------------
# Helpers & Mocks
# ---------------------------------------------------------------------------


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


DUMMY_AUDIO = [0.05, -0.05, 0.1, -0.1] * 400  # 1600 samples = 0.1s at 16kHz


def create_mock_engine() -> MagicMock:
    """Create a mock EngineClient for voice pipeline testing."""
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
    return engine


# ===========================================================================
# Initial State & State Machine Transition Tests
# ===========================================================================


class TestVoicePipelineStateMachine:
    def test_initial_state_is_idle(self) -> None:
        stt = MockSTTProvider()
        tts = MockTTSProvider()
        engine = create_mock_engine()
        pipeline = VoicePipelineManager(stt_provider=stt, tts_provider=tts, engine_client=engine)

        assert pipeline.state == VoicePipelineState.IDLE
        assert pipeline.is_idle is True
        assert pipeline.is_listening is False
        assert pipeline.is_processing is False
        assert pipeline.is_speaking is False
        assert pipeline.is_interrupted is False
        assert pipeline.is_error is False

    def test_valid_state_transitions(self) -> None:
        stt = MockSTTProvider()
        tts = MockTTSProvider()
        engine = create_mock_engine()
        pipeline = VoicePipelineManager(stt_provider=stt, tts_provider=tts, engine_client=engine)

        # IDLE -> LISTENING -> PROCESSING -> SPEAKING -> IDLE
        pipeline.transition_to(VoicePipelineState.LISTENING)
        assert pipeline.state == VoicePipelineState.LISTENING
        assert pipeline.is_listening is True

        pipeline.transition_to(VoicePipelineState.PROCESSING)
        assert pipeline.state == VoicePipelineState.PROCESSING
        assert pipeline.is_processing is True

        pipeline.transition_to(VoicePipelineState.SPEAKING)
        assert pipeline.state == VoicePipelineState.SPEAKING
        assert pipeline.is_speaking is True

        pipeline.transition_to(VoicePipelineState.IDLE)
        assert pipeline.state == VoicePipelineState.IDLE
        assert pipeline.is_idle is True

    def test_invalid_state_transition_raises(self) -> None:
        stt = MockSTTProvider()
        tts = MockTTSProvider()
        engine = create_mock_engine()
        pipeline = VoicePipelineManager(stt_provider=stt, tts_provider=tts, engine_client=engine)

        # IDLE cannot transition directly to SPEAKING
        with pytest.raises(
            InvalidStateTransitionError, match="Invalid voice pipeline state transition"
        ):
            pipeline.transition_to(VoicePipelineState.SPEAKING)

        # IDLE cannot transition directly to INTERRUPTED
        with pytest.raises(InvalidStateTransitionError):
            pipeline.transition_to(VoicePipelineState.INTERRUPTED)

    def test_idempotent_self_transition(self) -> None:
        stt = MockSTTProvider()
        tts = MockTTSProvider()
        engine = create_mock_engine()
        pipeline = VoicePipelineManager(stt_provider=stt, tts_provider=tts, engine_client=engine)

        # Transitioning to same state does nothing and does not raise
        pipeline.transition_to(VoicePipelineState.IDLE)
        assert pipeline.state == VoicePipelineState.IDLE

    def test_state_change_callbacks(self) -> None:
        stt = MockSTTProvider()
        tts = MockTTSProvider()
        engine = create_mock_engine()
        pipeline = VoicePipelineManager(stt_provider=stt, tts_provider=tts, engine_client=engine)

        recorded: list[tuple[VoicePipelineState, VoicePipelineState]] = []

        def on_change(
            old: VoicePipelineState, new: VoicePipelineState, meta: dict[str, Any]
        ) -> None:
            recorded.append((old, new))

        pipeline.add_state_callback(on_change)
        pipeline.transition_to(VoicePipelineState.LISTENING)
        pipeline.transition_to(VoicePipelineState.PROCESSING)
        pipeline.remove_state_callback(on_change)
        pipeline.transition_to(VoicePipelineState.IDLE)

        assert recorded == [
            (VoicePipelineState.IDLE, VoicePipelineState.LISTENING),
            (VoicePipelineState.LISTENING, VoicePipelineState.PROCESSING),
        ]


# ===========================================================================
# End-to-End Pipeline Execution Tests
# ===========================================================================


class TestVoicePipelineExecution:
    def test_successful_full_turn(self) -> None:
        stt = MockSTTProvider(transcript="What is the weather today?")
        tts = MockTTSProvider()
        engine = create_mock_engine()
        formatter = SpeechFormatter()

        async def mock_agent(prompt: str) -> str:
            assert prompt == "What is the weather today?"
            return "The weather is sunny with 75 degrees."

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            formatter=formatter,
            agent_handler=mock_agent,
        )

        states_visited: list[VoicePipelineState] = []
        pipeline.add_state_callback(lambda old, new, meta: states_visited.append(new))

        result = run_async(pipeline.run_turn(audio_samples=DUMMY_AUDIO))

        assert isinstance(result, VoiceTurnResult)
        assert result.transcript == "What is the weather today?"
        assert result.response_text == "The weather is sunny with 75 degrees."
        assert result.formatted_text == "The weather is sunny with 75 degrees."
        assert result.interrupted is False
        assert result.state == VoicePipelineState.IDLE
        assert pipeline.state == VoicePipelineState.IDLE

        # Check state transitions visited
        assert states_visited == [
            VoicePipelineState.LISTENING,
            VoicePipelineState.PROCESSING,
            VoicePipelineState.SPEAKING,
            VoicePipelineState.IDLE,
        ]

        # Verify playback was called with synthesized samples
        engine.play_audio_buffer.assert_called_once()

    def test_turn_with_engine_audio_capture(self) -> None:
        """When audio_samples is None, pipeline captures speech through EngineClient."""
        stt = MockSTTProvider(transcript="Hello from microphone")
        tts = MockTTSProvider()
        engine = create_mock_engine()

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=lambda p: "Hello back!",
        )

        result = run_async(pipeline.run_turn(audio_samples=None))

        assert result.transcript == "Hello from microphone"
        assert result.response_text == "Hello back!"
        engine.start_audio_capture.assert_called_once()
        engine.get_captured_speech.assert_called_once_with(clear=True)
        engine.stop_audio_capture.assert_called_once()
        engine.play_audio_buffer.assert_called_once()

    def test_agent_with_step_interface(self) -> None:
        """Pipeline can invoke an Agent object providing a .step() method."""
        stt = MockSTTProvider(transcript="Test agent step")
        tts = MockTTSProvider()
        engine = create_mock_engine()

        mock_agent_obj = MagicMock()
        mock_step_result = MagicMock()
        mock_step_result.text = "Response from agent step"
        mock_agent_obj.step = AsyncMock(return_value=mock_step_result)

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=mock_agent_obj,
        )

        result = run_async(pipeline.run_turn(audio_samples=DUMMY_AUDIO))

        assert result.response_text == "Response from agent step"
        mock_agent_obj.step.assert_called_once_with("Test agent step")

    def test_default_agent_handler_echoes(self) -> None:
        """When no agent_handler is provided, pipeline echoes by default."""
        stt = MockSTTProvider(transcript="Echo test")
        tts = MockTTSProvider()
        engine = create_mock_engine()

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=None,
        )

        result = run_async(pipeline.run_turn(audio_samples=DUMMY_AUDIO))
        assert result.response_text == "Echo: Echo test"


# ===========================================================================
# Empty & No-Speech Handling Tests
# ===========================================================================


class TestVoicePipelineEmptyInputs:
    def test_empty_audio_samples_returns_idle(self) -> None:
        stt = MockSTTProvider()
        tts = MockTTSProvider()
        engine = create_mock_engine()
        agent_mock = AsyncMock()

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=agent_mock,
        )

        result = run_async(pipeline.run_turn(audio_samples=[]))

        assert result.transcript == ""
        assert result.state == VoicePipelineState.IDLE
        assert pipeline.state == VoicePipelineState.IDLE
        # Agent, TTS, Playback must NOT be invoked
        agent_mock.assert_not_called()
        engine.play_audio_buffer.assert_not_called()

    def test_empty_transcript_from_stt_returns_idle(self) -> None:
        stt = MockSTTProvider(transcript="   ")  # Whitespace only
        tts = MockTTSProvider()
        engine = create_mock_engine()
        agent_mock = AsyncMock()

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=agent_mock,
        )

        result = run_async(pipeline.run_turn(audio_samples=DUMMY_AUDIO))

        assert result.transcript == ""
        assert result.state == VoicePipelineState.IDLE
        assert pipeline.state == VoicePipelineState.IDLE
        agent_mock.assert_not_called()
        engine.play_audio_buffer.assert_not_called()

    def test_empty_agent_response_returns_idle(self) -> None:
        stt = MockSTTProvider(transcript="Hello")
        tts = MockTTSProvider()
        engine = create_mock_engine()

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=lambda p: "",
        )

        result = run_async(pipeline.run_turn(audio_samples=DUMMY_AUDIO))

        assert result.transcript == "Hello"
        assert result.response_text == ""
        assert result.state == VoicePipelineState.IDLE
        engine.play_audio_buffer.assert_not_called()


# ===========================================================================
# Barge-In & Interruption Tests
# ===========================================================================


class TestVoicePipelineBargeIn:
    def test_barge_in_stops_playback_and_transitions_interrupted(self) -> None:
        stt = MockSTTProvider(transcript="Interruption test")
        tts = MockTTSProvider()
        engine = create_mock_engine()

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=lambda p: "Will be interrupted",
        )

        pipeline.transition_to(VoicePipelineState.LISTENING)
        pipeline.transition_to(VoicePipelineState.PROCESSING)
        pipeline.transition_to(VoicePipelineState.SPEAKING)

        run_async(pipeline.handle_barge_in())

        assert pipeline.state == VoicePipelineState.INTERRUPTED
        assert pipeline.is_interrupted is True
        engine.stop_audio_playback.assert_called_once()

    def test_barge_in_during_synthesis_prevents_playback(self) -> None:
        """When barge-in happens during TTS synthesis, playback is never called."""
        stt = MockSTTProvider(transcript="Slow synthesis")
        engine = create_mock_engine()

        # TTS mock that triggers barge-in mid-synthesis
        class InterruptedTTS(MockTTSProvider):
            def __init__(self, manager_ref: list[VoicePipelineManager]) -> None:
                super().__init__()
                self.manager_ref = manager_ref

            async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
                # Trigger barge-in while pipeline is in SPEAKING state
                await self.manager_ref[0].handle_barge_in()
                return await super().synthesize(request)

        manager_holder: list[VoicePipelineManager] = []
        tts = InterruptedTTS(manager_holder)

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=lambda p: "Response text",
        )
        manager_holder.append(pipeline)

        result = run_async(pipeline.run_turn(audio_samples=DUMMY_AUDIO))

        assert result.interrupted is True
        assert pipeline.state == VoicePipelineState.INTERRUPTED
        # Crucial check: engine playback was NEVER invoked with the synthesized audio
        engine.play_audio_buffer.assert_not_called()

    def test_repeated_rapid_barge_in_is_safe_and_idempotent(self) -> None:
        stt = MockSTTProvider()
        tts = MockTTSProvider()
        engine = create_mock_engine()

        pipeline = VoicePipelineManager(stt_provider=stt, tts_provider=tts, engine_client=engine)
        pipeline.transition_to(VoicePipelineState.LISTENING)
        pipeline.transition_to(VoicePipelineState.PROCESSING)
        pipeline.transition_to(VoicePipelineState.SPEAKING)

        # Call handle_barge_in multiple times rapidly
        run_async(pipeline.handle_barge_in())
        run_async(pipeline.handle_barge_in())
        run_async(pipeline.handle_barge_in())

        assert pipeline.state == VoicePipelineState.INTERRUPTED

    def test_barge_in_when_idle_is_safe_noop(self) -> None:
        stt = MockSTTProvider()
        tts = MockTTSProvider()
        engine = create_mock_engine()

        pipeline = VoicePipelineManager(stt_provider=stt, tts_provider=tts, engine_client=engine)
        assert pipeline.state == VoicePipelineState.IDLE

        # Safe no-op when already IDLE
        run_async(pipeline.handle_barge_in())
        assert pipeline.state == VoicePipelineState.IDLE

    def test_reset_returns_to_idle_from_interrupted(self) -> None:
        stt = MockSTTProvider()
        tts = MockTTSProvider()
        engine = create_mock_engine()

        pipeline = VoicePipelineManager(stt_provider=stt, tts_provider=tts, engine_client=engine)
        pipeline.transition_to(VoicePipelineState.LISTENING)
        run_async(pipeline.handle_barge_in())
        assert pipeline.state == VoicePipelineState.INTERRUPTED

        run_async(pipeline.reset())
        assert pipeline.state == VoicePipelineState.IDLE

    def test_starting_turn_from_interrupted_recovers_to_idle(self) -> None:
        """Starting a new turn after interruption seamlessly starts cleanly."""
        stt = MockSTTProvider(transcript="Recovered turn")
        tts = MockTTSProvider()
        engine = create_mock_engine()

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=lambda p: "Back online",
        )

        pipeline.transition_to(VoicePipelineState.LISTENING)
        run_async(pipeline.handle_barge_in())
        assert pipeline.state == VoicePipelineState.INTERRUPTED

        result = run_async(pipeline.run_turn(audio_samples=DUMMY_AUDIO))
        assert result.transcript == "Recovered turn"
        assert result.response_text == "Back online"
        assert result.state == VoicePipelineState.IDLE


# ===========================================================================
# Concurrency & Repeated Start Protection Tests
# ===========================================================================


class TestVoicePipelineConcurrency:
    def test_repeated_start_raises_busy_error(self) -> None:
        stt = MockSTTProvider()
        tts = MockTTSProvider()
        engine = create_mock_engine()

        pipeline = VoicePipelineManager(stt_provider=stt, tts_provider=tts, engine_client=engine)
        pipeline.transition_to(VoicePipelineState.LISTENING)

        with pytest.raises(
            VoicePipelineBusyError,
            match="Cannot start turn while voice pipeline is in state LISTENING",
        ):
            run_async(pipeline.run_turn(audio_samples=DUMMY_AUDIO))

    def test_task_cancellation_reraised_and_state_cleaned(self) -> None:
        """Async task cancellation is re-raised and leaves state clean."""
        stt = MockSTTProvider()
        tts = MockTTSProvider()
        engine = create_mock_engine()

        async def cancelling_agent(prompt: str) -> str:
            raise asyncio.CancelledError()

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=cancelling_agent,
        )

        with pytest.raises(asyncio.CancelledError):
            run_async(pipeline.run_turn(audio_samples=DUMMY_AUDIO))

        # Audio playback stopped on cancellation
        engine.stop_audio_playback.assert_called()
        assert pipeline.state == VoicePipelineState.IDLE


# ===========================================================================
# Error Handling & Resilience Tests
# ===========================================================================


class TestVoicePipelineErrorHandling:
    def test_stt_failure_transitions_to_error_and_raises(self) -> None:
        stt = MockSTTProvider(raise_error=STTTranscriptionError("Audio corrupt"))
        tts = MockTTSProvider()
        engine = create_mock_engine()

        pipeline = VoicePipelineManager(stt_provider=stt, tts_provider=tts, engine_client=engine)

        with pytest.raises(STTTranscriptionError, match="Audio corrupt"):
            run_async(pipeline.run_turn(audio_samples=DUMMY_AUDIO, raise_on_error=True))

        assert pipeline.state == VoicePipelineState.ERROR
        engine.stop_audio_playback.assert_called()

    def test_stt_failure_with_raise_on_error_false(self) -> None:
        stt = MockSTTProvider(raise_error=STTUnavailableError("FasterWhisper missing"))
        tts = MockTTSProvider()
        engine = create_mock_engine()

        pipeline = VoicePipelineManager(stt_provider=stt, tts_provider=tts, engine_client=engine)

        result = run_async(pipeline.run_turn(audio_samples=DUMMY_AUDIO, raise_on_error=False))
        assert "FasterWhisper missing" in (result.error or "")
        assert result.state == VoicePipelineState.ERROR

    def test_agent_failure_transitions_to_error(self) -> None:
        stt = MockSTTProvider(transcript="Test")
        tts = MockTTSProvider()
        engine = create_mock_engine()

        async def failing_agent(prompt: str) -> str:
            raise RuntimeError("Agent LLM context window exceeded")

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=failing_agent,
        )

        with pytest.raises(RuntimeError, match="Agent LLM context window exceeded"):
            run_async(pipeline.run_turn(audio_samples=DUMMY_AUDIO, raise_on_error=True))

        assert pipeline.state == VoicePipelineState.ERROR

    def test_tts_failure_transitions_to_error(self) -> None:
        stt = MockSTTProvider(transcript="Synthesize me")
        tts = MockTTSProvider(raise_error=TTSSynthesisError("ONNX CUDA error"))
        engine = create_mock_engine()

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=lambda p: "Okay",
        )

        with pytest.raises(TTSSynthesisError, match="ONNX CUDA error"):
            run_async(pipeline.run_turn(audio_samples=DUMMY_AUDIO, raise_on_error=True))

        assert pipeline.state == VoicePipelineState.ERROR
        engine.play_audio_buffer.assert_not_called()

    def test_playback_failure_transitions_to_error(self) -> None:
        stt = MockSTTProvider(transcript="Speak")
        tts = MockTTSProvider()
        engine = create_mock_engine()
        engine.play_audio_buffer.side_effect = RuntimeError("WASAPI device lost")

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=lambda p: "Speaking now",
        )

        with pytest.raises(RuntimeError, match="WASAPI device lost"):
            run_async(pipeline.run_turn(audio_samples=DUMMY_AUDIO, raise_on_error=True))

        assert pipeline.state == VoicePipelineState.ERROR

    def test_reset_from_error_recovers_to_idle(self) -> None:
        stt = MockSTTProvider(raise_error=STTTranscriptionError("error"))
        tts = MockTTSProvider()
        engine = create_mock_engine()

        pipeline = VoicePipelineManager(stt_provider=stt, tts_provider=tts, engine_client=engine)
        run_async(pipeline.run_turn(audio_samples=DUMMY_AUDIO, raise_on_error=False))
        assert pipeline.state == VoicePipelineState.ERROR

        run_async(pipeline.reset())
        assert pipeline.state == VoicePipelineState.IDLE
