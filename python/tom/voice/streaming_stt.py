"""Streaming STT capability on top of the existing STTProvider boundary.

Phase 9 Iteration 9.1:
- VAD (9.0) owns utterance start/end; this module does not endpoint.
- Rolling-window transcription of delimited PCM with partial stabilization.
- Chunk inference stays off the event loop via the inner provider's
  asyncio.to_thread path (FasterWhisperSTTProvider) or a direct await (mocks).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from typing import Any

from tom.core.context import CancellationToken
from tom.schemas.voice import TranscriptionRequest, TranscriptionResult
from tom.voice.audio_utils import pcm16_bytes_to_samples
from tom.voice.events import AudioFrameEvent, SpeechEndEvent, VoiceEvent
from tom.voice.stt import STTProvider

# 100 ms of 16 kHz 16-bit mono PCM — first partial stays well under the 250 ms offline gate.
_DEFAULT_MIN_WINDOW_BYTES = 3200
_DEFAULT_HOP_BYTES = 3200


@dataclass(frozen=True)
class PartialTranscript:
    """Incremental or finalized hypothesis for one VAD-delimited utterance."""

    text: str
    is_final: bool
    confidence: float


def stabilize_transcript(
    previous: str,
    candidate: str,
    *,
    is_final: bool,
) -> str | None:
    """Return text to emit, or None if the candidate is a duplicate/flicker.

    Finals always emit (empty string allowed). Partials skip blanks, exact
    duplicates, and shorter prefix regressions of the last emitted text.
    """
    text = " ".join(candidate.split())
    if is_final:
        return text
    if not text or text == previous:
        return None
    if previous and len(text) < len(previous) and previous.startswith(text):
        return None
    return text


class StreamingSTTProvider(STTProvider, ABC):
    """STTProvider plus incremental transcription of a delimited PCM stream."""

    @abstractmethod
    def stream_transcribe(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        token: CancellationToken | None = None,
    ) -> AsyncGenerator[PartialTranscript, None]:
        """Yield partials while ``audio_stream`` is open; one final when it ends.

        Stream end is the utterance boundary (from 9.0 VAD). Do not add a
        second endpointer here.
        """
        ...


class FasterWhisperStreamingAdapter(StreamingSTTProvider):
    """Rolling-window streaming adapter around any batch ``STTProvider``.

    Named for the production FasterWhisper path; tests inject ``MockSTTProvider``
    (or a growing fake) with no model download.

    Supports two modes:
    - rolling: rolling-window transcription with partials (legacy behavior)
    - final_only: buffer all audio during speech, single decode at SpeechEnd
    """

    def __init__(
        self,
        provider: STTProvider,
        *,
        sample_rate: int = 16000,
        min_window_bytes: int = _DEFAULT_MIN_WINDOW_BYTES,
        hop_bytes: int = _DEFAULT_HOP_BYTES,
        mode: Literal["final_only", "rolling"] = "final_only",
    ) -> None:
        self._provider = provider
        self._sample_rate = sample_rate
        self._min_window_bytes = min_window_bytes
        self._hop_bytes = hop_bytes
        self._mode = mode
        self._partial_decode_count = 0

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        return await self._provider.transcribe(request)

    async def check_health(self) -> bool:
        return await self._provider.check_health()

    def get_model_info(self) -> dict[str, Any]:
        info = dict(self._provider.get_model_info())
        info["streaming"] = True
        return info

    async def stream_transcribe(
        self,
        audio_stream: AsyncGenerator[bytes, None] | AsyncIterator[bytes],
        token: CancellationToken | None = None,
        *,
        on_decode_start: Callable[[], None] | None = None,
        on_decode_end: Callable[[], None] | None = None,
    ) -> AsyncGenerator[PartialTranscript, None]:
        buf = bytearray()
        since_last = 0
        last_emitted = ""
        first_partial_emitted = False

        if self._mode == "final_only":
            # Final-only mode: buffer all audio, single decode at end
            async for chunk in audio_stream:
                if token is not None and token.is_cancelled():
                    buf.clear()
                    return
                if not chunk:
                    continue
                buf.extend(chunk)

            if token is not None and token.is_cancelled():
                buf.clear()
                return

            if on_decode_start is not None:
                on_decode_start()
            result = await self._transcribe_pcm(bytes(buf)) if buf else None
            if on_decode_end is not None:
                on_decode_end()
            buf.clear()
            candidate = result.text if result is not None else ""
            confidence = result.confidence if result is not None else 1.0
            final_text = candidate.strip()
            yield PartialTranscript(text=final_text, is_final=True, confidence=confidence)
            return

        # Rolling mode (legacy behavior)
        async for chunk in audio_stream:
            if token is not None and token.is_cancelled():
                buf.clear()
                return
            if not chunk:
                continue
            buf.extend(chunk)
            since_last += len(chunk)
            if len(buf) < self._min_window_bytes or since_last < self._hop_bytes:
                continue
            since_last = 0
            if token is not None and token.is_cancelled():
                buf.clear()
                return
            if on_decode_start is not None:
                on_decode_start()
            partial = await self._window_partial(buf, last_emitted)
            if on_decode_end is not None:
                on_decode_end()
            if partial is not None:
                last_emitted = partial.text
                if not first_partial_emitted and partial.text.strip():
                    first_partial_emitted = True
                    # Mark first partial when first non-empty text is actually yielded
                    # This is done by the caller via the latency tracer callback
                yield partial

        if token is not None and token.is_cancelled():
            buf.clear()
            return

        if on_decode_start is not None:
            on_decode_start()
        result = await self._transcribe_pcm(bytes(buf)) if buf else None
        if on_decode_end is not None:
            on_decode_end()
        buf.clear()
        candidate = result.text if result is not None else last_emitted
        confidence = result.confidence if result is not None else 1.0
        final_text = stabilize_transcript(last_emitted, candidate, is_final=True)
        if final_text is None:
            final_text = last_emitted
        yield PartialTranscript(text=final_text, is_final=True, confidence=confidence)

    async def stream_transcribe_events(
        self,
        events: AsyncIterator[VoiceEvent],
        token: CancellationToken | None = None,
    ) -> AsyncGenerator[PartialTranscript, None]:
        """Map a 9.0 VoiceEvent stream to ``stream_transcribe``; stop on SpeechEnd."""
        async for partial in self.stream_transcribe(
            self._pcm_from_events(events, token),
            token=token,
        ):
            yield partial

    async def _pcm_from_events(
        self,
        events: AsyncIterator[VoiceEvent],
        token: CancellationToken | None,
    ) -> AsyncGenerator[bytes, None]:
        got_frames = False
        async for event in events:
            if token is not None and token.is_cancelled():
                return
            if isinstance(event, AudioFrameEvent):
                got_frames = True
                yield event.pcm_bytes
            elif isinstance(event, SpeechEndEvent):
                if not got_frames and event.pcm_bytes:
                    yield event.pcm_bytes
                return

    async def _window_partial(
        self,
        buf: bytearray,
        last_emitted: str,
    ) -> PartialTranscript | None:
        result = await self._transcribe_pcm(bytes(buf))
        text = stabilize_transcript(last_emitted, result.text, is_final=False)
        if text is None:
            return None
        return PartialTranscript(text=text, is_final=False, confidence=result.confidence)

    async def _transcribe_pcm(self, pcm: bytes) -> TranscriptionResult:
        samples = pcm16_bytes_to_samples(pcm)
        request = TranscriptionRequest(
            samples=samples,
            sample_rate=self._sample_rate,
            channels=1,
        )
        self._partial_decode_count += 1
        return await self._provider.transcribe(request)

    def get_partial_decode_count(self) -> int:
        """Return the number of partial decodes performed in the current turn."""
        return self._partial_decode_count

    def reset_partial_decode_count(self) -> None:
        """Reset the partial decode counter for a new turn."""
        self._partial_decode_count = 0


__all__ = [
    "FasterWhisperStreamingAdapter",
    "PartialTranscript",
    "StreamingSTTProvider",
    "stabilize_transcript",
]
