"""TTS provider abstraction, Mock, and Kokoro implementations.

Architecture (Decision 041, Decision 042):
- TTSProvider is the sole abstraction boundary; voice pipeline depends only on it.
- KokoroTTSProvider uses lazy import — kokoro_onnx is never imported at module load
  time; missing package raises TTSUnavailableError at use time.
- MockTTSProvider is deterministic and offline; requires no model or network.
- Audio output boundary: SynthesisResult.samples (normalised float32 PCM) directly
  compatible with EngineClient.play_audio_buffer(samples, ...).
- CPU-first execution: preserves GPU VRAM for primary LLM routing.
- asyncio.to_thread for non-blocking execution of synchronous model inference.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from tom.schemas.voice import SynthesisRequest, SynthesisResult

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TTSError(Exception):
    """Base class for all TTS provider errors."""


class TTSUnavailableError(TTSError):
    """Raised when the provider or its dependencies are not available.

    Typical causes: kokoro-onnx not installed, model/voices files missing.
    """


class TTSSynthesisError(TTSError):
    """Raised when synthesis fails at runtime."""


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class TTSProvider(ABC):
    """Abstract base class for all TOM text-to-speech providers.

    Concrete providers implement synthesize(), check_health(), and get_model_info().

    Error convention:
        - Missing dependency / model    → TTSUnavailableError
        - Synthesis failure             → TTSSynthesisError
        - asyncio.CancelledError        → re-raised, never swallowed
    """

    @abstractmethod
    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        """Synthesize text to audio samples.

        Args:
            request: Text and voice parameters.

        Returns:
            SynthesisResult with normalised float32 PCM samples, duration, latency.

        Raises:
            TTSUnavailableError: Provider or model not ready.
            TTSSynthesisError: Synthesis failed.
            asyncio.CancelledError: Re-raised if caller cancelled.
        """
        ...

    @abstractmethod
    async def check_health(self) -> bool:
        """Return True if the provider is ready to synthesize.

        Must not raise; return False on any failure.
        """
        ...

    @abstractmethod
    def get_model_info(self) -> dict[str, Any]:
        """Return provider/model metadata for logging and introspection."""
        ...


# ---------------------------------------------------------------------------
# MockTTSProvider — deterministic, offline, no dependencies
# ---------------------------------------------------------------------------


class MockTTSProvider(TTSProvider):
    """Deterministic offline TTS provider for tests.

    Configured at construction time with pre-built samples or result.
    Set ``raise_error`` to simulate failure scenarios.

    Usage::

        provider = MockTTSProvider()
        result = await provider.synthesize(SynthesisRequest(text="hello"))

        failing = MockTTSProvider(raise_error=TTSUnavailableError("no model"))
        await failing.synthesize(request)  # raises TTSUnavailableError
    """

    def __init__(
        self,
        samples: list[float] | None = None,
        result: SynthesisResult | None = None,
        raise_error: TTSError | None = None,
        latency_ms: int = 0,
        sample_rate: int = 24000,
        channels: int = 1,
    ) -> None:
        self._samples = samples
        self._result = result
        self._raise_error = raise_error
        self._latency_ms = latency_ms
        self._sample_rate = sample_rate
        self._channels = channels

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        if self._raise_error is not None:
            raise self._raise_error
        if self._result is not None:
            return self._result

        # If custom samples provided, use them; otherwise 0.1s silence at requested sample rate
        sr = request.sample_rate or self._sample_rate
        if self._samples is not None:
            samples = list(self._samples)
        else:
            # Deterministic synthetic samples: 0.1s of silence
            samples = [0.0] * max(int(sr * 0.1), 1)

        duration_s = len(samples) / max(sr, 1)

        return SynthesisResult(
            samples=samples,
            sample_rate=sr,
            channels=self._channels,
            duration_s=duration_s,
            latency_ms=self._latency_ms,
            model_name="mock",
            provider="MockTTSProvider",
        )

    async def check_health(self) -> bool:
        return self._raise_error is None

    def get_model_info(self) -> dict[str, Any]:
        return {
            "provider": "MockTTSProvider",
            "model": "mock",
            "sample_rate": self._sample_rate,
        }


# ---------------------------------------------------------------------------
# TTSConfig — lightweight config class (kept inside this module)
# ---------------------------------------------------------------------------


class TTSConfig:
    """Configuration for KokoroTTSProvider.

    Defaults are CPU-safe (CPU device, 24kHz sample rate).
    """

    def __init__(
        self,
        model_path: str = "kokoro-v0_19.onnx",
        voices_path: str = "voices.bin",
        default_voice: str = "af_heart",
        device: str = "cpu",
        sample_rate: int = 24000,
        speed: float = 1.0,
        lang: str = "en-us",
    ) -> None:
        self.model_path = model_path
        self.voices_path = voices_path
        self.default_voice = default_voice
        self.device = device
        self.sample_rate = sample_rate
        self.speed = speed
        self.lang = lang


# ---------------------------------------------------------------------------
# KokoroTTSProvider — lazy import, CPU-first
# ---------------------------------------------------------------------------


class KokoroTTSProvider(TTSProvider):
    """Local TTS provider backed by Kokoro ONNX (hexgrad/Kokoro-82M).

    Key design points (Decision 041, Decision 042):
    - kokoro_onnx is imported lazily inside _ensure_loaded(); the module
      never triggers a model download or memory allocation at import time.
    - Defaults to CPU operation; preserves GPU VRAM for the primary LLM.
    - Synthesis runs in a thread-pool via asyncio.to_thread() to avoid
      blocking the event loop.
    - Raises TTSUnavailableError if kokoro-onnx is not installed or if
      the model files cannot be loaded.
    """

    def __init__(self, config: TTSConfig | None = None) -> None:
        self._config = config or TTSConfig()
        self._model: Any = None  # Kokoro instance, typed as Any to avoid import

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Load the Kokoro model if not already loaded.

        Raises:
            TTSUnavailableError: kokoro-onnx not installed or model load failed.
        """
        if self._model is not None:
            return
        try:
            import kokoro_onnx  # noqa: PLC0415
        except ImportError as exc:
            raise TTSUnavailableError(
                "kokoro-onnx is not installed. Install it with: pip install kokoro-onnx"
            ) from exc
        try:
            self._model = kokoro_onnx.Kokoro(
                self._config.model_path,
                self._config.voices_path,
            )
        except Exception as exc:
            raise TTSUnavailableError(
                f"Failed to load Kokoro model from '{self._config.model_path}': {exc}"
            ) from exc

    def _synthesize_sync(self, request: SynthesisRequest) -> tuple[list[float], int, float]:
        """Synchronous synthesis — runs in thread pool."""
        self._ensure_loaded()

        voice = request.voice or self._config.default_voice
        speed = request.speed or self._config.speed
        lang = request.language or self._config.lang

        result = self._model.create(  # type: ignore[union-attr]
            request.text,
            voice=voice,
            speed=speed,
            lang=lang,
        )

        # Kokoro ONNX returns (samples, sample_rate)
        if isinstance(result, tuple) and len(result) >= 2:
            raw_samples, sr = result[0], int(result[1])
        else:
            raw_samples, sr = result, self._config.sample_rate

        if hasattr(raw_samples, "tolist"):
            samples_list = [float(x) for x in raw_samples.tolist()]
        elif isinstance(raw_samples, (list, tuple)):
            samples_list = [float(x) for x in raw_samples]
        else:
            samples_list = []

        duration_s = len(samples_list) / max(sr, 1)
        return samples_list, sr, duration_s

    # ------------------------------------------------------------------
    # TTSProvider interface
    # ------------------------------------------------------------------

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        t0 = time.monotonic()
        try:
            samples, sr, duration_s = await asyncio.to_thread(self._synthesize_sync, request)
        except TTSError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise TTSSynthesisError(f"TTS synthesis failed: {exc}") from exc

        latency_ms = int((time.monotonic() - t0) * 1000)

        return SynthesisResult(
            samples=samples,
            sample_rate=sr,
            channels=1,
            duration_s=duration_s,
            latency_ms=latency_ms,
            model_name=self._config.model_path,
            provider="KokoroTTSProvider",
            metadata={
                "voice": request.voice or self._config.default_voice,
                "speed": request.speed or self._config.speed,
            },
        )

    async def check_health(self) -> bool:
        try:
            self._ensure_loaded()
            return True
        except TTSError:
            return False
        except Exception:
            return False

    def get_model_info(self) -> dict[str, Any]:
        return {
            "provider": "KokoroTTSProvider",
            "model": self._config.model_path,
            "voices_path": self._config.voices_path,
            "default_voice": self._config.default_voice,
            "device": self._config.device,
            "sample_rate": self._config.sample_rate,
            "loaded": self._model is not None,
        }


__all__ = [
    "KokoroTTSProvider",
    "MockTTSProvider",
    "TTSConfig",
    "TTSError",
    "TTSProvider",
    "TTSSynthesisError",
    "TTSUnavailableError",
]
