"""Unit tests for Phase 9 Iteration 9.5: InteractionCore and TurnExecutor.

Fully deterministic and offline: fake engine, fake streaming STT, fake
orchestrator, real ``MicrophoneLoop`` (driven by a threshold VAD), and the real
9.3 ``IncrementalTTSStreamer`` over a recording ``MockTTSProvider``. No
microphone, model, TTS engine, GPU, or network is touched.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Coroutine, Sequence
from typing import Any

from tom.core.context import CancellationToken
from tom.schemas.task import Task, TaskState
from tom.schemas.turn import ResponseAction, TurnStatus
from tom.schemas.voice import SynthesisRequest, SynthesisResult, VoicePipelineState
from tom.telemetry.logging import get_logger
from tom.voice.audio_utils import samples_to_pcm16_bytes
from tom.voice.interaction_core import (
    InteractionCore,
    PlaybackStopGuard,
    TurnEpochGuard,
    TurnExecutor,
    TurnProgress,
)
from tom.voice.microphone import MicrophoneConfig, MicrophoneLoop
from tom.voice.pipeline import VoicePipelineError, VoicePipelineManager
from tom.voice.response_policy import ResponsePolicy
from tom.voice.smart_turn import TurnArbitrationDecision
from tom.voice.streaming_stt import PartialTranscript, StreamingSTTProvider
from tom.voice.streaming_tts import IncrementalTTSStreamer
from tom.voice.tts import MockTTSProvider
from tom.voice.turn_manager import ConversationTurnManager
from tom.voice.vad import VADDetector

logger = get_logger(__name__)

SPEECH_FRAME = [0.4] * 320  # 20 ms at 16 kHz, well above the VAD threshold
SILENCE_FRAME = [0.0] * 320
ANSWER = "The build finished successfully and nothing else to report."
ACK_TEXT = "Looking into that..."


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Deterministic fakes
# ---------------------------------------------------------------------------


class ThresholdVAD(VADDetector):
    """Stateless energy gate: loud frames are speech, silent frames are not."""

    def __init__(self, threshold: float = 0.1) -> None:
        self._threshold = threshold

    def is_speech(self, frame_samples: Sequence[float], sample_rate: int = 16000) -> bool:
        if not frame_samples:
            return False
        mean_square = sum(s * s for s in frame_samples) / len(frame_samples)
        return mean_square**0.5 >= self._threshold


class FakeEngine:
    """EngineClient stand-in recording playback and barge-in flushes.

    ``block_playback=True`` keeps ``play_audio_buffer()`` live so barge-in can
    be observed while audio is actually on the wire; ``stop_audio_playback()``
    always releases it, exactly like the real engine flushing its buffer.
    """

    def __init__(self, *, block_playback: bool = False) -> None:
        self.play_calls: list[list[float]] = []
        self.stop_calls = 0
        self.capture_starts = 0
        self.capture_stops = 0
        self.block_playback = block_playback
        self.playback_started = asyncio.Event()
        self.release_playback = asyncio.Event()
        if not block_playback:
            self.release_playback.set()

    async def start_audio_capture(self, **kwargs: Any) -> None:
        self.capture_starts += 1

    async def stop_audio_capture(self) -> None:
        self.capture_stops += 1

    async def get_captured_speech(self, clear: bool = True) -> Any:
        raise AssertionError("InteractionCore must not poll the engine directly")

    async def play_audio_buffer(
        self,
        samples: list[float],
        device_name: str | None = None,
        sample_rate: int = 24000,
        channels: int = 1,
    ) -> None:
        self.play_calls.append(list(samples))
        self.playback_started.set()
        if self.block_playback:
            await self.release_playback.wait()

    async def stop_audio_playback(self) -> None:
        self.stop_calls += 1
        self.release_playback.set()


class ScriptedSTT(StreamingSTTProvider):
    """Streaming STT committing one scripted final transcript per utterance."""

    def __init__(self, transcripts: list[str] | None = None, default: str = "") -> None:
        self._transcripts = list(transcripts or [])
        self._default = default or "explain the streaming tts module"
        self.utterance_bytes: list[int] = []
        self.cancelled = 0

    async def transcribe(self, request: Any) -> Any:
        raise AssertionError("TurnExecutor must use the 9.1 streaming STT contract")

    async def check_health(self) -> bool:
        return True

    def get_model_info(self) -> dict[str, Any]:
        return {"provider": "ScriptedSTT"}

    async def stream_transcribe(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        token: CancellationToken | None = None,
    ) -> AsyncGenerator[PartialTranscript, None]:
        size = 0
        async for chunk in audio_stream:
            size += len(chunk)
            if token is not None and token.is_cancelled():
                self.cancelled += 1
                return
        if token is not None and token.is_cancelled():
            self.cancelled += 1
            return
        self.utterance_bytes.append(size)
        text = self._transcripts.pop(0) if self._transcripts else self._default
        yield PartialTranscript(text=text, is_final=True, confidence=1.0)


class ScriptedOrchestrator:
    """AgentOrchestrator stand-in emitting scripted token deltas."""

    def __init__(self, deltas: list[str] | None = None, default: str = ANSWER) -> None:
        self._deltas = list(deltas) if deltas else [default]
        self.prompts: list[str] = []
        self.deltas_emitted = 0
        self.gate: asyncio.Event | None = None

    async def stream(
        self,
        prompt: str,
        token: CancellationToken | None = None,
    ) -> AsyncGenerator[str, None]:
        self.prompts.append(prompt)
        for delta in self._deltas:
            if token is not None and token.is_cancelled():
                return
            if self.gate is not None:
                await self.gate.wait()
            self.deltas_emitted += 1
            yield delta


class CompleteArbiter:
    """Deterministic offline arbiter used by the existing interaction tests."""

    async def analyze(self, audio: bytes) -> Any:
        return TurnArbitrationDecision.COMPLETE

    async def cleanup(self) -> None:
        return None


class RecordingTTS(MockTTSProvider):
    """Mock provider that logs each synthesized sentence and can wedge one.

    ``gate_text`` makes synthesis of that exact sentence block until ``gate`` is
    set, which simulates a wedged TTS engine for cancellation tests.
    """

    def __init__(self, *, gate_text: str = "", samples: list[float] | None = None) -> None:
        super().__init__(samples=samples if samples is not None else [0.2] * 16)
        self.requests: list[str] = []
        self.gate = asyncio.Event()
        self._gate_text = gate_text

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        self.requests.append(request.text)
        if self._gate_text and request.text == self._gate_text:
            await self.gate.wait()
        return await super().synthesize(request)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def build_core(
    *,
    transcripts: list[str] | None = None,
    deltas: list[str] | None = None,
    gate_text: str = "",
    engine: FakeEngine | None = None,
    task_manager: Any = None,
    reuse_pipeline: bool = False,
    turn_arbitrator: Any = None,
) -> tuple[InteractionCore, dict[str, Any]]:
    """Wire a complete 9.0-9.5 stack from deterministic fakes."""
    engine = engine or FakeEngine()
    mic = MicrophoneLoop(
        config=MicrophoneConfig(
            silence_duration_ms=50,
            min_speech_frames=2,
            frame_duration_ms=20,
        ),
        vad=ThresholdVAD(),
    )
    stt = ScriptedSTT(transcripts)
    orchestrator = ScriptedOrchestrator(deltas)
    provider = RecordingTTS(gate_text=gate_text)
    tts = IncrementalTTSStreamer(provider, engine)  # type: ignore[arg-type]
    turn_manager = ConversationTurnManager(task_manager=task_manager)
    pipeline = None
    if reuse_pipeline:
        pipeline = VoicePipelineManager(
            stt_provider=stt,  # type: ignore[arg-type]
            tts_provider=tts,  # type: ignore[arg-type]
            engine_client=engine,  # type: ignore[arg-type]
        )
    core = InteractionCore(
        mic_loop=mic,
        stt=stt,
        tts=tts,
        orchestrator=orchestrator,  # type: ignore[arg-type]
        turn_manager=turn_manager,
        policy=ResponsePolicy(),
        engine=engine,  # type: ignore[arg-type]
        pipeline=pipeline,
        turn_arbitrator=turn_arbitrator or CompleteArbiter(),
    )
    return core, {
        "mic": mic,
        "engine": engine,
        "stt": stt,
        "orchestrator": orchestrator,
        "tts": tts,
        "provider": provider,
        "turn_manager": turn_manager,
    }


async def wait_until(predicate: Any, *, timeout: float = 5.0) -> None:
    """Deterministic bounded poll; yields to other tasks instead of sleeping."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not reached before timeout")


