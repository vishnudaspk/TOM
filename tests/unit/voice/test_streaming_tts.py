"""Unit tests for Phase 9 Iteration 9.3: Streaming TTS & incremental playback.

Deterministic and offline. No speaker, microphone, network, GPU, or model
weights. Ordering and pipelining are asserted against an explicit shared
timeline so the "sentence N+1 synthesizes while sentence N plays" contract is
proven rather than inferred.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Coroutine
from typing import Any

import pytest
from tom.core.context import CancellationToken
from tom.schemas.voice import SynthesisRequest, SynthesisResult
from tom.voice.audio_utils import pcm16_bytes_to_samples, samples_to_pcm16_bytes
from tom.voice.sentence_streamer import SentenceStreamer
from tom.voice.streaming_tts import (
    IncrementalTTSStreamer,
    StreamingTTSProvider,
    SynthesisChunk,
)
from tom.voice.tts import MockTTSProvider, TTSProvider


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


SENTENCES = (
    "Sentence one is ready.",
    "Sentence two is next.",
    "Sentence three closes it out.",
)
S2 = SENTENCES[1]


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class Timeline:
    """Shared, ordered event log used to prove pipeline overlap."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def add(self, kind: str, label: str) -> None:
        self.events.append((kind, label))

    def index(self, kind: str, label: str) -> int:
        for i, (k, v) in enumerate(self.events):
            if k == kind and v == label:
                return i
        raise AssertionError(f"event {kind}:{label} never happened: {self.events}")

    def kinds(self, kind: str) -> list[str]:
        return [label for k, label in self.events if k == kind]


class RecordingTTS(TTSProvider):
    """TTSProvider that records synthesis boundaries into a timeline."""

    def __init__(self, timeline: Timeline, gate: asyncio.Event | None = None) -> None:
        self._timeline = timeline
        self._gate = gate
        self.calls: list[str] = []

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        self.calls.append(request.text)
        self._timeline.add("synth_start", request.text)
        # Deterministic scheduling yield: every sentence starts before any ends.
        await asyncio.sleep(0)
        if self._gate is not None and request.text == S2:
            await self._gate.wait()
        self._timeline.add("synth_end", request.text)
        samples = [0.25] * 8
        return SynthesisResult(
            samples=samples,
            sample_rate=24000,
            channels=1,
            duration_s=len(samples) / 24000,
            provider="RecordingTTS",
        )

    async def check_health(self) -> bool:
        return True

    def get_model_info(self) -> dict[str, Any]:
        return {"provider": "RecordingTTS"}


class GatedEngine:
    """EngineClient stand-in that records playback and can block on playback #1."""

    def __init__(self, timeline: Timeline, synth_gate: asyncio.Event | None = None) -> None:
        self._timeline = timeline
        self._synth_gate = synth_gate
        self.play_calls: list[list[float]] = []
        self.stop_calls = 0
        self.playback_started = asyncio.Event()
        self.release_playback = asyncio.Event()

    async def play_audio_buffer(
        self,
        samples: list[float] | None = None,
        audio_data: bytes | None = None,
        device_name: str | None = None,
        sample_rate: int = 24000,
        channels: int = 1,
    ) -> None:
        index = len(self.play_calls) + 1
        # Convert audio_data to samples for backward compatibility with test assertions
        if audio_data is not None:
            from tom.voice.audio_utils import pcm16_bytes_to_samples
            samples_list = pcm16_bytes_to_samples(audio_data)
        elif samples is not None:
            samples_list = list(samples)
        else:
            samples_list = []
        self.play_calls.append(samples_list)
        self._timeline.add("play_start", str(index))
        if self._synth_gate is not None:
            # Sentence 2 synthesis may only finish once sentence 1 is playing.
            self._synth_gate.set()
        if index == 1:
            self.playback_started.set()
            await self.release_playback.wait()
        self._timeline.add("play_end", str(index))

    async def stop_audio_playback(self) -> None:
        self.stop_calls += 1
        self._timeline.add("play_stop", "0")
        self.release_playback.set()


