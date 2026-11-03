"""Unit tests for greeting templates and session startup greeting behavior."""

from unittest.mock import AsyncMock

import pytest
from tom.voice.greetings import (
    _GREETINGS,
    ASSISTANT_NAME,
    WAKE_ACK_PHRASES,
    pick_greeting,
    pick_wake_ack,
)


def test_greeting_varies_by_time_of_day() -> None:
    """Verify that appropriate greeting pools are selected across all 24 hours."""
    # Morning: 5-11
    morning_greetings = set(_GREETINGS["morning"])
    for h in [5, 8, 11]:
        assert pick_greeting(h) in morning_greetings

    # Afternoon: 12-17
    afternoon_greetings = set(_GREETINGS["afternoon"])
    for h in [12, 14, 17]:
        assert pick_greeting(h) in afternoon_greetings

    # Evening: 18-22
    evening_greetings = set(_GREETINGS["evening"])
    for h in [18, 20, 22]:
        assert pick_greeting(h) in evening_greetings

    # Night: 23-4
    night_greetings = set(_GREETINGS["night"])
    for h in [23, 0, 2, 4]:
        assert pick_greeting(h) in night_greetings


def test_no_consecutive_repeat() -> None:
    """Calling pick_greeting with last greeting never returns the exact same greeting."""
    for hour in [8, 14, 19, 1]:
        first = pick_greeting(hour)
        # In multiple attempts, immediate next greeting must never equal 'first'
        for _ in range(10):
            second = pick_greeting(hour, last=first)
            assert second != first


def test_wake_ack_varies_and_no_consecutive_repeat() -> None:
    """Wake acknowledgement differs from last ack when multiple are available."""
    assert len(WAKE_ACK_PHRASES) > 1
    for _ in range(10):
        ack1 = pick_wake_ack()
        ack2 = pick_wake_ack(last=ack1)
        assert ack2 != ack1


def test_greetings_identity_consistency() -> None:
    """Verify all greetings identify as TOM and never use 'Alex' or confuse 'user'."""
    assert ASSISTANT_NAME == "TOM"
    for _period, templates in _GREETINGS.items():
        for t in templates:
            assert "Alex" not in t, f"Found 'Alex' in greeting template: {t}"
            assert "user" not in t.lower(), f"Confused 'user' role with identity in: {t}"


@pytest.mark.asyncio
async def test_startup_greeting_only_runs_once() -> None:
    """Startup greeting flag is cleared after execution, preventing replay on reconnect."""
    from unittest.mock import MagicMock

    from tom.voice.interaction_core import InteractionCore

    engine = MagicMock()
    engine.play_audio_buffer = AsyncMock()
    tts = MagicMock()
    synth_res = MagicMock()
    synth_res.samples = [0.1, 0.2]
    synth_res.audio_data = b"\x00\x00"
    synth_res.sample_rate = 24000
    synth_res.channels = 1
    tts.synthesize = AsyncMock(return_value=synth_res)

    core = InteractionCore(
        mic_loop=MagicMock(),
        stt=MagicMock(),
        tts=tts,
        orchestrator=MagicMock(),
        turn_manager=MagicMock(),
        policy=MagicMock(),
        engine=engine,
        startup_greeting=True,
    )

    assert core._startup_greeting is True
    await core._run_startup_greeting()
    # Flag must be reset to False
    assert core._startup_greeting is False
    assert engine.play_audio_buffer.call_count == 1

    # Calling it a second time (e.g. on mic reconnect or recovery) must not replay
    await core._run_startup_greeting()
    # Still 1 call, not replayed
    assert engine.play_audio_buffer.call_count == 1