async def say(mic: MicrophoneLoop, *, frames: int = 4) -> None:
    """Push one VAD-delimited utterance (speech onset, then silence hangover)."""
    for _ in range(frames):
        await mic.push_samples(list(SPEECH_FRAME))
    for _ in range(3):
        await mic.push_samples(list(SILENCE_FRAME))


async def interrupt(mic: MicrophoneLoop) -> None:
    """Push a speech onset, which the core treats as a barge-in."""
    await mic.push_samples([0.5] * 320)
    await mic.push_samples([0.5] * 320)


async def wait_for_turns(core: InteractionCore, parts: dict[str, Any], count: int) -> None:
    """Wait until ``count`` turns are recorded and the lifecycle is back to LISTENING."""
    await wait_until(lambda: len(parts["turn_manager"]) >= count)
    await wait_until(lambda: core.state is VoicePipelineState.LISTENING)


def interaction_tasks() -> list[asyncio.Task[Any]]:
    return [
        task
        for task in asyncio.all_tasks()
        if (task.get_name() or "").startswith("tom.interaction")
    ]


# ---------------------------------------------------------------------------
# Normal turn
# ---------------------------------------------------------------------------


def test_complete_turn_flows_through_the_whole_pipeline() -> None:
    async def scenario() -> None:
        core, parts = build_core()
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        await say(parts["mic"])
        await wait_for_turns(core, parts, 1)

        record = parts["turn_manager"].history[0]
        assert record.user_text == "explain the streaming tts module"
        assert record.assistant_text == ANSWER
        assert record.status is TurnStatus.COMPLETED
        assert record.interrupted is False
        assert record.response_action is ResponseAction.SPEAK_DIRECT

        assert parts["stt"].utterance_bytes, "the 9.1 streaming STT contract was not used"
        assert parts["orchestrator"].prompts == ["explain the streaming tts module"]
        assert parts["provider"].requests == [ANSWER]
        assert len(parts["engine"].play_calls) == 1
        assert core.state is VoicePipelineState.LISTENING
        assert core.epoch == 1
        assert core.playback_stop_calls == 0

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)
        assert run_task.exception() is None

    run_async(scenario())


