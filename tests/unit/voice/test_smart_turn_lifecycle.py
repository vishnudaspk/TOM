from __future__ import annotations

import asyncio
from collections.abc import Sequence

from tom.voice.events import AudioFrameEvent, SpeechEndEvent, SpeechStartEvent
from tom.voice.smart_turn import TurnArbiter, TurnArbitrationDecision

from tests.unit.voice.test_interaction_core import (
    build_core,
    wait_for_turns,
    wait_until,
)


class ScriptedArbiter(TurnArbiter):
    def __init__(self, decisions: Sequence[TurnArbitrationDecision]) -> None:
        self.decisions = list(decisions)
        self.audio: list[bytes] = []
        self.calls = 0

    async def analyze(self, audio: bytes) -> TurnArbitrationDecision:
        self.audio.append(audio)
        self.calls += 1
        if not self.decisions:
            return TurnArbitrationDecision.CONTINUING
        decision = self.decisions.pop(0)
        return decision

    async def cleanup(self) -> None:
        return None


def test_continuing_keeps_turn_open_until_later_speech_completes() -> None:
    async def scenario() -> None:
        arbiter = ScriptedArbiter(
            [TurnArbitrationDecision.CONTINUING, TurnArbitrationDecision.COMPLETE]
        )
        core, parts = build_core(turn_arbitrator=arbiter)
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is not None)

        await core._handle_event(
            SpeechEndEvent(
                samples=[0.1],
                pcm_bytes=b"first",
                duration_s=0.1,
                frame_count=1,
            )
        )
        assert core.epoch == 0
        assert core.active_turn_task is None
        assert parts["stt"].utterance_bytes == []
        assert parts["orchestrator"].prompts == []

        await core._handle_event(SpeechStartEvent())
        await core._handle_event(
            AudioFrameEvent(
                samples=[0.1],
                pcm_bytes=b"second",
                frame_index=0,
                is_speech=True,
            )
        )
        await core._handle_event(
            SpeechEndEvent(
                samples=[0.1],
                pcm_bytes=b"second",
                duration_s=0.1,
                frame_count=1,
            )
        )
        await wait_for_turns(core, parts, 1)

        assert arbiter.calls == 2
        assert len(parts["stt"].utterance_bytes) == 1
        assert len(parts["orchestrator"].prompts) == 1
        assert len(parts["provider"].requests) == 1
        assert core.state is not None

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    asyncio.run(scenario())


def test_complete_decision_starts_one_response_pipeline() -> None:
    async def scenario() -> None:
        arbiter = ScriptedArbiter([TurnArbitrationDecision.COMPLETE])
        core, parts = build_core(turn_arbitrator=arbiter)
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is not None)

        await core._handle_event(
            SpeechEndEvent(
                samples=[0.1],
                pcm_bytes=b"complete",
                duration_s=0.1,
                frame_count=1,
            )
        )
        await wait_for_turns(core, parts, 1)

        assert arbiter.calls == 1
        assert len(parts["stt"].utterance_bytes) == 1
        assert len(parts["orchestrator"].prompts) == 1
        assert len(parts["provider"].requests) == 1
        assert len(parts["engine"].play_calls) == 1

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    asyncio.run(scenario())


def test_continuing_decision_force_completes_after_timeout() -> None:
    """Item 5: CONTINUING decision force-completes when user stays silent past timeout."""
    async def scenario() -> None:
        arbiter = ScriptedArbiter([TurnArbitrationDecision.CONTINUING])
        core, parts = build_core(turn_arbitrator=arbiter)
        # Fast timeout for test
        core._smart_turn_max_incomplete_wait_ms = 40
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is not None)

        await core._handle_event(
            SpeechEndEvent(
                samples=[0.1],
                pcm_bytes=b"unfinished utterance",
                duration_s=0.1,
                frame_count=1,
            )
        )

        # Before timeout: candidate is held, turn has not started
        assert core._smart_turn_candidate == b"unfinished utterance"
        assert core._smart_turn_timeout_handle is not None

        # Wait past the 40ms timeout
        await asyncio.sleep(0.08)
        await wait_for_turns(core, parts, 1)

        # After timeout: turn was force-committed
        assert len(parts["stt"].utterance_bytes) == 1
        assert parts["stt"].utterance_bytes[0] == len(b"unfinished utterance")
        assert core._smart_turn_candidate is None

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    asyncio.run(scenario())


def test_continuing_timeout_cancelled_by_new_speech_start() -> None:
    """Item 5: Pending incomplete timeout is cancelled when user speaks again."""
    async def scenario() -> None:
        arbiter = ScriptedArbiter([TurnArbitrationDecision.CONTINUING])
        core, parts = build_core(turn_arbitrator=arbiter)
        core._smart_turn_max_incomplete_wait_ms = 1000
        run_task = asyncio.create_task(core.run())
        await wait_until(lambda: core.state is not None)

        await core._handle_event(
            SpeechEndEvent(
                samples=[0.1],
                pcm_bytes=b"part1",
                duration_s=0.1,
                frame_count=1,
            )
        )
        assert core._smart_turn_timeout_handle is not None

        # User starts speaking again before timeout
        await core._handle_event(SpeechStartEvent())
        assert core._smart_turn_timeout_handle is None

        await core.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    asyncio.run(scenario())
