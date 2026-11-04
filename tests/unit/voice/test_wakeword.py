"""Unit tests for wake-word detection, session lifecycle, and gating in InteractionCore."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from tom.schemas.voice import VoicePipelineState
from tom.voice.events import SpeechStartEvent
from tom.voice.interaction_core import InteractionCore
from tom.voice.streaming_stt import PartialTranscript, StreamingSTTProvider
from tom.voice.wakeword import is_wake_phrase, strip_wake_phrase

# ---------------------------------------------------------------------------
# Unit tests for wakeword string utilities
# ---------------------------------------------------------------------------


def test_is_wake_phrase() -> None:
    assert is_wake_phrase("hey tom") is True
    assert is_wake_phrase("Hey TOM") is True
    assert is_wake_phrase("Hey, Tom.") is True
    assert is_wake_phrase("Hey Dom") is True
    assert is_wake_phrase("Don?") is True
    assert is_wake_phrase("I don't.") is False
    assert is_wake_phrase("Tom") is True
    assert is_wake_phrase("Tom.") is True
    assert is_wake_phrase("Tom, what time is it?") is True
    assert is_wake_phrase("Hey Dom, what time is it?") is True
    assert is_wake_phrase("HEY TOM, what's up?") is True
    assert is_wake_phrase("Oh hey tom can you hear me") is True
    assert is_wake_phrase("hey alex") is False
    assert is_wake_phrase("tom is great") is False
    assert is_wake_phrase("what time is it?") is False


def test_strip_wake_phrase() -> None:
    assert strip_wake_phrase("hey tom", "hey tom") == ""
    assert strip_wake_phrase("Hey TOM!", "hey tom") == ""
    assert strip_wake_phrase("Hey, Tom.", "hey tom") == ""
    assert strip_wake_phrase("Hey Dom", "hey tom") == ""
    assert strip_wake_phrase("Don?", "hey tom") == ""
    assert strip_wake_phrase("Tom.", "hey tom") == ""
    assert strip_wake_phrase("Tom", "hey tom") == ""
    assert strip_wake_phrase("hey tom what time is it", "hey tom") == "what time is it"
    assert strip_wake_phrase("Hey, Tom, can you help?", "hey tom") == "can you help"
    assert strip_wake_phrase("Hey Dom, what time is it?", "hey tom") == "what time is it"
    assert strip_wake_phrase("Tom, what time is it?", "hey tom") == "what time is it"
    assert strip_wake_phrase("random text without wake word", "hey tom") == "random text without wake word"


# ---------------------------------------------------------------------------
# Mock STT Provider for Testing
# ---------------------------------------------------------------------------


class MockScriptedSTT(StreamingSTTProvider):
    def __init__(self, transcripts: list[str]) -> None:
        self._transcripts = list(transcripts)

    async def stream_transcribe(self, audio_stream, token=None, **kwargs):
        async for _ in audio_stream:
            pass
        text = self._transcripts.pop(0) if self._transcripts else ""
        yield PartialTranscript(text=text, is_final=True, confidence=1.0)

    async def transcribe(self, request):
        return None

    async def check_health(self):
        return True

    def get_model_info(self):
        return {"provider": "MockScriptedSTT"}


# ---------------------------------------------------------------------------
# Tests for InteractionCore Wakeword Gating and Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wakeword_gating_discards_unactivated_speech() -> None:
    """When wakeword is enabled, speech without the wake word is discarded."""
    stt = MockScriptedSTT(["what's the weather today"])
    tts = MagicMock()
    engine = MagicMock()
    engine.play_audio_buffer = AsyncMock()
    mic = MagicMock()

    core = InteractionCore(
        mic_loop=mic,
        stt=stt,
        tts=tts,
        orchestrator=MagicMock(),
        turn_manager=MagicMock(),
        policy=MagicMock(),
        engine=engine,
        turn_arbitrator=None,
        startup_greeting=False,
        wakeword_enabled=True,
        wakeword_phrase="hey tom",
    )

    assert core._wakeword_enabled is True
    assert core._wakeword_active is False

    pcm_audio = b"\x00\x00" * 1600
    await core._handle_speech_end(pcm_audio)

    # Wake word was not present: should not activate, should not play ack, should not begin turn
    assert core._wakeword_active is False
    assert engine.play_audio_buffer.call_count == 0


@pytest.mark.asyncio
async def test_wakeword_activation_with_standalone_phrase() -> None:
    """When user speaks 'Hey TOM' alone, TOM activates, plays short ack, and arms timeout."""
    stt = MockScriptedSTT(["Hey TOM"])
    synth_res = MagicMock()
    synth_res.samples = [0.1]
    synth_res.audio_data = b"\x00\x00"
    synth_res.sample_rate = 24000
    synth_res.channels = 1
    tts = MagicMock()
    tts.synthesize = AsyncMock(return_value=synth_res)
    engine = MagicMock()
    engine.play_audio_buffer = AsyncMock()
    mic = MagicMock()

    core = InteractionCore(
        mic_loop=mic,
        stt=stt,
        tts=tts,
        orchestrator=MagicMock(),
        turn_manager=MagicMock(),
        policy=MagicMock(),
        engine=engine,
        turn_arbitrator=None,
        startup_greeting=False,
        wakeword_enabled=True,
        wakeword_phrase="hey tom",
        wakeword_timeout_seconds=30.0,
    )

    assert core._wakeword_active is False

    pcm_audio = b"\x00\x00" * 1600
    await core._handle_speech_end(pcm_audio)

    # Must be active now
    assert core._wakeword_active is True
    # Ack must be synthesized and played
    assert tts.synthesize.call_count == 1
    assert engine.play_audio_buffer.call_count == 1
    # Pipeline should be in LISTENING
    assert core._pipeline.state is VoicePipelineState.LISTENING
    # Timeout handle must be armed
    assert core._wakeword_timeout_handle is not None
    core._cancel_wakeword_timeout()


@pytest.mark.asyncio
async def test_wakeword_activation_with_embedded_command() -> None:
    """When user speaks 'Hey TOM what is the time', TOM activates and strips phrase."""
    stt = MockScriptedSTT(["Hey TOM what is the time"])
    tts = MagicMock()
    engine = MagicMock()
    engine.play_audio_buffer = AsyncMock()
    mic = MagicMock()

    core = InteractionCore(
        mic_loop=mic,
        stt=stt,
        tts=tts,
        orchestrator=MagicMock(),
        turn_manager=MagicMock(),
        policy=MagicMock(),
        engine=engine,
        turn_arbitrator=None,
        startup_greeting=False,
        wakeword_enabled=True,
        wakeword_phrase="hey tom",
    )

    core._begin_turn = AsyncMock()

    pcm_audio = b"\x00\x00" * 1600
    await core._handle_speech_end(pcm_audio)

    # Must be active now
    assert core._wakeword_active is True
    # Turn was initiated immediately with stripped command transcript
    core._begin_turn.assert_awaited_once_with(pcm_audio, override_transcript="what is the time")
    # Ack was not played because the command was already provided
    assert engine.play_audio_buffer.call_count == 0


@pytest.mark.asyncio
async def test_multiple_followup_turns_without_repeating_wakeword() -> None:
    """Once activated, follow-up utterances are accepted without saying 'Hey TOM'."""
    stt = MockScriptedSTT(["Hey TOM"])
    synth_res = MagicMock(samples=[0.1], audio_data=b"\x00\x00", sample_rate=24000, channels=1)
    tts = MagicMock(synthesize=AsyncMock(return_value=synth_res))
    engine = MagicMock(play_audio_buffer=AsyncMock())

    core = InteractionCore(
        mic_loop=MagicMock(),
        stt=stt,
        tts=tts,
        orchestrator=MagicMock(),
        turn_manager=MagicMock(),
        policy=MagicMock(),
        engine=engine,
        turn_arbitrator=None,
        startup_greeting=False,
        wakeword_enabled=True,
        wakeword_phrase="hey tom",
    )

    # 1. Activate with wake word
    await core._handle_speech_end(b"\x00\x00" * 1600)
    assert core._wakeword_active is True

    # 2. Mock _begin_turn to record calls
    core._begin_turn = AsyncMock()

    # 3. First follow-up turn (no wake word)
    pcm_q1 = b"\x01\x00" * 1600
    await core._handle_speech_end(pcm_q1)
    core._begin_turn.assert_awaited_once_with(pcm_q1)

    # 4. Second follow-up turn (no wake word)
    core._begin_turn.reset_mock()
    pcm_q2 = b"\x02\x00" * 1600
    await core._handle_speech_end(pcm_q2)
    core._begin_turn.assert_awaited_once_with(pcm_q2)

    # Session remains active
    assert core._wakeword_active is True
    core._cancel_wakeword_timeout()

@pytest.mark.asyncio
async def test_inactivity_timeout_returns_to_inactive_mode() -> None:
    """Inactivity timeout in LISTENING state deactivates session; ordinary speech is then ignored."""
    core = InteractionCore(
        mic_loop=MagicMock(),
        stt=MockScriptedSTT(["ordinary speech"]),
        tts=MagicMock(),
        orchestrator=MagicMock(),
        turn_manager=MagicMock(),
        policy=MagicMock(),
        engine=MagicMock(play_audio_buffer=AsyncMock()),
        startup_greeting=False,
        wakeword_enabled=True,
        wakeword_phrase="hey tom",
        wakeword_timeout_seconds=0.05,
    )

    # Manually activate session
    core._wakeword_active = True
    core._pipeline.transition_to(VoicePipelineState.LISTENING)

    # Arm timeout with short duration
    core._arm_wakeword_timeout()
    assert core._wakeword_timeout_handle is not None

    # Wait for timeout to fire
    await asyncio.sleep(0.08)

    # Session must have reverted to inactive
    assert core._wakeword_active is False

    # Subsequent ordinary speech without wake word is ignored
    core._begin_turn = AsyncMock()
    await core._handle_speech_end(b"\x00\x00" * 1600)
    assert core._begin_turn.call_count == 0


@pytest.mark.asyncio
async def test_timeout_does_not_interrupt_processing_or_playback() -> None:
    """Inactivity timeout does NOT deactivate session while TOM is processing or speaking."""
    core = InteractionCore(
        mic_loop=MagicMock(),
        stt=MagicMock(),
        tts=MagicMock(),
        orchestrator=MagicMock(),
        turn_manager=MagicMock(),
        policy=MagicMock(),
        engine=MagicMock(),
        startup_greeting=False,
        wakeword_enabled=True,
        wakeword_phrase="hey tom",
    )

    core._wakeword_active = True
    core._pipeline.transition_to(VoicePipelineState.PROCESSING)

    # Fire timeout callback while PROCESSING
    core._on_wakeword_timeout()
    # Must NOT deactivate because state is not LISTENING
    assert core._wakeword_active is True

    # Test during playback
    core._pipeline.transition_to(VoicePipelineState.SPEAKING)
    core._playback_active = True
    core._on_wakeword_timeout()
    assert core._wakeword_active is True


@pytest.mark.asyncio
async def test_timeout_cancelled_on_speech_start() -> None:
    """When user begins speaking, pending inactivity timeout countdown is cancelled."""
    core = InteractionCore(
        mic_loop=MagicMock(),
        stt=MagicMock(),
        tts=MagicMock(),
        orchestrator=MagicMock(),
        turn_manager=MagicMock(),
        policy=MagicMock(),
        engine=MagicMock(),
        startup_greeting=False,
        wakeword_enabled=True,
        wakeword_phrase="hey tom",
        wakeword_timeout_seconds=30.0,
    )

    core._wakeword_active = True
    core._pipeline.transition_to(VoicePipelineState.LISTENING)
    core._arm_wakeword_timeout()
    assert core._wakeword_timeout_handle is not None

    # User starts speaking
    await core._handle_event(SpeechStartEvent(timestamp_ns=100))

    # Timer must have been cancelled
    assert core._wakeword_timeout_handle is None


@pytest.mark.asyncio
async def test_timeout_does_not_arm_during_processing_or_speaking() -> None:
    """_arm_wakeword_timeout must not arm when pipeline is in PROCESSING or SPEAKING."""
    core = InteractionCore(
        mic_loop=MagicMock(),
        stt=MagicMock(),
        tts=MagicMock(),
        orchestrator=MagicMock(),
        turn_manager=MagicMock(),
        policy=MagicMock(),
        engine=MagicMock(),
        startup_greeting=False,
        wakeword_enabled=True,
        wakeword_phrase="hey tom",
        wakeword_timeout_seconds=30.0,
    )
    core._wakeword_active = True

    # 1. While in PROCESSING
    core._pipeline.transition_to(VoicePipelineState.PROCESSING)
    core._arm_wakeword_timeout()
    assert core._wakeword_timeout_handle is None

    # 2. While in SPEAKING
    core._pipeline.transition_to(VoicePipelineState.SPEAKING)
    core._arm_wakeword_timeout()
    assert core._wakeword_timeout_handle is None

    # 3. While _playback_active is True even if state were LISTENING
    core._pipeline.transition_to(VoicePipelineState.IDLE)
    core._pipeline.transition_to(VoicePipelineState.LISTENING)
    core._playback_active = True
    core._arm_wakeword_timeout()
    assert core._wakeword_timeout_handle is None
    core._playback_active = False

    # 4. In pure idle LISTENING, it arms correctly
    core._arm_wakeword_timeout()
    assert core._wakeword_timeout_handle is not None
    core._cancel_wakeword_timeout()


@pytest.mark.asyncio
async def test_wake_ack_gates_mic_and_drains_before_timeout() -> None:
    """_play_wake_ack gates mic during playback, sleeps duration, ungates mic, and arms timeout."""
    mic = MagicMock()
    synth_res = MagicMock(samples=[0.1] * 2400, audio_data=b"\x00\x00" * 2400, sample_rate=24000, channels=1)
    tts = MagicMock(synthesize=AsyncMock(return_value=synth_res))
    engine = MagicMock(play_audio_buffer=AsyncMock())

    core = InteractionCore(
        mic_loop=mic,
        stt=MagicMock(),
        tts=tts,
        orchestrator=MagicMock(),
        turn_manager=MagicMock(),
        policy=MagicMock(),
        engine=engine,
        turn_arbitrator=None,
        startup_greeting=False,
        wakeword_enabled=True,
        wakeword_phrase="hey tom",
        wakeword_timeout_seconds=30.0,
    )
    core._wakeword_active = True

    await core._play_wake_ack()

    # Mic must have been gated and ungated
    assert mic.gate.call_count == 1
    assert mic.ungate.call_count == 1
    # State must be LISTENING
    assert core._pipeline.state is VoicePipelineState.LISTENING
    # Timeout must be armed
    assert core._wakeword_timeout_handle is not None
    core._cancel_wakeword_timeout()


@pytest.mark.asyncio
async def test_speech_after_timeout_is_discarded_unless_wakeword() -> None:
    """After 30s timeout, non-wake speech is discarded until wake word activates again."""
    stt = MockScriptedSTT([
        "hey tom",                  # 1. initial activation
        "ordinary query after timeout",  # 2. should be discarded
        "hey tom explain recursion",     # 3. re-activates and executes turn
    ])
    synth_res = MagicMock(samples=[0.1], audio_data=b"\x00\x00", sample_rate=24000, channels=1)
    tts = MagicMock(synthesize=AsyncMock(return_value=synth_res))
    engine = MagicMock(play_audio_buffer=AsyncMock())

    core = InteractionCore(
        mic_loop=MagicMock(),
        stt=stt,
        tts=tts,
        orchestrator=MagicMock(),
        turn_manager=MagicMock(),
        policy=MagicMock(),
        engine=engine,
        turn_arbitrator=None,
        startup_greeting=False,
        wakeword_enabled=True,
        wakeword_phrase="hey tom",
        wakeword_timeout_seconds=0.05,
    )

    # 1. Initial wake word activation
    await core._handle_speech_end(b"\x00\x00" * 1600)
    assert core._wakeword_active is True

    # 2. Inactivity timeout occurs
    await asyncio.sleep(0.08)
    assert core._wakeword_active is False

    # 3. Speech without wake word is discarded
    core._begin_turn = AsyncMock()
    await core._handle_speech_end(b"\x00\x00" * 1600)
    assert core._begin_turn.call_count == 0
    assert core._wakeword_active is False

    # 4. Wake word with command re-activates session and executes turn
    await core._handle_speech_end(b"\x00\x00" * 1600)
    assert core._wakeword_active is True
    core._begin_turn.assert_awaited_once_with(b"\x00\x00" * 1600, override_transcript="explain recursion")
    core._cancel_wakeword_timeout()


@pytest.mark.asyncio
async def test_startup_greeting_activates_session_followup_window() -> None:
    """Startup greeting proactively activates session; speech without wake phrase is accepted within window."""
    stt = MockScriptedSTT([
        "what time is it",  # 1. Accepted within active window right after greeting
        "can you hear me",  # 2. Rejected after timeout expires
    ])
    synth_res = MagicMock(samples=[0.1], audio_data=b"\x00\x00", sample_rate=24000, channels=1)
    tts = MagicMock(synthesize=AsyncMock(return_value=synth_res))
    engine = MagicMock(play_audio_buffer=AsyncMock())

    core = InteractionCore(
        mic_loop=MagicMock(),
        stt=stt,
        tts=tts,
        orchestrator=MagicMock(),
        turn_manager=MagicMock(),
        policy=MagicMock(),
        engine=engine,
        startup_greeting=True,
        wakeword_enabled=True,
        wakeword_phrase="hey tom",
        wakeword_timeout_seconds=0.05,
    )

    assert core._wakeword_active is False
    core._pipeline.transition_to(VoicePipelineState.LISTENING)

    # 1. Startup greeting plays
    await core._run_startup_greeting()
    assert core._wakeword_active is True
    assert core._wakeword_timeout_handle is not None

    # 2. User immediately responds without wake phrase (accepted)
    core._begin_turn = AsyncMock()
    await core._handle_speech_end(b"\x00\x00" * 1600)
    assert core._begin_turn.call_count == 1

    # Re-arm timeout (as would happen after turn completion)
    core._arm_wakeword_timeout()

    # 3. Timeout expires
    await asyncio.sleep(0.08)
    assert core._wakeword_active is False

    # 4. User speaks after timeout without wake phrase (rejected)
    core._begin_turn.reset_mock()
    await core._handle_speech_end(b"\x00\x00" * 1600)
    assert core._begin_turn.call_count == 0
    core._cancel_wakeword_timeout()


@pytest.mark.asyncio
async def test_wake_ack_greets_on_first_wake_and_after_timeout() -> None:
    """Standalone wake word greets on first conversation and after timeout break."""
    from tom.voice.greetings import _GREETINGS

    all_greetings = set()
    for period_greetings in _GREETINGS.values():
        all_greetings.update(period_greetings)

    stt = MockScriptedSTT([
        "Tom.",             # 1. First wake up: should use full greeting
        "Hey, Tom.",        # 2. Wake up after timeout: should greet again
    ])
    synth_res = MagicMock(samples=[0.1], audio_data=b"\x00\x00", sample_rate=24000, channels=1)
    tts = MagicMock(synthesize=AsyncMock(return_value=synth_res))
    engine = MagicMock(play_audio_buffer=AsyncMock())

    core = InteractionCore(
        mic_loop=MagicMock(),
        stt=stt,
        tts=tts,
        orchestrator=MagicMock(),
        turn_manager=MagicMock(),
        policy=MagicMock(),
        engine=engine,
        turn_arbitrator=None,
        startup_greeting=False,
        wakeword_enabled=True,
        wakeword_phrase="hey tom",
        wakeword_timeout_seconds=30.0,
    )

    # Starts dormant and silent without startup greeting
    assert core._startup_greeting is False
    assert core._wakeword_active is False
    core._pipeline.transition_to(VoicePipelineState.LISTENING)

    # 1. First standalone wake word: greets and activates
    await core._handle_speech_end(b"\x00\x00" * 1600)
    first_ack = tts.synthesize.call_args[0][0].text
    assert first_ack in all_greetings
    assert core._wakeword_active is True

    # 2. Follow-up query within active window: accepted and begins turn
    core._begin_turn = AsyncMock()
    await core._handle_speech_end(b"\x00\x00" * 1600)
    assert core._begin_turn.call_count == 1

    # 3. Simulate inactivity timeout firing
    core._on_wakeword_timeout()
    assert core._wakeword_active is False
    assert core._woke_from_timeout is True

    # 4. Wake word after timeout break: greets again
    tts.synthesize.reset_mock()
    await core._handle_speech_end(b"\x00\x00" * 1600)
    third_ack = tts.synthesize.call_args[0][0].text
    assert third_ack in all_greetings
    assert core._wakeword_active is True
    core._cancel_wakeword_timeout()