def test_pipeline_state_machine_is_driven_through_listening_processing_speaking() -> None:
    async def scenario() -> None:
        core, parts = build_core()
        seen: list[str] = []
        core.pipeline.add_state_callback(lambda old, new, meta: seen.append(new.value))
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        await say(parts["mic"])
        await wait_for_turns(core, parts, 1)
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        assert seen[:3] == ["LISTENING", "PROCESSING", "SPEAKING"]
        assert seen[-1] == "LISTENING"

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    run_async(scenario())


def test_interaction_core_reuses_an_existing_pipeline_manager() -> None:
    """Decision 044: the closed Phase 6 manager stays the single state machine."""

    async def scenario() -> None:
        core, parts = build_core(reuse_pipeline=True)
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        await say(parts["mic"])
        await wait_for_turns(core, parts, 1)

        assert core.pipeline is not None
        assert core.state is VoicePipelineState.LISTENING

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    run_async(scenario())


def test_prompt_carries_structured_history_and_referents() -> None:
    async def scenario() -> None:
        core, parts = build_core(transcripts=["open streaming_tts.py", "summarize that file"])
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        await say(parts["mic"])
        await wait_for_turns(core, parts, 1)
        core.record_tool_call("files.read_file", {"path": "python/tom/voice/streaming_tts.py"})

        await say(parts["mic"])
        await wait_until(lambda: len(parts["turn_manager"]) == 2)

        follow_up = parts["orchestrator"].prompts[-1]
        assert "Recent conversation:" in follow_up
        assert "python/tom/voice/streaming_tts.py" in follow_up
        assert follow_up.endswith("User: summarize that file")

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    run_async(scenario())


