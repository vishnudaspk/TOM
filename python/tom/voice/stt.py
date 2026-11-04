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
from typing import TYPE_CHECKING, Any, Literal

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
        model_name: str = "C:/Users/vishnuu/Projects/TOM/models/faster-whisper-small.en",
        device: str = "cpu",
        compute_type: str = "int8",
        cpu_threads: int = 4,
        language: str | None = None,
        beam_size: int = 1,  # Optimized: beam_size=1 is ~10-15% faster than 5
        vad_filter: bool = False,
        mode: Literal["final_only", "rolling"] = "final_only",
        # Hallucination filter thresholds (Item 4)
        no_speech_prob_threshold: float = 0.6,
        avg_logprob_threshold: float = -1.0,
        compression_ratio_threshold: float = 2.4,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.cpu_threads = cpu_threads
        self.language = language
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self.mode = mode
        self.no_speech_prob_threshold = no_speech_prob_threshold
        self.avg_logprob_threshold = avg_logprob_threshold
        self.compression_ratio_threshold = compression_ratio_threshold


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
    - CUDA initialization failure is memoized to prevent repeated failing
      attempts on every utterance; falls back to CPU once and stays on CPU.
    - Serialization lock prevents concurrent model access when timeouts
      leave worker threads running (asyncio.wait_for() doesn't terminate them).
    """

    def __init__(self, config: STTConfig | None = None) -> None:
        self._config = config or STTConfig()
        self._model: Any = None  # WhisperModel, typed as Any to avoid import
        self._cuda_attempted: bool = False
        self._cuda_failed: bool = False
        self._cuda_failure_reason: str | None = None
        self._serialization_lock = asyncio.Lock()
        self._dropped_utterance_count: int = 0  # Item 4: hallucination filter counter

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _register_nvidia_dll_dirs() -> list[str]:
        """Add nvidia CUDA DLL directories to os.add_dll_directory and PATH.

        Required on Windows before importing ctranslate2/faster_whisper so the
        CUDA runtime libraries are found.  Returns the list of paths that were
        actually added (for logging / error reporting).
        """
        import os
        import site
        import sys

        added: list[str] = []
        # Collect candidate nvidia bin dirs from all active site-packages.
        site_pkgs: list[str] = site.getsitepackages() or []
        if hasattr(sys, "real_prefix") or (
            hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix
        ):
            # Inside a virtualenv — also check the venv site-packages.
            import sysconfig
            venv_sp = sysconfig.get_path("purelib")
            if venv_sp and venv_sp not in site_pkgs:
                site_pkgs.insert(0, venv_sp)

        nvidia_subdirs = ["cublas/bin", "cudnn/bin", "cuda_runtime/bin", "cublas64_12/bin"]
        for sp in site_pkgs:
            for sub in nvidia_subdirs:
                candidate = os.path.join(sp, "nvidia", sub)
                if os.path.isdir(candidate) and candidate not in added:
                    added.append(candidate)

        if added:
            # Prepend to PATH so subprocess spawned by ctranslate2 can find them.
            current_path = os.environ.get("PATH", "")
            prefix = os.pathsep.join(added)
            os.environ["PATH"] = prefix + os.pathsep + current_path
            # Also register each dir with the Windows DLL loader (Python 3.8+).
            if hasattr(os, "add_dll_directory"):
                for d in added:
                    try:
                        os.add_dll_directory(d)
                    except OSError:
                        pass  # non-fatal — PATH prepend is the primary mechanism

        return added

    def _ensure_loaded(self) -> None:
        """Load the WhisperModel if not already loaded.

        Raises:
            STTUnavailableError: faster-whisper not installed or model load failed.
        """
        if self._model is not None:
            return

        import logging
        import sys
        logger = logging.getLogger(__name__)

        device = self._config.device
        compute_type = self._config.compute_type
        cpu_threads = self._config.cpu_threads

        # On Windows, register nvidia DLL dirs *before* the first ctranslate2/
        # faster_whisper import so the CUDA runtime is found by the loader.
        if device == "cuda" and sys.platform == "win32" and not self._cuda_attempted:
            dll_dirs = self._register_nvidia_dll_dirs()
            if dll_dirs:
                logger.info(
                    "stt_startup: registered nvidia DLL dirs: %s", dll_dirs
                )
            else:
                logger.warning(
                    "stt_startup: no nvidia DLL dirs found; CUDA may fail "
                    "(searched site-packages/nvidia/*/bin)"
                )

        try:
            from faster_whisper import WhisperModel  # noqa: PLC0415
        except ImportError as exc:
            raise STTUnavailableError(
                "faster-whisper is not installed. Install it with: pip install faster-whisper"
            ) from exc

        # If CUDA was previously attempted and failed, skip CUDA and go straight to CPU
        if self._cuda_failed:
            logger.info(
                "Skipping CUDA initialization (previous failure: %s); using CPU",
                self._cuda_failure_reason,
            )
            device = "cpu"
            compute_type = "int8"

        if device == "cuda":
            # Try CUDA with int8_float16 for better performance
            if compute_type == "int8":
                compute_type = "int8_float16"
            try:
                self._model = WhisperModel(
                    self._config.model_name,
                    device="cuda",
                    compute_type=compute_type,
                )
                # Warm-up decode: eliminates first-utterance JIT compilation delay.
                import numpy as np
                dummy_audio = np.zeros(16000, dtype=np.float32)  # 1 second of silence
                list(self._model.transcribe(dummy_audio, beam_size=1, language="en")[0])
                logger.info(
                    "stt_startup: device=cuda, compute_type=%s, model=%s",
                    compute_type, self._config.model_name,
                )
                self._cuda_attempted = True
                self._cuda_failed = False
                self._cuda_failure_reason = None
                return
            except Exception as cuda_exc:
                self._cuda_attempted = True
                self._cuda_failed = True
                self._cuda_failure_reason = f"{type(cuda_exc).__name__}: {cuda_exc}"
                # Log ERROR loudly — never silently swallow CUDA failures.
                err_msg = (
                    f"stt_startup: CUDA initialization FAILED — {self._cuda_failure_reason}. "
                    f"DLL dirs tried: {getattr(self, '_last_dll_dirs', [])}. "
                    "Falling back to CPU/int8. STT will be significantly slower."
                )
                logger.error(err_msg)
                print(err_msg, file=sys.stderr)
                # Fall through to CPU

        # CPU fallback (or primary if CUDA failed/was skipped)
        try:
            self._model = WhisperModel(
                self._config.model_name,
                device="cpu",
                compute_type="int8",
                cpu_threads=cpu_threads,
            )
            logger.info(
                "stt_startup: device=cpu, compute_type=int8, model=%s, cpu_threads=%d",
                self._config.model_name, cpu_threads,
            )
        except Exception as exc:
            raise STTUnavailableError(
                f"Failed to load faster-whisper model '{self._config.model_name}': {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Hallucination filter (Item 4)
    # ------------------------------------------------------------------

    # Whisper commonly hallucinates these phrases on silent/noisy audio.
    _HALLUCINATION_BLOCKLIST: tuple[str, ...] = (
        "thank you for watching",
        "thanks for watching",
        "please subscribe",
        "subtitles by",
        "transcribed by",
        "www.",
        "visit our website",
        "copyright",
        "all rights reserved",
        "like and subscribe",
        "don't forget to subscribe",
        "[music]",
        "[applause]",
        "[laughter]",
        "[silence]",
    )

    def _is_hallucination(
        self,
        segments: list[Any],
        audio_duration_s: float,
    ) -> tuple[bool, str]:
        """Return (True, reason) if the transcription looks like a hallucination.

        Uses segment-level stats first; falls back to blocklist only when the
        audio is short or the model is uncertain.
        """
        import logging
        logger = logging.getLogger(__name__)

        if not segments:
            return False, ""

        # Aggregate stats across all segments.
        no_speech_probs = [getattr(s, "no_speech_prob", None) for s in segments]
        avg_logprobs = [getattr(s, "avg_logprob", None) for s in segments]
        compression_ratios = [getattr(s, "compression_ratio", None) for s in segments]

        no_speech_probs_valid = [p for p in no_speech_probs if isinstance(p, (int, float))]
        avg_logprobs_valid = [p for p in avg_logprobs if isinstance(p, (int, float))]
        compression_ratios_valid = [p for p in compression_ratios if isinstance(p, (int, float))]

        if no_speech_probs_valid:
            avg_no_speech = sum(no_speech_probs_valid) / len(no_speech_probs_valid)
            if avg_no_speech > self._config.no_speech_prob_threshold:
                return True, f"no_speech_prob={avg_no_speech:.3f} > {self._config.no_speech_prob_threshold}"

        if avg_logprobs_valid:
            avg_logprob = sum(avg_logprobs_valid) / len(avg_logprobs_valid)
            if avg_logprob < self._config.avg_logprob_threshold:
                return True, f"avg_logprob={avg_logprob:.3f} < {self._config.avg_logprob_threshold}"

        if compression_ratios_valid:
            max_cr = max(compression_ratios_valid)
            if max_cr > self._config.compression_ratio_threshold:
                return True, f"compression_ratio={max_cr:.3f} > {self._config.compression_ratio_threshold}"

        # Blocklist: only apply to short or low-confidence audio.
        is_short = audio_duration_s < 3.0
        is_uncertain = avg_logprobs_valid and (sum(avg_logprobs_valid) / len(avg_logprobs_valid)) < -0.5
        if is_short or is_uncertain:
            full_text = " ".join(
                str(getattr(s, "text", "")).strip().lower()
                for s in segments
                if isinstance(getattr(s, "text", None), str)
            )
            for phrase in self._HALLUCINATION_BLOCKLIST:
                if phrase in full_text:
                    return True, f"blocklist_match={phrase!r}"

        return False, ""

    def _transcribe_sync(
        self, request: TranscriptionRequest
    ) -> tuple[str, str | None, list[TranscriptionSegment]]:
        """Synchronous transcription — runs in thread pool."""
        self._ensure_loaded()

        import logging
        import numpy as np  # noqa: PLC0415

        logger = logging.getLogger(__name__)

        from tom.voice.audio_utils import pcm16_bytes_to_samples

        if isinstance(request.samples, bytes):
            audio = np.array(pcm16_bytes_to_samples(request.samples), dtype=np.float32)
        else:
            audio = np.array(request.samples, dtype=np.float32)

        audio_duration_s = len(audio) / max(getattr(request, "sample_rate", 16000), 1)

        # condition_on_previous_text=False prevents the model from "hallucinating"
        # continuations of prior transcriptions across unrelated utterances.
        segments_iter, info = self._model.transcribe(  # type: ignore[union-attr]
            audio,
            language=request.language or self._config.language,
            beam_size=self._config.beam_size,
            vad_filter=self._config.vad_filter,
            condition_on_previous_text=False,
            initial_prompt="Hey TOM. TOM.",
        )

        raw_segments: list[Any] = list(segments_iter)

        # Hallucination check (Item 4).
        is_hallucination, reason = self._is_hallucination(raw_segments, audio_duration_s)
        if is_hallucination:
            self._dropped_utterance_count += 1
            logger.warning(
                "stt_utterance_dropped: %s (dropped_total=%d)",
                reason,
                self._dropped_utterance_count,
            )
            detected_lang: str | None = getattr(info, "language", None)
            return "", detected_lang, []

        segments: list[TranscriptionSegment] = []
        full_text_parts: list[str] = []
        for seg in raw_segments:
            segments.append(
                TranscriptionSegment(
                    start=seg.start,
                    end=seg.end,
                    text=seg.text,
                    confidence=None,  # faster-whisper does not expose per-seg confidence
                )
            )
            full_text_parts.append(seg.text)

        detected_lang = getattr(info, "language", None)
        return "".join(full_text_parts).strip(), detected_lang, segments

    # ------------------------------------------------------------------
    # STTProvider interface
    # ------------------------------------------------------------------

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        t0 = time.monotonic()
        # Serialize calls to prevent concurrent model access when timeouts
        # leave worker threads running (asyncio.wait_for() doesn't terminate them).
        async with self._serialization_lock:
            try:
                # Add timeout to prevent indefinite blocking in thread pool (CPU inference)
                # 30s max utterance duration + 10s buffer = 40s timeout
                text, lang, segments = await asyncio.wait_for(
                    asyncio.to_thread(self._transcribe_sync, request),
                    timeout=40.0,
                )
            except STTError:
                raise
            except TimeoutError as exc:
                raise STTTranscriptionError(f"Transcription timed out after 40s: {exc}") from exc
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
        info = {
            "provider": "FasterWhisperSTTProvider",
            "model": self._config.model_name,
            "device": self._config.device,
            "compute_type": self._config.compute_type,
            "loaded": self._model is not None,
            "dropped_utterance_count": self._dropped_utterance_count,
        }
        if self._cuda_attempted:
            info["cuda_attempted"] = True
            info["cuda_failed"] = self._cuda_failed
            if self._cuda_failed:
                info["cuda_failure_reason"] = self._cuda_failure_reason
        return info

    @property
    def dropped_utterance_count(self) -> int:
        """Number of utterances dropped by the hallucination filter."""
        return self._dropped_utterance_count


__all__ = [
    "FasterWhisperSTTProvider",
    "MockSTTProvider",
    "STTConfig",
    "STTError",
    "STTProvider",
    "STTTranscriptionError",
    "STTUnavailableError",
]
