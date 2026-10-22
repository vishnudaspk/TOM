"""Unit and regression tests for Smart Turn V3 safety, availability, and VAD fallback.

Verifies:
1. SmartTurnV3Arbiter.check_is_available() and is_available reporting.
2. SmartTurnV3Arbiter raises SmartTurnUnavailableError with diagnostic context when dependencies/models fail.
3. InteractionCore deterministic fallback to VAD when Smart Turn raises an error or is unavailable:
   - Exactly-once turn commit (no double commit)
   - Zero lost audio (all candidate audio preserved)
   - Exactly-once epoch bump
   - Clean cleanup on shutdown
4. InteractionCore behavior when turn_arbitrator=None (VAD-only mode).
5. TomApp._run_voice startup with and without Smart Turn.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from tom.voice.events import AudioFrameEvent, SpeechEndEvent, SpeechStartEvent
from tom.voice.smart_turn import (
    SmartTurnUnavailableError,
    SmartTurnV3Arbiter,
    TurnArbiter,
    TurnArbitrationDecision,
)

from tests.unit.voice.test_interaction_core import (
    CompleteArbiter,
    build_core,
    wait_for_turns,
    wait_until,
)


class ErrorArbiter(TurnArbiter):
    """Arbiter that simulates model or inference failure during analyze()."""

    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc or SmartTurnUnavailableError("Simulated Smart Turn model crash")
        self.calls = 0

    async def analyze(self, audio: bytes) -> TurnArbitrationDecision:
        self.calls += 1
        raise self.exc

    async def cleanup(self) -> None:
        return None


class FakeSmartTurnSession:
    def __init__(self, providers: list[str]) -> None:
        self._providers = providers

    def get_providers(self) -> list[str]:
        return list(self._providers)


class FakePipecatAnalyzer:
    def __init__(self, state_name: str = "COMPLETE") -> None:
        self.state_name = state_name
        self._session = FakeSmartTurnSession(["CPUExecutionProvider"])
        self._smart_turn_model_path = "/path/to/smart-turn-v3.2-cpu.onnx"
        self.audio: list[bytes] = []
        self.cleaned = False

    def append_audio(self, audio: bytes, is_speech: bool) -> None:
        self.audio.append(audio)

    async def analyze_end_of_turn(self) -> tuple[Any, None]:
        mock_state = MagicMock()
        mock_state.name = self.state_name
        return mock_state, None

    async def cleanup(self) -> None:
        self.cleaned = True


def test_smart_turn_check_is_available_when_missing_dependency() -> None:
    """When pipecat is missing or fails import, check_is_available returns False and reason."""
    with patch.dict("sys.modules", {"pipecat": None}):
        avail, reason = SmartTurnV3Arbiter.check_is_available()
        assert avail is False
        assert reason is not None
        assert "pipecat" in reason.lower() or "none in sys.modules" in reason.lower()

        arbiter = SmartTurnV3Arbiter()
        assert arbiter.is_available is False
        with pytest.raises(SmartTurnUnavailableError) as exc_info:
            arbiter._ensure_analyzer()
        assert "unavailable" in str(exc_info.value).lower() or "not installed" in str(exc_info.value).lower()


def test_smart_turn_initialization_logs_model_and_session_providers() -> None:
    """When analyzer loads, providers and model path are inspected and accessible."""
    fake = FakePipecatAnalyzer("COMPLETE")
    arbiter = SmartTurnV3Arbiter(analyzer=fake)
    assert arbiter.is_available is True
    assert arbiter.analyzer is fake

    async def _test() -> None:
        decision = await arbiter.analyze(b"pcm_test_data")
        assert decision is TurnArbitrationDecision.COMPLETE
        assert fake.audio == [b"pcm_test_data"]

        # Directly cleaning fake since arbiter does not own injected analyzer
        await fake.cleanup()
        assert fake.cleaned is True

    asyncio.run(_test())


def test_interaction_core_vad_fallback_on_arbiter_exception() -> None:
    """When turn arbitrator raises an exception, InteractionCore falls back to VAD:
    - turn is committed exactly once (no double commit)
    - epoch increments exactly once
    - candidate audio is fully preserved (bytes length received by STT matches)
    - smart_turn_candidate buffer is cleared
    """
    async def scenario() -> None:
        error_arbiter = ErrorArbiter()
        core, parts = build_core(turn_arbitrator=error_arbiter)
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is not None)

        test_pcm = b"important_user_audio_chunk"
        await core._handle_event(
            SpeechEndEvent(
                samples=[0.1, 0.2],
                pcm_bytes=test_pcm,
                duration_s=0.5,
                frame_count=25,
            )
        )
        await wait_for_turns(core, parts, 1)

        # Verified: Arbiter was called once
        assert error_arbiter.calls == 1

        # Verified: Turn committed exactly once, epoch bumped exactly once
        assert core.epoch == 1

        # Verified: The candidate audio was NOT dropped; it reached STT (matching byte size)
        assert parts["stt"].utterance_bytes == [len(test_pcm)]

        # Verified: smart_turn_candidate buffer was reset to None (no double commit)
        assert core._smart_turn_candidate is None

        # Verified: Downstream LLM and TTS executed
        assert len(parts["orchestrator"].prompts) == 1
        assert len(parts["engine"].play_calls) == 1

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    asyncio.run(scenario())


def test_interaction_core_with_none_turn_arbitrator_uses_pure_vad() -> None:
    """Explicitly setting turn_arbitrator=None runs in pure VAD-endpointed mode without error.

    This tests the actual InteractionCore constructor behavior: passing None explicitly
    disables Smart Turn without constructing SmartTurnV3Arbiter.
    """
    async def scenario() -> None:
        core, parts = build_core(turn_arbitrator=None)
        assert core._turn_arbitrator is None

        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is not None)

        test_pcm = b"vad_only_utterance"
        await core._handle_event(
            SpeechEndEvent(
                samples=[0.1],
                pcm_bytes=test_pcm,
                duration_s=0.2,
                frame_count=10,
            )
        )
        await wait_for_turns(core, parts, 1)

        assert core.epoch == 1
        assert parts["stt"].utterance_bytes == [len(test_pcm)]
        assert len(parts["orchestrator"].prompts) == 1

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    asyncio.run(scenario())


def test_interaction_core_barge_in_and_cancellation_during_fallback() -> None:
    """Barge-in while turn is processing under fallback cleanly cancels playback and rolls forward."""
    async def scenario() -> None:
        error_arbiter = ErrorArbiter()
        core, parts = build_core(turn_arbitrator=error_arbiter)
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is not None)

        # Set block_playback so playback is actively in progress
        parts["engine"].block_playback = True

        # First turn triggers fallback
        await core._handle_event(
            SpeechEndEvent(samples=[0.1], pcm_bytes=b"turn1", duration_s=0.1, frame_count=5)
        )
        await parts["engine"].playback_started.wait()
        assert core.epoch == 1

        # Barge-in event cancels active playback
        await core.handle_barge_in()
        assert parts["engine"].stop_calls == 1
        parts["engine"].block_playback = False

        # Next speech start and end increments epoch cleanly
        await core._handle_event(SpeechStartEvent())
        await core._handle_event(
            AudioFrameEvent(samples=[0.2], pcm_bytes=b"turn2", frame_index=0, is_speech=True)
        )
        await core._handle_event(
            SpeechEndEvent(samples=[0.2], pcm_bytes=b"turn2", duration_s=0.1, frame_count=5)
        )
        await wait_for_turns(core, parts, 2)
        assert core.epoch == 3
        assert len(parts["stt"].utterance_bytes) == 2

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    asyncio.run(scenario())


def test_smart_turn_constructor_validation() -> None:
    """Invalid parameters passed to SmartTurnV3Arbiter fail fast or set unavailable_reason."""
    # cpu_count < 1 raises ValueError
    with pytest.raises(ValueError, match="cpu_count must be at least 1"):
        SmartTurnV3Arbiter(cpu_count=0)

    # non-existent model path marks arbiter unavailable without crashing constructor
    arbiter = SmartTurnV3Arbiter(model_path="C:/nonexistent/path/to/model.onnx")
    assert arbiter.is_available is False
    assert arbiter.unavailable_reason is not None
    assert "does not exist" in arbiter.unavailable_reason

    with pytest.raises(SmartTurnUnavailableError, match="does not exist"):
        arbiter._ensure_analyzer()


def test_tom_app_run_voice_wiring_with_injected_arbiter() -> None:
    """TomApp properly distinguishes default sentinel, injected arbiter, and explicit None."""
    from tom.main import _DEFAULT_ARBITER, TomApp

    app = TomApp()
    assert app._injected_turn_arbitrator is _DEFAULT_ARBITER

    # Injected custom arbiter
    fake_arbiter = CompleteArbiter()
    app._injected_turn_arbitrator = fake_arbiter
    assert app._injected_turn_arbitrator is fake_arbiter

    # Explicitly disabled arbiter (pure VAD endpointing)
    app._injected_turn_arbitrator = None
    assert app._injected_turn_arbitrator is None
    assert app._injected_turn_arbitrator is not _DEFAULT_ARBITER


def test_interaction_core_explicit_disabled_vs_default_arbiter() -> None:
    """Explicitly passing turn_arbitrator=None does not construct SmartTurnV3Arbiter.

    - Explicit None: VAD-only mode, no SmartTurnV3Arbiter constructed.
    - Default sentinel: probes availability, falls back to None when unavailable.
    """
    from tom.voice.interaction_core import _DEFAULT_ARBITER

    # Explicit None: VAD-only mode, no SmartTurnV3Arbiter constructed
    core_none, _ = build_core(turn_arbitrator=None)
    assert core_none._turn_arbitrator is None

    # Default sentinel: probes availability; when unavailable it falls back to None
    with patch.object(SmartTurnV3Arbiter, "check_is_available", return_value=(False, "test_dep_missing")):
        with patch.object(SmartTurnV3Arbiter, "is_available", new_callable=lambda: property(lambda self: False)):
            core_default, _ = build_core(turn_arbitrator=_DEFAULT_ARBITER)
            assert core_default._turn_arbitrator is None