def test_background_task_snapshot_reaches_the_prompt() -> None:
    async def scenario() -> None:
        class FakeTaskManager:
            def list_active(self) -> list[Any]:
                return [
                    Task(
                        task_id="task_1",
                        goal="Rebuild the documentation",
                        state=TaskState.EXECUTING,
                        max_steps=4,
                    )
                ]

        core, parts = build_core(task_manager=FakeTaskManager())
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        await say(parts["mic"])
        await wait_for_turns(core, parts, 1)

        assert "Rebuild the documentation" in parts["orchestrator"].prompts[0]
        assert "Active background tasks:" in parts["orchestrator"].prompts[0]

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    run_async(scenario())


def test_multi_sentence_response_is_synthesized_and_played_in_order() -> None:
    async def scenario() -> None:
        deltas = [
            "The first part is complete. ",
            "The second part follows next. ",
            "And the third part closes.",
        ]
        core, parts = build_core(deltas=deltas)
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        await say(parts["mic"])
        await wait_for_turns(core, parts, 1)

        assert sorted(parts["provider"].requests) == sorted(
            [
                "The first part is complete.",
                "The second part follows next.",
                "And the third part closes.",
            ]
        )
        assert len(parts["engine"].play_calls) == 3
        assert parts["turn_manager"].history[0].assistant_text == (
            "The first part is complete. The second part follows next. And the third part closes."
        )

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    run_async(scenario())


def test_silent_act_produces_no_audio() -> None:
    async def scenario() -> None:
        core, parts = build_core(transcripts=["Minimize the current test terminal silently."])
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        await say(parts["mic"])
        await wait_for_turns(core, parts, 1)

        assert parts["orchestrator"].prompts == []
        assert parts["provider"].requests == []
        assert parts["engine"].play_calls == []
        record = parts["turn_manager"].history[0]
        assert record.response_action is ResponseAction.SILENT_ACT
        assert record.assistant_text == ""
        assert core.state is VoicePipelineState.LISTENING

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    run_async(scenario())


def test_fast_ack_is_spoken_before_the_real_answer() -> None:
    async def scenario() -> None:
        engine = FakeEngine(block_playback=True)
        core, parts = build_core(
            transcripts=["Search my repository for the resource manager."],
            engine=engine,
        )
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        await say(parts["mic"])

        # The acknowledgment is the first thing synthesized and the first thing
        # handed to playback; the answer is still queued behind it.
        await wait_until(lambda: len(engine.play_calls) == 1)
        assert parts["provider"].requests == [ACK_TEXT]
        assert core.state is VoicePipelineState.SPEAKING

        engine.release_playback.set()
        await wait_for_turns(core, parts, 1)

        assert sorted(parts["provider"].requests) == sorted([ACK_TEXT, ANSWER])
        assert len(engine.play_calls) == 2
        record = parts["turn_manager"].history[0]
        assert record.response_action is ResponseAction.FAST_ACK_THEN_WORK
        assert record.assistant_text == f"{ACK_TEXT} {ANSWER}"

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    run_async(scenario())


