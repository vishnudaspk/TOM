from __future__ import annotations

import asyncio
from enum import Enum

from tom.voice.smart_turn import SmartTurnV3Arbiter, TurnArbitrationDecision

try:
    from pipecat.audio.turn.base_turn_analyzer import EndOfTurnState
except ImportError:
    class EndOfTurnState(Enum):
        COMPLETE = 1
        INCOMPLETE = 2



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
        complete = SmartTurnV3Arbiter(FakeAnalyzer(EndOfTurnState.COMPLETE))
        continuing = SmartTurnV3Arbiter(FakeAnalyzer(EndOfTurnState.INCOMPLETE))
        assert await complete.analyze(b"pcm") is TurnArbitrationDecision.COMPLETE
        assert await continuing.analyze(b"pcm") is TurnArbitrationDecision.CONTINUING
        assert complete.analyzer.audio == b"pcm"

    asyncio.run(_test())


def test_smart_turn_real_onnx_inference() -> None:
    """When Smart Turn is available, verify real ONNX model inference and CPU provider."""
    import pytest

    avail, reason = SmartTurnV3Arbiter.check_is_available()
    if not avail:
        pytest.skip(f"Smart Turn V3 dependencies unavailable in this environment: {reason}")

    async def _test() -> None:
        arbiter = SmartTurnV3Arbiter()
        assert arbiter.is_available is True

        # Generate 1 second of 16kHz mono 16-bit PCM (silence)
        pcm_1s = bytes(16000 * 2)
        decision = await arbiter.analyze(pcm_1s)
        assert isinstance(decision, TurnArbitrationDecision)
        assert arbiter.execution_providers == ["CPUExecutionProvider"]

        await arbiter.cleanup()
        assert arbiter._analyzer is None

    asyncio.run(_test())
