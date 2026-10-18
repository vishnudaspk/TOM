from __future__ import annotations

import asyncio

from tom.voice.smart_turn import SmartTurnV3Arbiter, TurnArbitrationDecision


class FakeAnalyzer:
    def __init__(self, state: object) -> None:
        self.state = state
        self.audio: bytes | None = None
        self.cleaned = False

    def append_audio(self, audio: bytes, is_speech: bool) -> None:
        self.audio = audio
        assert is_speech is True

    async def analyze_end_of_turn(self) -> tuple[object, None]:
        return self.state, None

    async def cleanup(self) -> None:
        self.cleaned = True


def test_smart_turn_completion_is_advisory_and_vad_remains_authoritative():
    async def _test() -> None:
        from pipecat.audio.turn.base_turn_analyzer import EndOfTurnState

        complete = SmartTurnV3Arbiter(FakeAnalyzer(EndOfTurnState.COMPLETE))
        continuing = SmartTurnV3Arbiter(FakeAnalyzer(EndOfTurnState.INCOMPLETE))

        assert await complete.analyze(b"pcm") is TurnArbitrationDecision.COMPLETE
        assert await continuing.analyze(b"pcm") is TurnArbitrationDecision.CONTINUING
        assert complete.analyzer.audio == b"pcm"

        await complete.cleanup()
        await continuing.cleanup()

    asyncio.run(_test())