def test_empty_transcript_closes_the_turn_without_audio() -> None:
    async def scenario() -> None:
        core, parts = build_core(transcripts=["   "])
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        await say(parts["mic"])
        await wait_until(lambda: core.epoch == 1)
        await wait_until(lambda: core.active_turn_task is None)
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        assert parts["engine"].play_calls == []
        assert parts["turn_manager"].history == []
        assert core.state is VoicePipelineState.LISTENING
        assert core.epoch == 1

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    run_async(scenario())


# ---------------------------------------------------------------------------
# Barge-in
# ---------------------------------------------------------------------------


def test_barge_in_while_speaking_stops_playback_and_invalidates_epoch() -> None:
    async def scenario() -> None:
        engine = FakeEngine(block_playback=True)
        core, parts = build_core(engine=engine)
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        await say(parts["mic"])
        await wait_until(lambda: engine.play_calls)
        assert core.state is VoicePipelineState.SPEAKING
        epoch_before = core.epoch

        await interrupt(parts["mic"])  # user talks over TOM
        await wait_until(lambda: core.barge_in_count == 1)
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        # 1. Playback halted immediately, exactly once.
        assert engine.stop_calls == 1
        assert core.playback_stop_calls == 1

        # 2. Epoch bumped.
        assert core.epoch == epoch_before + 1

        # 3. Partial assistant content retained as INTERRUPTED context.
        record = parts["turn_manager"].history[0]
        assert record.status is TurnStatus.INTERRUPTED
        assert record.interrupted is True
        assert record.assistant_text == ANSWER

        # 4. New turn accepted immediately, no leftover state.
        assert core.state is VoicePipelineState.LISTENING
        assert core.active_turn_task is None

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

        # 5. No duplicate playback stop on shutdown.
        assert engine.stop_calls == 1

    run_async(scenario())


def test_barge_in_rejects_stale_synthesis_before_playback() -> None:
    async def scenario() -> None:
        engine = FakeEngine()
        core, parts = build_core(
            deltas=[
                "The first part is complete. ",
                "The second part follows next. ",
                "And the third part closes.",
            ],
            gate_text="And the third part closes.",
            engine=engine,
        )
        provider = parts["provider"]
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        await say(parts["mic"])

        # Sentence 1 played; sentence 3 is wedged inside synthesis.
        await wait_until(lambda: engine.play_calls)
        await wait_until(lambda: "And the third part closes." in provider.requests)
        played_before = len(engine.play_calls)

        await interrupt(parts["mic"])
        await wait_until(lambda: core.barge_in_count == 1)
        await wait_until(lambda: core.active_turn_task is None)

        # The stale, gated synthesis never reached playback, and the engine was
        # flushed exactly once for the interrupted epoch.
        assert len(engine.play_calls) == played_before
        assert engine.stop_calls == 1
        assert parts["turn_manager"].history[0].status is TurnStatus.INTERRUPTED

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    run_async(scenario())


def test_barge_in_cancels_llm_streaming() -> None:
    async def scenario() -> None:
        core, parts = build_core()
        orchestrator = parts["orchestrator"]
        orchestrator.gate = asyncio.Event()  # hold generation open
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        await say(parts["mic"])
        await wait_until(lambda: orchestrator.prompts)

        epoch_before = core.epoch
        emitted_before = orchestrator.deltas_emitted
        await interrupt(parts["mic"])
        await wait_until(lambda: core.barge_in_count == 1)
        await wait_until(lambda: core.active_turn_task is None)

        assert core.epoch == epoch_before + 1
        assert orchestrator.deltas_emitted == emitted_before
        assert parts["turn_manager"].history[0].status is TurnStatus.INTERRUPTED
        assert interaction_tasks() == []

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    run_async(scenario())


