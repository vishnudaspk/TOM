"""Unit tests for Phase 6 Iteration 1: STT Provider Abstraction.

All tests are deterministic and offline.
No microphone, network, GPU, CUDA, or faster-whisper installation required.

Convention matches existing TOM tests:
- Synchronous def test_ methods
- Async coroutines run with run_async(asyncio.run(...)) helper
- No pytest.mark.asyncio (strict-markers mode)
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Coroutine
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from tom.ipc.protocol import AudioSpeechResponse
from tom.schemas.voice import (
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionSegment,
)
from tom.voice.stt import (
    FasterWhisperSTTProvider,
    MockSTTProvider,
    STTConfig,
    STTError,
    STTProvider,
    STTTranscriptionError,
    STTUnavailableError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


SILENCE = [0.0] * 1600  # 0.1s of silence at 16kHz


def _make_request(
    samples: list[float] | None = None,
    language: str | None = None,
) -> TranscriptionRequest:
    return TranscriptionRequest(
        samples=samples or SILENCE,
        sample_rate=16000,
        channels=1,
        language=language,
    )


# ===========================================================================
# TranscriptionSegment schema
# ===========================================================================


class TestTranscriptionSegment:
    def test_basic_construction(self) -> None:
        seg = TranscriptionSegment(start=0.0, end=1.5, text="hello")
        assert seg.start == 0.0
        assert seg.end == 1.5
        assert seg.text == "hello"
        assert seg.confidence is None

    def test_with_confidence(self) -> None:
        seg = TranscriptionSegment(start=0.0, end=1.0, text="hi", confidence=0.95)
        assert seg.confidence == 0.95

    def test_confidence_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            TranscriptionSegment(start=0.0, end=1.0, text="x", confidence=1.5)

    def test_confidence_lower_bound(self) -> None:
        with pytest.raises(ValidationError):
            TranscriptionSegment(start=0.0, end=1.0, text="x", confidence=-0.1)

    def test_negative_start_invalid(self) -> None:
        with pytest.raises(ValidationError):
            TranscriptionSegment(start=-0.1, end=1.0, text="x")


# ===========================================================================
# TranscriptionRequest schema
# ===========================================================================


class TestTranscriptionRequest:
    def test_default_sample_rate(self) -> None:
        req = TranscriptionRequest(samples=SILENCE)
        assert req.sample_rate == 16000
        assert req.channels == 1
        assert req.language is None

    def test_bytes_samples(self) -> None:
        raw = b"\x00\x00\x80\x3f"  # 1.0 as float32 little-endian
        req = TranscriptionRequest(samples=raw)
        assert isinstance(req.samples, bytes)

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            TranscriptionRequest(samples=SILENCE, unknown_field="x")  # type: ignore[call-arg]

    def test_language_field(self) -> None:
        req = TranscriptionRequest(samples=SILENCE, language="en")
        assert req.language == "en"


# ===========================================================================
# TranscriptionResult schema
# ===========================================================================


class TestTranscriptionResult:
    def test_defaults(self) -> None:
        result = TranscriptionResult(text="hello")
        assert result.text == "hello"
        assert result.confidence == 1.0
        assert result.duration_s == 0.0
        assert result.segments == []
        assert result.latency_ms == 0
        assert result.model_name == ""
        assert result.provider == ""
        assert result.metadata == {}

    def test_confidence_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            TranscriptionResult(text="x", confidence=1.1)

    def test_with_segments(self) -> None:
        seg = TranscriptionSegment(start=0.0, end=1.0, text="hello")
        result = TranscriptionResult(text="hello", segments=[seg])
        assert len(result.segments) == 1

    def test_extra_fields_ignored(self) -> None:
        # extra='ignore' — unknown fields must not raise
        result = TranscriptionResult.model_validate({"text": "hi", "unknown": "dropped"})
        assert result.text == "hi"


# ===========================================================================
# STTProvider is abstract
# ===========================================================================


class TestSTTProviderInterface:
    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            STTProvider()  # type: ignore[abstract]

    def test_subclass_missing_check_health_not_instantiable(self) -> None:
        class Broken(STTProvider):
            async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
                return TranscriptionResult(text="x")

            def get_model_info(self) -> dict[str, Any]:
                return {}

        with pytest.raises(TypeError):
            Broken()  # type: ignore[abstract]

    def test_subclass_missing_transcribe_not_instantiable(self) -> None:
        class Broken(STTProvider):
            async def check_health(self) -> bool:
                return True

            def get_model_info(self) -> dict[str, Any]:
                return {}

        with pytest.raises(TypeError):
            Broken()  # type: ignore[abstract]


# ===========================================================================
# MockSTTProvider
# ===========================================================================


class TestMockSTTProvider:
    def test_default_transcript(self) -> None:
        async def _test() -> None:
            provider = MockSTTProvider()
            result = await provider.transcribe(_make_request())
            assert result.text == "mock transcription"
            assert result.provider == "MockSTTProvider"
            assert result.model_name == "mock"

        run_async(_test())

    def test_custom_transcript(self) -> None:
        async def _test() -> None:
            provider = MockSTTProvider(transcript="hello world")
            result = await provider.transcribe(_make_request())
            assert result.text == "hello world"

        run_async(_test())

    def test_language_from_request(self) -> None:
        async def _test() -> None:
            provider = MockSTTProvider()
            result = await provider.transcribe(_make_request(language="fr"))
            assert result.language == "fr"

        run_async(_test())

    def test_language_default_when_request_none(self) -> None:
        async def _test() -> None:
            provider = MockSTTProvider(language="de")
            result = await provider.transcribe(_make_request())
            assert result.language == "de"

        run_async(_test())

    def test_duration_calculated_from_samples(self) -> None:
        async def _test() -> None:
            samples = [0.0] * 16000  # 1 second at 16kHz
            provider = MockSTTProvider()
            result = await provider.transcribe(_make_request(samples=samples))
            assert abs(result.duration_s - 1.0) < 0.01

        run_async(_test())

    def test_preconfigured_result(self) -> None:
        async def _test() -> None:
            expected = TranscriptionResult(text="preconfigured", language="es", confidence=0.9)
            provider = MockSTTProvider(result=expected)
            result = await provider.transcribe(_make_request())
            assert result is expected

        run_async(_test())

    def test_raises_unavailable_error(self) -> None:
        async def _test() -> None:
            error = STTUnavailableError("no model")
            provider = MockSTTProvider(raise_error=error)
            with pytest.raises(STTUnavailableError, match="no model"):
                await provider.transcribe(_make_request())

        run_async(_test())

    def test_raises_transcription_error(self) -> None:
        async def _test() -> None:
            error = STTTranscriptionError("audio corrupt")
            provider = MockSTTProvider(raise_error=error)
            with pytest.raises(STTTranscriptionError, match="audio corrupt"):
                await provider.transcribe(_make_request())

        run_async(_test())

    def test_check_health_true_when_no_error(self) -> None:
        async def _test() -> None:
            provider = MockSTTProvider()
            assert await provider.check_health() is True

        run_async(_test())

    def test_check_health_false_when_error_configured(self) -> None:
        async def _test() -> None:
            provider = MockSTTProvider(raise_error=STTUnavailableError("down"))
            assert await provider.check_health() is False

        run_async(_test())

    def test_get_model_info(self) -> None:
        provider = MockSTTProvider()
        info = provider.get_model_info()
        assert info["provider"] == "MockSTTProvider"
        assert info["model"] == "mock"

    def test_latency_ms_propagated(self) -> None:
        async def _test() -> None:
            provider = MockSTTProvider(latency_ms=42)
            result = await provider.transcribe(_make_request())
            assert result.latency_ms == 42

        run_async(_test())


# ===========================================================================
# STTConfig
# ===========================================================================


class TestSTTConfig:
    def test_defaults_are_cpu_safe(self) -> None:
        cfg = STTConfig()
        assert cfg.device == "cpu"
        assert cfg.compute_type == "int8"
        assert cfg.cpu_threads == 4
        assert cfg.language is None
        assert cfg.vad_filter is False

    def test_custom_values(self) -> None:
        cfg = STTConfig(
            model_name="tiny.en",
            device="cuda",
            compute_type="float16",
            cpu_threads=8,
            language="en",
            beam_size=3,
            vad_filter=True,
        )
        assert cfg.model_name == "tiny.en"
        assert cfg.device == "cuda"
        assert cfg.compute_type == "float16"
        assert cfg.language == "en"
        assert cfg.beam_size == 3
        assert cfg.vad_filter is True


# ===========================================================================
# FasterWhisperSTTProvider — all tests mock faster_whisper
# ===========================================================================


class TestFasterWhisperSTTProviderConfig:
    def test_default_config(self) -> None:
        provider = FasterWhisperSTTProvider()
        info = provider.get_model_info()
        assert info["device"] == "cpu"
        assert info["compute_type"] == "int8"
        assert info["loaded"] is False

    def test_custom_config_reflected_in_info(self) -> None:
        cfg = STTConfig(model_name="tiny", device="cpu", compute_type="float32")
        provider = FasterWhisperSTTProvider(config=cfg)
        info = provider.get_model_info()
        assert info["model"] == "tiny"
        assert info["compute_type"] == "float32"

    def test_model_not_loaded_at_construction(self) -> None:
        # Constructing must NEVER import faster_whisper or load a model
        provider = FasterWhisperSTTProvider()
        assert provider._model is None  # noqa: SLF001


class TestFasterWhisperMissingDependency:
    def test_missing_faster_whisper_raises_unavailable(self) -> None:
        """Simulate faster_whisper not installed."""
        # Temporarily remove faster_whisper from sys.modules
        saved = sys.modules.pop("faster_whisper", ...)
        try:
            provider = FasterWhisperSTTProvider()
            provider._model = None  # noqa: SLF001
            with pytest.raises(STTUnavailableError, match="faster-whisper"):
                provider._ensure_loaded()  # noqa: SLF001
        finally:
            if saved is not ...:
                sys.modules["faster_whisper"] = saved  # type: ignore[assignment]
            elif "faster_whisper" in sys.modules:
                del sys.modules["faster_whisper"]

    def test_transcribe_raises_unavailable_on_missing_dep(self) -> None:
        async def _test() -> None:
            saved = sys.modules.pop("faster_whisper", ...)
            try:
                provider = FasterWhisperSTTProvider()
                provider._model = None  # noqa: SLF001
                with pytest.raises(STTUnavailableError):
                    await provider.transcribe(_make_request())
            finally:
                if saved is not ...:
                    sys.modules["faster_whisper"] = saved  # type: ignore[assignment]
                elif "faster_whisper" in sys.modules:
                    del sys.modules["faster_whisper"]

        run_async(_test())

    def test_check_health_false_on_missing_dep(self) -> None:
        async def _test() -> None:
            saved = sys.modules.pop("faster_whisper", ...)
            try:
                provider = FasterWhisperSTTProvider()
                provider._model = None  # noqa: SLF001
                result = await provider.check_health()
                assert result is False
            finally:
                if saved is not ...:
                    sys.modules["faster_whisper"] = saved  # type: ignore[assignment]
                elif "faster_whisper" in sys.modules:
                    del sys.modules["faster_whisper"]

        run_async(_test())


def _make_mock_whisper_module() -> MagicMock:
    """Return a fake faster_whisper module with a WhisperModel class."""
    mock_module = MagicMock()

    mock_segment = MagicMock()
    mock_segment.start = 0.0
    mock_segment.end = 1.0
    mock_segment.text = " hello world"

    mock_info = MagicMock()
    mock_info.language = "en"

    mock_model_instance = MagicMock()
    mock_model_instance.transcribe.return_value = ([mock_segment], mock_info)

    mock_module.WhisperModel.return_value = mock_model_instance
    return mock_module


def _make_mock_numpy() -> MagicMock:
    mock_np = MagicMock()
    mock_np.frombuffer.return_value = MagicMock()
    mock_np.array.return_value = MagicMock()
    return mock_np


class TestFasterWhisperSTTProviderWithMockModel:
    """Tests using a fully mocked WhisperModel — no real faster-whisper needed."""

    def test_transcribe_success(self) -> None:
        async def _test() -> None:
            mock_fw = _make_mock_whisper_module()
            mock_np = _make_mock_numpy()

            provider = FasterWhisperSTTProvider(config=STTConfig(model_name="tiny"))
            sys.modules["faster_whisper"] = mock_fw
            sys.modules["numpy"] = mock_np
            try:
                result = await provider.transcribe(_make_request())
            finally:
                sys.modules.pop("faster_whisper", None)
                sys.modules.pop("numpy", None)

            assert result.text == "hello world"
            assert result.language == "en"
            assert len(result.segments) == 1
            assert result.segments[0].start == 0.0
            assert result.provider == "FasterWhisperSTTProvider"

        run_async(_test())

    def test_lazy_load_model_on_first_transcribe(self) -> None:
        async def _test() -> None:
            mock_fw = _make_mock_whisper_module()
            mock_np = _make_mock_numpy()

            provider = FasterWhisperSTTProvider()
            assert provider._model is None  # noqa: SLF001

            sys.modules["faster_whisper"] = mock_fw
            sys.modules["numpy"] = mock_np
            try:
                await provider.transcribe(_make_request())
            finally:
                sys.modules.pop("faster_whisper", None)
                sys.modules.pop("numpy", None)

            assert provider._model is not None  # noqa: SLF001

        run_async(_test())

    def test_model_loaded_only_once(self) -> None:
        async def _test() -> None:
            mock_fw = _make_mock_whisper_module()
            mock_np = _make_mock_numpy()

            provider = FasterWhisperSTTProvider()
            sys.modules["faster_whisper"] = mock_fw
            sys.modules["numpy"] = mock_np
            try:
                await provider.transcribe(_make_request())
                await provider.transcribe(_make_request())
            finally:
                sys.modules.pop("faster_whisper", None)
                sys.modules.pop("numpy", None)

            mock_fw.WhisperModel.assert_called_once()

        run_async(_test())

    def test_check_health_true_when_model_loads(self) -> None:
        async def _test() -> None:
            mock_fw = _make_mock_whisper_module()

            provider = FasterWhisperSTTProvider()
            sys.modules["faster_whisper"] = mock_fw
            try:
                result = await provider.check_health()
            finally:
                sys.modules.pop("faster_whisper", None)

            assert result is True

        run_async(_test())

    def test_model_load_failure_raises_unavailable(self) -> None:
        mock_fw = MagicMock()
        mock_fw.WhisperModel.side_effect = RuntimeError("model files not found")

        provider = FasterWhisperSTTProvider()
        sys.modules["faster_whisper"] = mock_fw
        try:
            with pytest.raises(STTUnavailableError, match="Failed to load"):
                provider._ensure_loaded()  # noqa: SLF001
        finally:
            sys.modules.pop("faster_whisper", None)

    def test_runtime_transcription_error_wrapped(self) -> None:
        async def _test() -> None:
            mock_fw = _make_mock_whisper_module()
            mock_fw.WhisperModel.return_value.transcribe.side_effect = ValueError("bad audio")
            mock_np = _make_mock_numpy()

            provider = FasterWhisperSTTProvider()
            sys.modules["faster_whisper"] = mock_fw
            sys.modules["numpy"] = mock_np
            try:
                with pytest.raises(STTTranscriptionError, match="Transcription failed"):
                    await provider.transcribe(_make_request())
            finally:
                sys.modules.pop("faster_whisper", None)
                sys.modules.pop("numpy", None)

        run_async(_test())

    def test_bytes_samples_handled(self) -> None:
        async def _test() -> None:
            mock_fw = _make_mock_whisper_module()
            mock_np = _make_mock_numpy()

            raw_bytes = b"\x00\x00\x80\x3f"
            provider = FasterWhisperSTTProvider()
            req = TranscriptionRequest(samples=raw_bytes, sample_rate=16000)

            sys.modules["faster_whisper"] = mock_fw
            sys.modules["numpy"] = mock_np
            try:
                result = await provider.transcribe(req)
            finally:
                sys.modules.pop("faster_whisper", None)
                sys.modules.pop("numpy", None)

            mock_np.frombuffer.assert_called_once()
            assert result.text == "hello world"

        run_async(_test())

    def test_language_from_request_passed_to_model(self) -> None:
        async def _test() -> None:
            mock_fw = _make_mock_whisper_module()
            mock_np = _make_mock_numpy()

            provider = FasterWhisperSTTProvider()
            sys.modules["faster_whisper"] = mock_fw
            sys.modules["numpy"] = mock_np
            try:
                await provider.transcribe(_make_request(language="de"))
            finally:
                sys.modules.pop("faster_whisper", None)
                sys.modules.pop("numpy", None)

            call_kwargs = mock_fw.WhisperModel.return_value.transcribe.call_args.kwargs
            assert call_kwargs.get("language") == "de"

        run_async(_test())


# ===========================================================================
# EngineClient → STT integration seam
# ===========================================================================


class TestEngineClientAudioToSTTSeam:
    """Validate the hand-off from AudioSpeechResponse -> TranscriptionRequest.

    No real engine or microphone — AudioSpeechResponse constructed directly.
    """

    @staticmethod
    def _make_audio_response(samples: list[float] | None = None) -> AudioSpeechResponse:
        s = SILENCE if samples is None else samples
        return AudioSpeechResponse(
            samples=s,
            sample_rate=16000,
            channels=1,
            sample_count=len(s),
        )

    def test_audio_response_to_transcription_request(self) -> None:
        async def _test() -> None:
            audio = self._make_audio_response(samples=[0.1] * 3200)
            req = TranscriptionRequest(
                samples=audio.samples,
                sample_rate=audio.sample_rate,
                channels=audio.channels,
            )
            provider = MockSTTProvider(transcript="from audio seam")
            result = await provider.transcribe(req)
            assert result.text == "from audio seam"

        run_async(_test())

    def test_sample_rate_propagated(self) -> None:
        audio = self._make_audio_response()
        req = TranscriptionRequest(
            samples=audio.samples,
            sample_rate=audio.sample_rate,
            channels=audio.channels,
        )
        assert req.sample_rate == 16000

    def test_mock_provider_duration_from_audio_response(self) -> None:
        async def _test() -> None:
            samples_1s = [0.0] * 16000
            audio = self._make_audio_response(samples=samples_1s)
            req = TranscriptionRequest(
                samples=audio.samples,
                sample_rate=audio.sample_rate,
                channels=audio.channels,
            )
            provider = MockSTTProvider()
            result = await provider.transcribe(req)
            assert abs(result.duration_s - 1.0) < 0.01

        run_async(_test())

    def test_empty_audio_response_handled(self) -> None:
        async def _test() -> None:
            audio = self._make_audio_response(samples=[])
            req = TranscriptionRequest(
                samples=audio.samples,
                sample_rate=audio.sample_rate,
                channels=audio.channels,
            )
            provider = MockSTTProvider()
            result = await provider.transcribe(req)
            assert result.duration_s == 0.0

        run_async(_test())

    def test_engine_client_exposes_get_captured_speech(self) -> None:
        from tom.core.engine import EngineClient

        assert hasattr(EngineClient, "get_captured_speech")


# ===========================================================================
# Error hierarchy
# ===========================================================================


class TestSTTErrorHierarchy:
    def test_unavailable_is_stt_error(self) -> None:
        err = STTUnavailableError("missing dep")
        assert isinstance(err, STTError)
        assert isinstance(err, Exception)

    def test_transcription_error_is_stt_error(self) -> None:
        err = STTTranscriptionError("bad audio")
        assert isinstance(err, STTError)
        assert isinstance(err, Exception)

    def test_unavailable_not_transcription(self) -> None:
        err = STTUnavailableError("x")
        assert not isinstance(err, STTTranscriptionError)
