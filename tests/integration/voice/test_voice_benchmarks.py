"""Deterministic latency benchmarks for TOM's Phase 6 voice pipeline.

Measures software orchestration overhead across:
1. SpeechFormatter processing (short, medium, markdown-heavy)
2. STT provider invocation path
3. Agent processing path
4. TTS provider invocation path
5. Audio playback handoff
6. Full end-to-end mock turn pipeline
7. Barge-in / interruption response latency

Note: These benchmarks measure software execution and IPC orchestration overhead.
Hardware/model inference targets (wake word <500ms, real Whisper <2s for 10s audio,
real Kokoro <1s) require physical hardware and local neural network execution.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from tom.core.engine import EngineClient
from tom.ipc.protocol import AudioOperationResponse, AudioSpeechResponse
from tom.voice.formatter import SpeechFormatter
from tom.voice.pipeline import VoicePipelineManager
from tom.voice.stt import MockSTTProvider
from tom.voice.tts import MockTTSProvider

DUMMY_AUDIO = [0.05, -0.05, 0.1, -0.1] * 400


def percentile(values: list[float], p: float) -> float:
    """Calculate the p-th percentile of a sorted list."""
    if not values:
        return 0.0
    k = (len(values) - 1) * p
    f = int(k)
    c = min(f + 1, len(values) - 1)
    d = k - f
    return values[f] + d * (values[c] - values[f])


def compute_stats(samples: list[float]) -> dict[str, float]:
    """Compute min, median, p95, and max in milliseconds."""
    sorted_ms = sorted(s * 1000.0 for s in samples)
    return {
        "min_ms": round(sorted_ms[0], 4),
        "median_ms": round(percentile(sorted_ms, 0.50), 4),
        "p95_ms": round(percentile(sorted_ms, 0.95), 4),
        "max_ms": round(sorted_ms[-1], 4),
    }


class TestVoiceLatencyBenchmarks:
    """Deterministic latency measurement suite for voice components."""

    def test_speech_formatter_latency_benchmarks(self) -> None:
        """Benchmark SpeechFormatter throughput on short, medium, and markdown text."""
        formatter = SpeechFormatter()

        short_text = "Hello, how can I help you today?"
        medium_text = (
            "Here is the daily weather update. The morning will be mostly cloudy with temperatures "
            "around 65 degrees. Expect clear skies by afternoon with a high of 75 degrees."
        )
        markdown_text = (
            "### System Report\n"
            "- **Status**: `HEALTHY` (100% uptime)\n"
            "- **CPU**: 14.5% across 8 cores\n"
            "- **Memory**: 6.2GB / 16.0GB (38.8% used)\n"
            "- **Network**: 1.2 MB/s in, 450 KB/s out\n\n"
            "For full logs, visit [dashboard](https://local.tom/dashboard) or check `/var/log/tom.log`."
        )

        iterations = 100

        # Short text
        short_durations: list[float] = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            _ = formatter.format(short_text)
            short_durations.append(time.perf_counter() - t0)
        short_stats = compute_stats(short_durations)

        # Medium text
        medium_durations: list[float] = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            _ = formatter.format(medium_text)
            medium_durations.append(time.perf_counter() - t0)
        medium_stats = compute_stats(medium_durations)

        # Markdown text
        markdown_durations: list[float] = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            _ = formatter.format(markdown_text)
            markdown_durations.append(time.perf_counter() - t0)
        markdown_stats = compute_stats(markdown_durations)

        print(f"\n[BENCHMARK] Formatter Short: {short_stats}")
        print(f"[BENCHMARK] Formatter Medium: {medium_stats}")
        print(f"[BENCHMARK] Formatter Markdown: {markdown_stats}")

        # Formatter should be sub-millisecond
        assert short_stats["median_ms"] < 1.0, f"Short formatter too slow: {short_stats}"
        assert medium_stats["median_ms"] < 2.0, f"Medium formatter too slow: {medium_stats}"
        assert markdown_stats["median_ms"] < 5.0, f"Markdown formatter too slow: {markdown_stats}"

    @pytest.mark.anyio
    async def test_end_to_end_mock_turn_pipeline_latency(self) -> None:
        """Measure full turn orchestration latency over 50 iterations."""
        engine = MagicMock(spec=EngineClient)
        engine.start_audio_capture = AsyncMock(return_value=AudioOperationResponse(success=True))
        engine.stop_audio_capture = AsyncMock(return_value=AudioOperationResponse(success=True))
        engine.get_captured_speech = AsyncMock(
            return_value=AudioSpeechResponse(
                samples=DUMMY_AUDIO,
                sample_rate=16000,
                channels=1,
                sample_count=len(DUMMY_AUDIO),
            )
        )
        engine.play_audio_buffer = AsyncMock(return_value=AudioOperationResponse(success=True))
        engine.stop_audio_playback = AsyncMock(return_value=AudioOperationResponse(success=True))

        stt = MockSTTProvider(transcript="Turn latency test")
        tts = MockTTSProvider()
        formatter = SpeechFormatter()

        async def immediate_agent(_: str) -> str:
            return "Orchestration test response."

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            formatter=formatter,
            agent_handler=immediate_agent,
        )

        durations: list[float] = []
        iterations = 50

        for _ in range(iterations):
            t0 = time.perf_counter()
            res = await pipeline.run_turn()
            t1 = time.perf_counter()
            assert res.state.value == "IDLE"
            durations.append(t1 - t0)

        stats = compute_stats(durations)
        print(f"\n[BENCHMARK] Turn Orchestration (50 iters): {stats}")
        # Software orchestration overhead (excluding heavy ML inference) should be well under 50ms
        assert stats["median_ms"] < 25.0, f"Pipeline orchestration overhead too high: {stats}"
        assert stats["p95_ms"] < 50.0, f"Pipeline P95 overhead too high: {stats}"

    @pytest.mark.anyio
    async def test_barge_in_response_latency(self) -> None:
        """Measure latency of handle_barge_in (playback stop + epoch invalidation + state transition)."""
        engine = MagicMock(spec=EngineClient)
        engine.stop_audio_playback = AsyncMock(return_value=AudioOperationResponse(success=True))
        engine.start_audio_capture = AsyncMock(return_value=AudioOperationResponse(success=True))
        engine.stop_audio_capture = AsyncMock(return_value=AudioOperationResponse(success=True))
        engine.get_captured_speech = AsyncMock(
            return_value=AudioSpeechResponse(
                samples=DUMMY_AUDIO,
                sample_rate=16000,
                channels=1,
                sample_count=len(DUMMY_AUDIO),
            )
        )

        playback_event = asyncio.Event()

        async def blocking_play(*_: Any, **__: Any) -> AudioOperationResponse:
            playback_event.set()
            await asyncio.sleep(1.0)
            return AudioOperationResponse(success=True)

        engine.play_audio_buffer = AsyncMock(side_effect=blocking_play)

        stt = MockSTTProvider(transcript="Test barge-in latency")
        tts = MockTTSProvider()

        pipeline = VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
            agent_handler=lambda p: "Long speech to interrupt",
        )

        barge_in_durations: list[float] = []
        iterations = 10

        for _ in range(iterations):
            playback_event.clear()
            turn_task = asyncio.create_task(pipeline.run_turn())
            await playback_event.wait()

            t0 = time.perf_counter()
            await pipeline.handle_barge_in()
            t1 = time.perf_counter()
            barge_in_durations.append(t1 - t0)

            try:
                await turn_task
            except (asyncio.CancelledError, Exception):
                pass
            await pipeline.reset()

        stats = compute_stats(barge_in_durations)
        print(f"\n[BENCHMARK] Barge-in Response (10 iters): {stats}")
        # Barge-in stop and state change should complete in under 10ms
        assert stats["median_ms"] < 10.0, f"Barge-in response latency too slow: {stats}"