def test_new_turn_starts_immediately_after_interruption() -> None:
    async def scenario() -> None:
        engine = FakeEngine(block_playback=True)
        core, parts = build_core(transcripts=["first question", "second question"], engine=engine)
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        await say(parts["mic"])
        await wait_until(lambda: engine.play_calls)

        await interrupt(parts["mic"])
        await wait_until(lambda: core.barge_in_count == 1)
        await wait_until(lambda: core.active_turn_task is None)

        await say(parts["mic"])
        await wait_until(lambda: len(parts["turn_manager"]) == 2)
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        interrupted, completed = parts["turn_manager"].history
        assert interrupted.status is TurnStatus.INTERRUPTED
        assert completed.status is TurnStatus.COMPLETED
        assert completed.user_text == "second question"
        assert completed.assistant_text == ANSWER
        assert core.epoch == 3

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    run_async(scenario())


def test_barge_in_transitions_through_interrupted_on_the_existing_machine() -> None:
    """Decision 044: barge-in must not invent a parallel lifecycle."""

    async def scenario() -> None:
        engine = FakeEngine(block_playback=True)
        core, parts = build_core(engine=engine)
        seen: list[str] = []
        core.pipeline.add_state_callback(lambda old, new, meta: seen.append(new.value))
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        await say(parts["mic"])
        await wait_until(lambda: engine.play_calls)
        assert seen[-1] == "SPEAKING"

        await interrupt(parts["mic"])
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        assert seen[-2:] == ["INTERRUPTED", "LISTENING"]
        assert seen.count("INTERRUPTED") == 1

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    run_async(scenario())


def test_repeated_barge_in_keeps_epochs_monotonic_and_stop_single() -> None:
    async def scenario() -> None:
        engine = FakeEngine(block_playback=True)
        core, parts = build_core(engine=engine)
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        await say(parts["mic"])
        await wait_until(lambda: engine.play_calls)

        epochs = [core.epoch]
        await interrupt(parts["mic"])
        await wait_until(lambda: core.barge_in_count == 1)
        epochs.append(core.epoch)
        for _ in range(3):
            await core.handle_barge_in()
            epochs.append(core.epoch)

        assert epochs == sorted(epochs)
        assert len(set(epochs)) == len(epochs)
        assert core.barge_in_count == 4
        # Audio was live once, so exactly one engine stop was needed.
        assert engine.stop_calls == 1
        assert core.playback_stop_calls == 1

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    run_async(scenario())


def test_handle_barge_in_is_safe_with_no_active_turn() -> None:
    async def scenario() -> None:
        core, parts = build_core()
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        await core.handle_barge_in()
        assert core.state is VoicePipelineState.LISTENING
        assert parts["engine"].stop_calls == 0
        assert parts["turn_manager"].history == []

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    run_async(scenario())


def test_barge_in_offline_latency_is_under_100ms() -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        engine = FakeEngine(block_playback=True)
        core, parts = build_core(engine=engine)
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        await say(parts["mic"])
        await wait_until(lambda: engine.play_calls)
        assert core.state is VoicePipelineState.SPEAKING

        started = loop.time()
        await core.handle_barge_in()
        elapsed_ms = (loop.time() - started) * 1000.0

        assert engine.stop_calls == 1
        assert elapsed_ms < 100.0, f"barge-in took {elapsed_ms:.2f} ms"

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    run_async(scenario())


def test_barge_in_does_not_block_on_wedged_synthesis() -> None:
    """A synthesis that never returns must not stall barge-in or shutdown."""

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        core, parts = build_core(gate_text=ANSWER)
        provider = parts["provider"]
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        await say(parts["mic"])
        await wait_until(lambda: ANSWER in provider.requests)
        assert parts["engine"].play_calls == []

        started = loop.time()
        await asyncio.wait_for(core.handle_barge_in(), timeout=1.0)
        elapsed_ms = (loop.time() - started) * 1000.0

        assert elapsed_ms < 100.0, f"barge-in blocked for {elapsed_ms:.2f} ms"
        # The turn already entered SPEAKING, so its epoch is flushed once even
        # though the wedged synthesis never produced a chunk.
        assert parts["engine"].stop_calls == 1
        assert core.active_turn_task is None
        assert parts["turn_manager"].history[0].status is TurnStatus.INTERRUPTED

        provider.gate.set()
        await asyncio.wait_for(core.stop(), timeout=1.0)
        await asyncio.wait_for(run_task, timeout=2.0)
        assert interaction_tasks() == []

    run_async(scenario())


