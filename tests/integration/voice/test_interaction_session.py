"""Phase 9.6 acceptance: an 8-turn deterministic session with failure injection.

One ``InteractionCore`` runs eight consecutive turns over the real
``MicrophoneLoop`` while STT, the LLM, and TTS each fail once. The point of the
session is not throughput but *invariants that must survive failure*:

* every turn, including silent and failed ones, still yields a latency report;
* a turn that never produced audio never reports audio latency;
* an interrupted turn keeps its partial reply and stops playback exactly once;
* each failure is contained: the next turn starts clean with no leaked tasks.

No microphone, model, TTS engine, GPU, or network is touched, and no wall-clock
sleeping is used — every wait is a bounded poll on observable state.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Coroutine, Sequence
from typing import Any

from tom.core.context import CancellationToken
from tom.schemas.turn import ResponseAction, TurnStatus
from tom.schemas.voice import (
    SynthesisRequest,
    SynthesisResult,
    VoicePipelineState,
)
from tom.voice.audio_utils import samples_to_pcm16_bytes
from tom.voice.interaction_core import InteractionCore
from tom.voice.latency import LatencyReport
from tom.voice.microphone import MicrophoneConfig, MicrophoneLoop
from tom.voice.response_policy import ResponsePolicy
from tom.voice.smart_turn import TurnArbitrationDecision
from tom.voice.streaming_stt import PartialTranscript, StreamingSTTProvider
from tom.voice.streaming_tts import IncrementalTTSStreamer
from tom.voice.tts import MockTTSProvider
from tom.voice.turn_manager import ConversationTurnManager
from tom.voice.vad import VADDetector

SPEECH_FRAME = [0.4] * 320  # 20 ms at 16 kHz, above the VAD threshold
SILENCE_FRAME = [0.0] * 320

DIRECT_QUESTION = "explain the streaming tts module"
LATENT_QUESTION = "search across the repo for the audio path"
DIRECT_ANSWER = "The pipeline synthesizes one sentence at a time and plays it immediately."
ACK_ANSWER = "Searching the repository now, one moment."
FAILURE_ANSWER = "This answer is cut short by the failure injection."
RECOVERY_ANSWER = "Session recovered and the answer completed normally."

# Metrics that can only exist once a turn actually produced audio. Any of these
# appearing on a turn that never reached the speaker would be fabricated data.
SPEAKER_METRICS = {
    "tts_first_audio_ms",
    "playback_latency_ms",
    "perceived_response_ms",
}
# Model-side metrics: measured whenever the LLM ran, even if synthesis failed.
MODEL_METRICS = {"llm_ttft_ms", "sentence1_ms"}
FULL_AUDIO_METRICS = SPEAKER_METRICS | MODEL_METRICS | {"stt_finalization_ms"}


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Deterministic fakes with per-turn failure switches
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


class FlakyEngine:
    """Engine stand-in that can wedge one playback call for barge-in testing."""

    def __init__(self) -> None:
        self.play_calls: list[bytes] = []
        self.stop_calls = 0
        self.play_failures = 0
        self.block_next_playback = False
        self._gate = asyncio.Event()

    async def start_audio_capture(self, **kwargs: Any) -> None:
        return None

    async def stop_audio_capture(self) -> None:
        return None

    async def get_system_time(self) -> dict:
        return {
            "timestamp_secs": 1700000000,
            "timestamp_nanos": 0,
            "offset_seconds": -28800,
            "timezone_name": "PST",
            "iso8601": "2023-11-14T12:00:00-08:00",
        }

    async def play_audio_buffer(
        self,
        audio_data: bytes | None = None,
        samples: list[float] | None = None,
        device_name: str | None = None,
        sample_rate: int = 24000,
        channels: int = 1,
    ) -> None:
        if audio_data is not None:
            self.play_calls.append(audio_data)
        elif samples is not None:
            self.play_calls.append(samples)
        if self.fail_next_play:
            self.fail_next_play = False
            self.play_failures += 1
            raise RuntimeError("engine playback failed")
        if self.block_next_playback:
            self.block_next_playback = False
            await self._gate.wait()

    async def stop_audio_playback(self) -> None:
        self.stop_calls += 1
        self._gate.set()

    fail_next_play = False

    def release_playback(self) -> None:
        self._gate.set()


class FlakySTT(StreamingSTTProvider):
    """Scripted streaming STT that can fail the whole utterance once."""

    def __init__(self, default: str = DIRECT_QUESTION) -> None:
        self._default = default
        self.queue: list[str] = []
        self.fail_next = False
        self.utterances = 0

    def script(self, *transcripts: str) -> None:
        self.queue.extend(transcripts)

    async def transcribe(self, request: Any) -> Any:
        raise AssertionError("must use the 9.1 streaming STT contract")

    async def check_health(self) -> bool:
        return True

    def get_model_info(self) -> dict[str, Any]:
        return {"provider": "FlakySTT"}

    async def stream_transcribe(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        token: CancellationToken | None = None,
        *,
        on_decode_start: Callable[[], None] | None = None,
        on_decode_end: Callable[[], None] | None = None,
    ) -> AsyncGenerator[PartialTranscript, None]:
        async for _chunk in audio_stream:
            if token is not None and token.is_cancelled():
                return
        # Pop before failing: a scripted transcript belongs to this utterance
        # only, so one failure must not shift every later turn's script.
        text = self.queue.pop(0) if self.queue else self._default
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("stt backend unavailable")
        self.utterances += 1
        if on_decode_start is not None:
            on_decode_start()
        if on_decode_end is not None:
            on_decode_end()
        yield PartialTranscript(text=text, is_final=True, confidence=1.0)


class FlakyOrchestrator:
    """LLM stand-in that can stall a turn or die mid-stream, once."""

    def __init__(self, default: str = DIRECT_ANSWER) -> None:
        self._default = default
        self.queue: list[list[str]] = []
        self.fail_next_after_first = False
        self.prompts: list[str] = []

    def script(self, *deltas: str) -> None:
        self.queue.append(list(deltas))

    async def stream(
        self,
        prompt: str,
        token: CancellationToken | None = None,
    ) -> AsyncGenerator[str, None]:
        self.prompts.append(prompt)
        deltas = self.queue.pop(0) if self.queue else [self._default]
        for index, delta in enumerate(deltas):
            if token is not None and token.is_cancelled():
                return
            if self.fail_next_after_first and index == 1:
                self.fail_next_after_first = False
                raise RuntimeError("llm stream aborted")
            yield delta


class FlakyTTS(MockTTSProvider):
    """Mock provider that can fail every synthesis of one turn."""

    def __init__(self) -> None:
        super().__init__(samples=[0.2] * 16)
        self.requests: list[str] = []
        self.fail_next = 0

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        self.requests.append(request.text)
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError("tts backend unavailable")
        return await super().synthesize(request)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class CompleteArbiter:
    """Deterministic offline arbiter used by the integration session."""

    async def analyze(self, audio: bytes) -> Any:
        return TurnArbitrationDecision.COMPLETE

    async def cleanup(self) -> None:
        return None


class Session:
    """One wired 9.0-9.6 stack plus the fake parts, for assertions."""

    def __init__(self, *, latency_history: int = 16) -> None:
        self.engine = FlakyEngine()
        self.mic = MicrophoneLoop(
            config=MicrophoneConfig(
                silence_duration_ms=50,
                min_speech_frames=2,
                frame_duration_ms=20,
            ),
            vad=ThresholdVAD(),
        )
        self.stt = FlakySTT()
        self.orchestrator = FlakyOrchestrator()
        self.provider = FlakyTTS()
        self.tts = IncrementalTTSStreamer(self.provider, self.engine)  # type: ignore[arg-type]
        self.turn_manager = ConversationTurnManager()
        self.core = InteractionCore(
            mic_loop=self.mic,
            stt=self.stt,
            tts=self.tts,
            orchestrator=self.orchestrator,  # type: ignore[arg-type]
            turn_manager=self.turn_manager,
            policy=ResponsePolicy(),
            engine=self.engine,  # type: ignore[arg-type]
            latency_history=latency_history,
            turn_arbitrator=CompleteArbiter(),
        )


async def wait_until(predicate: Any, *, timeout: float = 5.0, what: str = "condition") -> None:
    """Bounded poll that yields to the loop instead of sleeping."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError(f"{what} not reached before timeout")


