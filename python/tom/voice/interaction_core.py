"""Unified interaction coordinator binding Phase 9 Iterations 9.0-9.4.

Phase 9 Iteration 9.5.

Two collaborators, one per responsibility:

``InteractionCore``
    Top-level lifecycle coordinator. Consumes the 9.0 ``MicrophoneLoop`` event
    stream, owns the active turn/epoch, triggers barge-in, and shuts down
    cleanly.

``TurnExecutor``
    Per-turn pipeline:

        STT final -> TurnContext -> ResponsePolicy -> optional FAST_ACK
        -> AgentOrchestrator.stream() -> SentenceStreamer
        -> StreamingTTSProvider / IncrementalTTSStreamer -> EngineClient playback

Relationship to ``VoicePipelineManager`` (Decision 044, authoritative)
---------------------------------------------------------------------
``VoicePipelineManager`` remains the **only** owner of the
``IDLE -> LISTENING -> PROCESSING -> SPEAKING`` lifecycle, of the transition
matrix, and of ``VoicePipelineState``. ``InteractionCore`` never declares a
state, never validates a transition, and never invents a second machine: it
drives the existing manager through its public ``transition_to()`` /
``state`` surface and only ever *reports* progress through a callback. All
deterministic lifecycle, epoch, and cancellation decisions stay in code, out
of the LLM (Phase 9 Architectural Principle 2).

Barge-in ordering (deliberate deviation from plan step order)
------------------------------------------------------------
The plan lists ``stop_audio_playback()`` as barge-in step 1. This module
invalidates **synchronously first** and only then awaits the engine stop:

    1. ``TurnEpochGuard.bump()``      -- every downstream result of the old
                                          epoch becomes stale immediately.
    2. ``CancellationToken.cancel()`` -- producers stop enqueueing immediately.
    3. ``PlaybackStopGuard.stop()``   -- one ``stop_audio_playback()`` per epoch.
    4. cancel + await the turn task   -- generators close, queued synthesis is
                                          discarded, playback task is reaped.

Both steps 1 and 2 are synchronous and cost microseconds, while step 3 is an
IPC await. Doing them first guarantees no stale chunk can be enqueued *after*
the engine buffer is flushed. Audio is still halted by a single awaited IPC
call, so the sub-100 ms / 150 ms budget is unchanged.

Why the streamer is driven without a ``CancellationToken``
-----------------------------------------------------------
``InteractionCore`` is the single owner of the barge-in playback stop. The 9.3
``IncrementalTTSStreamer`` is therefore invoked with ``token=None``: its own
cancellation-stop would be a second, duplicate ``stop_audio_playback()`` call.
Pending synthesis is still discarded immediately, because closing the
generator runs the 9.3 ``_shutdown`` path (cancel producer / sequencer /
playback and reap them). ``IncrementalTTSStreamer`` used directly with a token
is unchanged.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing, suppress
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from tom.core.context import CancellationToken
from tom.ipc.errors import ConnectionLostError, NotConnectedError
from tom.schemas.turn import ResponseAction, ResponseDecision, TurnContext, TurnStatus
from tom.schemas.voice import SynthesisRequest, VoicePipelineState
from tom.telemetry.logging import VoiceEventNames, get_logger, set_utterance_id
from tom.voice.debug_trace import DebugTraceSession
from tom.voice.formatter import SpeechFormatter
from tom.voice.greetings import pick_greeting, pick_wake_ack
from tom.voice.latency import (
    InteractionLatencyTracer,
    LatencyReport,
    LatencyStage,
)
from tom.voice.pipeline import (
    InvalidStateTransitionError,
    VoicePipelineError,
    VoicePipelineManager,
)
from tom.voice.response_policy import ResponsePolicy
from tom.voice.sentence_streamer import SentenceStreamer
from tom.voice.smart_turn import SmartTurnV3Arbiter, TurnArbitrationDecision
from tom.voice.turn_manager import ConversationTurnManager
from tom.voice.wakeword import is_wake_phrase, strip_wake_phrase

if TYPE_CHECKING:
    from tom.agents.orchestrator import AgentOrchestrator
    from tom.core.engine import EngineClient
    from tom.voice.events import VoiceEvent
    from tom.voice.microphone import MicrophoneLoop
    from tom.voice.smart_turn import TurnArbiter
    from tom.voice.streaming_stt import StreamingSTTProvider
    from tom.voice.streaming_tts import StreamingTTSProvider

logger = get_logger(__name__)

# Sentence backpressure between the LLM producer and the 9.3 streamer. Bounded
# so a fast model cannot outrun synthesis and grow memory without limit.
_SENTENCE_QUEUE_MAXSIZE = 64

# Re-check interval while waiting for sentence-queue capacity; keeps the epoch
# guard responsive when the consumer is stalled.
_QUEUE_PUT_POLL_S = 0.05

# Sentinel marking the end of the model's sentence stream.
_EOF = object()


# ---------------------------------------------------------------------------
# Epoch and playback-stop ownership
# ---------------------------------------------------------------------------


class TurnEpochGuard:
    """Monotonic turn-epoch counter shared by ``InteractionCore`` and ``TurnExecutor``.

    Every turn start and every barge-in allocates a new epoch. Downstream work
    carries the epoch it was created under and is rejected once it no longer
    matches, so only the newest turn can reach playback.
    """

    __slots__ = ("_epoch",)

    def __init__(self) -> None:
        self._epoch = 0

    @property
    def current(self) -> int:
        """The only epoch whose work is still allowed to affect audio."""
        return self._epoch

    def bump(self) -> int:
        """Invalidate the previous epoch and return the new one."""
        self._epoch += 1
        return self._epoch

    def is_current(self, epoch: int) -> bool:
        return epoch == self._epoch


class PlaybackStopGuard:
    """Issues at most one ``stop_audio_playback()`` per epoch.

    Makes "no duplicate playback-stop calls" a structural guarantee instead of
    a call-ordering coincidence: barge-in and shutdown both go through here.
    """

    def __init__(self, engine: EngineClient) -> None:
        self._engine = engine
        self._stopped: set[int] = set()
        self._engine_calls = 0

    @property
    def engine_calls(self) -> int:
        """Number of ``stop_audio_playback()`` calls actually issued."""
        return self._engine_calls

    async def stop(self, epoch: int) -> bool:
        """Flush the engine playback buffer once for ``epoch``.

        Returns:
            True when an engine call was issued, False when suppressed because
            this epoch already stopped playback (or the call failed).
        """
        if epoch in self._stopped:
            return False
        self._stopped.add(epoch)
        try:
            await self._engine.stop_audio_playback()
        except Exception as exc:
            logger.warning("interaction_playback_stop_failed", error=str(exc))
            return False
        self._engine_calls += 1
        return True


# ---------------------------------------------------------------------------
# Turn result types
# ---------------------------------------------------------------------------


@dataclass
class TurnProgress:
    """Live, mutable view of one turn.

    Shared with ``InteractionCore`` so partial assistant content is available
    even when the turn task is cancelled before it can return a result.
    """

    epoch_id: int
    transcript: str = ""
    generated_text: str = ""
    spoken_sentences: list[str] = field(default_factory=list)
    response_action: ResponseAction | None = None
    decision: ResponseDecision | None = None
    reached_speaking: bool = False

    @property
    def spoken_text(self) -> str:
        """Assistant text that actually reached playback."""
        return " ".join(self.spoken_sentences)


@dataclass(frozen=True)
class TurnExecutionResult:
    """Outcome of one ``TurnExecutor.execute()`` call."""

    epoch_id: int
    status: TurnStatus
    transcript: str
    assistant_text: str
    response_action: ResponseAction | None
    interrupted: bool
    reached_speaking: bool
    spoken_chunks: int = 0


# ---------------------------------------------------------------------------
# TurnExecutor
# ---------------------------------------------------------------------------


class TurnExecutor:
    """Runs one utterance end-to-end through the 9.1-9.4 components.

    Owns no state machine and no lifecycle: it is handed an ``epoch`` and a
    ``CancellationToken`` by ``InteractionCore`` and reports ``on_speaking`` so
    the coordinator can drive ``VoicePipelineManager``.
    """

    def __init__(
        self,
        *,
        stt: StreamingSTTProvider,
        tts: StreamingTTSProvider,
        orchestrator: AgentOrchestrator,
        turn_manager: ConversationTurnManager,
        policy: ResponsePolicy,
        epochs: TurnEpochGuard,
        min_sentence_chars: int = 20,
        include_context_in_prompt: bool = True,
        debug_trace: DebugTraceSession | None = None,
        formatter: SpeechFormatter | None = None,
        wakeword_phrase: str = "hey tom",
    ) -> None:
        self._stt = stt
        self._tts = tts
        self._orchestrator = orchestrator
        self._turn_manager = turn_manager
        self._policy = policy
        self._epochs = epochs
        self._min_sentence_chars = min_sentence_chars
        self._include_context = include_context_in_prompt
        self._debug_trace = debug_trace
        self._formatter = formatter or SpeechFormatter()
        self._wakeword_phrase = wakeword_phrase

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    async def execute(
        self,
        audio: bytes,
        *,
        epoch: int,
        token: CancellationToken,
        progress: TurnProgress,
        on_speaking: Callable[[int], None] | None = None,
        on_playback_completed: Callable[[], None] | None = None,
        tracer: InteractionLatencyTracer | None = None,
        override_transcript: str | None = None,
    ) -> TurnExecutionResult:
        # Log transcript (text_raw) if debug trace active
        # Actual transcript comes from _transcribe
        pass
        """Execute one turn, preserving streaming end to end.

        Args:
            audio: VAD-delimited utterance PCM (16 kHz, 16-bit, mono).
            epoch: Epoch this turn owns; stale work is rejected downstream.
            token: Cooperative cancellation for this turn.
            progress: Live progress sink read by the coordinator on barge-in.
            on_speaking: Called once, just before the first audio is produced.
            on_playback_completed: Called when playback finishes (after tail).
            tracer: Optional Phase 9.6 latency recorder. Purely observational:
                it never gates, delays, or alters pipeline behaviour.
            override_transcript: Pre-transcribed / stripped command transcript.

        Returns:
            TurnExecutionResult. ``asyncio.CancelledError`` propagates after the
            turn has been recorded as INTERRUPTED with its partial reply.
        """
        speaking_notified = False

        def _notify_speaking() -> None:
            nonlocal speaking_notified
            if not speaking_notified:
                speaking_notified = True
                if on_speaking is not None:
                    on_speaking(epoch)

        context: TurnContext | None = None
        try:
            if override_transcript is not None:
                transcript = override_transcript
                progress.transcript = transcript
                if tracer is not None:
                    tracer.mark(LatencyStage.STT_FINAL)
            else:
                transcript = await self._transcribe(
                    audio,
                    epoch=epoch,
                    token=token,
                    progress=progress,
                    tracer=tracer,
                )
                if self._wakeword_phrase and is_wake_phrase(transcript, self._wakeword_phrase):
                    stripped = strip_wake_phrase(transcript, self._wakeword_phrase)
                    if stripped:
                        transcript = stripped
                        progress.transcript = transcript

            if not self._alive(epoch, token):
                return self._empty_result(progress, interrupted=True)
            if not transcript:
                # VAD noise false trigger (plan §11): stay silent, close cleanly.
                return self._empty_result(progress, interrupted=False)

            context = self._turn_manager.start_turn(transcript, metadata={"epoch_id": epoch})
            decision = self._policy.decide(transcript, context)
            progress.decision = decision
            progress.response_action = decision.action

            if decision.action is ResponseAction.SILENT_ACT:
                self._turn_manager.record_assistant_reply(
                    context, "", response_action=decision.action
                )
                return self._result(progress, TurnStatus.COMPLETED, 0)

            queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=_SENTENCE_QUEUE_MAXSIZE)
            prompt = self._build_prompt(transcript)
            producer = asyncio.create_task(
                self._produce_sentences(
                    queue,
                    prompt=prompt,
                    epoch=epoch,
                    token=token,
                    progress=progress,
                    tracer=tracer,
                ),
                name=f"tom.interaction.sentences.{epoch}",
            )

            chunks = 0
            try:
                if decision.action is ResponseAction.FAST_ACK_THEN_WORK and decision.ack_text:
                    _notify_speaking()
                    progress.reached_speaking = True
                    await self._speak_ack(
                        decision.ack_text, epoch=epoch, token=token, tracer=tracer
                    )
                    progress.spoken_sentences.append(decision.ack_text)
                    chunks += 1

                _notify_speaking()
                chunks += await self._stream_response(
                    queue,
                    epoch=epoch,
                    token=token,
                    progress=progress,
                    tracer=tracer,
                    on_playback_completed=on_playback_completed,
                )
            finally:
                await _reap(producer)

            if not self._alive(epoch, token):
                self._turn_manager.interrupt_turn(context, partial_text=progress.spoken_text)
                return self._result(progress, TurnStatus.INTERRUPTED, chunks)

            self._turn_manager.record_assistant_reply(
                context,
                progress.spoken_text,
                response_action=decision.action,
            )
            return self._result(progress, TurnStatus.COMPLETED, chunks)

        except asyncio.CancelledError:
            if context is not None:
                self._turn_manager.interrupt_turn(context, partial_text=progress.spoken_text)
            raise
        except Exception as exc:
            logger.warning("turn_executor_failed", epoch_id=epoch, error=str(exc))
            if context is not None:
                self._turn_manager.interrupt_turn(context, partial_text=progress.spoken_text)
            return self._empty_result(progress, interrupted=True)

    # ------------------------------------------------------------------
    # Stages
    # ------------------------------------------------------------------

    async def _transcribe(
        self,
        audio: bytes,
        *,
        epoch: int,
        token: CancellationToken,
        progress: TurnProgress,
        tracer: InteractionLatencyTracer | None = None,
    ) -> str:
        """Commit the 9.1 final transcript for one VAD-delimited utterance.

        Returns:
            Final transcript text. Empty string means either:
            - VAD noise (no speech detected) — pipeline recovers to LISTENING cleanly
            - STT error (infrastructure failure) — logged with telemetry, pipeline recovers
        """

        async def _pcm() -> AsyncGenerator[bytes, None]:
            if audio:
                yield audio

        final = ""
        partial_marked = False
        stt_error: Exception | None = None
        try:
            async for partial in self._stt.stream_transcribe(
                _pcm(),
                token,
                on_decode_start=lambda: tracer.mark(LatencyStage.STT_DECODE_START) if tracer else None,
                on_decode_end=lambda: tracer.mark(LatencyStage.STT_DECODE_END) if tracer else None,
            ):
                if not self._alive(epoch, token):
                    return ""
                if not partial_marked and not partial.is_final and partial.text.strip():
                    partial_marked = True
                    if tracer is not None:
                        tracer.mark(LatencyStage.STT_PARTIAL1)
                if partial.is_final:
                    final = partial.text.strip()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            stt_error = exc
            logger.warning(
                "turn_executor_stt_failed",
                epoch_id=epoch,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            # Distinguish empty transcript from STT error for telemetry
            return ""

        progress.transcript = final
        if tracer is not None:
            tracer.mark(LatencyStage.STT_FINAL)
        # Log STT error for telemetry without blocking pipeline recovery
        if stt_error is not None:
            logger.info(
                "stt_error_recovered",
                epoch_id=epoch,
                error_type=type(stt_error).__name__,
                transcript_empty=not final,
            )
        # Debug trace: log raw transcript
        if self._debug_trace:
            self._debug_trace.log_text("text_raw", turn_id=epoch, text=final)
        return final

    async def _produce_sentences(
        self,
        queue: asyncio.Queue[Any],
        *,
        prompt: str,
        epoch: int,
        token: CancellationToken,
        progress: TurnProgress,
        tracer: InteractionLatencyTracer | None = None,
    ) -> None:
        """Stream 9.2 LLM deltas through ``SentenceStreamer`` into ``queue``."""
        streamer = SentenceStreamer(min_sentence_chars=self._min_sentence_chars)
        try:
            async for delta in self._orchestrator.stream(prompt, token):
                if not self._alive(epoch, token):
                    return
                if tracer is not None:
                    tracer.mark(LatencyStage.LLM_FIRST_TOKEN)
                progress.generated_text += delta
                for sentence in streamer.push_chunk(delta):
                    if not await self._put(
                        queue, sentence, epoch=epoch, token=token, tracer=tracer
                    ):
                        return
            for sentence in streamer.flush():
                if not await self._put(queue, sentence, epoch=epoch, token=token, tracer=tracer):
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("turn_executor_llm_stream_failed", epoch_id=epoch, error=str(exc))
        finally:
            queue.put_nowait(_EOF)

    async def _stream_response(
        self,
        queue: asyncio.Queue[Any],
        *,
        epoch: int,
        token: CancellationToken,
        progress: TurnProgress,
        tracer: InteractionLatencyTracer | None = None,
        on_playback_completed: Callable[[], None] | None = None,
    ) -> int:
        """Feed queued sentences to the 9.3 streamer; count what reached playback."""

        async def _sentences() -> AsyncGenerator[str, None]:
            while self._alive(epoch, token):
                item = await queue.get()
                if item is _EOF:
                    return
                if not self._alive(epoch, token):
                    return
                # Strip markdown before it reaches TTS (Item 3).
                item = self._formatter.format(item)
                if not item:
                    # Formatter reduced chunk to empty (e.g. a bare URL or code block);
                    # skip rather than synthesising silence.
                    continue
                # Debug trace: log sanitized text sent to TTS
                if self._debug_trace:
                    self._debug_trace.log_text("text_sanitized", turn_id=epoch, text=item)
                yield item

        chunks = 0
        # aclosing() runs the 9.3 generator's shutdown path (cancel producer /
        # sequencer / playback and reap them) even on the early-exit below, so a
        # stale turn never leaves detached tasks behind.
        try:
            async with aclosing(
                self._tts.stream_synthesize(
                    _sentences(),
                    token=None,
                    playback_observer=self._playback_observer(epoch, token, tracer),
                    on_first_synth_start=lambda: tracer.mark(LatencyStage.TTS_FIRST_SYNTH_START) if tracer else None,
                    on_first_synth_end=lambda: tracer.mark(LatencyStage.TTS_FIRST_SYNTH_END) if tracer else None,
                    on_playback_started=lambda: tracer.mark(LatencyStage.PLAYBACK_STARTED) if tracer else None,
                    on_playback_completed=on_playback_completed,
                )
            ) as stream:
                async for chunk in stream:
                    if not self._alive(epoch, token):
                        break
                    progress.reached_speaking = True
                    progress.spoken_sentences.append(chunk.sentence)
                    # Debug trace: log exact TTS chunk text
                    if self._debug_trace:
                        self._debug_trace.log_text("text_tts", turn_id=epoch, text=chunk.sentence)
                    if tracer is not None:
                        tracer.mark(LatencyStage.FIRST_AUDIO)
                    chunks += 1
        except (ConnectionLostError, NotConnectedError) as exc:
            logger.warning(
                "turn_executor_tts_stream_ipc_disconnected",
                epoch_id=epoch,
                error=str(exc),
            )
        return chunks

    async def _speak_ack(
        self,
        ack_text: str,
        *,
        epoch: int,
        token: CancellationToken,
        tracer: InteractionLatencyTracer | None = None,
    ) -> None:
        """Speak the deterministic 9.4 acknowledgment ahead of the real answer."""

        async def _ack() -> AsyncGenerator[str, None]:
            yield ack_text

        with suppress(Exception):
            async with aclosing(
                self._tts.stream_synthesize(
                    _ack(),
                    token=None,
                    playback_observer=self._playback_observer(epoch, token, tracer),
                )
            ) as stream:
                async for _chunk in stream:
                    if tracer is not None:
                        tracer.mark(LatencyStage.FIRST_AUDIO)
                    if not self._alive(epoch, token):
                        return

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _alive(self, epoch: int, token: CancellationToken) -> bool:
        """True while this turn may still influence audio."""
        return self._epochs.is_current(epoch) and not token.is_cancelled()

    def _playback_observer(
        self,
        epoch: int,
        token: CancellationToken,
        tracer: InteractionLatencyTracer | None,
    ) -> Callable[[], None] | None:
        """Build the 9.6 hook that stamps playback start, or ``None`` when off.

        The epoch re-check keeps a stale turn from stamping the current turn's
        tracer if its audio slips through playback after a barge-in.
        """
        if tracer is None:
            return None

        def _observe() -> None:
            if self._alive(epoch, token):
                tracer.mark(LatencyStage.PLAYBACK_STARTED)

        return _observe

    async def _put(
        self,
        queue: asyncio.Queue[Any],
        sentence: str,
        *,
        epoch: int,
        token: CancellationToken,
        tracer: InteractionLatencyTracer | None = None,
    ) -> bool:
        """Bounded, cancellation-aware enqueue that periodically re-checks the epoch."""
        while self._alive(epoch, token):
            try:
                await asyncio.wait_for(queue.put(sentence), timeout=_QUEUE_PUT_POLL_S)
                if tracer is not None:
                    tracer.mark(LatencyStage.SENTENCE1)
                return True
            except TimeoutError:
                continue
        return False

    def _build_prompt(self, transcript: str) -> str:
        """Prefix the 9.4 structured context so the model can resolve referents."""
        if not self._include_context:
            return transcript
        rendered = self._turn_manager.render_model_context()
        if not rendered:
            return transcript
        return f"{rendered}\n\nUser: {transcript}"

    @staticmethod
    def _result(progress: TurnProgress, status: TurnStatus, chunks: int) -> TurnExecutionResult:
        return TurnExecutionResult(
            epoch_id=progress.epoch_id,
            status=status,
            transcript=progress.transcript,
            assistant_text=progress.spoken_text,
            response_action=progress.response_action,
            interrupted=status is TurnStatus.INTERRUPTED,
            reached_speaking=progress.reached_speaking,
            spoken_chunks=chunks,
        )

    def _empty_result(self, progress: TurnProgress, *, interrupted: bool) -> TurnExecutionResult:
        status = TurnStatus.INTERRUPTED if interrupted else TurnStatus.COMPLETED
        return TurnExecutionResult(
            epoch_id=progress.epoch_id,
            status=status,
            transcript=progress.transcript,
            assistant_text=progress.spoken_text,
            response_action=progress.response_action,
            interrupted=interrupted,
            reached_speaking=progress.reached_speaking,
            spoken_chunks=0,
        )


# ---------------------------------------------------------------------------
# InteractionCore
# ---------------------------------------------------------------------------


# Sentinel for default turn_arbitrator behavior (probe availability, fallback to VAD)
class _DefaultArbiterSentinel:
    """Private sentinel marking the default turn arbitration strategy."""
    __slots__ = ()
    def __repr__(self) -> str:
        return "<DEFAULT_ARBITER>"

_DEFAULT_ARBITER: _DefaultArbiterSentinel = _DefaultArbiterSentinel()


class InteractionCore:
    """Persistent event-driven coordinator for the whole voice interaction.

    Owns the microphone event stream, the active turn task, the epoch, and
    shutdown. Delegates the turn body to ``TurnExecutor`` and every lifecycle
    decision to the existing ``VoicePipelineManager``.
    """

    def __init__(
        self,
        mic_loop: MicrophoneLoop,
        stt: StreamingSTTProvider,
        tts: StreamingTTSProvider,
        orchestrator: AgentOrchestrator,
        turn_manager: ConversationTurnManager,
        policy: ResponsePolicy,
        engine: EngineClient,
        *,
        pipeline: VoicePipelineManager | None = None,
        epochs: TurnEpochGuard | None = None,
        min_sentence_chars: int = 20,
        include_context_in_prompt: bool = True,
        tracer_factory: Callable[[int | None], InteractionLatencyTracer] | None = None,
        latency_history: int = 8,
        turn_arbitrator: TurnArbiter | None | _DefaultArbiterSentinel = _DEFAULT_ARBITER,
        startup_greeting: bool = False,
        debug_trace: DebugTraceSession | None = None,
        smart_turn_max_incomplete_wait_ms: int | None = None,
        wakeword_enabled: bool = False,
        wakeword_phrase: str = "hey tom",
        wakeword_timeout_seconds: float = 30.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._mic = mic_loop
        self._engine = engine
        self._turn_manager = turn_manager
        self._policy = policy
        self._epochs = epochs or TurnEpochGuard()
        self._stops = PlaybackStopGuard(engine)
        self._orchestrator = orchestrator
        self._stt = stt
        self._tts = tts
        if turn_arbitrator is _DEFAULT_ARBITER:
            arbiter = SmartTurnV3Arbiter()
            self._turn_arbitrator = arbiter if arbiter.is_available else None
        else:
            self._turn_arbitrator = turn_arbitrator

        # Decision 044: the closed Phase 6 manager stays the single state
        # machine. Reuse the caller's instance when one exists.
        self._pipeline = pipeline or VoicePipelineManager(
            stt_provider=stt,
            tts_provider=tts,
            engine_client=engine,
        )

        self._executor = TurnExecutor(
            stt=stt,
            tts=tts,
            orchestrator=orchestrator,
            turn_manager=turn_manager,
            policy=policy,
            epochs=self._epochs,
            min_sentence_chars=min_sentence_chars,
            include_context_in_prompt=include_context_in_prompt,
            debug_trace=debug_trace,
            formatter=SpeechFormatter(),
            wakeword_phrase=wakeword_phrase,
        )

        self._running = False
        self._stopping = False
        self._stopped = False
        self._barging_in = False
        self._playback_active = False
        self._barge_in_count = 0
        self._startup_greeting = startup_greeting
        self._debug_trace = debug_trace
        self._session_id: str | None = getattr(debug_trace, "session_id", None)

        # Wake-word session lifecycle:
        self._wakeword_enabled = wakeword_enabled
        self._wakeword_phrase = wakeword_phrase
        self._wakeword_timeout_seconds = wakeword_timeout_seconds
        self._wakeword_clock_injected = clock is not None
        self._wakeword_clock = clock or time.monotonic
        self._wakeword_active = not wakeword_enabled  # True means "awake / active session"
        self._wakeword_timeout_handle: asyncio.TimerHandle | None = None
        self._wakeword_expires_at: float | None = None
        self._last_greeting_text: str | None = None
        self._last_wake_ack: str | None = None
        self._first_activation: bool = True
        self._woke_from_timeout: bool = False
        self._last_interaction_time: float = self._wakeword_clock()

        self._frames = bytearray()
        self._smart_turn_candidate: bytes | None = None
        if smart_turn_max_incomplete_wait_ms is not None:
            self._smart_turn_max_incomplete_wait_ms = smart_turn_max_incomplete_wait_ms
        elif hasattr(mic_loop, "_config") and hasattr(mic_loop._config, "smart_turn_max_incomplete_wait_ms"):
            self._smart_turn_max_incomplete_wait_ms = mic_loop._config.smart_turn_max_incomplete_wait_ms
        else:
            self._smart_turn_max_incomplete_wait_ms = 1800
        self._smart_turn_timeout_handle: asyncio.TimerHandle | None = None
        self._active_turn: asyncio.Task[TurnExecutionResult] | None = None
        self._active_token: CancellationToken | None = None
        self._active_progress: TurnProgress | None = None

        # Phase 9.6 latency telemetry. Observational only: the tracer records
        # monotonic marks and never gates, delays, or reorders pipeline work.
        self._tracer_factory = tracer_factory or (
            lambda turn_start_ns: InteractionLatencyTracer(turn_start_ns=turn_start_ns)
        )
        self._latency_history = max(1, latency_history)
        self._tracer: InteractionLatencyTracer | None = None
        self._turn_start_ns: int | None = None
        self._last_speech_frame_ns: int | None = None
        self._last_frame_received_at: float | None = None  # monotonic; updated on every AudioFrameEvent
        self._reports: deque[LatencyReport] = deque(maxlen=self._latency_history)
        # P2: Split-utterance merging during PROCESSING
        self._active_audio: bytes | None = None
        self._pending_cancelled_audio: bytes | None = None
        self._pending_cancelled_time: float | None = None
        self._merge_window_s: float = 2.0

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def state(self) -> VoicePipelineState:
        """Current lifecycle state, owned by ``VoicePipelineManager``."""
        return self._pipeline.state

    @property
    def pipeline(self) -> VoicePipelineManager:
        """The single state machine this coordinator drives."""
        return self._pipeline

    @property
    def epoch(self) -> int:
        """Only this epoch's work may still reach playback."""
        return self._epochs.current

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_stopped(self) -> bool:
        return self._stopped

    @property
    def barge_in_count(self) -> int:
        return self._barge_in_count

    @property
    def playback_stop_calls(self) -> int:
        """``stop_audio_playback()`` calls issued by this coordinator."""
        return self._stops.engine_calls

    @property
    def active_turn_task(self) -> asyncio.Task[TurnExecutionResult] | None:
        return self._active_turn

    @property
    def active_tracer(self) -> InteractionLatencyTracer | None:
        """Tracer for the in-flight turn, or ``None`` when idle."""
        return self._tracer

    @property
    def last_latency_report(self) -> LatencyReport | None:
        """Most recent completed turn's report, including interrupted turns."""
        return self._reports[-1] if self._reports else None

    @property
    def latency_reports(self) -> tuple[LatencyReport, ...]:
        """Newest-last history of the last ``latency_history`` turns."""
        return tuple(self._reports)

    def record_tool_call(
        self,
        tool_name: str,
        parameters: dict[str, Any] | None = None,
    ) -> Any:
        """Register referents for a tool call on the active turn (9.4 contract)."""
        return self._turn_manager.record_tool_call(
            self._turn_manager.current, tool_name, parameters
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Run the persistent interaction loop until the microphone stops.

        Raises:
            VoicePipelineError: If this core is already running. A stopped core
                is terminal; construct a new one to start a fresh session.
        """
        if self._stopped:
            return
        if self._running:
            raise VoicePipelineError("InteractionCore.run() is already active")

        self._running = True
        self._stopping = False
        # Subscribe synchronously so no event emitted during start() is lost.
        subscription = self._mic.subscribe()
        heartbeat_task: asyncio.Task[None] | None = None
        watchdog_task: asyncio.Task[None] | None = None
        try:
            if not self._mic.is_running:
                await self._mic.start()
            self._transition(VoicePipelineState.LISTENING)

            # P0.6: LISTENING heartbeat — emits every 5 s while state == LISTENING
            # so we can detect deaf-LISTENING (gated mic) from logs alone.
            heartbeat_task = asyncio.create_task(
                self._listening_heartbeat(), name="tom.listening_heartbeat"
            )
            # P0.4: LISTENING watchdog — recovers mic if paused > 1s in LISTENING
            watchdog_task = asyncio.create_task(
                self._listening_watchdog(), name="tom.listening_watchdog"
            )

            # Pre-synthesize greeting pool in background for instant wake-ack latency.
            # ponytail: fire-and-forget via executor; no await, no crash if TTS offline.
            from tom.voice.greetings import pre_synthesize_greeting_pool as _pre_synth
            _tts_ref = self._tts
            asyncio.get_event_loop().run_in_executor(None, _pre_synth, _tts_ref)

            # Run startup greeting once per voice session (if enabled)
            if self._startup_greeting:
                await self._run_startup_greeting()

            async for event in subscription:
                if self._stopping:
                    break
                await self._handle_event(event)
        finally:
            if watchdog_task is not None:
                watchdog_task.cancel()
                try:
                    await watchdog_task
                except asyncio.CancelledError:
                    pass
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
            subscription.close()
            self._running = False

    async def _listening_heartbeat(self) -> None:
        """Emit a structured heartbeat log every 5 s while state is LISTENING.

        Allows post-hoc diagnosis of deaf-LISTENING (mic gated while pipeline
        believes it is listening) from logs alone, without live inspection.
        Fields are real measurements, never estimated.
        """
        _INTERVAL_S = 5.0
        prev_frame_count = len(self._frames)

        while True:
            await asyncio.sleep(_INTERVAL_S)
            if self._stopping or self._stopped:
                return
            if self._pipeline.state != VoicePipelineState.LISTENING:
                prev_frame_count = len(self._frames)
                continue

            cur_frame_count = len(self._frames)
            frames_since_last = cur_frame_count - prev_frame_count
            prev_frame_count = cur_frame_count

            seconds_since_speech: float | None = None
            if self._last_speech_frame_ns is not None:
                seconds_since_speech = (
                    time.monotonic_ns() - self._last_speech_frame_ns
                ) / 1e9

            mic = self._mic
            mic_frame_at = getattr(mic, "last_frame_received_at", None)
            last_frame_ts = mic_frame_at if mic_frame_at is not None else self._last_frame_received_at
            logger.info(
                VoiceEventNames.LISTENING_HEARTBEAT,
                pipeline_state="listening",
                mic_gated=getattr(mic, "_gated", None),
                mic_paused=getattr(mic, "_paused", None),
                mic_muted=getattr(mic, "_muted", None),
                frames_since_last_heartbeat=frames_since_last,
                seconds_since_last_speech_onset=seconds_since_speech,
                capture_loop_alive=getattr(mic, "is_running", lambda: None)() if callable(getattr(mic, "is_running", None)) else getattr(mic, "is_running", None),
                ipc_healthy=getattr(self._engine, "is_connected", None),
                input_rms=getattr(mic, "_last_rms", None),
                input_peak=getattr(mic, "_last_peak", None),
                time_since_last_frame_s=(time.monotonic() - last_frame_ts) if last_frame_ts is not None else None,
            )

    async def _listening_watchdog(
        self,
        poll_interval_s: float = 0.2,
        timeout_s: float = 1.0,
        starvation_threshold_s: float = 5.0,
    ) -> None:
        """Watchdog enforcing the LISTENING invariant (P0.4).

        Detects two failure modes while pipeline_state == LISTENING:
        1. Mic paused/gated > timeout_s  → attempt resume, ERROR on failure.
        2. No audio frames received > starvation_threshold_s (mic not paused)
           → emit CAPTURE_STARVATION ERROR, attempt recovery, ERROR on failure.

        Never creates duplicate capture loops.
        """
        paused_since: float | None = None
        recovery_attempted = False
        starvation_since: float | None = None
        starvation_recovery_attempted = False

        while not (self._stopping or self._stopped):
            try:
                await asyncio.sleep(poll_interval_s)
                if self._stopping or self._stopped:
                    return

                if self._pipeline.state != VoicePipelineState.LISTENING:
                    paused_since = None
                    recovery_attempted = False
                    starvation_since = None
                    starvation_recovery_attempted = False
                    continue

                mic = self._mic

                # --- session inactivity timeout watchdog ---
                now = time.monotonic()
                if (
                    self._wakeword_enabled
                    and self._wakeword_active
                    and self._wakeword_timeout_seconds > 0
                    and self._pipeline.state is VoicePipelineState.LISTENING
                    and self._active_turn is None
                    and not self._playback_active
                    and (getattr(mic, "is_in_speech", False) is not True)
                ):
                    if (now - self._last_interaction_time) >= self._wakeword_timeout_seconds:
                        self._on_wakeword_timeout()

                is_paused = getattr(mic, "_paused", False) or getattr(mic, "_gated", False)

                # --- paused/gated detection (existing) ---
                if is_paused:
                    starvation_since = None
                    starvation_recovery_attempted = False
                    if paused_since is None:
                        paused_since = time.monotonic()
                        recovery_attempted = False
                        continue

                    pause_duration = time.monotonic() - paused_since
                    if pause_duration >= timeout_s and not recovery_attempted:
                        recovery_attempted = True
                        was_paused = getattr(mic, "_paused", False)
                        was_gated = getattr(mic, "_gated", False)
                        turn_id = (
                            getattr(self._turn_manager.current, "turn_id", None)
                            if self._turn_manager and self._turn_manager.current
                            else None
                        )

                        recovery_ok = False
                        try:
                            if hasattr(mic, "resume"):
                                mic.resume()
                            elif hasattr(mic, "ungate"):
                                mic.ungate()
                            recovery_ok = not (
                                getattr(mic, "_paused", False) or getattr(mic, "_gated", False)
                            )
                        except Exception as exc:
                            logger.error(
                                "microphone_watchdog_recovery_exception",
                                error=str(exc),
                                pause_duration=pause_duration,
                            )
                            recovery_ok = False

                        if recovery_ok:
                            logger.error(
                                VoiceEventNames.MIC_RESUMED_BY_WATCHDOG,
                                session_id=self._session_id or "voice_session",
                                turn_id=turn_id,
                                pause_duration=pause_duration,
                                previous_microphone_state={"paused": was_paused, "gated": was_gated},
                                recovery_result="resumed",
                            )
                        else:
                            logger.error(
                                VoiceEventNames.MIC_RESUMED_BY_WATCHDOG,
                                session_id=self._session_id or "voice_session",
                                turn_id=turn_id,
                                pause_duration=pause_duration,
                                previous_microphone_state={"paused": was_paused, "gated": was_gated},
                                recovery_result="failed",
                            )
                            try:
                                self._transition(VoicePipelineState.ERROR)
                            except Exception as tr_exc:
                                logger.error(f"Failed to transition to ERROR state: {tr_exc}")
                    continue  # skip starvation check when paused/gated

                # mic is not paused/gated — reset paused tracking
                paused_since = None
                recovery_attempted = False

                # --- capture starvation detection ---
                now = time.monotonic()
                mic_frame_at = getattr(mic, "last_frame_received_at", None)
                last_frame = mic_frame_at if mic_frame_at is not None else self._last_frame_received_at
                if last_frame is None:
                    # No frames ever received since session start; use session start proxy.
                    continue

                stale_s = now - last_frame
                if stale_s < starvation_threshold_s:
                    starvation_since = None
                    starvation_recovery_attempted = False
                    continue

                if starvation_since is None:
                    starvation_since = now

                if not starvation_recovery_attempted:
                    starvation_recovery_attempted = True
                    turn_id = (
                        getattr(self._turn_manager.current, "turn_id", None)
                        if self._turn_manager and self._turn_manager.current
                        else None
                    )

                    recovery_ok = False
                    try:
                        if hasattr(mic, "resume"):
                            mic.resume()
                        recovery_ok = not (
                            getattr(mic, "_paused", False) or getattr(mic, "_gated", False)
                        )
                    except Exception as exc:
                        logger.error(
                            "microphone_watchdog_starvation_recovery_exception",
                            error=str(exc),
                            stale_s=stale_s,
                        )
                        recovery_ok = False

                    logger.error(
                        VoiceEventNames.CAPTURE_STARVATION,
                        session_id=self._session_id or "voice_session",
                        turn_id=turn_id,
                        stale_s=stale_s,
                        mic_paused=getattr(mic, "_paused", False),
                        mic_gated=getattr(mic, "_gated", False),
                        mic_muted=getattr(mic, "_muted", False),
                        recovery_result="resumed" if recovery_ok else "failed",
                    )

                    if not recovery_ok:
                        try:
                            self._transition(VoicePipelineState.ERROR)
                        except Exception as tr_exc:
                            logger.error(f"Failed to transition to ERROR state after starvation: {tr_exc}")

            except asyncio.CancelledError:
                break
            except Exception as loop_exc:
                logger.warning(f"Error in listening watchdog: {loop_exc}")

    async def stop(self) -> None:
        """Stop the interaction loop cleanly. Idempotent and never leaks tasks."""
        if self._stopped:
            return
        self._stopping = True
        self._stopped = True

        progress = self._active_progress
        task = self._active_turn
        token = self._active_token

        # 1-2. Invalidate synchronously before any await so nothing stale starts.
        self._cancel_wakeword_timeout()
        epoch = self._epochs.bump()
        if token is not None:
            token.cancel()

        # 3. Flush live audio before reaping the turn, mirroring barge-in.
        #    Read the flag here: reaping triggers _on_turn_done, which clears it.
        if self._playback_active:
            try:
                await self._stops.stop(epoch)
            except (ConnectionLostError, NotConnectedError) as exc:
                logger.warning(
                    "shutdown_stop_playback_ipc_disconnected",
                    error=str(exc),
                )
            self._playback_active = False

        # 4. Cancel and reap: generators close, queued synthesis is discarded.
        if task is not None and not task.done():
            task.cancel()
        await _reap(task)

        if self._turn_arbitrator is not None:
            await self._turn_arbitrator.cleanup()
            self._turn_arbitrator = None

        self._active_turn = None
        self._active_token = None
        self._active_progress = None

        self._close_open_turn(progress)
        self._frames.clear()
        self._cancel_smart_turn_timeout()
        self._cancel_wakeword_timeout()
        self._smart_turn_candidate = None
        self._active_audio = None
        self._pending_cancelled_audio = None
        self._pending_cancelled_time = None

        # Stops the mic and ends the run() event stream.
        try:
            await self._mic.stop()
        except (ConnectionLostError, NotConnectedError) as exc:
            logger.warning(
                "shutdown_mic_stop_ipc_disconnected",
                error=str(exc),
            )

        self._transition(VoicePipelineState.INTERRUPTED)
        self._transition(VoicePipelineState.IDLE)
        self._running = False
        self._stopping = False

    async def _run_startup_greeting(self) -> None:
        """Run the proactive startup greeting using local time-aware templates."""
        if not self._startup_greeting:
            return
        self._startup_greeting = False
        logger.info(VoiceEventNames.STARTUP_GREETING_STARTED)

        try:
            # 1. Select template-based greeting (local time-aware, no LLM call)
            greeting_text = pick_greeting(datetime.now().hour, self._last_greeting_text)
            self._last_greeting_text = greeting_text

            logger.info(
                VoiceEventNames.STARTUP_GREETING_LLM_COMPLETED,
                greeting_length=len(greeting_text),
                greeting=greeting_text,
            )

            # 2. Synthesize and play via TTS
            logger.info(VoiceEventNames.STARTUP_GREETING_TTS_STARTED)
            from tom.voice.greetings import get_pre_synthesized
            cached = get_pre_synthesized(greeting_text.strip())
            if cached is not None:
                synth_res = cached
            else:
                synth_req = SynthesisRequest(text=greeting_text.strip())
                try:
                    synth_res = await self._tts.synthesize(synth_req)
                except (ConnectionLostError, NotConnectedError) as exc:
                    logger.warning(
                        "startup_greeting_tts_ipc_disconnected",
                        error=str(exc),
                    )
                    return

            if not synth_res.samples:
                logger.warning("startup_greeting_empty_tts_output")
                return

            logger.info(
                VoiceEventNames.STARTUP_GREETING_TTS_COMPLETED,
                sample_count=len(synth_res.samples),
                sample_rate=synth_res.sample_rate,
                channels=synth_res.channels,
            )

            # 3. Play through engine
            logger.info(VoiceEventNames.STARTUP_GREETING_PLAYBACK_STARTED)
            try:
                await self._engine.play_audio_buffer(
                    audio_data=synth_res.audio_data,
                    sample_rate=synth_res.sample_rate,
                    channels=synth_res.channels,
                )
            except (ConnectionLostError, NotConnectedError) as exc:
                logger.warning(
                    "startup_greeting_playback_ipc_disconnected",
                    error=str(exc),
                )
                return
            sr = synth_res.sample_rate or 24000
            duration_s = len(synth_res.samples) / max(sr, 1)
            await asyncio.sleep(duration_s + 0.4)
            logger.info(VoiceEventNames.STARTUP_GREETING_PLAYBACK_COMPLETED)
            if self._wakeword_enabled:
                self._wakeword_active = True
                self._arm_wakeword_timeout()

        except Exception as exc:
            logger.error(
                VoiceEventNames.STARTUP_GREETING_FAILED,
                error=str(exc),
            )
            # Don't re-raise - startup greeting failure shouldn't block the voice session

    async def handle_barge_in(self) -> None:
        """Halt playback, invalidate the epoch, and hand control back to LISTENING.

        Safe to call repeatedly and with no active turn: the playback stop is
        issued at most once per epoch and only while audio is actually live.
        """
        self._barge_in_count += 1
        self._barging_in = True
        progress = self._active_progress
        task = self._active_turn
        token = self._active_token
        tracer = self._tracer

        # P2: Check if this barge-in occurred during PROCESSING before audio output started
        was_processing = (
            self._pipeline.state == VoicePipelineState.PROCESSING
            and not self._playback_active
            and self._active_audio is not None
        )
        if was_processing:
            now = time.monotonic()
            if self._pending_cancelled_audio is None or self._pending_cancelled_time is None:
                self._pending_cancelled_audio = self._active_audio
                self._pending_cancelled_time = now
            else:
                elapsed = now - self._pending_cancelled_time
                if elapsed <= self._merge_window_s:
                    if not self._pending_cancelled_audio.endswith(self._active_audio):
                        self._pending_cancelled_audio += self._active_audio
                else:
                    self._pending_cancelled_audio = self._active_audio
                    self._pending_cancelled_time = now
        else:
            self._pending_cancelled_audio = None
            self._pending_cancelled_time = None

        try:
            # 1-2. Synchronous invalidation (no awaits between bump and cancel).
            self._cancel_wakeword_timeout()
            epoch = self._epochs.bump()
            if token is not None:
                token.cancel()

            # 9.6: onset is stamped before the flush so the stop latency covers
            # exactly the work this method performs.
            if tracer is not None:
                tracer.mark(LatencyStage.BARGE_IN_ONSET)

            # 3. Single authoritative engine flush.
            if self._playback_active:
                try:
                    await self._stops.stop(epoch)
                except (ConnectionLostError, NotConnectedError) as exc:
                    logger.warning(
                        "barge_in_stop_playback_ipc_disconnected",
                        error=str(exc),
                    )
                self._playback_active = False

            if tracer is not None:
                tracer.mark(LatencyStage.BARGE_IN_STOP)

            # 4. Cancel and reap: generators close, queued synthesis is
            #    discarded, the 9.3 playback task is cancelled and detached
            #    synthesis unwinds.
            if task is not None and not task.done():
                task.cancel()
            await _reap(task)

            self._active_turn = None
            self._active_token = None
            self._active_progress = None
            self._smart_turn_candidate = None
            self._active_audio = None

            logger.warning(
                VoiceEventNames.PIPELINE_CANCELLED,
                reason="barge_in",
                epoch=epoch,
                barge_in_count=self._barge_in_count,
            )

            # Debug trace: log barge-in event
            if self._debug_trace:
                self._debug_trace.log_barge_in("accepted", epoch=epoch, count=self._barge_in_count)

            # 5. Partial assistant content is already retained by TurnExecutor;
            #    this closes any turn that never reached it.
            self._close_open_turn(progress)

            # 6. Back to LISTENING for the new speech stream.
            self._transition(VoicePipelineState.INTERRUPTED, epoch)
            self._transition(VoicePipelineState.LISTENING, epoch)
            # Resume microphone after barge-in
            self._mic.resume()
        finally:
            self._barging_in = False
            if self._pipeline.state is VoicePipelineState.LISTENING:
                self._arm_wakeword_timeout()

    def _cancel_smart_turn_timeout(self) -> None:
        """Cancel any pending incomplete turn timeout."""
        if self._smart_turn_timeout_handle is not None:
            self._smart_turn_timeout_handle.cancel()
            self._smart_turn_timeout_handle = None

    def _on_smart_turn_timeout(self) -> None:
        """Force-complete the candidate turn when user remains silent past timeout (Item 5)."""
        self._smart_turn_timeout_handle = None
        candidate = self._smart_turn_candidate
        if candidate is not None and not (self._stopping or self._stopped):
            logger.info(
                "smart_turn_incomplete_timeout_force_complete",
                epoch=self._epochs.current,
                candidate_bytes=len(candidate),
            )
            self._smart_turn_candidate = None
            asyncio.create_task(self._begin_turn(candidate))

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    async def _handle_event(self, event: VoiceEvent) -> None:
        from tom.voice.events import AudioFrameEvent, SpeechEndEvent, SpeechStartEvent

        if isinstance(event, SpeechStartEvent):
            self._cancel_wakeword_timeout()
            self._cancel_smart_turn_timeout()
            if (self._active_turn is not None and not self._active_turn.done()) or self._playback_active:
                await self.handle_barge_in()
            self._frames.clear()
            # Turn start anchor for the 9.6 VAD endpointing metric.
            self._turn_start_ns = time.monotonic_ns()
            self._last_speech_frame_ns = None
            self._transition(VoicePipelineState.LISTENING)
        elif isinstance(event, AudioFrameEvent):
            if not self._stopping:
                self._last_frame_received_at = time.monotonic()
                self._frames.extend(event.pcm_bytes)
                if event.is_speech:
                    self._last_speech_frame_ns = time.monotonic_ns()
        elif isinstance(event, SpeechEndEvent):
            audio = bytes(self._frames) or event.pcm_bytes
            self._frames.clear()
            logger.info(
                "speech_end_received",
                audio_bytes=len(audio),
                wakeword_enabled=self._wakeword_enabled,
                wakeword_active=self._wakeword_active,
            )
            await self._handle_speech_end(audio)

    async def _transcribe_audio(self, audio: bytes) -> str:
        """Transcribe an audio segment using the streaming STT provider."""
        async def _pcm() -> AsyncGenerator[bytes, None]:
            if audio:
                yield audio

        token = CancellationToken()
        final = ""
        t0 = time.monotonic()
        logger.info("stt_transcription_started", audio_bytes=len(audio))
        try:
            async for partial in self._stt.stream_transcribe(_pcm(), token):
                if partial.is_final:
                    final = partial.text.strip()
        except Exception as exc:
            logger.warning("transcribe_audio_failed", error=str(exc), latency_ms=round((time.monotonic() - t0) * 1000))
            return ""
        latency_ms = round((time.monotonic() - t0) * 1000)
        if final:
            logger.info("stt_transcription_completed", transcript_preview=final[:80], latency_ms=latency_ms)
        else:
            logger.info("stt_transcription_empty", audio_bytes=len(audio), latency_ms=latency_ms)
        return final

    async def _play_wake_ack(self) -> None:
        """Play a greeting or acknowledgement after wake-word detection."""
        if self._first_activation or self._woke_from_timeout:
            ack_text = pick_greeting(datetime.now().hour, self._last_wake_ack)
            self._first_activation = False
            self._woke_from_timeout = False
        else:
            ack_text = pick_wake_ack(self._last_wake_ack)
        self._last_wake_ack = ack_text
        try:
            self._transition(VoicePipelineState.SPEAKING)
            self._mic.gate()
            from tom.voice.greetings import get_pre_synthesized
            cached = get_pre_synthesized(ack_text)
            if cached is not None:
                synth_res = cached
            else:
                synth_req = SynthesisRequest(text=ack_text)
                synth_res = await self._tts.synthesize(synth_req)
            if synth_res.samples:
                await self._engine.play_audio_buffer(
                    audio_data=synth_res.audio_data,
                    sample_rate=synth_res.sample_rate,
                    channels=synth_res.channels,
                )
                sr = synth_res.sample_rate or 24000
                duration_s = len(synth_res.samples) / max(sr, 1)
                await asyncio.sleep(duration_s + 0.4)
        except Exception as exc:
            logger.warning("wake_ack_playback_failed", error=str(exc))
        finally:
            self._mic.ungate()
            self._transition(VoicePipelineState.LISTENING)
            self._arm_wakeword_timeout()

    def _cancel_wakeword_timeout(self) -> None:
        """Cancel any pending inactivity timeout callback."""
        if self._wakeword_timeout_handle is not None:
            self._wakeword_timeout_handle.cancel()
            self._wakeword_timeout_handle = None

    def _arm_wakeword_timeout(self) -> None:
        """Arm the inactivity timeout countdown once TOM returns to LISTENING."""
        self._cancel_wakeword_timeout()
        now = self._wakeword_clock()
        self._last_interaction_time = now
        self._wakeword_expires_at = (
            now + self._wakeword_timeout_seconds
            if self._wakeword_enabled and self._wakeword_active and self._wakeword_timeout_seconds > 0
            else None
        )
        if (
            self._wakeword_enabled
            and self._wakeword_active
            and self._wakeword_timeout_seconds > 0
            and self._pipeline.state is VoicePipelineState.LISTENING
            and self._active_turn is None
            and not self._playback_active
            and (getattr(self._mic, "is_in_speech", False) is not True)
            and not self._stopping
            and not self._stopped
        ):
            try:
                loop = asyncio.get_running_loop()
                if self._wakeword_clock_injected:
                    delay_s = max(0.0, self._wakeword_expires_at - self._wakeword_clock())
                else:
                    delay_s = max(0.0, self._wakeword_expires_at - loop.time())
                self._wakeword_timeout_handle = loop.call_later(delay_s, self._on_wakeword_timeout)
            except RuntimeError:
                pass

    def _on_wakeword_timeout(self) -> None:
        """Deactivate session after inactivity timeout in LISTENING state."""
        self._wakeword_timeout_handle = None
        if not (self._wakeword_enabled and self._wakeword_active):
            return
        if self._wakeword_expires_at is not None and self._wakeword_clock() < self._wakeword_expires_at:
            try:
                loop = asyncio.get_running_loop()
                delay_s = max(0.0, self._wakeword_expires_at - self._wakeword_clock())
                self._wakeword_timeout_handle = loop.call_later(delay_s, self._on_wakeword_timeout)
            except RuntimeError:
                pass
            return
        if (
            self._pipeline.state is VoicePipelineState.LISTENING
            and self._active_turn is None
            and not self._playback_active
        ):
            logger.info(
                "wakeword_session_timed_out",
                timeout_s=self._wakeword_timeout_seconds,
            )
            self._wakeword_active = False
            self._wakeword_expires_at = None
            self._woke_from_timeout = True

    async def _handle_speech_end(self, audio: bytes) -> None:
        """Arbitrate a VAD-delimited candidate before committing a turn."""
        if not audio:
            return

        # Timer callbacks may be delayed while the event loop is busy. Enforce
        # expiry at the input boundary so that delay cannot bypass the wake gate.
        if (
            self._wakeword_enabled
            and self._wakeword_active
            and self._wakeword_expires_at is not None
            and self._wakeword_clock() >= self._wakeword_expires_at
            and self._pipeline.state is VoicePipelineState.LISTENING
            and self._active_turn is None
            and not self._playback_active
        ):
            self._wakeword_active = False
            self._wakeword_expires_at = None
            self._cancel_wakeword_timeout()
            self._woke_from_timeout = True

        self._cancel_smart_turn_timeout()

        candidate = audio
        if self._smart_turn_candidate is not None:
            candidate = self._smart_turn_candidate + audio

        # Wake-word gate: when enabled and not yet active, only proceed if wake phrase detected
        if self._wakeword_enabled and not self._wakeword_active:
            transcript = await self._transcribe_audio(candidate)
            if is_wake_phrase(transcript, self._wakeword_phrase):
                logger.info(
                    "wakeword_detected",
                    phrase=self._wakeword_phrase,
                    transcript=transcript[:80] if transcript else "",
                )
                self._wakeword_active = True
                self._last_interaction_time = self._wakeword_clock()
                self._smart_turn_candidate = None
                from tom.voice.wakeword import strip_wake_phrase

                remainder = strip_wake_phrase(transcript, self._wakeword_phrase)
                if remainder:
                    self._first_activation = False
                    self._woke_from_timeout = False
                    logger.info("wakeword_gate_accepted_with_command", remainder=remainder[:80])
                    await self._begin_turn(candidate, override_transcript=remainder)
                    return
                logger.info("wakeword_gate_accepted_ack_only")
                await self._play_wake_ack()
                return
            else:
                logger.info(
                    "wakeword_gate_rejected",
                    transcript=transcript[:80] if transcript else "",
                    wakeword_active=self._wakeword_active,
                )
                self._smart_turn_candidate = None
                return

        if self._turn_arbitrator is None:
            await self._begin_turn(candidate)
            return

        try:
            decision = await self._turn_arbitrator.analyze(candidate)
            logger.info(
                VoiceEventNames.TURN_DECISION,
                epoch=self._epochs.current,
                decision=decision.value,
                candidate_duration_s=len(candidate) / 16000 / 2,  # 16-bit mono
            )
        except Exception as exc:
            logger.warning(
                "smart_turn_arbitration_failed_fallback_to_vad",
                epoch=self._epochs.current,
                error=str(exc),
                fallback="vad_speech_end",
            )
            self._smart_turn_candidate = None
            await self._begin_turn(candidate)
            return

        # Arbitration is asynchronous. The wake session can expire while it is
        # running, so re-enter the gate before treating the candidate as speech.
        if self._wakeword_enabled and not self._wakeword_active:
            self._smart_turn_candidate = None
            await self._handle_speech_end(candidate)
            return

        if decision is TurnArbitrationDecision.CONTINUING:
            self._smart_turn_candidate = candidate
            loop = asyncio.get_running_loop()
            self._smart_turn_timeout_handle = loop.call_later(
                self._smart_turn_max_incomplete_wait_ms / 1000.0,
                self._on_smart_turn_timeout,
            )
            return

        self._smart_turn_candidate = None
        await self._begin_turn(candidate)

    async def _begin_turn(self, audio: bytes, override_transcript: str | None = None) -> None:
        """Allocate an epoch and run one turn in the background."""
        if self._stopping or self._stopped or not audio:
            return
        self._cancel_wakeword_timeout()
        if self._active_turn is not None and not self._active_turn.done():
            await self.handle_barge_in()

        # P2: Merge split utterances during PROCESSING
        if self._pending_cancelled_audio is not None and self._pending_cancelled_time is not None:
            elapsed = time.monotonic() - self._pending_cancelled_time
            if 0.0 <= elapsed <= self._merge_window_s:
                logger.info(
                    "utterance_merged_during_processing",
                    original_bytes=len(self._pending_cancelled_audio),
                    continuation_bytes=len(audio),
                    merged_bytes=len(self._pending_cancelled_audio) + len(audio),
                    elapsed_s=round(elapsed, 3),
                )
                audio = self._pending_cancelled_audio + audio
            else:
                logger.info(
                    "utterance_merge_expired",
                    elapsed_s=round(elapsed, 3),
                    window_s=self._merge_window_s,
                )
            self._pending_cancelled_audio = None
            self._pending_cancelled_time = None

        self._active_audio = audio

        # Defensive: ensure pipeline is in a valid state before starting a new turn.
        # If a previous error left the pipeline in ERROR state, reset to IDLE first.
        # If pipeline is stuck in SPEAKING, recover through INTERRUPTED.
        if self._pipeline.state is VoicePipelineState.ERROR:
            logger.warning(
                "pipeline_in_error_state_resetting_before_new_turn",
                epoch=self._epochs.current,
            )
            self._pipeline.transition_to(VoicePipelineState.IDLE)
            self._pipeline.transition_to(VoicePipelineState.LISTENING)
        elif self._pipeline.state is VoicePipelineState.SPEAKING:
            # Should not happen if barge-in worked, but recover defensively
            logger.warning(
                "pipeline_stuck_in_speaking_recovering_before_new_turn",
                epoch=self._epochs.current,
            )
            self._pipeline.transition_to(VoicePipelineState.INTERRUPTED)
            self._pipeline.transition_to(VoicePipelineState.IDLE)
            self._pipeline.transition_to(VoicePipelineState.LISTENING)

        self._cancel_wakeword_timeout()
        epoch = self._epochs.bump()
        token = CancellationToken()
        progress = TurnProgress(epoch_id=epoch)
        self._active_token = token
        self._active_progress = progress
        self._playback_active = False
        # 9.6: stamp VAD commit immediately, before any turn work can start.
        self._tracer = self._tracer_factory(self._turn_start_ns)
        if self._last_speech_frame_ns is not None:
            self._tracer.mark(LatencyStage.USER_SPEECH_STOP, at_ns=self._last_speech_frame_ns)
        self._tracer.mark(LatencyStage.SPEECH_END)
        self._transition(VoicePipelineState.PROCESSING, epoch)

        # Generate utterance ID for correlation
        utterance_id = f"turn-{epoch}-{uuid.uuid4().hex[:8]}"
        set_utterance_id(utterance_id)

        logger.info(
            VoiceEventNames.SESSION_STARTED,
            epoch=epoch,
            utterance_id=utterance_id,
        )

        task = asyncio.create_task(
            self._run_turn(
                audio,
                epoch,
                token,
                progress,
                self._tracer,
                override_transcript=override_transcript,
            ),
            name=f"tom.interaction.turn.{epoch}",
        )
        self._active_turn = task
        task.add_done_callback(self._on_turn_done)

    async def _run_turn(
        self,
        audio: bytes,
        epoch: int,
        token: CancellationToken,
        progress: TurnProgress,
        tracer: InteractionLatencyTracer,
        override_transcript: str | None = None,
    ) -> TurnExecutionResult:
        barge_in_tick_task: asyncio.Task | None = None

        async def _barge_in_tick_loop() -> None:
            """Log barge-in tick every ~50ms during SPEAKING/PROCESSING."""
            while True:
                if self._pipeline.state not in (VoicePipelineState.SPEAKING, VoicePipelineState.PROCESSING):
                    await asyncio.sleep(0.05)
                    continue
                # Decision logic
                mic_active = self._mic.is_in_speech if hasattr(self._mic, 'is_in_speech') else False
                barge_in_threshold = getattr(self._mic, '_barge_in_rms_threshold', 0.0)
                sustained_ms = getattr(self._mic, '_barge_in_sustained_ms', 0)
                decision = "ignored"
                reason = "not_in_speaking_or_threshold_not_met"
                if self._pipeline.state == VoicePipelineState.SPEAKING and mic_active:
                    decision = "accepted"
                    reason = "mic_rms_above_threshold"
                elif self._pipeline.state == VoicePipelineState.PROCESSING and mic_active:
                    decision = "accepted"
                    reason = "barge_in_during_processing"

                if self._debug_trace:
                    self._debug_trace.log_barge_in(
                        "tick",
                        turn_id=epoch,
                        state=self._pipeline.state.value,
                        mic_rms=0.0,  # Would need access to current frame
                        playback_rms=0.0,
                        threshold=barge_in_threshold,
                        sustained_ms=sustained_ms,
                        decision=decision,
                        reason=reason,
                    )
                await asyncio.sleep(0.05)

        barge_in_tick_task = asyncio.create_task(_barge_in_tick_loop(), name=f"tom.barge_in_tick.{epoch}")

        def _on_speaking(active_epoch: int) -> None:
            self._playback_active = True
            self._transition(VoicePipelineState.SPEAKING, active_epoch)
            # Gate microphone during playback to avoid audio feedback loop
            if self._epochs.is_current(active_epoch):
                self._mic.gate()

        _playback_completed_called = False

        def _on_playback_completed() -> None:
            # Ungate microphone after playback tail completes.
            # Called by IncrementalTTSStreamer._play_pending (guaranteed via
            # finally) and also by the _run_turn finally as a safety net.
            nonlocal _playback_completed_called
            if _playback_completed_called:
                return
            _playback_completed_called = True
            if self._epochs.is_current(epoch):
                self._mic.ungate()

        try:
            result = await self._executor.execute(
                audio,
                epoch=epoch,
                token=token,
                progress=progress,
                on_speaking=_on_speaking,
                on_playback_completed=_on_playback_completed,
                tracer=tracer,
                override_transcript=override_transcript,
            )
            return result
        finally:
            # Safety net: if TTS failed and _on_playback_completed was never
            # called, ungate the microphone here so LISTENING is not deaf.
            _on_playback_completed()
            if barge_in_tick_task:
                barge_in_tick_task.cancel()
                try:
                    await barge_in_tick_task
                except asyncio.CancelledError:
                    pass

    def _on_turn_done(self, task: asyncio.Task[TurnExecutionResult]) -> None:
        """Reap the finished turn and return the lifecycle to LISTENING."""
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                logger.warning(
                    VoiceEventNames.PIPELINE_CANCELLED,
                    error=str(exc),
                    epoch=self._epochs.current,
                )
        if self._active_turn is task:
            self._active_turn = None
            self._active_token = None
            self._active_progress = None
            # 9.6: snapshot the turn's marks before the tracer is dropped so
            # interrupted and failed turns are still measurable.
            if self._tracer is not None:
                report = self._tracer.get_report()
                self._reports.append(report)
                logger.info(
                    VoiceEventNames.LATENCY_REPORT,
                    latency_report=report.as_dict(),
                )
            self._tracer = None
        self._active_audio = None
        self._playback_active = False
        if self._stopping or self._stopped or self._barging_in:
            # Shutdown and barge-in own the lifecycle transition themselves.
            return
        self._transition(VoicePipelineState.IDLE)
        self._transition(VoicePipelineState.LISTENING)
        self._arm_wakeword_timeout()
        # NOTE: Microphone is now ungated via on_playback_completed callback
        # after playback tail completes, not here.

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _close_open_turn(self, progress: TurnProgress | None) -> None:
        """Record any still-open 9.4 turn as INTERRUPTED (safety net)."""
        context = self._turn_manager.current
        if context is None:
            return
        partial = progress.spoken_text if progress is not None else ""
        self._turn_manager.interrupt_turn(context, partial_text=partial)

    def _transition(self, target: VoicePipelineState, epoch: int | None = None) -> None:
        """Ask ``VoicePipelineManager`` to move; never declare state locally."""
        if epoch is not None and not self._epochs.is_current(epoch):
            return
        if self._pipeline.state is target:
            return
        old_state = self._pipeline.state
        try:
            self._pipeline.transition_to(target)
            logger.info(
                "voice.pipeline.state_changed",
                old_state=str(old_state),
                new_state=str(target),
                epoch=epoch,
            )
            # Debug trace: log state transition
            if self._debug_trace:
                self._debug_trace.log_state(str(old_state), str(target), epoch=epoch)
        except InvalidStateTransitionError as exc:
            logger.debug(
                "interaction_transition_skipped",
                current=str(old_state),
                target=str(target),
                reason=str(exc),
            )


async def _reap(task: asyncio.Task[Any] | None) -> None:
    """Cancel and reap a task without ever masking the caller's cancellation."""
    if task is None:
        return
    if not task.done():
        task.cancel()
        try:
            await asyncio.wait({task})
        except asyncio.CancelledError:
            raise
    if not task.cancelled():
        exc = task.exception()
        if exc is not None:
            logger.warning("interaction_task_failed", error=str(exc))


__all__ = [
    "InteractionCore",
    "PlaybackStopGuard",
    "TurnEpochGuard",
    "TurnExecutionResult",
    "TurnExecutor",
    "TurnProgress",
]