# ---------------------------------------------------------------------------
# Lifecycle and cleanup
# ---------------------------------------------------------------------------


def test_stop_is_idempotent_and_clean() -> None:
    async def scenario() -> None:
        core, parts = build_core()
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        await core.stop()
        await core.stop()
        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

        assert run_task.done()
        assert run_task.exception() is None
        assert core.is_stopped is True
        assert core.is_running is False
        assert core.active_turn_task is None
        assert core.state is VoicePipelineState.IDLE
        assert parts["mic"].is_running is False
        assert interaction_tasks() == []

    run_async(scenario())


def test_stop_while_speaking_halts_audio_once_without_orphans() -> None:
    async def scenario() -> None:
        engine = FakeEngine(block_playback=True)
        core, parts = build_core(engine=engine)
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        await say(parts["mic"])
        await wait_until(lambda: engine.play_calls)

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

        assert engine.stop_calls == 1
        assert core.active_turn_task is None
        assert parts["turn_manager"].history[0].status is TurnStatus.INTERRUPTED
        assert interaction_tasks() == []

    run_async(scenario())


def test_run_is_rejected_when_already_running() -> None:
    async def scenario() -> None:
        core, _parts = build_core()
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.is_running)

        try:
            await core.run()
        except VoicePipelineError:
            pass
        else:
            raise AssertionError("a second run() must be rejected")

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    run_async(scenario())


def test_run_after_stop_is_a_no_op() -> None:
    async def scenario() -> None:
        core, _parts = build_core()
        await core.stop()
        await asyncio.wait_for(core.run(), timeout=1.0)
        assert core.is_running is False

    run_async(scenario())


def test_repeated_turns_do_not_leak_tasks_or_contaminate_state() -> None:
    async def scenario() -> None:
        core, parts = build_core()
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        for index in range(4):
            await say(parts["mic"])
            await wait_until(lambda i=index: len(parts["turn_manager"]) == i + 1)
            await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        assert core.epoch == 4
        assert core.playback_stop_calls == 0
        for record in parts["turn_manager"].history:
            assert record.status is TurnStatus.COMPLETED
            assert record.assistant_text == ANSWER

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)
        assert interaction_tasks() == []

    run_async(scenario())


def test_interrupted_turn_does_not_leak_into_the_next_turn() -> None:
    async def scenario() -> None:
        engine = FakeEngine(block_playback=True)
        core, parts = build_core(transcripts=["first", "second"], engine=engine)
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        await say(parts["mic"])
        await wait_until(lambda: engine.play_calls)

        await core.handle_barge_in()
        engine.block_playback = False

        await say(parts["mic"])
        await wait_until(lambda: len(parts["turn_manager"]) == 2)
        await wait_until(lambda: core.state is VoicePipelineState.LISTENING)

        interrupted, completed = parts["turn_manager"].history
        assert interrupted.status is TurnStatus.INTERRUPTED
        assert completed.status is TurnStatus.COMPLETED
        assert completed.user_text == "second"
        assert completed.assistant_text == ANSWER

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    run_async(scenario())


# ---------------------------------------------------------------------------
# Guards and executor in isolation
# ---------------------------------------------------------------------------


def test_epoch_guard_is_monotonic() -> None:
    guard = TurnEpochGuard()
    assert guard.current == 0
    assert guard.bump() == 1
    assert guard.bump() == 2
    assert guard.is_current(2) is True
    assert guard.is_current(1) is False


def test_playback_stop_guard_issues_one_call_per_epoch() -> None:
    async def scenario() -> None:
        engine = FakeEngine()
        guard = PlaybackStopGuard(engine)  # type: ignore[arg-type]
        assert await guard.stop(1) is True
        assert await guard.stop(1) is False
        assert await guard.stop(2) is True
        assert engine.stop_calls == 2
        assert guard.engine_calls == 2

    run_async(scenario())