async def say(mic: MicrophoneLoop, *, frames: int = 4) -> None:
    """Push one VAD-delimited utterance."""
    for _ in range(frames):
        await mic.push_samples(list(SPEECH_FRAME))
    for _ in range(3):
        await mic.push_samples(list(SILENCE_FRAME))


async def utter_and_wait(session: Session, *, reports: int) -> LatencyReport:
    """Speak one utterance, then wait for exactly ``reports`` completed turns."""
    await say(session.mic)
    await wait_until(
        lambda: len(session.core.latency_reports) >= reports,
        what=f"latency report #{reports}",
    )
    await wait_until(
        lambda: session.core.state is VoicePipelineState.LISTENING,
        what="return to LISTENING",
    )
    return session.core.last_latency_report  # type: ignore[return-value]


async def utter_and_settle(session: Session) -> LatencyReport:
    """Speak one utterance and wait for the epoch to advance past it.

    Used where report history is deliberately capped, so the wait must key off
    the epoch counter rather than the length of the retained reports.
    """
    epoch_before = session.core.epoch
    await say(session.mic)
    await wait_until(
        lambda: session.core.epoch > epoch_before,
        what="the turn to allocate a new epoch",
    )
    await wait_until(
        lambda: session.core.state is VoicePipelineState.LISTENING,
        what="return to LISTENING",
    )
    return session.core.last_latency_report  # type: ignore[return-value]


