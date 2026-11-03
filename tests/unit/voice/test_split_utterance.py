"""Unit tests for Phase V2 / P2: Merge split utterances during PROCESSING.

Ensures that if a user begins speaking during PROCESSING (before audio output
starts), the cancelled turn's PCM is preserved and concatenated with the
continuation PCM, decode runs once, and no audio is dropped.
"""

from __future__ import annotations

import asyncio
from typing import Any
from collections.abc import AsyncGenerator, Callable

import pytest

from tom.core.context import CancellationToken
from tom.schemas.voice import VoicePipelineState
from tom.voice.events import AudioFrameEvent, SpeechEndEvent, SpeechStartEvent
from tom.voice.streaming_stt import PartialTranscript, StreamingSTTProvider
from tests.unit.voice.test_interaction_core import (
    CompleteArbiter,
    FakeEngine,
    RecordingTTS,
    ScriptedOrchestrator,
    ThresholdVAD,
    build_core,
    run_async,
)


class InspectingSTT(StreamingSTTProvider):
    """STT provider that records the exact audio bytes passed to each decode."""

    def __init__(self) -> None:
        self.received_audio_chunks: list[bytes] = []
        self.decode_calls = 0
        self.decode_gate: asyncio.Event | None = None

    async def check_health(self) -> bool:
        return True

    async def transcribe(self, request: Any) -> Any:
        raise AssertionError("TurnExecutor must use streaming STT contract")

    def get_model_info(self) -> dict[str, Any]:
        return {"provider": "InspectingSTT"}

    async def stream_transcribe(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        token: CancellationToken | None = None,
        *,
        on_decode_start: Callable[[], None] | None = None,
        on_decode_end: Callable[[], None] | None = None,
    ) -> AsyncGenerator[PartialTranscript, None]:
        self.decode_calls += 1
        buffer = bytearray()
        async for chunk in audio_stream:
            buffer.extend(chunk)
            if token is not None and token.is_cancelled():
                return
        if self.decode_gate is not None:
            await self.decode_gate.wait()
        if token is not None and token.is_cancelled():
            return
        audio_bytes = bytes(buffer)
        self.received_audio_chunks.append(audio_bytes)
        if on_decode_start is not None:
            on_decode_start()
        # Decode text from content: if contains part1 and part2, emit merged text
        text = "Merged transcript" if len(audio_bytes) > 2000 else "Single transcript"
        if on_decode_end is not None:
            on_decode_end()
        yield PartialTranscript(text=text, is_final=True, confidence=1.0)


PCM_PART_1 = b"\x01\x02" * 1600  # 3200 bytes (~100ms PCM16)
PCM_PART_2 = b"\x03\x04" * 1600  # 3200 bytes


def test_split_utterance_merged_during_processing() -> None:
    """PCM from turn cancelled during PROCESSING is concatenated with continuation."""

    async def _test() -> None:
        stt = InspectingSTT()
        stt.decode_gate = asyncio.Event()

        core, _ = build_core(stt=stt)
        core._pipeline.transition_to(VoicePipelineState.LISTENING)

        # 1. First utterance begins -> enters PROCESSING (STT decoding)
        await core._handle_speech_end(PCM_PART_1)
        await asyncio.sleep(0.01)
        assert core.state == VoicePipelineState.PROCESSING
        assert core._active_turn is not None

        # 2. User starts speaking continuation while still in PROCESSING (barge-in)
        # Unblock STT gate so the cancelled task can unwind cleanly
        stt.decode_gate.set()
        await core._handle_event(SpeechStartEvent())
        assert core.state == VoicePipelineState.LISTENING
        # Core preserved the cancelled PCM
        assert core._pending_cancelled_audio == PCM_PART_1

        # 3. Continuation utterance ends within 2-second merge window
        stt.decode_gate = None
        await core._handle_speech_end(PCM_PART_2)
        await asyncio.sleep(0.02)
        if core._active_turn:
            await core._active_turn

        # STT received the merged audio (part1 + part2 in exact order) in one single decode
        assert len(stt.received_audio_chunks) == 1
        assert stt.received_audio_chunks[0] == PCM_PART_1 + PCM_PART_2

    run_async(_test())


