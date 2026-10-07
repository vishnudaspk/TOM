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
from collections import deque
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing, suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from tom.core.context import CancellationToken
from tom.schemas.turn import ResponseAction, ResponseDecision, TurnContext, TurnStatus
from tom.schemas.voice import VoicePipelineState
from tom.telemetry.logging import get_logger
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
    ) -> None:
        self._stt = stt
        self._tts = tts
        self._orchestrator = orchestrator
        self._turn_manager = turn_manager
        self._policy = policy
        self._epochs = epochs
        self._min_sentence_chars = min_sentence_chars
        self._include_context = include_context_in_prompt

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
        tracer: InteractionLatencyTracer | None = None,
    ) -> TurnExecutionResult:
        """Execute one turn, preserving streaming end to end.

        Args:
            audio: VAD-delimited utterance PCM (16 kHz, 16-bit, mono).
            epoch: Epoch this turn owns; stale work is rejected downstream.
            token: Cooperative cancellation for this turn.
            progress: Live progress sink read by the coordinator on barge-in.
            on_speaking: Called once, just before the first audio is produced.
            tracer: Optional Phase 9.6 latency recorder. Purely observational:
                it never gates, delays, or alters pipeline behaviour.

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
            transcript = await self._transcribe(
                audio,
                epoch=epoch,
                token=token,
                progress=progress,
                tracer=tracer,
            )
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
        """Commit the 9.1 final transcript for one VAD-delimited utterance."""

        async def _pcm() -> AsyncGenerator[bytes, None]:
            if audio:
                yield audio

        final = ""
        partial_marked = False
        try:
            async for partial in self._stt.stream_transcribe(_pcm(), token):
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
            logger.warning("turn_executor_stt_failed", epoch_id=epoch, error=str(exc))
            return ""

        progress.transcript = final
        if tracer is not None:
            tracer.mark(LatencyStage.STT_FINAL)
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
    ) -> int:
        """Feed queued sentences to the 9.3 streamer; count what reached playback."""

        async def _sentences() -> AsyncGenerator[str, None]:
            while self._alive(epoch, token):
                item = await queue.get()
                if item is _EOF:
                    return
                if not self._alive(epoch, token):
                    return
                yield item

        chunks = 0
        # aclosing() runs the 9.3 generator's shutdown path (cancel producer /
        # sequencer / playback and reap them) even on the early-exit below, so a
        # stale turn never leaves detached tasks behind.
        async with aclosing(
            self._tts.stream_synthesize(
                _sentences(),
                token=None,
                playback_observer=self._playback_observer(epoch, token, tracer),
            )
        ) as stream:
            async for chunk in stream:
                if not self._alive(epoch, token):
                    break
                progress.reached_speaking = True
                progress.spoken_sentences.append(chunk.sentence)
                if tracer is not None:
                    tracer.mark(LatencyStage.FIRST_AUDIO)
                chunks += 1
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
        turn_arbitrator: TurnArbiter | None = None,
    ) -> None:
        self._mic = mic_loop
        self._engine = engine
        self._turn_manager = turn_manager
        self._policy = policy
        self._epochs = epochs or TurnEpochGuard()
        self._stops = PlaybackStopGuard(engine)
        self._turn_arbitrator = (
            turn_arbitrator
            if turn_arbitrator is not None
            else SmartTurnV3Arbiter()
        )

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
        )

        self._running = False
        self._stopping = False
        self._stopped = False
        self._barging_in = False
        self._playback_active = False
        self._barge_in_count = 0

        self._frames = bytearray()
        self._smart_turn_candidate: bytes | None = None
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
        self._reports: deque[LatencyReport] = deque(maxlen=self._latency_history)

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
        try:
            if not self._mic.is_running:
                await self._mic.start()
            self._transition(VoicePipelineState.LISTENING)
            async for event in subscription:
                if self._stopping:
                    break
                await self._handle_event(event)
        finally:
            subscription.close()
            self._running = False

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
        epoch = self._epochs.bump()
        if token is not None:
            token.cancel()

        # 3. Flush live audio before reaping the turn, mirroring barge-in.
        #    Read the flag here: reaping triggers _on_turn_done, which clears it.
        if self._playback_active:
            await self._stops.stop(epoch)
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
        self._smart_turn_candidate = None

        # Stops the mic and ends the run() event stream.
        await self._mic.stop()

        self._transition(VoicePipelineState.INTERRUPTED)
        self._transition(VoicePipelineState.IDLE)
        self._running = False
        self._stopping = False

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

        try:
            # 1-2. Synchronous invalidation (no awaits between bump and cancel).
            epoch = self._epochs.bump()
            if token is not None:
                token.cancel()

            # 9.6: onset is stamped before the flush so the stop latency covers
            # exactly the work this method performs.
            if tracer is not None:
                tracer.mark(LatencyStage.BARGE_IN_ONSET)

            # 3. Single authoritative engine flush.
            if self._playback_active:
                await self._stops.stop(epoch)
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

            # 5. Partial assistant content is already retained by TurnExecutor;
            #    this closes any turn that never reached it.
            self._close_open_turn(progress)

            # 6. Back to LISTENING for the new speech stream.
            self._transition(VoicePipelineState.INTERRUPTED, epoch)
            self._transition(VoicePipelineState.LISTENING, epoch)
        finally:
            self._barging_in = False

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    async def _handle_event(self, event: VoiceEvent) -> None:
        from tom.voice.events import AudioFrameEvent, SpeechEndEvent, SpeechStartEvent

        if isinstance(event, SpeechStartEvent):
            if self._active_turn is not None and not self._active_turn.done():
                await self.handle_barge_in()
            self._frames.clear()
            # Turn start anchor for the 9.6 VAD endpointing metric.
            self._turn_start_ns = time.monotonic_ns()
            self._transition(VoicePipelineState.LISTENING)
        elif isinstance(event, AudioFrameEvent):
            if not self._stopping:
                self._frames.extend(event.pcm_bytes)
        elif isinstance(event, SpeechEndEvent):
            audio = bytes(self._frames) or event.pcm_bytes
            self._frames.clear()
            await self._handle_speech_end(audio)

    async def _handle_speech_end(self, audio: bytes) -> None:
        """Arbitrate a VAD-delimited candidate before committing a turn."""
        if not audio:
            return

        candidate = audio
        if self._smart_turn_candidate is not None:
            candidate = self._smart_turn_candidate + audio

        if self._turn_arbitrator is None:
            await self._begin_turn(candidate)
            return

        try:
            decision = await self._turn_arbitrator.analyze(candidate)
            logger.info(
                "smart_turn_arbitration",
                epoch=self._epochs.current,
                decision=decision.value,
            )
        except Exception as exc:
            logger.warning(
                "smart_turn_arbitration_failed",
                epoch=self._epochs.current,
                error=str(exc),
            )
            decision = TurnArbitrationDecision.COMPLETE

        if decision is TurnArbitrationDecision.CONTINUING:
            self._smart_turn_candidate = candidate
            return

        self._smart_turn_candidate = None
        await self._begin_turn(candidate)

    async def _begin_turn(self, audio: bytes) -> None:
        """Allocate an epoch and run one turn in the background."""
        if self._stopping or self._stopped or not audio:
            return
        if self._active_turn is not None and not self._active_turn.done():
            await self.handle_barge_in()

        epoch = self._epochs.bump()
        token = CancellationToken()
        progress = TurnProgress(epoch_id=epoch)
        self._active_token = token
        self._active_progress = progress
        self._playback_active = False
        # 9.6: stamp VAD commit immediately, before any turn work can start.
        self._tracer = self._tracer_factory(self._turn_start_ns)
        self._tracer.mark(LatencyStage.SPEECH_END)
        self._transition(VoicePipelineState.PROCESSING, epoch)

        task = asyncio.create_task(
            self._run_turn(audio, epoch, token, progress, self._tracer),
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
    ) -> TurnExecutionResult:
        def _on_speaking(active_epoch: int) -> None:
            self._playback_active = True
            self._transition(VoicePipelineState.SPEAKING, active_epoch)

        return await self._executor.execute(
            audio,
            epoch=epoch,
            token=token,
            progress=progress,
            on_speaking=_on_speaking,
            tracer=tracer,
        )

    def _on_turn_done(self, task: asyncio.Task[TurnExecutionResult]) -> None:
        """Reap the finished turn and return the lifecycle to LISTENING."""
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                logger.warning("interaction_turn_task_failed", error=str(exc))
        if self._active_turn is task:
            self._active_turn = None
            self._active_token = None
            self._active_progress = None
            # 9.6: snapshot the turn's marks before the tracer is dropped so
            # interrupted and failed turns are still measurable.
            if self._tracer is not None:
                self._reports.append(self._tracer.get_report())
            self._tracer = None
        self._playback_active = False
        if self._stopping or self._stopped or self._barging_in:
            # Shutdown and barge-in own the lifecycle transition themselves.
            return
        self._transition(VoicePipelineState.IDLE)
        self._transition(VoicePipelineState.LISTENING)

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
        try:
            self._pipeline.transition_to(target)
        except InvalidStateTransitionError as exc:
            logger.debug(
                "interaction_transition_skipped",
                current=str(self._pipeline.state),
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