def assert_healthy(report: LatencyReport) -> None:
    """Every spoken turn measures the full chain and no delta is negative."""
    assert FULL_AUDIO_METRICS.issubset(report.measured), report.as_dict()
    assert report.failures == [], report.format_table()
    assert all(value >= 0.0 for value in report.measured.values()), report.as_dict()


def assert_never_spoke(report: LatencyReport) -> None:
    """No turn that produced no audio may claim any speaker-side latency."""
    assert not SPEAKER_METRICS & set(report.measured), report.as_dict()
    assert "speech_end" in report.marks_ns


def assert_silent_turn(report: LatencyReport) -> None:
    """A turn that never invoked the model reports nothing past STT."""
    assert not SPEAKER_METRICS & set(report.measured), report.as_dict()
    assert not MODEL_METRICS & set(report.measured), report.as_dict()
    assert "speech_end" in report.marks_ns


def spoken_history(manager: ConversationTurnManager) -> list[str]:
    """User texts of every recorded turn, for readable assertion failures."""
    return [record.user_text for record in manager.history]


def voice_tasks() -> list[asyncio.Task[Any]]:
    return [
        task
        for task in asyncio.all_tasks()
        if (task.get_name() or "").startswith(("tom.interaction", "tom.streaming_tts"))
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_eight_turn_session_survives_every_failure_and_stays_measurable() -> None:
    """Turn 1-8: direct, silent, fast-ack, barge-in, STT, LLM, TTS, recovery."""

    async def scenario() -> None:
        session = Session()
        core = session.core
        manager = session.turn_manager
        run_task = asyncio.create_task(core.run())
        await wait_until(
            lambda: core.state is VoicePipelineState.LISTENING, what="initial LISTENING"
        )

        # -- Turn 1: direct answer, full audio chain measured -----------------
        session.stt.script(DIRECT_QUESTION)
        session.orchestrator.script(DIRECT_ANSWER)
        turn1 = await utter_and_wait(session, reports=1)
        record1 = manager.history[0]
        assert record1.status is TurnStatus.COMPLETED
        assert record1.response_action is ResponseAction.SPEAK_DIRECT
        assert record1.assistant_text == DIRECT_ANSWER
        assert_healthy(turn1)
        assert len(session.engine.play_calls) == 1

        # -- Turn 2: VAD false trigger, silent, STT-only telemetry ------------
        session.stt.script("")
        turn2 = await utter_and_wait(session, reports=2)
        assert_silent_turn(turn2)
        assert turn2.measured.get("stt_finalization_ms") is not None
        assert len(session.engine.play_calls) == 1, "silence must not reach the speaker"
        assert len(manager.history) == 1, (
            "an empty transcript starts no turn",
            spoken_history(manager),
        )

        # -- Turn 3: latency-bearing intent answers with a fast ack -----------
        session.stt.script(LATENT_QUESTION)
        session.orchestrator.script("Found three call sites in the audio path.")
        turn3 = await utter_and_wait(session, reports=3)
        record3 = manager.history[1]
        assert record3.response_action is ResponseAction.FAST_ACK_THEN_WORK
        assert len(session.engine.play_calls) == 3, "ack plus answer must both be spoken"
        assert_healthy(turn3)

        # -- Turn 4: barge-in while audio is live -----------------------------
        session.engine.block_next_playback = True
        session.stt.script("tell me a long story about the build pipeline")
        session.orchestrator.script("Once upon a time the pipeline streamed every sentence.")
        await say(session.mic)
        await wait_until(
            lambda: session.engine.block_next_playback is False,
            what="playback entering the wedge",
        )
        session.mic.resume()
        await session.mic.push_samples([0.5] * 320)
        await session.mic.push_samples([0.5] * 320)
        await wait_until(lambda: len(core.latency_reports) >= 4, what="barge-in report")
        await wait_until(
            lambda: core.state is VoicePipelineState.LISTENING, what="post-barge LISTENING"
        )
        session.engine.release_playback()

        turn4 = core.last_latency_report
        record4 = manager.history[-1]
        assert record4.status is TurnStatus.INTERRUPTED
        assert record4.interrupted is True
        assert core.barge_in_count == 1
        assert session.engine.stop_calls == 1, "barge-in flushes playback exactly once"
        assert turn4.measured.get("barge_in_stop_ms") is not None
        assert turn4.measured["barge_in_stop_ms"] >= 0.0

        # -- Turn 5: STT failure, no transcript, no audio ---------------------
        plays_before_stt_failure = len(session.engine.play_calls)
        session.stt.fail_next = True
        session.stt.script("this utterance never reaches the model")
        turn5 = await utter_and_wait(session, reports=5)
        assert_silent_turn(turn5)
        assert "stt_finalization_ms" not in turn5.measured, "no final transcript was produced"
        assert len(session.engine.play_calls) == plays_before_stt_failure
        assert len(manager.history) == 3, (
            "a failed STT turn never reaches history",
            spoken_history(manager),
        )

        # -- Turn 6: LLM dies mid-stream, partial reply survives ---------------
        session.stt.script("summarize the streaming tts module briefly")
        session.orchestrator.script(FAILURE_ANSWER, "and then the stream died")
        session.orchestrator.fail_next_after_first = True
        turn6 = await utter_and_wait(session, reports=6)
        record6 = manager.history[-1]
        assert record6.status is TurnStatus.COMPLETED
        assert FAILURE_ANSWER in record6.assistant_text
        assert turn6.measured.get("llm_ttft_ms") is not None
        assert_no_spurious_completion(turn6)

        # -- Turn 7: TTS failure, zero audio, latency stops honestly ----------
        session.stt.script("read this back to me please")
        session.orchestrator.script("Nothing will be synthesized for this turn.")
        session.provider.fail_next = 1
        plays_before = len(session.engine.play_calls)
        turn7 = await utter_and_wait(session, reports=7)
        record7 = manager.history[-1]
        assert record7.status is TurnStatus.COMPLETED
        assert record7.assistant_text == ""
        assert len(session.engine.play_calls) == plays_before, "no audio without synthesis"
        assert session.provider.fail_next == 0, "the failure injection was exactly one turn"
        assert_never_spoke(turn7)

        # -- Turn 8: recovery, full chain measured again ----------------------
        session.stt.script(DIRECT_QUESTION)
        session.orchestrator.script(RECOVERY_ANSWER)
        turn8 = await utter_and_wait(session, reports=8)
        record8 = manager.history[-1]
        assert record8.status is TurnStatus.COMPLETED
        assert record8.assistant_text == RECOVERY_ANSWER
        assert len(session.engine.play_calls) == plays_before + 1
        assert_healthy(turn8)

        # -- Session invariants ----------------------------------------------
        assert len(core.latency_reports) == 8
        assert_healthy(core.latency_reports[0])
        assert_silent_turn(core.latency_reports[1])
        assert_silent_turn(core.latency_reports[4])
        assert_never_spoke(core.latency_reports[6])
        assert core.active_tracer is None, "the tracer is released when the turn ends"
        assert core.epoch >= 8, "epochs only ever move forward"
        assert session.engine.stop_calls == 1, "only the barge-in flushed audio"
        # Turns 2 and 5 produced no transcript, so they never opened a turn.
        assert len(manager.history) == 6, (
            "turns 1, 3, 4, 6, 7, and 8 were substantial enough to record",
            spoken_history(manager),
        )

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)
        assert run_task.exception() is None
        assert voice_tasks() == [], "voice tasks outlived the session"

    run_async(scenario())


