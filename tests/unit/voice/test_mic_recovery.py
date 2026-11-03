"""P0.7 — hermetic mic-recovery regression tests.

Every scenario simulates a TTS / playback failure and verifies that the
microphone is ungated (i.e. on_playback_completed fires) on every exit path.

No real hardware, no asyncio.run() on Windows (uses pytest-asyncio).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tom.voice.streaming_tts import IncrementalTTSStreamer
from tom.voice.tts import TTSProvider
from tom.schemas.voice import SynthesisRequest, SynthesisResult
from tom.core.context import CancellationToken
from tom.ipc.errors import ConnectionLostError


# ---------------------------------------------------------------------------
# Minimal fakes
# ---------------------------------------------------------------------------

class _FakeTTSProvider(TTSProvider):
    """Returns a tiny chunk of silence for every sentence."""

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        # 0.01 s of silence at 24 kHz, PCM16
        samples = [0.0] * 240
        return SynthesisResult(samples=samples, sample_rate=24000, channels=1)

    async def check_health(self) -> bool:
        return True

    def get_model_info(self) -> dict:
        return {}


class _RaisingSynthProvider(TTSProvider):
    """Raises on every synthesis call."""

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        raise RuntimeError("synthesis-exploded")

    async def check_health(self) -> bool:
        return True

    def get_model_info(self) -> dict:
        return {}


async def _sentences(*texts: str) -> AsyncIterator[str]:
    for t in texts:
        yield t


def _make_streamer(
    engine: object | None = None,
    provider: TTSProvider | None = None,
    on_playback_completed=None,
) -> IncrementalTTSStreamer:
    return IncrementalTTSStreamer(
        provider=provider or _FakeTTSProvider(),
        engine_client=engine,
        playback_tail_ms=0,
        on_playback_completed=on_playback_completed,
    )


# ---------------------------------------------------------------------------
# (a) Drain exception → mic ungated
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_drain_exception_fires_callback():
    """asyncio.sleep raising inside drain must still fire on_playback_completed."""
    completed = []

    engine = MagicMock()
    engine.play_audio_buffer = AsyncMock(return_value=None)

    streamer = _make_streamer(
        engine=engine, on_playback_completed=lambda: completed.append(1)
    )

    # Patch asyncio.sleep to raise after the first real call (startup).
    original_sleep = asyncio.sleep
    call_count = [0]

    async def _boom(delay, *args, **kwargs):
        call_count[0] += 1
        if call_count[0] > 1:
            raise asyncio.TimeoutError("drain timed out")
        await original_sleep(0)

    with patch("asyncio.sleep", side_effect=_boom):
        chunks = []
        async for chunk in streamer.stream_synthesize(
            sentences=_sentences("hello world"),
            token=None,
        ):
            chunks.append(chunk)

    assert completed, "on_playback_completed must fire even when drain sleep raises"


# ---------------------------------------------------------------------------
# (b) Cancellation during SPEAKING → recovery
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancellation_fires_callback():
    """CancellationToken cancellation during stream must still fire callback."""
    completed = []

    engine = MagicMock()
    # Make play_audio_buffer hang until cancelled
    play_event = asyncio.Event()

    async def _slow_play(**kwargs):
        await play_event.wait()

    engine.play_audio_buffer = _slow_play

    token = CancellationToken()
    streamer = _make_streamer(
        engine=engine, on_playback_completed=lambda: completed.append(1)
    )

    async def _cancel_soon():
        await asyncio.sleep(0.05)
        token.cancel()
        play_event.set()

    cancel_task = asyncio.create_task(_cancel_soon())
    chunks = []
    async for chunk in streamer.stream_synthesize(
        sentences=_sentences("one two three"),
        token=token,
    ):
        chunks.append(chunk)

    await cancel_task
    # Callback must have fired (cancellation path)
    assert completed, "on_playback_completed must fire after cancellation"


# ---------------------------------------------------------------------------
# (c) TTS synthesis exception → recovery + logged
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesis_exception_fires_callback(caplog):
    """Provider raising during synthesis must still fire on_playback_completed."""
    import logging
    completed = []

    engine = MagicMock()
    engine.play_audio_buffer = AsyncMock(return_value=None)

    streamer = _make_streamer(
        engine=engine,
        provider=_RaisingSynthProvider(),
        on_playback_completed=lambda: completed.append(1),
    )

    with caplog.at_level(logging.WARNING, logger="tom.voice.streaming_tts"):
        chunks = []
        async for chunk in streamer.stream_synthesize(
            sentences=_sentences("hello"),
            token=None,
        ):
            chunks.append(chunk)

    # No chunks (synthesis failed), but callback must still fire
    assert completed, "on_playback_completed must fire even when synthesis raises"
    # Should have logged TTS failure
    assert any("voice.tts.failed" in r.message or "synthesis" in r.message.lower()
               for r in caplog.records), "synthesis failure must be logged"


# ---------------------------------------------------------------------------
# (d) IPC disconnect during playback → callback fires
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ipc_disconnect_fires_callback():
    """ConnectionLostError during play_audio_buffer must still fire callback."""
    completed = []

    engine = MagicMock()
    engine.play_audio_buffer = AsyncMock(side_effect=ConnectionLostError("disconnected"))

    streamer = _make_streamer(
        engine=engine, on_playback_completed=lambda: completed.append(1)
    )

    chunks = []
    async for chunk in streamer.stream_synthesize(
        sentences=_sentences("hello world"),
        token=None,
    ):
        chunks.append(chunk)

    assert completed, "on_playback_completed must fire after IPC disconnect"


# ---------------------------------------------------------------------------
# (e) Idempotent guard — callback fires exactly once even on double-trigger
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_callback_fires_exactly_once_on_normal_completion():
    """on_playback_completed must fire exactly once on the happy path."""
    completed = []

    engine = MagicMock()
    engine.play_audio_buffer = AsyncMock(return_value=None)

    streamer = _make_streamer(
        engine=engine, on_playback_completed=lambda: completed.append(1)
    )

    async for _ in streamer.stream_synthesize(
        sentences=_sentences("one", "two"),
        token=None,
    ):
        pass

    assert len(completed) == 1, f"callback must fire exactly once; fired {len(completed)} times"


# ---------------------------------------------------------------------------
# (f) 30-turn fault-injection soak — zero deaf-LISTENING states
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fault_injection_soak_zero_deaf_listening():
    """30 turns with random failures — callback always fires (no deaf-LISTENING)."""
    rng = random.Random(42)

    fail_modes = [
        "none",
        "synthesis_raise",
        "ipc_disconnect",
        "drain_timeout",
        "cancel",
    ]

    deaf_count = 0

    for turn in range(30):
        mode = rng.choice(fail_modes)
        completed = []

        engine = MagicMock()
        token = CancellationToken()

        if mode == "ipc_disconnect":
            engine.play_audio_buffer = AsyncMock(
                side_effect=ConnectionLostError("disconnected")
            )
            provider = _FakeTTSProvider()
        elif mode == "synthesis_raise":
            engine.play_audio_buffer = AsyncMock(return_value=None)
            provider = _RaisingSynthProvider()
        else:
            engine.play_audio_buffer = AsyncMock(return_value=None)
            provider = _FakeTTSProvider()

        streamer = _make_streamer(
            engine=engine,
            provider=provider,
            on_playback_completed=lambda: completed.append(1),
        )

        if mode == "cancel":
            async def _run_with_cancel():
                async def _cancel():
                    await asyncio.sleep(0.01)
                    token.cancel()
                cancel_t = asyncio.create_task(_cancel())
                async for _ in streamer.stream_synthesize(
                    sentences=_sentences("hello world"),
                    token=token,
                ):
                    pass
                await cancel_t

            if mode == "drain_timeout":
                original_sleep = asyncio.sleep

                async def _quick_timeout(delay, *a, **kw):
                    if delay > 0.05:
                        raise asyncio.TimeoutError()
                    await original_sleep(0)

                with patch("asyncio.sleep", side_effect=_quick_timeout):
                    await _run_with_cancel()
            else:
                await _run_with_cancel()

        elif mode == "drain_timeout":
            original_sleep = asyncio.sleep
            call_count = [0]

            async def _timeout_sleep(delay, *a, **kw):
                call_count[0] += 1
                if call_count[0] > 1 and delay > 0.05:
                    raise asyncio.TimeoutError()
                await original_sleep(0)

            with patch("asyncio.sleep", side_effect=_timeout_sleep):
                async for _ in streamer.stream_synthesize(
                    sentences=_sentences("hello"),
                    token=None,
                ):
                    pass
        else:
            async for _ in streamer.stream_synthesize(
                sentences=_sentences("hello world"),
                token=None,
            ):
                pass

        if not completed:
            deaf_count += 1

    assert deaf_count == 0, (
        f"{deaf_count}/30 turns left mic gated (deaf-LISTENING) after failures"
    )


# ---------------------------------------------------------------------------
# P0.4 — Watchdog tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_watchdog_resumes_paused_mic_in_listening():
    """Watchdog detects mic paused > threshold in LISTENING and recovers it."""
    from tests.unit.voice.test_interaction_core import build_core
    from tom.schemas.voice import VoicePipelineState

    core, deps = build_core()
    core._pipeline.transition_to(VoicePipelineState.LISTENING)
    mic = deps["mic"]
    mic.pause()
    assert getattr(mic, "_paused", False) is True

    with patch("tom.voice.interaction_core.logger.error") as mock_err:
        task = asyncio.create_task(core._listening_watchdog(poll_interval_s=0.01, timeout_s=0.05))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert getattr(mic, "_paused", False) is False
        assert getattr(mic, "_gated", False) is False
        # Verify ERROR event was logged with structured fields
        matching = [
            call for call in mock_err.call_args_list
            if call.args and call.args[0] == "microphone_resumed_by_watchdog"
        ]
        assert len(matching) == 1
        kwargs = matching[0].kwargs
        assert kwargs["recovery_result"] == "resumed"
        assert kwargs["pause_duration"] >= 0.05


@pytest.mark.asyncio
async def test_watchdog_transitions_to_error_when_recovery_fails():
    """Watchdog transitions pipeline to ERROR state when mic recovery fails."""
    from tests.unit.voice.test_interaction_core import build_core
    from tom.schemas.voice import VoicePipelineState

    core, deps = build_core()
    core._pipeline.transition_to(VoicePipelineState.LISTENING)
    mic = deps["mic"]
    mic.pause()
    # Force recovery failure
    mic.resume = MagicMock(side_effect=RuntimeError("hardware disconnect"))

    with patch("tom.voice.interaction_core.logger.error") as mock_err:
        task = asyncio.create_task(core._listening_watchdog(poll_interval_s=0.01, timeout_s=0.05))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert core.state == VoicePipelineState.ERROR
        matching = [
            call for call in mock_err.call_args_list
            if call.args and call.args[0] == "microphone_resumed_by_watchdog"
        ]
        assert len(matching) == 1
        assert matching[0].kwargs["recovery_result"] == "failed"