def test_continuation_after_2s_merge_window_is_not_merged() -> None:
    """If continuation arrives after the 2-second window, it is not merged."""

    async def _test() -> None:
        stt = InspectingSTT()
        stt.decode_gate = asyncio.Event()

        core, _ = build_core(stt=stt)
        core._pipeline.transition_to(VoicePipelineState.LISTENING)

        # 1. First utterance begins and is cancelled in PROCESSING
        await core._handle_speech_end(PCM_PART_1)
        await asyncio.sleep(0.01)
        stt.decode_gate.set()
        await core._handle_event(SpeechStartEvent())
        assert core._pending_cancelled_audio == PCM_PART_1

        # 2. Simulate 2.5 seconds passing (merge window expired)
        core._pending_cancelled_time -= 2.5

        # 3. Continuation arrives
        stt.decode_gate = None
        await core._handle_speech_end(PCM_PART_2)
        await asyncio.sleep(0.02)
        if core._active_turn:
            await core._active_turn

        # Decode received ONLY part 2 (expired part 1 was discarded)
        assert len(stt.received_audio_chunks) == 1
        assert stt.received_audio_chunks[0] == PCM_PART_2

    run_async(_test())


def test_no_merge_when_audio_output_already_started() -> None:
    """Barge-in during SPEAKING (audio playing) must NOT merge with continuation."""

    async def _test() -> None:
        stt = InspectingSTT()
        core, harness = build_core(stt=stt)
        core._pipeline.transition_to(VoicePipelineState.LISTENING)

        # Set up a turn that reaches SPEAKING
        await core._handle_speech_end(PCM_PART_1)
        await asyncio.sleep(0.05)
        # Force playback_active to simulate SPEAKING
        core._playback_active = True
        core._pipeline.transition_to(VoicePipelineState.SPEAKING)

        # User barges in during SPEAKING
        await core.handle_barge_in()
        # Must NOT preserve PCM for merge
        assert core._pending_cancelled_audio is None

        # Next utterance arrives
        await core._handle_speech_end(PCM_PART_2)
        await asyncio.sleep(0.05)
        if core._active_turn:
            await core._active_turn

        # Second utterance was NOT merged with part 1
        assert stt.received_audio_chunks[-1] == PCM_PART_2

    run_async(_test())


def test_repeated_cancellation_during_processing() -> None:
    """Repeated barge-ins during PROCESSING concatenate multiple parts within 2s."""

    async def _test() -> None:
        stt = InspectingSTT()
        stt.decode_gate = asyncio.Event()

        core, _ = build_core(stt=stt)
        core._pipeline.transition_to(VoicePipelineState.LISTENING)

        # Part 1 starts and cancels during PROCESSING
        await core._handle_speech_end(PCM_PART_1)
        await asyncio.sleep(0.01)
        assert core.state == VoicePipelineState.PROCESSING
        stt.decode_gate.set()
        await core.handle_barge_in()
        assert core._pending_cancelled_audio == PCM_PART_1

        # Part 2 starts and cancels during PROCESSING
        stt.decode_gate = asyncio.Event()
        await core._handle_speech_end(PCM_PART_2)
        await asyncio.sleep(0.01)
        assert core.state == VoicePipelineState.PROCESSING
        stt.decode_gate.set()
        await core.handle_barge_in()
        assert core._pending_cancelled_audio == PCM_PART_1 + PCM_PART_2

        # Part 3 finishes normally
        pcm_part_3 = b"\x05\x06" * 800
        stt.decode_gate = None
        await core._handle_speech_end(pcm_part_3)
        await asyncio.sleep(0.02)

        if core._active_turn:
            await core._active_turn

        # Merged all three in order in a single decode
        assert len(stt.received_audio_chunks) == 1
        assert stt.received_audio_chunks[0] == PCM_PART_1 + PCM_PART_2 + pcm_part_3

    run_async(_test())


def test_duplicate_continuation_not_duplicated() -> None:
    """If the exact same PCM segment is cancelled again, duplicate is prevented."""

    async def _test() -> None:
        core, _ = build_core()
        core._pipeline.transition_to(VoicePipelineState.PROCESSING)
        core._active_audio = PCM_PART_1

        await core.handle_barge_in()
        assert core._pending_cancelled_audio == PCM_PART_1

        # Simulate same audio still active in duplicate call
        core._pipeline.transition_to(VoicePipelineState.PROCESSING)
        core._active_audio = PCM_PART_1
        await core.handle_barge_in()

        # Should not have doubled the PCM
        assert core._pending_cancelled_audio == PCM_PART_1
        assert len(core._pending_cancelled_audio) == len(PCM_PART_1)

    run_async(_test())