def assert_no_spurious_completion(report: LatencyReport) -> None:
    """A dead LLM stream must not claim the user heard a finished answer."""
    assert report.metric("perceived_response_ms").status in {
        "not_measured",
        "pass",
        "pass_ceiling_only",
    }
    marks = list(report.marks_ns.values())
    assert marks == sorted(marks), "monotonic marks must never move backwards"


def test_latency_reports_are_bounded_and_serializable() -> None:
    """Telemetry history is capped and every report is JSON-friendly."""

    async def scenario() -> None:
        session = Session(latency_history=3)
        run_task = asyncio.create_task(session.core.run())
        await wait_until(
            lambda: session.core.state is VoicePipelineState.LISTENING, what="LISTENING"
        )

        for _ in range(4):
            await utter_and_settle(session)

        reports = session.core.latency_reports
        assert len(reports) == 3, "history is capped, not unbounded"
        payload = reports[-1].as_dict()
        assert isinstance(payload["marks_ns"], dict)
        assert payload["metrics"]["perceived_response_ms"]["metric"] == "perceived_response_ms"
        assert payload["failures"] == []
        assert isinstance(payload["all_passed"], bool)

        await session.core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    run_async(scenario())


def test_silence_directive_turn_is_recorded_but_never_spoken() -> None:
    """A 'be quiet' turn is a real turn with intent, and it produces no audio."""

    async def scenario() -> None:
        session = Session()
        run_task = asyncio.create_task(session.core.run())
        await wait_until(
            lambda: session.core.state is VoicePipelineState.LISTENING, what="LISTENING"
        )

        session.stt.script("do not announce the file names")
        report = await utter_and_wait(session, reports=1)
        record = session.turn_manager.history[-1]

        assert record.response_action is ResponseAction.SILENT_ACT
        assert record.status is TurnStatus.COMPLETED
        assert record.assistant_text == ""
        assert session.engine.play_calls == []
        assert_silent_turn(report)

        await session.core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    run_async(scenario())


def test_pcm_helpers_produce_playable_frames_for_the_session_harness() -> None:
    """Guard the harness's own audio contract: 16-bit mono frames round-trip."""
    pcm = samples_to_pcm16_bytes([0.5, -0.5])
    assert len(pcm) == 4
