"""TOM-owned Smart Turn V3 arbitration boundary.

The existing microphone loop remains the owner of speech framing and VAD
endpointing. This adapter is responsible only for deciding whether a VAD-
committed PCM segment is complete enough to continue into STT and the rest of
the turn pipeline.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3


class TurnArbitrationDecision(str, Enum):
    """Outcome returned to the TOM interaction coordinator."""

    COMPLETE = "complete"
    CONTINUING = "continuing"


class TurnArbiter:
    """Protocol for turn arbitration without coupling TOM to a model."""

    async def analyze(self, audio: bytes) -> TurnArbitrationDecision:
        raise NotImplementedError

    async def cleanup(self) -> None:
        return None


class SmartTurnV3Arbiter(TurnArbiter):
    """Local Pipecat Smart Turn V3 adapter.

    The analyzer is loaded lazily so importing TOM does not trigger ONNX model
    loading. The same analyzer instance processes a single VAD-delimited
    utterance and is cleaned up by the owning interaction session.
    """

    def __init__(
        self,
        analyzer: Any | None = None,
        *,
        cpu_count: int = 1,
        sample_rate: int = 16000,
        model_path: str | None = None,
    ) -> None:
        self._analyzer = analyzer
        self._cpu_count = cpu_count
        self._sample_rate = sample_rate
        self._model_path = model_path
        self._owns_analyzer = analyzer is None
        self._prepared = False

    @property
    def analyzer(self) -> Any:
        """Return the configured analyzer, creating it on first use."""
        if self._analyzer is None:
            from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import (
                LocalSmartTurnAnalyzerV3,
            )

            self._analyzer = LocalSmartTurnAnalyzerV3(
                cpu_count=self._cpu_count,
                sample_rate=self._sample_rate,
                smart_turn_model_path=self._model_path,
            )
            self._owns_analyzer = True
        return self._analyzer

    async def analyze(self, audio: bytes) -> TurnArbitrationDecision:
        """Analyze one VAD-delimited PCM segment.

        CONTINUING is intentionally not an error or a suppression signal. The
        coordinator treats it as a VAD fallback and continues through the
        existing STT/LLM/TTS path, while recording telemetry through the
        interaction latency tracer.
        """
        if not audio:
            return TurnArbitrationDecision.CONTINUING

        analyzer = self.analyzer
        analyzer.append_audio(audio, is_speech=True)
        state, _ = await analyzer.analyze_end_of_turn()
        self._prepared = state is not None
        state_name = getattr(state, "name", str(state))
        if state_name == "COMPLETE" or str(state) == "EndOfTurnState.COMPLETE":
            return TurnArbitrationDecision.COMPLETE
        return TurnArbitrationDecision.CONTINUING

    async def cleanup(self) -> None:
        if self._owns_analyzer and self._analyzer is not None:
            await self._analyzer.cleanup()
            self._analyzer = None
            self._prepared = False


__all__ = [
    "SmartTurnV3Arbiter",
    "TurnArbitrationDecision",
    "TurnArbiter",
]