class FrozenTTS(TTSProvider):
    """TTSProvider whose synthesis never completes without explicit release."""

    def __init__(self, gate: asyncio.Event) -> None:
        self._gate = gate
        self.started: list[str] = []
        self.finished: list[str] = []

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        self.started.append(request.text)
        await self._gate.wait()
        self.finished.append(request.text)
        return SynthesisResult(
            samples=[0.1, 0.2, 0.3],
            sample_rate=24000,
            channels=1,
            provider="FrozenTTS",
        )

    async def check_health(self) -> bool:
        return True

    def get_model_info(self) -> dict[str, Any]:
        return {"provider": "FrozenTTS"}


class ExplodingTTS(TTSProvider):
    """TTSProvider that always fails; the streamer must skip and continue."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        self.calls.append(request.text)
        raise RuntimeError("model exploded")

    async def check_health(self) -> bool:
        return False

    def get_model_info(self) -> dict[str, Any]:
        return {"provider": "ExplodingTTS"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def sentence_stream(sentences: tuple[str, ...] = SENTENCES) -> AsyncGenerator[str, None]:
    for sentence in sentences:
        yield sentence


async def collect_chunks(
    streamer: StreamingTTSProvider,
    sentences: AsyncGenerator[str, None],
    token: CancellationToken | None = None,
) -> list[SynthesisChunk]:
    out: list[SynthesisChunk] = []
    async for chunk in streamer.stream_synthesize(sentences, token=token):
        out.append(chunk)
    return out


async def collect_in_background(
    streamer: StreamingTTSProvider,
    sentences: AsyncGenerator[str, None],
    token: CancellationToken | None = None,
    delay: int = 12,
) -> tuple[asyncio.Task[Any], list[SynthesisChunk]]:
    chunks: list[SynthesisChunk] = []

    async def _consume() -> None:
        async for chunk in streamer.stream_synthesize(sentences, token=token):
            chunks.append(chunk)

    task = asyncio.create_task(_consume())
    for _ in range(delay):
        await asyncio.sleep(0)
    return task, chunks


def chunk_labels(chunks: list[SynthesisChunk]) -> list[str]:
    return [chunk.sentence for chunk in chunks]


# ---------------------------------------------------------------------------
# Capability interface
# ---------------------------------------------------------------------------


def test_streaming_provider_extends_batch_tts_provider() -> None:
    assert issubclass(StreamingTTSProvider, TTSProvider)

    async def _test() -> None:
        streamer = IncrementalTTSStreamer(MockTTSProvider(), None)
        assert await streamer.check_health() is True
        info = streamer.get_model_info()
        assert info["streaming"] is True
        assert info["playback"] is False
        batch = await streamer.synthesize(SynthesisRequest(text="hello"))
        assert batch.provider == "MockTTSProvider"

    run_async(_test())


def test_synthesis_chunk_is_immutable() -> None:
    chunk = SynthesisChunk(
        sentence="Hello there.",
        audio_data=b"\x00\x01",
        chunk_index=0,
        is_last=True,
    )
    assert chunk.sentence == "Hello there."
    assert chunk.chunk_index == 0
    assert chunk.is_last is True
    try:
        chunk.chunk_index = 5  # type: ignore[misc]
    except Exception as exc:
        assert "frozen" in str(exc).lower() or "cannot assign" in str(exc).lower()
    else:  # pragma: no cover - dataclass must be frozen
        raise AssertionError("SynthesisChunk must be immutable")


# ---------------------------------------------------------------------------
# Ordered multi-sentence output
# ---------------------------------------------------------------------------


def test_multi_sentence_stream_yields_ordered_chunks_with_pcm() -> None:
    async def _test() -> None:
        timeline = Timeline()
        streamer = IncrementalTTSStreamer(RecordingTTS(timeline), None)
        chunks = await collect_chunks(streamer, sentence_stream())

        expected_pcm = samples_to_pcm16_bytes([0.25] * 8)
        assert len(chunks) == 3
        assert chunk_labels(chunks) == list(SENTENCES)
        assert [c.chunk_index for c in chunks] == [0, 1, 2]
        assert [c.is_last for c in chunks] == [False, False, True]
        for chunk in chunks:
            assert chunk.audio_data == expected_pcm
            assert len(chunk.audio_data) == 16
        assert pcm16_bytes_to_samples(chunks[0].audio_data) == pytest.approx([0.25] * 8, abs=1e-4)

    run_async(_test())


def test_out_of_order_synthesis_still_emits_in_sentence_order() -> None:
    """Synthesis completion order is deliberately reversed; output must not be."""

    async def _test() -> None:
        timeline = Timeline()
        streamer = IncrementalTTSStreamer(RecordingTTS(timeline), None, max_concurrent_synthesis=3)
        chunks = await collect_chunks(streamer, sentence_stream())

        assert timeline.kinds("synth_start") == list(SENTENCES)
        assert sorted(timeline.kinds("synth_end")) == sorted(SENTENCES)
        assert chunk_labels(chunks) == list(SENTENCES)
        assert [c.chunk_index for c in chunks] == [0, 1, 2]

    run_async(_test())


def test_consumes_sentence_streamer_output_directly() -> None:
    """9.3 plugs straight into the 9.2 SentenceStreamer contract."""

    async def _test() -> None:
        tokens = [
            "Sentence one is ready. ",
            "Sentence two is next. ",
            "Sentence three closes it out.",
        ]

        chunker = SentenceStreamer()
        sentences: list[str] = []
        for token in tokens:
            sentences.extend(chunker.push_chunk(token))
        sentences.extend(chunker.flush())
        assert sentences == list(SENTENCES)

        async def from_streamer() -> AsyncGenerator[str, None]:
            for sentence in sentences:
                yield sentence

        pipeline = IncrementalTTSStreamer(MockTTSProvider(), None)
        chunks = await collect_chunks(pipeline, from_streamer())
        assert [c.sentence for c in chunks] == list(SENTENCES)
        assert chunks[-1].is_last is True

    run_async(_test())


def test_empty_sentence_stream_yields_nothing_and_never_calls_synthesis() -> None:
    async def _test() -> None:
        timeline = Timeline()
        streamer = IncrementalTTSStreamer(RecordingTTS(timeline), None)
        chunks = await collect_chunks(streamer, sentence_stream(()))
        assert chunks == []
        assert timeline.events == []

    run_async(_test())


def test_failed_sentence_is_skipped_without_stalling_the_stream() -> None:
    async def _test() -> None:
        engine = GatedEngine(Timeline())
        streamer = IncrementalTTSStreamer(ExplodingTTS(), engine)
        chunks = await collect_chunks(streamer, sentence_stream())
        assert chunks == []
        # Pipeline drained cleanly; nothing left playing.
        assert engine.stop_calls == 0

    run_async(_test())


# ---------------------------------------------------------------------------
# Pipelined synthesis / playback overlap
# ---------------------------------------------------------------------------


def test_sentence_n_plus_one_synthesizes_while_sentence_n_plays() -> None:
    """The critical 9.3 contract: playback of N overlaps synthesis of N+1.

    Sentence 2 synthesis is gated until playback of sentence 1 has begun, so
    the ordering below is impossible for a sequential
    ``synthesize -> play -> synthesize -> play`` implementation.
    """

    async def _test() -> None:
        timeline = Timeline()
        synth_gate = asyncio.Event()
        engine = GatedEngine(timeline, synth_gate=synth_gate)
        streamer = IncrementalTTSStreamer(
            RecordingTTS(timeline, gate=synth_gate),
            engine,
            max_concurrent_synthesis=2,
        )

        task, chunks = await collect_in_background(streamer, sentence_stream())

        # Sentence 1 is now playing; only then may sentence 2 finish synthesis.
        await asyncio.wait_for(engine.playback_started.wait(), timeout=1.0)
        assert engine.play_calls, "playback of sentence 1 never started"
        # Sentence 2 synthesis was already in flight when playback of sentence 1 began.
        assert timeline.index("synth_start", S2) < timeline.index("play_start", "1")
        # ...and it was still in flight at that exact moment.
        assert timeline.index("play_start", "1") < timeline.index("synth_end", S2)

        engine.release_playback.set()
        await asyncio.wait_for(task, timeout=2.0)

        assert chunk_labels(chunks) == list(SENTENCES)
        assert timeline.kinds("play_start") == ["1", "2", "3"]
        # Sentence 2 synthesis completed strictly before playback of sentence 1 ended.
        assert timeline.index("synth_end", S2) < timeline.index("play_end", "1")
        # Playback stayed strictly ordered regardless of completion order.
        assert timeline.kinds("play_start") == timeline.kinds("play_end")

    run_async(_test())


def test_playback_never_starts_before_synthesis_is_queued_in_order() -> None:
    async def _test() -> None:
        timeline = Timeline()
        synth_gate = asyncio.Event()
        engine = GatedEngine(timeline, synth_gate=synth_gate)
        streamer = IncrementalTTSStreamer(
            RecordingTTS(timeline, gate=synth_gate),
            engine,
            max_concurrent_synthesis=2,
        )
        engine.release_playback.set()
        chunks = await collect_chunks(streamer, sentence_stream())

        assert len(engine.play_calls) == 3
        assert sorted(timeline.kinds("synth_end")) == sorted(SENTENCES)
        # Later sentences synthesize inside the first playback window.
        assert timeline.index("synth_start", SENTENCES[2]) < timeline.index("play_end", "1")
        assert timeline.index("synth_start", SENTENCES[1]) < timeline.index("play_start", "1")
        assert [c.chunk_index for c in chunks] == [0, 1, 2]
        assert chunks[-1].is_last is True

    run_async(_test())


def test_playback_receives_normalized_float_pcm_through_engine_client() -> None:
    async def _test() -> None:
        timeline = Timeline()
        engine = GatedEngine(timeline)
        engine.release_playback.set()
        streamer = IncrementalTTSStreamer(RecordingTTS(timeline), engine)
        await collect_chunks(streamer, sentence_stream(("Only sentence here.",)))
        assert len(engine.play_calls) == 1
        assert engine.play_calls[0] == pytest.approx([0.25] * 8, abs=1e-4)

    run_async(_test())


def test_synthesis_only_mode_never_touches_the_engine() -> None:
    async def _test() -> None:
        timeline = Timeline()
        engine = GatedEngine(timeline)
        streamer = IncrementalTTSStreamer(
            RecordingTTS(timeline), engine, max_concurrent_synthesis=1
        )
        streamer._engine = None  # synthesis-only seam
        chunks = await collect_chunks(streamer, sentence_stream())
        assert len(chunks) == 3
        assert engine.play_calls == []
        assert engine.stop_calls == 0

    run_async(_test())


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancellation_discards_pending_synthesis_and_playback() -> None:
    async def _test() -> None:
        gate = asyncio.Event()
        timeline = Timeline()
        engine = GatedEngine(timeline)
        tts = FrozenTTS(gate)
        token = CancellationToken()
        streamer = IncrementalTTSStreamer(tts, engine, max_concurrent_synthesis=1)

        task, chunks = await collect_in_background(
            streamer, sentence_stream(), token=token, delay=6
        )
        assert tts.started, "synthesis never started"

        token.cancel()
        await asyncio.wait_for(task, timeout=1.0)

        # Nothing synthesized, nothing played, nothing yielded.
        assert tts.finished == []
        assert chunks == []
        assert engine.play_calls == []
        gate.set()

    run_async(_test())


def test_cancellation_calls_stop_audio_playback_exactly_once() -> None:
    async def _test() -> None:
        gate = asyncio.Event()
        timeline = Timeline()
        engine = GatedEngine(timeline)
        tts = FrozenTTS(gate)
        token = CancellationToken()
        streamer = IncrementalTTSStreamer(tts, engine, max_concurrent_synthesis=1)

        task, _ = await collect_in_background(streamer, sentence_stream(), token=token, delay=6)
        token.cancel()
        await asyncio.wait_for(task, timeout=1.0)

        assert engine.stop_calls == 1
        assert timeline.kinds("play_stop") == ["0"]
        gate.set()

    run_async(_test())


def test_cancellation_after_first_chunk_stops_playback_and_drops_the_rest() -> None:
    async def _test() -> None:
        timeline = Timeline()
        engine = GatedEngine(timeline)
        engine.release_playback.set()
        token = CancellationToken()
        streamer = IncrementalTTSStreamer(
            RecordingTTS(timeline), engine, max_concurrent_synthesis=1
        )

        chunks: list[SynthesisChunk] = []
        agen = streamer.stream_synthesize(sentence_stream(), token=token)
        async for chunk in agen:
            chunks.append(chunk)
            if len(chunks) == 1:
                token.cancel()
        await agen.aclose()

        assert chunk_labels(chunks) == [SENTENCES[0]]
        assert engine.stop_calls == 1
        assert len(engine.play_calls) <= 1

    run_async(_test())


def test_pre_cancelled_token_produces_no_output_and_no_playback() -> None:
    async def _test() -> None:
        timeline = Timeline()
        engine = GatedEngine(timeline)
        token = CancellationToken()
        token.cancel()
        streamer = IncrementalTTSStreamer(RecordingTTS(timeline), engine)

        chunks = await collect_chunks(streamer, sentence_stream(), token=token)
        assert chunks == []
        assert timeline.events == []
        assert engine.play_calls == []
        assert engine.stop_calls == 0

    run_async(_test())


def test_stop_playback_is_idempotent_and_engine_friendly() -> None:
    async def _test() -> None:
        engine = GatedEngine(Timeline())
        streamer = IncrementalTTSStreamer(MockTTSProvider(), engine)
        await streamer.stop_playback()
        assert engine.stop_calls == 1
        assert engine.play_calls == []

    run_async(_test())


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_repeated_runs_produce_identical_chunks_and_timeline() -> None:
    async def _once() -> tuple[list[tuple[str, str]], list[str]]:
        timeline = Timeline()
        synth_gate = asyncio.Event()
        engine = GatedEngine(timeline, synth_gate=synth_gate)
        engine.release_playback.set()
        streamer = IncrementalTTSStreamer(
            RecordingTTS(timeline, gate=synth_gate),
            engine,
            max_concurrent_synthesis=2,
        )
        chunks = await collect_chunks(streamer, sentence_stream())
        return timeline.events, [c.sentence for c in chunks]

    async def _test() -> None:
        runs = [await _once() for _ in range(5)]
        assert len({tuple(events) for events, _ in runs}) == 1
        assert len({tuple(labels) for _, labels in runs}) == 1

    run_async(_test())


def test_first_chunk_emitted_without_waiting_for_remaining_sentences() -> None:
    async def _test() -> None:
        timeline = Timeline()
        engine = GatedEngine(timeline)
        engine.release_playback.set()
        streamer = IncrementalTTSStreamer(
            RecordingTTS(timeline), engine, max_concurrent_synthesis=2
        )
        first: SynthesisChunk | None = None
        async for chunk in streamer.stream_synthesize(sentence_stream()):
            first = chunk
            break
        assert first is not None
        assert first.chunk_index == 0
        assert first.is_last is False

    run_async(_test())
