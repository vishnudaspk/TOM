from __future__ import annotations

from tom.voice.smart_turn import TurnArbitrationDecision


def test_continuing_falls_back_to_existing_vad_endpoint():
    assert TurnArbitrationDecision.CONTINUING.value == "continuing"
    assert TurnArbitrationDecision.COMPLETE.value == "complete"
