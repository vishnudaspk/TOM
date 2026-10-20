from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("pipecat", reason="pipecat optional dep not installed")

from tom.voice.smart_turn import SmartTurnV3Arbiter, TurnArbitrationDecision


class FakeAnalyzer:
    def __init__(self, state: object) -> None:
        self.state = state
        self.audio: bytes | None = None

    def append_audio(self, audio: bytes, is_speech: bool) -> None:
        self.audio = audio
        assert is_speech is True

    async def analyze_end_of_turn(self) -> tuple[object, None]:
        return self.state, None

    async def cleanup(self) -> None:
        return None


def test_arbitration_maps_complete_and_continuing():
    async def _test() -> None:
        from pipecat.audio.turn.base_turn_analyzer import EndOfTurnState

        complete = SmartTurnV3Arbiter(FakeAnalyzer(EndOfTurnState.COMPLETE))
        continuing = SmartTurnV3Arbiter(FakeAnalyzer(EndOfTurnState.INCOMPLETE))
        assert await complete.analyze(b"pcm") is TurnArbitrationDecision.COMPLETE
        assert await continuing.analyze(b"pcm") is TurnArbitrationDecision.CONTINUING
        assert complete.analyzer.audio == b"pcm"

    asyncio.run(_test())
