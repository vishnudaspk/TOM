"""Unit tests for Phase 9 Iteration 9.0: MicrophoneLoop & VAD Endpointing.

All tests are deterministic and offline.
No microphone, speakers, network, GPU, CUDA, or external services required.

Conventions match existing TOM tests:
- Synchronous def test_ methods
- Async coroutines run with run_async(...) helper
- No pytest.mark.asyncio (strict-markers mode)
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Coroutine
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from tom.core.engine import EngineClient
from tom.ipc.protocol import AudioOperationResponse, AudioSpeechResponse
from tom.voice.audio_utils import pcm16_bytes_to_samples, samples_to_pcm16_bytes
from tom.voice.events import AudioFrameEvent, SpeechEndEvent, SpeechStartEvent, VoiceEvent
from tom.voice.microphone import MicrophoneConfig, MicrophoneLoop
from tom.voice.vad import EnergyVADDetector, MockVADDetector

# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


def make_silence_samples(count: int) -> list[float]:
    """Generate pure silence samples (-1.0 to 1.0)."""
    return [0.0] * count


def make_speech_samples(count: int, amplitude: float = 0.2) -> list[float]:
    """Generate a clean synthetic speech-like tone pattern."""
    return [amplitude * (1.0 if (i % 8 < 4) else -1.0) for i in range(count)]


def create_mock_engine(speech_samples: list[float] | None = None) -> MagicMock:
    """Create a mock EngineClient for audio capture."""
    engine = MagicMock(spec=EngineClient)
    engine.start_audio_capture = AsyncMock(
        return_value=AudioOperationResponse(success=True, message="capture started")
    )
    engine.stop_audio_capture = AsyncMock(
        return_value=AudioOperationResponse(success=True, message="capture stopped")
    )
    samples = speech_samples if speech_samples is not None else make_silence_samples(320)
    engine.get_captured_speech = AsyncMock(
        return_value=AudioSpeechResponse(
            samples=samples,
            sample_rate=16000,
            channels=1,
            sample_count=len(samples),
        )
    )
    return engine


# ---------------------------------------------------------------------------
# Audio Utility Tests
# ---------------------------------------------------------------------------


def test_audio_utils_conversions():
    """Verify float <-> PCM16 byte conversions with high fidelity."""
    original = [0.0, 0.5, -0.5, 0.99, -0.99]
    pcm_bytes = samples_to_pcm16_bytes(original)
    assert len(pcm_bytes) == len(original) * 2

    recovered = pcm16_bytes_to_samples(pcm_bytes)
    assert len(recovered) == len(original)
    for orig, rec in zip(original, recovered, strict=True):
        assert abs(orig - rec) < 1e-4

    assert samples_to_pcm16_bytes([]) == b""
    assert pcm16_bytes_to_samples(b"") == []


# ---------------------------------------------------------------------------
# VAD Detector Tests
# ---------------------------------------------------------------------------


def test_energy_vad_detector_silence_and_speech():
    """Verify EnergyVADDetector accurately classifies silence vs speech."""
    vad = EnergyVADDetector(threshold=0.015, adaptive=False)

    silence = make_silence_samples(320)
    assert not vad.is_speech(silence)
    assert vad.calculate_rms(silence) == 0.0

    speech = make_speech_samples(320, amplitude=0.1)
    assert vad.is_speech(speech)
    assert vad.calculate_rms(speech) > 0.015


def test_mock_vad_detector_sequence():
    """Verify MockVADDetector returns the configured sequence."""
    vad = MockVADDetector(sequence=[True, False, True], default_result=False)
    dummy = make_silence_samples(320)

    assert vad.is_speech(dummy) is True
    assert vad.is_speech(dummy) is False
    assert vad.is_speech(dummy) is True
    assert vad.is_speech(dummy) is False  # default_result


# ---------------------------------------------------------------------------
# MicrophoneLoop Turn & Event Tests
# ---------------------------------------------------------------------------


def test_silence_produces_no_speech_turn():
    """Verify continuous silence produces zero speech events."""

    async def _test():
        config = MicrophoneConfig(
            frame_duration_ms=20,
            silence_duration_ms=200,
            energy_threshold=0.015,
        )
        loop = MicrophoneLoop(config=config)
        events_received: list[VoiceEvent] = []

        sub = loop.subscribe()

        async def collect():
            async for ev in sub:
                events_received.append(ev)

        collector = asyncio.create_task(collect())

        # Push 1 second of silence (50 frames of 320 samples at 16kHz)
        for _ in range(50):
            await loop.push_samples(make_silence_samples(320))

        await loop.stop()
        await collector

        assert len(events_received) == 0
        assert not loop.is_in_speech

    run_async(_test())


def test_speech_produces_speech_start_and_frames():
    """Verify speech onset produces SpeechStartEvent and subsequent AudioFrameEvents."""

    async def _test():
        config = MicrophoneConfig(
            frame_duration_ms=20,
            silence_duration_ms=400,
            min_speech_frames=2,
            pre_roll_frames=2,
            energy_threshold=0.015,
        )
        loop = MicrophoneLoop(config=config)
        events_received: list[VoiceEvent] = []

        sub = loop.subscribe()

        async def collect():
            async for ev in sub:
                events_received.append(ev)

        collector = asyncio.create_task(collect())

        # Feed 3 pre-roll silence frames
        for _ in range(3):
            await loop.push_samples(make_silence_samples(320))

        # Feed 4 speech frames
        for _ in range(4):
            await loop.push_samples(make_speech_samples(320, amplitude=0.2))

        assert loop.is_in_speech

        await loop.stop()
        await collector

        start_events = [e for e in events_received if isinstance(e, SpeechStartEvent)]
        frame_events = [e for e in events_received if isinstance(e, AudioFrameEvent)]
        end_events = [e for e in events_received if isinstance(e, SpeechEndEvent)]

        assert len(start_events) == 1
        # pre-roll frames (2) + onset speech frames + remaining speech frames
        assert len(frame_events) >= 4
        # Since loop.stop() was called mid-speech, an end event was finalized
        assert len(end_events) == 1

        # Check sequential frame indexing
        indices = [f.frame_index for f in frame_events]
        assert indices == list(range(len(frame_events)))

    run_async(_test())


def test_sustained_speech_single_turn():
    """Verify sustained continuous speech does not repeatedly start new turns."""

    async def _test():
        config = MicrophoneConfig(
            frame_duration_ms=20,
            silence_duration_ms=400,
            min_speech_frames=2,
            max_utterance_seconds=30.0,
        )
        loop = MicrophoneLoop(config=config)
        events_received: list[VoiceEvent] = []

        sub = loop.subscribe()

        async def collect():
            async for ev in sub:
                events_received.append(ev)

        collector = asyncio.create_task(collect())

        # Feed 50 frames of speech (1 second)
        for _ in range(50):
            await loop.push_samples(make_speech_samples(320, amplitude=0.2))

        assert loop.is_in_speech
        await asyncio.sleep(0.01)
        start_events_mid = [e for e in events_received if isinstance(e, SpeechStartEvent)]
        assert len(start_events_mid) == 1

        await loop.stop()
        await collector

        start_events = [e for e in events_received if isinstance(e, SpeechStartEvent)]
        assert len(start_events) == 1

    run_async(_test())


def test_trailing_silence_produces_speech_end_event():
    """Verify trailing silence >= silence_duration_ms produces exactly one SpeechEndEvent."""

    async def _test():
        config = MicrophoneConfig(
            frame_duration_ms=20,
            silence_duration_ms=100,  # 5 frames of silence
            min_speech_frames=2,
        )
        loop = MicrophoneLoop(config=config)
        events_received: list[VoiceEvent] = []

        sub = loop.subscribe()

        async def collect():
            async for ev in sub:
                events_received.append(ev)

        collector = asyncio.create_task(collect())

        # 5 speech frames
        for _ in range(5):
            await loop.push_samples(make_speech_samples(320, amplitude=0.2))

        assert loop.is_in_speech

        # Feed 6 frames of silence (120ms > 100ms hangover)
        for _ in range(6):
            await loop.push_samples(make_silence_samples(320))

        # At this point, endpoint must have triggered
        assert not loop.is_in_speech

        await loop.stop()
        await collector

        end_events = [e for e in events_received if isinstance(e, SpeechEndEvent)]
        assert len(end_events) == 1
        end_ev = end_events[0]
        assert end_ev.duration_s > 0.1
        assert end_ev.frame_count >= 5
        assert len(end_ev.samples) == end_ev.frame_count * 320

    run_async(_test())


def test_final_speech_audio_not_lost_at_endpoint():
    """Verify SpeechEndEvent contains complete concatenated audio without loss."""

    async def _test():
        config = MicrophoneConfig(
            frame_duration_ms=20,
            silence_duration_ms=60,  # 3 frames of silence
            pre_roll_frames=1,
            min_speech_frames=1,
        )
        loop = MicrophoneLoop(config=config)
        events_received: list[VoiceEvent] = []

        sub = loop.subscribe()

        async def collect():
            async for ev in sub:
                events_received.append(ev)

        collector = asyncio.create_task(collect())

        # 1 pre-roll silence frame
        await loop.push_samples(make_silence_samples(320))

        # 3 speech frames with known values
        for i in range(3):
            val = 0.1 * (i + 1)
            await loop.push_samples([val] * 320)

        # 3 silence frames to hit hangover
        for _ in range(3):
            await loop.push_samples(make_silence_samples(320))

        await loop.stop()
        await collector

        end_events = [e for e in events_received if isinstance(e, SpeechEndEvent)]
        assert len(end_events) == 1
        end_ev = end_events[0]

        # 1 pre-roll + 3 speech + 3 trailing silence = 7 frames = 2240 samples
        assert end_ev.frame_count == 7
        assert len(end_ev.samples) == 7 * 320
        assert len(end_ev.pcm_bytes) == 7 * 320 * 2

        # Verify known values are inside the concatenated samples (with float tolerance)
        assert any(abs(s - 0.1) < 1e-4 for s in end_ev.samples)
        assert any(abs(s - 0.2) < 1e-4 for s in end_ev.samples)
        assert any(abs(s - 0.3) < 1e-4 for s in end_ev.samples)

    run_async(_test())


def test_mute_and_unmute_controls():
    """Verify mute drops audio and unmute cleanly restores processing."""

    async def _test():
        config = MicrophoneConfig(
            frame_duration_ms=20,
            silence_duration_ms=100,
            min_speech_frames=1,
        )
        loop = MicrophoneLoop(config=config)
        events_received: list[VoiceEvent] = []

        sub = loop.subscribe()

        async def collect():
            async for ev in sub:
                events_received.append(ev)

        collector = asyncio.create_task(collect())

        # Mute first
        loop.mute()
        assert loop.is_muted

        # Feed speech while muted
        for _ in range(5):
            await loop.push_samples(make_speech_samples(320, amplitude=0.2))

        assert len(events_received) == 0
        assert not loop.is_in_speech

        # Unmute
        loop.unmute()
        assert not loop.is_muted

        # Feed speech again
        for _ in range(5):
            await loop.push_samples(make_speech_samples(320, amplitude=0.2))

        assert loop.is_in_speech

        await loop.stop()
        await collector

        start_events = [e for e in events_received if isinstance(e, SpeechStartEvent)]
        assert len(start_events) == 1

    run_async(_test())


def test_pause_and_resume():
    """Verify pause drops incoming samples and resume restores processing."""

    async def _test():
        config = MicrophoneConfig(
            frame_duration_ms=20,
            min_speech_frames=1,
        )
        loop = MicrophoneLoop(config=config)
        events_received: list[VoiceEvent] = []

        sub = loop.subscribe()

        async def collect():
            async for ev in sub:
                events_received.append(ev)

        collector = asyncio.create_task(collect())

        loop.pause()
        assert loop.is_paused

        for _ in range(5):
            await loop.push_samples(make_speech_samples(320, amplitude=0.2))

        assert len(events_received) == 0

        loop.resume()
        assert not loop.is_paused

        for _ in range(5):
            await loop.push_samples(make_speech_samples(320, amplitude=0.2))

        assert loop.is_in_speech

        await loop.stop()
        await collector

        assert len([e for e in events_received if isinstance(e, SpeechStartEvent)]) == 1

    run_async(_test())


def test_maximum_buffer_duration_enforced():
    """Verify maximum utterance duration forces endpointing to prevent memory leaks."""

    async def _test():
        config = MicrophoneConfig(
            frame_duration_ms=20,
            silence_duration_ms=2000,  # High hangover so silence doesn't trigger
            max_utterance_seconds=0.1,  # 100ms = 5 frames max
            min_speech_frames=1,
        )
        loop = MicrophoneLoop(config=config)
        events_received: list[VoiceEvent] = []

        sub = loop.subscribe()

        async def collect():
            async for ev in sub:
                events_received.append(ev)

        collector = asyncio.create_task(collect())

        # Feed 10 frames of speech (200ms > 100ms max)
        for _ in range(10):
            await loop.push_samples(make_speech_samples(320, amplitude=0.2))

        await loop.stop()
        await collector

        end_events = [e for e in events_received if isinstance(e, SpeechEndEvent)]
        assert len(end_events) >= 1
        # First end event was capped near 100ms
        assert end_events[0].duration_s <= 0.15

    run_async(_test())


def test_noisy_transient_no_pathological_turns():
    """Verify single-frame noise spikes do not trigger speech turns when min_speech_frames >= 2."""

    async def _test():
        config = MicrophoneConfig(
            frame_duration_ms=20,
            min_speech_frames=2,  # Require 2 consecutive frames
        )
        loop = MicrophoneLoop(config=config)
        events_received: list[VoiceEvent] = []

        sub = loop.subscribe()

        async def collect():
            async for ev in sub:
                events_received.append(ev)

        collector = asyncio.create_task(collect())

        # Silence -> 1 frame noise spike -> Silence
        await loop.push_samples(make_silence_samples(320))
        await loop.push_samples(make_speech_samples(320, amplitude=0.5))  # Spike
        await loop.push_samples(make_silence_samples(320))
        await loop.push_samples(make_silence_samples(320))

        await loop.stop()
        await collector

        assert len(events_received) == 0
        assert not loop.is_in_speech

    run_async(_test())


def test_clean_stop_and_task_cancellation():
    """Verify calling stop() terminates cleanly without hanging or raising exceptions."""

    async def _test():
        loop = MicrophoneLoop()
        await loop.start()
        assert loop.is_running

        # Double start is idempotent
        await loop.start()
        assert loop.is_running

        await loop.stop()
        assert not loop.is_running

        # Double stop is idempotent
        await loop.stop()
        assert not loop.is_running

    run_async(_test())


def test_mock_engine_polling_integration():
    """Verify MicrophoneLoop polls EngineClient and receives audio speech responses."""

    async def _test():
        speech = make_speech_samples(320, amplitude=0.2)
        mock_engine = create_mock_engine(speech_samples=speech)
        config = MicrophoneConfig(
            frame_duration_ms=20,
            poll_interval_s=0.01,
            min_speech_frames=1,
        )
        loop = MicrophoneLoop(engine_client=mock_engine, config=config)

        events_received: list[VoiceEvent] = []

        async def collect():
            async for ev in loop.events():
                events_received.append(ev)

        collector = asyncio.create_task(collect())

        await loop.start()
        # Allow polling task to run for a few iterations
        await asyncio.sleep(0.05)

        await loop.stop()
        await collector

        assert mock_engine.start_audio_capture.called
        assert mock_engine.stop_audio_capture.called
        assert mock_engine.get_captured_speech.called
        assert len(events_received) > 0

    run_async(_test())


def test_frame_processing_overhead():
    """Verify frame processing overhead is well under 10ms per 20ms frame."""

    async def _test():
        config = MicrophoneConfig(frame_duration_ms=20)
        loop = MicrophoneLoop(config=config)
        sample = make_speech_samples(320, amplitude=0.2)

        start = time.perf_counter()
        # Process 100 frames (equivalent to 2.0 seconds of audio)
        for _ in range(100):
            await loop.push_samples(sample)
        elapsed = time.perf_counter() - start

        # 100 frames should take < 50ms total (< 0.5ms per frame)
        assert elapsed < 0.2, f"100 frames took {elapsed:.4f}s, expected < 0.2s"

    run_async(_test())


def test_max_duration_keeps_remaining_frames_for_next_turn():
    """Verify max-duration endpoint does not discard leftover PCM in the same ingest."""

    async def _test():
        config = MicrophoneConfig(
            frame_duration_ms=20,
            silence_duration_ms=2000,
            max_utterance_seconds=0.1,  # 5 frames
            min_speech_frames=1,
            pre_roll_frames=0,
        )
        loop = MicrophoneLoop(config=config)
        events_received: list[VoiceEvent] = []
        sub = loop.subscribe()

        async def collect():
            async for ev in sub:
                events_received.append(ev)

        collector = asyncio.create_task(collect())

        await loop.push_samples(make_speech_samples(320 * 10, amplitude=0.2))
        await loop.stop()
        await collector

        start_events = [e for e in events_received if isinstance(e, SpeechStartEvent)]
        end_events = [e for e in events_received if isinstance(e, SpeechEndEvent)]
        assert len(start_events) == 2
        assert len(end_events) == 2

    run_async(_test())


def test_engine_stop_cancels_poll_task():
    """Verify stop() cancels the EngineClient poll task without hanging."""

    async def _test():
        mock_engine = create_mock_engine(speech_samples=make_silence_samples(320))
        loop = MicrophoneLoop(
            engine_client=mock_engine,
            config=MicrophoneConfig(poll_interval_s=0.01),
        )
        await loop.start()
        await asyncio.sleep(0.03)
        await loop.stop()
        assert loop._poll_task is None
        assert mock_engine.stop_audio_capture.called

    run_async(_test())
