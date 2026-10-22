"""TOM-owned Smart Turn V3 arbitration boundary.

The existing microphone loop remains the owner of speech framing and VAD
endpointing. This adapter is responsible only for deciding whether a VAD-
committed PCM segment is complete enough to continue into STT and the rest of
the turn pipeline.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from tom.telemetry.logging import get_logger

logger = get_logger(__name__)


class SmartTurnUnavailableError(RuntimeError):
    """Raised when Smart Turn dependencies or model cannot be loaded."""


class TurnArbitrationDecision(StrEnum):
    """Outcome returned to the TOM interaction coordinator."""

    COMPLETE = "complete"
    CONTINUING = "continuing"


class TurnArbiter:
    """Protocol for turn arbitration without coupling TOM to a model."""

    @property
    def is_available(self) -> bool:
        """Whether this arbiter is ready and available to analyze audio."""
        return True

    async def analyze(self, audio: bytes) -> TurnArbitrationDecision:
        raise NotImplementedError

    async def cleanup(self) -> None:
        return None


class SmartTurnV3Arbiter(TurnArbiter):
    """Local Pipecat Smart Turn V3 adapter.

    The analyzer is loaded lazily so importing TOM does not trigger ONNX model
    loading or fail if optional Pipecat dependencies are absent. The same
    analyzer instance processes VAD-delimited utterances and is cleaned up by
    the owning interaction session.
    """

    def __init__(
        self,
        analyzer: Any | None = None,
        *,
        cpu_count: int = 1,
        sample_rate: int = 16000,
        model_path: str | None = None,
    ) -> None:
        if cpu_count < 1:
            raise ValueError(f"cpu_count must be at least 1, got {cpu_count}")

        self._analyzer = analyzer
        self._cpu_count = cpu_count
        self._sample_rate = sample_rate
        self._model_path = model_path
        self._owns_analyzer = analyzer is None
        self._prepared = False
        self._unavailable_reason: str | None = None
        self._execution_providers: list[str] = []
        self._resolved_model_path: str | None = None

        if model_path is not None:
            import os

            if not os.path.isfile(model_path):
                self._unavailable_reason = f"Specified model file does not exist: {model_path}"

    @classmethod
    def check_is_available(cls) -> tuple[bool, str | None]:
        """Check whether Pipecat Smart Turn dependencies can be imported cleanly."""
        try:
            import pipecat  # noqa: F401
            from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import (
                LocalSmartTurnAnalyzerV3,  # noqa: F401
            )

            return True, None
        except ModuleNotFoundError as exc:
            return False, f"Missing dependency: {exc.name or str(exc)}"
        except ImportError as exc:
            return False, f"Import failure: {exc}"
        except Exception as exc:
            return False, f"Availability check error: {exc}"

    @property
    def unavailable_reason(self) -> str | None:
        """The reason why Smart Turn V3 is unavailable, or None if available."""
        if self._unavailable_reason is None and self._analyzer is None:
            avail, reason = self.check_is_available()
            if not avail:
                self._unavailable_reason = reason
        return self._unavailable_reason

    @property
    def is_available(self) -> bool:
        """Return True if an analyzer is already injected or dependencies are importable."""
        if self._analyzer is not None:
            return True
        if self._unavailable_reason is not None:
            return False
        avail, reason = self.check_is_available()
        if not avail:
            self._unavailable_reason = reason
            return False

        # If using bundled model, ensure model file exists
        if self._model_path is None:
            from pathlib import Path

            bundled_found = False
            try:
                import pipecat

                p_path = (
                    Path(pipecat.__file__).parent
                    / "audio"
                    / "turn"
                    / "smart_turn"
                    / "data"
                    / "smart-turn-v3.2-cpu.onnx"
                )
                if p_path.is_file():
                    bundled_found = True
            except Exception:
                pass

            if not bundled_found:
                tom_path = Path(__file__).parent / "models" / "smart-turn-v3.2-cpu.onnx"
                if tom_path.is_file():
                    bundled_found = True

            if not bundled_found:
                self._unavailable_reason = "Bundled Smart Turn model 'smart-turn-v3.2-cpu.onnx' not found"
                return False

        return True

    @property
    def execution_providers(self) -> list[str]:
        """List of active ONNX execution providers selected by the session."""
        return list(self._execution_providers)

    @property
    def resolved_model_path(self) -> str | None:
        """Path to the loaded ONNX model file."""
        return self._resolved_model_path

    def _ensure_analyzer(self) -> Any:
        """Load the configured analyzer, raising SmartTurnUnavailableError on failure."""
        if self._analyzer is not None:
            return self._analyzer

        if self._unavailable_reason is not None:
            raise SmartTurnUnavailableError(
                f"Smart Turn V3 is unavailable: {self._unavailable_reason}"
            )

        try:
            from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import (
                LocalSmartTurnAnalyzerV3,
            )
        except (ModuleNotFoundError, ImportError) as exc:
            self._unavailable_reason = (
                f"Pipecat Smart Turn dependencies are not installed: {exc}"
            )
            raise SmartTurnUnavailableError(self._unavailable_reason) from exc
        except Exception as exc:
            self._unavailable_reason = (
                f"Unexpected error loading Pipecat Smart Turn: {exc}"
            )
            raise SmartTurnUnavailableError(self._unavailable_reason) from exc

        try:
            analyzer = LocalSmartTurnAnalyzerV3(
                cpu_count=self._cpu_count,
                sample_rate=self._sample_rate,
                smart_turn_model_path=self._model_path,
            )
            self._analyzer = analyzer
            self._owns_analyzer = True

            session = getattr(analyzer, "_session", None)
            providers = (
                session.get_providers()
                if session is not None and hasattr(session, "get_providers")
                else ["CPUExecutionProvider"]
            )
            self._execution_providers = list(providers)
            resolved_path = (
                getattr(analyzer, "_smart_turn_model_path", None)
                or self._model_path
            )
            self._resolved_model_path = str(resolved_path) if resolved_path else None

            logger.info(
                "smart_turn_v3_initialized",
                model_path=self._resolved_model_path,
                providers=self._execution_providers,
                cpu_count=self._cpu_count,
            )
            return self._analyzer
        except Exception as exc:
            self._unavailable_reason = f"Failed to initialize Smart Turn V3 analyzer: {exc}"
            raise SmartTurnUnavailableError(self._unavailable_reason) from exc

    @property
    def analyzer(self) -> Any:
        """Return the configured analyzer, creating it on first use."""
        return self._ensure_analyzer()

    async def analyze(self, audio: bytes) -> TurnArbitrationDecision:
        """Analyze one VAD-delimited PCM segment.

        CONTINUING is intentionally not an error or a suppression signal. The
        coordinator treats it as a VAD fallback and continues through the
        existing STT/LLM/TTS path, while recording telemetry through the
        interaction latency tracer.
        """
        if not audio:
            return TurnArbitrationDecision.CONTINUING

        analyzer = self._ensure_analyzer()
        analyzer.append_audio(audio, is_speech=True)
        state, _ = await analyzer.analyze_end_of_turn()
        self._prepared = state is not None
        state_name = getattr(state, "name", str(state))
        if state_name == "COMPLETE" or str(state) == "EndOfTurnState.COMPLETE":
            return TurnArbitrationDecision.COMPLETE
        return TurnArbitrationDecision.CONTINUING

    async def cleanup(self) -> None:
        if self._owns_analyzer and self._analyzer is not None:
            cleanup_fn = getattr(self._analyzer, "cleanup", None)
            if cleanup_fn is not None:
                import inspect

                res = cleanup_fn()
                if inspect.isawaitable(res):
                    await res
            self._analyzer = None
            self._prepared = False


__all__ = [
    "SmartTurnUnavailableError",
    "SmartTurnV3Arbiter",
    "TurnArbitrationDecision",
    "TurnArbiter",
]
