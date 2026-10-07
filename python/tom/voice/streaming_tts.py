"""Streaming TTS capability and pipelined incremental playback.

Phase 9 Iteration 9.3:
- ``StreamingTTSProvider`` is a capability interface layered on the closed
  Phase 6 ``TTSProvider`` boundary; the batch ``synthesize()`` contract is
  untouched.
- Sentences arriving from 9.2 ``SentenceStreamer`` are dispatched to one
  synthesis task each (bounded by ``max_concurrent_synthesis``), so synthesis
  of sentence N+1 overlaps playback of sentence N:

      Sentence 1 --synthesize--> playback ------------>
      Sentence 2 -------------synthesize--> playback -->
      Sentence 3 ----------------------synthesize--> ...

- PCM is emitted in sentence order and handed to the existing
  ``EngineClient.play_audio_buffer()`` / ``EngineClient.stop_audio_playback()``
  IPC calls (Decision 041: utterance-buffered audio, no streaming binary pipe).
- ``CancellationToken`` gives immediate halt: queued synthesis and playback are
  discarded, in-flight playback is abandoned, and ``stop_audio_playback()`` is
  issued exactly once.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tom.core.context import CancellationToken
from tom.schemas.voice import SynthesisRequest, SynthesisResult
from tom.telemetry.logging import get_logger
from tom.voice.audio_utils import pcm16_bytes_to_samples, samples_to_pcm16_bytes
from tom.voice.tts import TTSProvider

if TYPE_CHECKING:
    from tom.core.engine import EngineClient

logger = get_logger(__name__)

# Upper bound on how long stream_synthesize() waits for the final in-flight
# playback call to drain. Prevents an unresponsive engine from deadlocking the
# turn while leaving normal (sub-second) playback untouched.
_PLAYBACK_DRAIN_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class SynthesisChunk:
    """One synthesized sentence, ready for ordered playback.

    ``audio_data`` is 16-bit little-endian mono PCM (see ``audio_utils``).
    """

    sentence: str
    audio_data: bytes
    chunk_index: int
    is_last: bool


class StreamingTTSProvider(TTSProvider, ABC):
    """TTSProvider plus incremental synthesis of a sentence stream."""

    @abstractmethod
    def stream_synthesize(
        self,
        sentences: AsyncGenerator[str, None] | AsyncIterator[str],
        token: CancellationToken | None = None,
        *,
        playback_observer: Callable[[], None] | None = None,
    ) -> AsyncGenerator[SynthesisChunk, None]:
        """Yield ordered ``SynthesisChunk`` values for ``sentences``.

        Args:
            sentences: Ordered speakable sentences.
            token: Cooperative cancellation for this stream.
            playback_observer: Optional Phase 9.6 hook invoked once, immediately
                before the first PCM buffer is handed to the engine. Purely
                observational: implementations that do not play audio ignore it.
        """
        ...


# Queue markers. Distinct sentinels keep normal completion and cancellation
# distinguishable without extra per-chunk state.
_EOF = object()
_CANCELLED = object()


@dataclass(frozen=True)
class _Playable:
    """Chunk plus the PCM layout needed by ``EngineClient.play_audio_buffer``."""

    chunk: SynthesisChunk
    sample_rate: int
    channels: int


class IncrementalTTSStreamer(StreamingTTSProvider):
    """Pipelined sentence-at-a-time synthesis with ordered incremental playback.

        Wraps any batch ``TTSProvider`` (production: ``KokoroTTSProvider``; tests:
        ``MockTTSProvider`` or a fake) and, when supplied, an ``EngineClient`` for
        playback. Both are optional seams — omitting ``engine_client`` yields a
        synthesis-only pipeline.

        Execution model:

        * ``_produce_sentences`` pulls sentences from the upstream
          ``SentenceStreamer`` and creates one synthesis task per sentence, bounded
          by ``max_concurrent_synthesis`` so the worker pool stays small on a
          16-core CPU (plan §12.2).
        * ``stream_synthesize`` (the sequencer) awaits those tasks strictly in
          index order, so PCM is queued in sentence order regardless of which
          synthesis finishes first.
    * ``_play_pending`` consumes the ordered queue concurrently and issues
          ``EngineClient.play_audio_buffer()`` per chunk. It runs as a separate
          task, so a slow playback call never blocks the next synthesis.
        * ``playback_observer`` (Phase 9.6) is an optional zero-cost hook invoked
          immediately before the first ``play_audio_buffer()`` call, used for
          latency instrumentation. It changes no timing or ordering when unset.
    """

    def __init__(
        self,
        provider: TTSProvider,
        engine_client: EngineClient | None = None,
        *,
        voice: str | None = None,
        speed: float = 1.0,
        language: str | None = None,
        sample_rate: int = 24000,
        max_concurrent_synthesis: int = 2,
        playback_drain_timeout_s: float = _PLAYBACK_DRAIN_TIMEOUT_S,
    ) -> None:
        if max_concurrent_synthesis < 1:
            raise ValueError("max_concurrent_synthesis must be >= 1")
        self._provider = provider
        self._engine = engine_client
        self._voice = voice
        self._speed = speed
        self._language = language
        self._sample_rate = sample_rate
        self._max_concurrent = max_concurrent_synthesis
        self._drain_timeout_s = playback_drain_timeout_s
        self._executor: ThreadPoolExecutor | None = None

    # ------------------------------------------------------------------
    # TTSProvider interface (Phase 6 contract preserved)
    # ------------------------------------------------------------------

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        return await self._provider.synthesize(request)

    async def check_health(self) -> bool:
        return await self._provider.check_health()

    def get_model_info(self) -> dict[str, Any]:
        info = dict(self._provider.get_model_info())
        info["streaming"] = True
        info["max_concurrent_synthesis"] = self._max_concurrent
        info["playback"] = self._engine is not None
        return info

    # ------------------------------------------------------------------
    # Streaming interface
    # ------------------------------------------------------------------

    async def stop_playback(self) -> None:
        """Halt engine playback immediately (no-op without an engine client)."""
        if self._engine is None:
            return
        try:
            await self._engine.stop_audio_playback()
        except Exception as exc:
            logger.warning("streaming_tts_playback_stop_failed", error=str(exc))

    async def stream_synthesize(
        self,
        sentences: AsyncGenerator[str, None] | AsyncIterator[str],
        token: CancellationToken | None = None,
        *,
        playback_observer: Callable[[], None] | None = None,
    ) -> AsyncGenerator[SynthesisChunk, None]:
        """Synthesize ``sentences`` in order, pipelining synthesis against playback.

        On ``token`` cancellation the generator terminates promptly, every queued
        synthesis and playback unit is discarded, and
        ``EngineClient.stop_audio_playback()`` is issued once. A sentence whose
        synthesis fails is skipped rather than stalling the pipeline; the chunk
        stream then simply has no ``is_last`` marker for that sentence.
        """
        if token is not None and token.is_cancelled():
            return

        slots: asyncio.Queue[Any] = asyncio.Queue()
        out_queue: asyncio.Queue[Any] = asyncio.Queue()
        play_queue: asyncio.Queue[Any] = asyncio.Queue()

        producer = asyncio.create_task(
            self._produce_sentences(sentences, slots, token),
            name="tom.streaming_tts.produce",
        )
        sequencer = asyncio.create_task(
            self._sequence(slots, out_queue, play_queue, token),
            name="tom.streaming_tts.sequence",
        )
        player = (
            asyncio.create_task(
                self._play_pending(play_queue, token, playback_observer),
                name="tom.streaming_tts.playback",
            )
            if self._engine is not None
            else None
        )
        watcher = (
            asyncio.create_task(
                self._watch_cancellation(token, out_queue),
                name="tom.streaming_tts.cancel_watch",
            )
            if token is not None
            else None
        )

        drained = False
        try:
            while True:
                item = await out_queue.get()
                if item is _EOF or item is _CANCELLED:
                    drained = True
                    break
                yield item
        finally:
            cancelled = token is not None and token.is_cancelled()
            if drained and not cancelled and player is not None:
                await self._drain_playback(player)
            await self._shutdown(producer, sequencer, player, watcher)
            if cancelled:
                await self.stop_playback()

    # ------------------------------------------------------------------
    # Internal pipeline stages
    # ------------------------------------------------------------------

    async def _produce_sentences(
        self,
        sentences: AsyncGenerator[str, None] | AsyncIterator[str],
        slots: asyncio.Queue[Any],
        token: CancellationToken | None,
    ) -> None:
        """Dispatch one synthesis task per sentence, in order, on a bounded pool.

        Reads one sentence ahead so ``is_last`` is decided by the only stage
        that can know it (the upstream generator) rather than guessed by the
        consumer.
        """
        iterator = sentences.__aiter__()
        semaphore = asyncio.Semaphore(self._max_concurrent)
        lookahead: str = ""
        has_lookahead = False
        index = 0

        try:
            while True:
                if token is not None and token.is_cancelled():
                    break

                if has_lookahead:
                    sentence = lookahead
                else:
                    try:
                        sentence = await iterator.__anext__()
                    except StopAsyncIteration:
                        break

                try:
                    lookahead = await iterator.__anext__()
                    has_lookahead = True
                    is_last = False
                except StopAsyncIteration:
                    has_lookahead = False
                    is_last = True

                text = sentence.strip()
                if not text:
                    if is_last:
                        break
                    continue

                await semaphore.acquire()
                slots.put_nowait(
                    asyncio.create_task(
                        self._synthesize_chunk(index, text, is_last, semaphore),
                        name=f"tom.streaming_tts.synth.{index}",
                    )
                )
                index += 1
                if is_last:
                    break
        finally:
            slots.put_nowait(_EOF)

    async def _synthesize_chunk(
        self,
        index: int,
        sentence: str,
        is_last: bool,
        semaphore: asyncio.Semaphore,
    ) -> _Playable | None:
        """Synthesize one sentence off the event loop; None when synthesis fails."""
        try:
            request = SynthesisRequest(
                text=sentence,
                voice=self._voice,
                speed=self._speed,
                language=self._language,
                sample_rate=self._sample_rate,
            )
            result = await self._dispatch_synthesis(request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "streaming_tts_synthesis_failed",
                chunk_index=index,
                error=str(exc),
            )
            return None
        finally:
            semaphore.release()

        return _Playable(
            chunk=SynthesisChunk(
                sentence=sentence,
                audio_data=samples_to_pcm16_bytes(result.samples),
                chunk_index=index,
                is_last=is_last,
            ),
            sample_rate=result.sample_rate,
            channels=result.channels,
        )

    async def _dispatch_synthesis(self, request: SynthesisRequest) -> SynthesisResult:
        """Run provider synthesis off the event loop.

        Async providers already offload internally (``asyncio.to_thread``);
        synchronous provider callables are pushed onto a dedicated bounded
        thread pool instead of blocking the loop.
        """
        if inspect.iscoroutinefunction(self._provider.synthesize):
            return await self._provider.synthesize(request)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._thread_pool(),
            functools.partial(self._provider.synthesize, request),
        )

    def _thread_pool(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_concurrent,
                thread_name_prefix="tom-tts",
            )
        return self._executor

    async def _sequence(
        self,
        slots: asyncio.Queue[Any],
        out_queue: asyncio.Queue[Any],
        play_queue: asyncio.Queue[Any],
        token: CancellationToken | None,
    ) -> None:
        """Await synthesis tasks strictly in index order and fan out the results."""
        try:
            while True:
                slot = await slots.get()
                if slot is _EOF:
                    break
                playable = await slot
                if playable is None:
                    continue
                if token is not None and token.is_cancelled():
                    break
                out_queue.put_nowait(playable.chunk)
                if self._engine is not None:
                    play_queue.put_nowait(playable)
        finally:
            if self._engine is not None:
                play_queue.put_nowait(_EOF)
            out_queue.put_nowait(_EOF)

    async def _play_pending(
        self,
        play_queue: asyncio.Queue[Any],
        token: CancellationToken | None,
        playback_observer: Callable[[], None] | None = None,
    ) -> None:
        """Play ordered PCM through EngineClient, one chunk at a time."""
        if self._engine is None:
            return
        observed = False
        while True:
            item = await play_queue.get()
            if item is _EOF:
                return
            if token is not None and token.is_cancelled():
                return
            if not observed and playback_observer is not None:
                observed = True
                playback_observer()
            chunk = item.chunk
            await self._engine.play_audio_buffer(
                samples=pcm16_bytes_to_samples(chunk.audio_data),
                sample_rate=item.sample_rate,
                channels=item.channels,
            )

    async def _watch_cancellation(
        self,
        token: CancellationToken | None,
        out_queue: asyncio.Queue[Any],
    ) -> None:
        """Unblock the sequencer the moment the shared token is cancelled."""
        if token is None:
            return
        await token.event.wait()
        out_queue.put_nowait(_CANCELLED)

    async def _drain_playback(self, player: asyncio.Task[Any]) -> None:
        """Let the player finish already-queued chunks before the turn returns."""
        try:
            await asyncio.wait_for(asyncio.shield(player), timeout=self._drain_timeout_s)
        except Exception as exc:
            logger.warning("streaming_tts_playback_drain_timeout", error=str(exc))

    @staticmethod
    async def _shutdown(*tasks: asyncio.Task[Any] | None) -> None:
        """Cancel and reap every internal task; never leak or raise."""
        pending = [task for task in tasks if task is not None and not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


__all__ = [
    "IncrementalTTSStreamer",
    "StreamingTTSProvider",
    "SynthesisChunk",
]