def test_playback_stop_guard_swallows_engine_failures() -> None:
    async def scenario() -> None:
        class BrokenEngine:
            def __init__(self) -> None:
                self.calls = 0

            async def stop_audio_playback(self) -> None:
                self.calls += 1
                raise RuntimeError("engine gone")

        engine = BrokenEngine()
        guard = PlaybackStopGuard(engine)  # type: ignore[arg-type]
        assert await guard.stop(1) is False
        assert await guard.stop(1) is False
        assert engine.calls == 1
        assert guard.engine_calls == 0

    run_async(scenario())


def test_turn_executor_runs_a_turn_standalone() -> None:
    async def scenario() -> None:
        engine = FakeEngine()
        stt = ScriptedSTT(["standalone"])
        orchestrator = ScriptedOrchestrator()
        provider = RecordingTTS()
        epochs = TurnEpochGuard()
        executor = TurnExecutor(
            stt=stt,
            tts=IncrementalTTSStreamer(provider, engine),  # type: ignore[arg-type]
            orchestrator=orchestrator,  # type: ignore[arg-type]
            turn_manager=ConversationTurnManager(),
            policy=ResponsePolicy(),
            epochs=epochs,
        )
        epoch = epochs.bump()
        result = await executor.execute(
            samples_to_pcm16_bytes(SPEECH_FRAME),
            epoch=epoch,
            token=CancellationToken(),
            progress=TurnProgress(epoch_id=epoch),
        )

        assert result.status is TurnStatus.COMPLETED
        assert result.epoch_id == epoch
        assert result.transcript == "standalone"
        assert result.assistant_text == ANSWER
        assert result.interrupted is False
        assert result.reached_speaking is True
        assert result.spoken_chunks == 1
        assert engine.play_calls

    run_async(scenario())


def test_turn_executor_rejects_a_stale_epoch() -> None:
    async def scenario() -> None:
        engine = FakeEngine()
        provider = RecordingTTS()
        epochs = TurnEpochGuard()
        executor = TurnExecutor(
            stt=ScriptedSTT(["stale"]),
            tts=IncrementalTTSStreamer(provider, engine),  # type: ignore[arg-type]
            orchestrator=ScriptedOrchestrator(),
            turn_manager=ConversationTurnManager(),
            policy=ResponsePolicy(),
            epochs=epochs,
        )
        epoch = epochs.bump()
        epochs.bump()  # a newer turn invalidated this one

        result = await executor.execute(
            samples_to_pcm16_bytes(SPEECH_FRAME),
            epoch=epoch,
            token=CancellationToken(),
            progress=TurnProgress(epoch_id=epoch),
        )

        assert result.status is TurnStatus.INTERRUPTED
        assert result.assistant_text == ""
        assert provider.requests == []
        assert engine.play_calls == []

    run_async(scenario())


def test_turn_executor_survives_a_cancelled_token() -> None:
    async def scenario() -> None:
        engine = FakeEngine()
        epochs = TurnEpochGuard()
        executor = TurnExecutor(
            stt=ScriptedSTT(["cancelled"]),
            tts=IncrementalTTSStreamer(RecordingTTS(), engine),  # type: ignore[arg-type]
            orchestrator=ScriptedOrchestrator(),
            turn_manager=ConversationTurnManager(),
            policy=ResponsePolicy(),
            epochs=epochs,
        )
        epoch = epochs.bump()
        token = CancellationToken()
        token.cancel()

        result = await executor.execute(
            samples_to_pcm16_bytes(SPEECH_FRAME),
            epoch=epoch,
            token=token,
            progress=TurnProgress(epoch_id=epoch),
        )

        assert result.status is TurnStatus.INTERRUPTED
        assert engine.play_calls == []
        assert executor._turn_manager.history == []  # type: ignore[reportPrivateUsage]

    run_async(scenario())
