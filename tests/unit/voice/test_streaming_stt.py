"""Unit tests for Phase 9 Iteration 9.1: Streaming STT.

Deterministic and offline. No microphone, network, GPU, or model weights.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Coroutine, Sequence
from typing import Any

from tom.core.context import CancellationToken
from tom.schemas.voice import TranscriptionRequest, TranscriptionResult
from tom.voice.audio_utils import samples_to_pcm16_bytes
from tom.voice.events import AudioFrameEvent, SpeechEndEvent, SpeechStartEvent
from tom.voice.streaming_stt import (
    FasterWhisperStreamingAdapter,
    PartialTranscript,
    stabilize_transcript,
)
from tom.voice.stt import MockSTTProvider, STTProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


FRAME_SAMPLES = 320  # 20 ms at 16 kHz
FRAME_PCM = samples_to_pcm16_bytes([0.1] * FRAME_SAMPLES)


class GrowingSTTProvider(STTProvider):
    """Returns longer prefixes of a transcript as more PCM arrives."""

    def __init__(self, words: Sequence[str] = ("hello", "world", "there")) -> None:
        self._words = list(words)
        self.calls = 0

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        self.calls += 1
        n = len(request.samples) if isinstance(request.samples, list) else 0
        # One word per 20 ms frame (320 samples).
        word_count = max(1, min(n // FRAME_SAMPLES, len(self._words)))
        text = " ".join(self._words[:word_count])
        return TranscriptionResult(
            text=text,
            language="en",
            confidence=0.9,
            duration_s=n / 16000,
            provider="GrowingSTTProvider",
        )

    async def check_health(self) -> bool:
        return True

    def get_model_info(self) -> dict[str, Any]:
        return {"provider": "GrowingSTTProvider"}


class DuplicateSTTProvider(STTProvider):
    """Always returns the same hypothesis (dedup target)."""

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        return TranscriptionResult(text="  hello   world  ", confidence=1.0, provider="dup")

    async def check_health(self) -> bool:
        return True

    def get_model_info(self) -> dict[str, Any]:
        return {"provider": "dup"}


async def pcm_chunks(n: int, pcm: bytes = FRAME_PCM) -> AsyncGenerator[bytes, None]:
    for _ in range(n):
        yield pcm


async def collect(
    adapter: FasterWhisperStreamingAdapter,
    stream: AsyncGenerator[bytes, None],
    token: CancellationToken | None = None,
) -> list[PartialTranscript]:
    out: list[PartialTranscript] = []
    async for item in adapter.stream_transcribe(stream, token=token):
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# stabilize_transcript
# ---------------------------------------------------------------------------


def test_stabilize_skips_duplicate_and_shorter_flicker() -> None:
    assert stabilize_transcript("", "hello", is_final=False) == "hello"
    assert stabilize_transcript("hello", "hello", is_final=False) is None
    assert stabilize_transcript("hello world", "hello", is_final=False) is None
    assert stabilize_transcript("hello", "hello world", is_final=False) == "hello world"
    assert stabilize_transcript("hello", "  hello   world  ", is_final=False) == "hello world"
    assert stabilize_transcript("hello", "", is_final=True) == ""


# ---------------------------------------------------------------------------
# Streaming adapter
# ---------------------------------------------------------------------------


def test_progressive_partials_then_final_matching_full_text() -> None:
    async def _test() -> None:
        inner = GrowingSTTProvider()
        adapter = FasterWhisperStreamingAdapter(
            inner,
            min_window_bytes=len(FRAME_PCM),
            hop_bytes=len(FRAME_PCM),
        )
        results = await collect(adapter, pcm_chunks(3))
        partials = [r for r in results if not r.is_final]
        finals = [r for r in results if r.is_final]
        assert [p.text for p in partials] == ["hello", "hello world", "hello world there"]
        assert len(finals) == 1
        assert finals[0].text == "hello world there"
        assert finals[0].text == partials[-1].text

    run_async(_test())


def test_partials_are_deduplicated() -> None:
    async def _test() -> None:
        adapter = FasterWhisperStreamingAdapter(
            DuplicateSTTProvider(),
            min_window_bytes=len(FRAME_PCM),
            hop_bytes=len(FRAME_PCM),
        )
        results = await collect(adapter, pcm_chunks(5))
        partials = [r for r in results if not r.is_final]
        finals = [r for r in results if r.is_final]
        assert [p.text for p in partials] == ["hello world"]
        assert len(finals) == 1
        assert finals[0].text == "hello world"

    run_async(_test())


def test_cancellation_stops_transcription_and_discards_queue() -> None:
    async def _test() -> None:
        inner = GrowingSTTProvider()
        adapter = FasterWhisperStreamingAdapter(
            inner,
            min_window_bytes=len(FRAME_PCM),
            hop_bytes=len(FRAME_PCM),
        )
        token = CancellationToken()

        async def gated() -> AsyncGenerator[bytes, None]:
            yield FRAME_PCM
            token.cancel()
            yield FRAME_PCM
            yield FRAME_PCM

        results = await collect(adapter, gated(), token=token)
        assert all(not r.is_final for r in results)
        assert inner.calls <= 1

    run_async(_test())


def test_first_partial_under_250ms_offline() -> None:
    async def _test() -> None:
        adapter = FasterWhisperStreamingAdapter(
            MockSTTProvider(transcript="ready"),
            min_window_bytes=len(FRAME_PCM),
            hop_bytes=len(FRAME_PCM),
        )
        t0 = time.perf_counter()
        first_ms: float | None = None
        async for item in adapter.stream_transcribe(pcm_chunks(2)):
            if not item.is_final and first_ms is None:
                first_ms = (time.perf_counter() - t0) * 1000
                break
        assert first_ms is not None
        assert first_ms < 250.0, f"first partial at {first_ms:.1f} ms"

    run_async(_test())


def test_voice_events_finalize_on_speech_end_not_internal_vad() -> None:
    async def _test() -> None:
        inner = GrowingSTTProvider()
        adapter = FasterWhisperStreamingAdapter(
            inner,
            min_window_bytes=len(FRAME_PCM),
            hop_bytes=len(FRAME_PCM),
        )

        async def events() -> AsyncGenerator[Any, None]:
            yield SpeechStartEvent()
            for i in range(3):
                yield AudioFrameEvent(
                    samples=[0.1] * FRAME_SAMPLES,
                    pcm_bytes=FRAME_PCM,
                    frame_index=i,
                    is_speech=True,
                )
            yield SpeechEndEvent(
                samples=[0.1] * FRAME_SAMPLES * 3,
                pcm_bytes=FRAME_PCM * 3,
                duration_s=0.06,
                frame_count=3,
            )

        results: list[PartialTranscript] = []
        async for item in adapter.stream_transcribe_events(events()):
            results.append(item)
        finals = [r for r in results if r.is_final]
        assert len(finals) == 1
        assert finals[0].text == "hello world there"

    run_async(_test())


def test_adapter_delegates_batch_transcribe() -> None:
    async def _test() -> None:
        inner = MockSTTProvider(transcript="batch")
        adapter = FasterWhisperStreamingAdapter(inner)
        result = await adapter.transcribe(
            TranscriptionRequest(samples=[0.0] * 160, sample_rate=16000)
        )
        assert result.text == "batch"
        assert await adapter.check_health() is True
        assert adapter.get_model_info()["streaming"] is True

    run_async(_test())
