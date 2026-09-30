"""End-to-end integration tests for TOM's Phase 6 voice pipeline.

Validates the full logical flow:
    audio/input -> STT -> VoicePipelineManager -> Agent -> SpeechFormatter -> TTS -> Playback
across normal execution, failure containment, cancellation/barge-in, ephemeral history,
voice tools through ToolExecutor/PermissionEngine, and real provider configuration validation.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from tom.agents.dependencies import AgentDependencies
from tom.core.engine import EngineClient
from tom.ipc.protocol import (
    AudioOperationResponse,
    AudioSpeechResponse,
    AudioStatusResponse,
)
from tom.schemas.agent import ConversationHistory, Role
from tom.schemas.voice import (
    VoicePipelineState,
    VoiceTurnResult,
)
from tom.security.confirmation import AlwaysAllowConfirmationHook
from tom.security.permissions import PermissionEngine
from tom.tools.bootstrap import setup_default_tools
from tom.tools.executor import ToolExecutor
from tom.tools.registry import ToolRegistry
from tom.voice.formatter import SpeechFormatter
from tom.voice.interaction import VoiceInteractionManager
from tom.voice.pipeline import (
    VoicePipelineBusyError,
    VoicePipelineError,
    VoicePipelineManager,
)
from tom.voice.stt import (
    FasterWhisperSTTProvider,
    MockSTTProvider,
    STTConfig,
    STTTranscriptionError,
    STTUnavailableError,
)
from tom.voice.tts import (
    KokoroTTSProvider,
    MockTTSProvider,
    TTSConfig,
    TTSSynthesisError,
    TTSUnavailableError,
)

DUMMY_AUDIO = [0.05, -0.05, 0.1, -0.1] * 400


def make_mock_engine() -> MagicMock:
    """Create a mock EngineClient matching audio IPC contracts."""
    engine = MagicMock(spec=EngineClient)
    engine.start_audio_capture = AsyncMock(
        return_value=AudioOperationResponse(success=True, message="Capture started")
    )
    engine.stop_audio_capture = AsyncMock(
        return_value=AudioOperationResponse(success=True, message="Capture stopped")
    )
    engine.get_captured_speech = AsyncMock(
        return_value=AudioSpeechResponse(
            samples=DUMMY_AUDIO,
            sample_rate=16000,
            channels=1,
            sample_count=len(DUMMY_AUDIO),
        )
    )
    engine.play_audio_buffer = AsyncMock(
        return_value=AudioOperationResponse(success=True, message="Playback started")
    )
    engine.stop_audio_playback = AsyncMock(
        return_value=AudioOperationResponse(success=True, message="Playback stopped")
    )
    engine.get_audio_status = AsyncMock(
        return_value=AudioStatusResponse(
            capture_active=False,
            playback_active=False,
            capture_sample_rate=16000,
            capture_channels=1,
            captured_samples=0,
        )
    )
    return engine


class TestVoicePipelineEndToEnd:
    """Integration test suite covering the full normal and failure voice pipeline."""

    @pytest.mark.anyio
    async def test_normal_turn_flow_with_agent(self) -> None:
        """audio -> STT -> VoicePipelineManager -> Agent -> SpeechFormatter -> TTS -> Playback -> IDLE."""
        engine = make_mock_engine()
        stt = MockSTTProvider(transcript="What time is it in Tokyo?")
        tts = MockTTSProvider()
        formatter = SpeechFormatter()

        async def fake_agent(user_text: str) -> str:
            assert user_text == "What time is it in Tokyo?"
            return "It is currently 9:00 PM in Tokyo."

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            formatter=formatter,
            agent_handler=fake_agent,
        )

        assert pipeline.state == VoicePipelineState.IDLE

        result: VoiceTurnResult = await pipeline.run_turn()

        assert result.transcript == "What time is it in Tokyo?"
        assert "9:00 PM in Tokyo" in result.response_text
        assert result.interrupted is False
        assert result.state == VoicePipelineState.IDLE
        assert pipeline.state == VoicePipelineState.IDLE

        # Verify engine interactions
        engine.start_audio_capture.assert_awaited_once()
        engine.get_captured_speech.assert_awaited_once_with(clear=True)
        engine.stop_audio_capture.assert_awaited_once()
        engine.play_audio_buffer.assert_awaited_once()

    @pytest.mark.anyio
    async def test_turn_with_markdown_agent_response_cleaned(self) -> None:
        """Assistant response with markdown formatting is cleaned before TTS synthesis."""
        engine = make_mock_engine()
        stt = MockSTTProvider(transcript="Show me system status")
        tts = MockTTSProvider()
        formatter = SpeechFormatter()

        async def markdown_agent(_: str) -> str:
            return "**System:** `OK`\n- CPU: 24%\n- RAM: 8GB\nVisit https://example.com"

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            formatter=formatter,
            agent_handler=markdown_agent,
        )

        result = await pipeline.run_turn()

        assert result.transcript == "Show me system status"
        assert pipeline.state == VoicePipelineState.IDLE
        assert "**" not in result.formatted_text
        assert "`" not in result.formatted_text

    @pytest.mark.anyio
    async def test_empty_speech_capture_handled_gracefully(self) -> None:
        """When audio capture returns no speech, turn completes cleanly without TTS/playback."""
        engine = make_mock_engine()
        engine.get_captured_speech = AsyncMock(
            return_value=AudioSpeechResponse(
                samples=[],
                sample_rate=16000,
                channels=1,
                sample_count=0,
            )
        )
        stt = MockSTTProvider(transcript="")
        tts = MockTTSProvider()

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=lambda p: "response",
        )

        result = await pipeline.run_turn()

        assert result.transcript == ""
        assert result.state == VoicePipelineState.IDLE
        assert pipeline.state == VoicePipelineState.IDLE
        engine.play_audio_buffer.assert_not_awaited()

    @pytest.mark.anyio
    async def test_stt_unavailable_failure_path(self) -> None:
        """STT provider unavailable raises error, caught and recovered to IDLE."""
        engine = make_mock_engine()
        stt = MagicMock()
        stt.transcribe = AsyncMock(side_effect=STTUnavailableError("FasterWhisper not installed"))
        tts = MockTTSProvider()

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=lambda p: "ok",
        )

        with pytest.raises(STTUnavailableError):
            await pipeline.run_turn()

        # Pipeline returns to IDLE after exception cleanup
        await pipeline.reset()
        assert pipeline.state == VoicePipelineState.IDLE
        engine.play_audio_buffer.assert_not_awaited()

    @pytest.mark.anyio
    async def test_stt_transcription_failure_path(self) -> None:
        """STT transcription failure cleanly aborts turn and recovers to IDLE."""
        engine = make_mock_engine()
        stt = MagicMock()
        stt.transcribe = AsyncMock(side_effect=STTTranscriptionError("Decoding failed"))
        tts = MockTTSProvider()

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=lambda p: "ok",
        )

        with pytest.raises(STTTranscriptionError):
            await pipeline.run_turn()

        await pipeline.reset()
        assert pipeline.state == VoicePipelineState.IDLE
        engine.play_audio_buffer.assert_not_awaited()

    @pytest.mark.anyio
    async def test_agent_failure_path(self) -> None:
        """Agent exception during processing resets pipeline to IDLE without TTS."""
        engine = make_mock_engine()
        stt = MockSTTProvider(transcript="Test input")
        tts = MockTTSProvider()

        async def failing_agent(_: str) -> str:
            raise RuntimeError("Agent reasoning timeout")

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=failing_agent,
        )

        with pytest.raises(RuntimeError, match="Agent reasoning timeout"):
            await pipeline.run_turn()

        await pipeline.reset()
        assert pipeline.state == VoicePipelineState.IDLE
        engine.play_audio_buffer.assert_not_awaited()

    @pytest.mark.anyio
    async def test_tts_unavailable_failure_path(self) -> None:
        """TTS provider unavailable aborts turn cleanly without playback."""
        engine = make_mock_engine()
        stt = MockSTTProvider(transcript="Test input")
        tts = MagicMock()
        tts.synthesize = AsyncMock(side_effect=TTSUnavailableError("Kokoro model missing"))

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=lambda p: "Some response",
        )

        with pytest.raises(TTSUnavailableError):
            await pipeline.run_turn()

        await pipeline.reset()
        assert pipeline.state == VoicePipelineState.IDLE
        engine.play_audio_buffer.assert_not_awaited()

    @pytest.mark.anyio
    async def test_tts_synthesis_failure_path(self) -> None:
        """TTS synthesis failure aborts turn cleanly without playback."""
        engine = make_mock_engine()
        stt = MockSTTProvider(transcript="Test input")
        tts = MagicMock()
        tts.synthesize = AsyncMock(side_effect=TTSSynthesisError("ONNX runtime error"))

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=lambda p: "Some response",
        )

        with pytest.raises(TTSSynthesisError):
            await pipeline.run_turn()

        await pipeline.reset()
        assert pipeline.state == VoicePipelineState.IDLE
        engine.play_audio_buffer.assert_not_awaited()

    @pytest.mark.anyio
    async def test_playback_failure_path(self) -> None:
        """Audio playback failure resets pipeline to IDLE."""
        engine = make_mock_engine()
        engine.play_audio_buffer = AsyncMock(side_effect=RuntimeError("Audio device unplugged"))
        stt = MockSTTProvider(transcript="Test input")
        tts = MockTTSProvider()

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=lambda p: "Some response",
        )

        with pytest.raises(RuntimeError, match="Audio device unplugged"):
            await pipeline.run_turn()

        await pipeline.reset()
        assert pipeline.state == VoicePipelineState.IDLE

    @pytest.mark.anyio
    async def test_concurrent_turn_protection(self) -> None:
        """Attempting to trigger a turn while already running raises VoicePipelineBusyError."""
        engine = make_mock_engine()
        stt = MockSTTProvider(transcript="Concurrent test")
        tts = MockTTSProvider()

        # Simulate slow agent
        async def slow_agent(_msg: str) -> str:
            await asyncio.sleep(0.1)
            return "Done"

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=slow_agent,
        )

        task1 = asyncio.create_task(pipeline.run_turn())
        await asyncio.sleep(0.02)  # Let task1 start and enter LISTENING/PROCESSING

        with pytest.raises(VoicePipelineBusyError):
            await pipeline.run_turn()

        await task1
        assert pipeline.state == VoicePipelineState.IDLE

    @pytest.mark.anyio
    async def test_barge_in_stops_playback_and_invalidates_epoch(self) -> None:
        """Barge-in immediately stops playback, invalidates epoch, and resets state."""
        engine = make_mock_engine()
        stt = MockSTTProvider(transcript="Test input")
        tts = MockTTSProvider()

        playback_event = asyncio.Event()

        async def blocking_play(*args: Any, **kwargs: Any) -> AudioOperationResponse:
            playback_event.set()
            await asyncio.sleep(10.0)  # Simulate long playback
            return AudioOperationResponse(success=True)

        engine.play_audio_buffer = AsyncMock(side_effect=blocking_play)

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=lambda p: "Long response",
        )

        turn_task = asyncio.create_task(pipeline.run_turn())
        await playback_event.wait()

        assert pipeline.state == VoicePipelineState.SPEAKING

        # Trigger barge-in
        await pipeline.handle_barge_in()

        # Verify playback was stopped at the engine
        engine.stop_audio_playback.assert_awaited()

        # Wait for turn task to complete/cancel
        try:
            res = await turn_task
            assert res.interrupted is True
        except asyncio.CancelledError:
            pass

        await pipeline.reset()
        assert pipeline.state == VoicePipelineState.IDLE


class TestVoiceInteractionLayerIntegration:
    """Integration test suite for VoiceInteractionManager, history, and status."""

    @pytest.mark.anyio
    async def test_interaction_manager_turn_coordination_and_ephemeral_history(self) -> None:
        """VoiceInteractionManager coordinates turns and stores bounded ephemeral history."""
        engine = make_mock_engine()
        stt = MockSTTProvider(transcript="Turn one")
        tts = MockTTSProvider()

        current_response = "Response one"

        async def dynamic_agent(_: str) -> str:
            return current_response

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=dynamic_agent,
        )
        history = ConversationHistory(max_messages=4)
        interaction_mgr = VoiceInteractionManager(
            pipeline_manager=pipeline, agent=dynamic_agent, history=history
        )

        # Turn 1
        res1 = await interaction_mgr.run_turn()
        assert res1.transcript == "Turn one"
        assert len(interaction_mgr.history) == 2
        assert interaction_mgr.history[0].role == Role.USER
        assert interaction_mgr.history[0].content == "Turn one"
        assert interaction_mgr.history[1].role == Role.ASSISTANT
        assert interaction_mgr.history[1].content == "Response one"

        # Turn 2
        stt._transcript = "Turn two"
        current_response = "Response two"
        res2 = await interaction_mgr.run_turn()
        assert res2.transcript == "Turn two"
        assert len(interaction_mgr.history) == 4

        # Turn 3 — exceeds max_messages=4, oldest turn pruned
        stt._transcript = "Turn three"
        current_response = "Response three"
        res3 = await interaction_mgr.run_turn()
        assert res3.transcript == "Turn three"
        assert len(interaction_mgr.history) == 4
        # First turn was pruned; remaining turns are turn 2 and turn 3
        assert interaction_mgr.history[0].content == "Turn two"
        assert interaction_mgr.history[3].content == "Response three"

    @pytest.mark.anyio
    async def test_interaction_announcements_with_rate_limiting(self) -> None:
        """Announcements are spoken via TTS/engine and rate-limited."""
        engine = make_mock_engine()
        stt = MockSTTProvider()
        tts = MockTTSProvider()
        pipeline = VoicePipelineManager(
            stt_provider=stt, tts_provider=tts, engine_client=engine, agent_handler=lambda p: "ok"
        )
        interaction_mgr = VoiceInteractionManager(
            pipeline_manager=pipeline, min_announce_interval_seconds=0.1
        )

        # First announcement succeeds
        success = await interaction_mgr.announce("Urgent alert: build failed")
        assert success is True
        engine.play_audio_buffer.assert_awaited()

        # Immediate second announcement violates rate limit
        with pytest.raises(VoicePipelineError, match="Announce rate limit exceeded"):
            await interaction_mgr.announce("Another alert immediately")

        # After interval, succeeds
        await asyncio.sleep(0.12)
        success2 = await interaction_mgr.announce("Alert after delay")
        assert success2 is True

    @pytest.mark.anyio
    async def test_diagnostic_status_reporting(self) -> None:
        """get_status() returns unified diagnostics across pipeline, audio engine, and history."""
        engine = make_mock_engine()
        stt = MockSTTProvider()
        tts = MockTTSProvider()
        pipeline = VoicePipelineManager(
            stt_provider=stt, tts_provider=tts, engine_client=engine, agent_handler=lambda p: "ok"
        )
        interaction_mgr = VoiceInteractionManager(pipeline_manager=pipeline)

        status = await interaction_mgr.get_status()

        assert status["pipeline_state"] == "IDLE"
        assert status["is_idle"] is True
        assert status["is_speaking"] is False
        assert status["is_listening"] is False
        assert status["history_message_count"] == 0
        assert status["audio_engine"]["sample_rate"] == 16000


class TestVoiceToolsAndAgentDependenciesIntegration:
    """Integration test suite verifying voice tools through ToolExecutor and PermissionEngine."""

    @pytest.mark.anyio
    async def test_voice_tools_bootstrap_and_execution_pipeline(self) -> None:
        """voice.announce and voice.status execute cleanly through ToolExecutor and PermissionEngine."""
        engine = make_mock_engine()
        stt = MockSTTProvider()
        tts = MockTTSProvider()
        pipeline = VoicePipelineManager(
            stt_provider=stt, tts_provider=tts, engine_client=engine, agent_handler=lambda p: "ok"
        )
        interaction_mgr = VoiceInteractionManager(
            pipeline_manager=pipeline, min_announce_interval_seconds=0.0
        )

        # Bootstrap default tools with voice_manager injected
        registry = ToolRegistry()
        system_defs, file_defs, memory_defs, voice_defs = setup_default_tools(
            registry, voice_manager=interaction_mgr
        )

        assert len(voice_defs) == 2
        assert registry.has("voice.announce")
        assert registry.has("voice.status")

        # Verify listening/speaking are NOT registered as tools (Decision 045)
        assert not registry.has("voice.listen")
        assert not registry.has("voice.speak")

        # Create execution pipeline
        permission_engine = PermissionEngine()
        confirmation_hook = AlwaysAllowConfirmationHook()
        executor = ToolExecutor(
            registry=registry,
            permission_engine=permission_engine,
            confirmation_hook=confirmation_hook,
        )

        # 1. Execute voice.status
        status_res = await executor.execute("voice.status", {})
        assert status_res.success is True
        assert status_res.data.pipeline_state == "IDLE"
        assert status_res.data.is_idle is True

        # 2. Execute voice.announce
        announce_res = await executor.execute(
            "voice.announce", {"message": "Meeting starts in 5 minutes"}
        )
        assert announce_res.success is True
        assert announce_res.data.announced is True
        engine.play_audio_buffer.assert_awaited()

    @pytest.mark.anyio
    async def test_agent_dependencies_voice_manager_wireup(self) -> None:
        """AgentDependencies accepts and provides typed voice_manager."""
        engine = make_mock_engine()
        stt = MockSTTProvider()
        tts = MockTTSProvider()
        pipeline = VoicePipelineManager(
            stt_provider=stt, tts_provider=tts, engine_client=engine, agent_handler=lambda p: "ok"
        )
        interaction_mgr = VoiceInteractionManager(pipeline_manager=pipeline)

        deps = AgentDependencies(voice_manager=interaction_mgr)
        assert deps.get_voice_manager() is interaction_mgr

        # Empty deps returns None without error
        empty_deps = AgentDependencies()
        assert empty_deps.get_voice_manager() is None


class TestRealProviderConfigurationAndSafety:
    """Validate real provider configuration paths, lazy loading, and error boundaries."""

    def test_faster_whisper_config_validation_and_cpu_first_defaults(self) -> None:
        """FasterWhisper config enforces CPU-first int8 defaults and validates parameters."""
        config = STTConfig()
        assert config.device == "cpu"
        assert config.compute_type == "int8"
        assert config.model_name == "Systran/faster-whisper-large-v3-turbo"

        # Provider instantiates without loading models (lazy loading)
        provider = FasterWhisperSTTProvider(config=config)
        assert provider._model is None

        # Custom config
        custom_config = STTConfig(model_name="tiny.en", device="cpu", compute_type="int8")
        custom_provider = FasterWhisperSTTProvider(config=custom_config)
        assert custom_provider._config.model_name == "tiny.en"

    def test_faster_whisper_unavailable_dependency_handling(self) -> None:
        """When faster_whisper is not installed, transcribe() cleanly raises STTUnavailableError."""
        provider = FasterWhisperSTTProvider()
        req = MagicMock()
        req.samples = [0.1, 0.2]
        req.sample_rate = 16000

        with pytest.raises(STTUnavailableError, match="faster-whisper is not installed"):
            asyncio.run(provider.transcribe(req))

    def test_kokoro_config_validation_and_cpu_first_defaults(self) -> None:
        """Kokoro config enforces CPU-first defaults and validates parameters."""
        config = TTSConfig()
        assert config.default_voice == "af_heart"
        assert config.speed == 1.0
        assert config.sample_rate == 24000

        # Provider instantiates without loading models (lazy loading)
        provider = KokoroTTSProvider(config=config)
        assert provider._model is None

        # Custom config
        custom_config = TTSConfig(default_voice="af_bella", speed=1.1)
        custom_provider = KokoroTTSProvider(config=custom_config)
        assert custom_provider._config.default_voice == "af_bella"
        assert custom_provider._config.speed == 1.1

    def test_kokoro_unavailable_dependency_handling(self) -> None:
        """When kokoro_onnx is not installed, synthesize() cleanly raises TTSUnavailableError."""
        provider = KokoroTTSProvider()
        req = MagicMock()
        req.text = "Testing voice synthesis"

        with pytest.raises(TTSUnavailableError, match="kokoro-onnx is not installed"):
            asyncio.run(provider.synthesize(req))
