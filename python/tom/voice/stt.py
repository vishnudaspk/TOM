"""STT provider abstraction, Mock, and FasterWhisper implementations.

Architecture (Decision 042):
- STTProvider is the sole abstraction boundary; voice pipeline depends only on it.
- FasterWhisperSTTProvider uses lazy import — faster_whisper is never imported
  at module load time; missing package raises STTUnavailableError at use time.
- MockSTTProvider is deterministic and offline; requires no model or network.
- Audio input boundary: TranscriptionRequest.samples sourced from
  EngineClient.get_captured_speech() → AudioSpeechResponse.samples.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from tom.schemas.voice import TranscriptionRequest, TranscriptionResult, TranscriptionSegment

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class STTError(Exception):
    """Base class for all STT provider errors."""


class STTUnavailableError(STTError):
    """Raised when the provider or its dependencies are not available.

    Typical causes: faster-whisper not installed, model files missing.
    """


class STTTranscriptionError(STTError):
    """Raised when transcription fails at runtime (audio too short, corrupt, etc.)."""


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class STTProvider(ABC):
    """Abstract base class for all TOM speech-to-text providers.

    Concrete providers implement transcribe() and check_health().

    Error convention:
        - Missing dependency / model    → STTUnavailableError
        - Transcription failure         → STTTranscriptionError
        - asyncio.CancelledError        → re-raised, never swallowed
    """

    @abstractmethod
    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        """Transcribe audio to text.

        Args:
            request: Audio payload and language hint.

        Returns:
            TranscriptionResult with text, confidence, segments, latency.

        Raises:
            STTUnavailableError: Provider not ready.
            STTTranscriptionError: Transcription failed.
            asyncio.CancelledError: Re-raised if caller cancelled.
        """
        ...

    @abstractmethod
    async def check_health(self) -> bool:
        """Return True if the provider is ready to transcribe.

        Must not raise; return False on any failure.
        """
        ...

    @abstractmethod
    def get_model_info(self) -> dict[str, Any]:
        """Return provider/model metadata for logging and introspection."""
        ...


# ---------------------------------------------------------------------------
# MockSTTProvider — deterministic, offline, no dependencies
# ---------------------------------------------------------------------------


class MockSTTProvider(STTProvider):
    """Deterministic offline STT provider for tests.

    Configured at construction time with a fixed transcript text
    or a pre-built TranscriptionResult.  Set ``raise_error`` to
    simulate failure scenarios.

    Usage::

        provider = MockSTTProvider(transcript="hello world")
        result = await provider.transcribe(request)

        failing = MockSTTProvider(raise_error=STTUnavailableError("no model"))
        await failing.transcribe(request)  # raises STTUnavailableError
    """

    def __init__(
        self,
        transcript: str = "mock transcription",
        result: TranscriptionResult | None = None,
        raise_error: STTError | None = None,
        latency_ms: int = 0,
        language: str = "en",
    ) -> None:
        self._result = result
        self._transcript = transcript
        self._raise_error = raise_error
        self._latency_ms = latency_ms
        self._language = language

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        if self._raise_error is not None:
            raise self._raise_error
        if self._result is not None:
            return self._result
        sample_count = len(request.samples) if isinstance(request.samples, (list, bytes)) else 0
        duration_s = sample_count / max(request.sample_rate, 1)
        return TranscriptionResult(
            text=self._transcript,
            language=request.language or self._language,
            confidence=1.0,
            duration_s=duration_s,
            latency_ms=self._latency_ms,
            model_name="mock",
            provider="MockSTTProvider",
        )

    async def check_health(self) -> bool:
        return self._raise_error is None

    def get_model_info(self) -> dict[str, Any]:
        return {"provider": "MockSTTProvider", "model": "mock"}


# ---------------------------------------------------------------------------
# STTConfig — lightweight dataclass (not Pydantic — kept inside this module)
# ---------------------------------------------------------------------------


class STTConfig:
    """Configuration for FasterWhisperSTTProvider.

    Defaults are CPU-safe (int8 compute, 4 threads).
    """

    def __init__(
        self,
        model_name: str = "Systran/faster-whisper-large-v3-turbo",
        device: str = "cpu",
        compute_type: str = "int8",
        cpu_threads: int = 4,
        language: str | None = None,
        beam_size: int = 5,
        vad_filter: bool = False,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.cpu_threads = cpu_threads
        self.language = language
        self.beam_size = beam_size
        self.vad_filter = vad_filter


# ---------------------------------------------------------------------------
# FasterWhisperSTTProvider — lazy import, CPU-first
# ---------------------------------------------------------------------------


class FasterWhisperSTTProvider(STTProvider):
    """Local STT provider backed by faster-whisper (Systran/faster-whisper).

    Key design points (Decision 042):
    - faster_whisper is imported lazily inside _ensure_loaded(); the module
      never triggers a model download or GPU allocation at import time.
    - Defaults to CPU/int8; set config.device='cuda' for GPU inference.
    - Inference runs in a thread-pool via asyncio.to_thread() to avoid
      blocking the event loop.
    - Raises STTUnavailableError if faster-whisper is not installed or if
      the model cannot be loaded.
    """

    def __init__(self, config: STTConfig | None = None) -> None:
        self._config = config or STTConfig()
        self._model: Any = None  # WhisperModel, typed as Any to avoid import

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Load the WhisperModel if not already loaded.

        Raises:
            STTUnavailableError: faster-whisper not installed or model load failed.
        """
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel  # noqa: PLC0415
        except ImportError as exc:
            raise STTUnavailableError(
                "faster-whisper is not installed. Install it with: pip install faster-whisper"
            ) from exc
        try:
            self._model = WhisperModel(
                self._config.model_name,
                device=self._config.device,
                compute_type=self._config.compute_type,
                cpu_threads=self._config.cpu_threads,
            )
        except Exception as exc:
            raise STTUnavailableError(
                f"Failed to load faster-whisper model '{self._config.model_name}': {exc}"
            ) from exc

    def _transcribe_sync(
        self, request: TranscriptionRequest
    ) -> tuple[str, str | None, list[TranscriptionSegment]]:
        """Synchronous transcription — runs in thread pool."""
        self._ensure_loaded()

        import numpy as np  # noqa: PLC0415

        if isinstance(request.samples, bytes):
            audio = np.frombuffer(request.samples, dtype=np.float32)
        else:
            audio = np.array(request.samples, dtype=np.float32)

        segments_iter, info = self._model.transcribe(  # type: ignore[union-attr]
            audio,
            language=request.language or self._config.language,
            beam_size=self._config.beam_size,
            vad_filter=self._config.vad_filter,
        )

        segments: list[TranscriptionSegment] = []
        full_text_parts: list[str] = []
        for seg in segments_iter:
            segments.append(
                TranscriptionSegment(
                    start=seg.start,
                    end=seg.end,
                    text=seg.text,
                    confidence=None,  # faster-whisper does not expose per-seg confidence
                )
            )
            full_text_parts.append(seg.text)

        detected_lang: str | None = getattr(info, "language", None)
        return "".join(full_text_parts).strip(), detected_lang, segments

    # ------------------------------------------------------------------
    # STTProvider interface
    # ------------------------------------------------------------------

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        t0 = time.monotonic()
        try:
            text, lang, segments = await asyncio.to_thread(self._transcribe_sync, request)
        except STTError:
            raise
        except Exception as exc:
            raise STTTranscriptionError(f"Transcription failed: {exc}") from exc

        latency_ms = int((time.monotonic() - t0) * 1000)
        sample_count = (
            len(request.samples)
            if isinstance(request.samples, list)
            else len(request.samples) // 4  # float32 = 4 bytes
        )
        duration_s = sample_count / max(request.sample_rate, 1)
        return TranscriptionResult(
            text=text,
            language=lang or request.language,
            confidence=1.0,
            duration_s=duration_s,
            segments=segments,
            latency_ms=latency_ms,
            model_name=self._config.model_name,
            provider="FasterWhisperSTTProvider",
        )

    async def check_health(self) -> bool:
        try:
            self._ensure_loaded()
            return True
        except STTError:
            return False

    def get_model_info(self) -> dict[str, Any]:
        return {
            "provider": "FasterWhisperSTTProvider",
            "model": self._config.model_name,
            "device": self._config.device,
            "compute_type": self._config.compute_type,
            "loaded": self._model is not None,
        }


__all__ = [
    "FasterWhisperSTTProvider",
    "MockSTTProvider",
    "STTConfig",
    "STTError",
    "STTProvider",
    "STTTranscriptionError",
    "STTUnavailableError",
]
