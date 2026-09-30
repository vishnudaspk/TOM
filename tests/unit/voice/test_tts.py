"""Unit tests for Phase 6 Iteration 2: TTS Provider Abstraction.

All tests are deterministic and offline.
No microphone, speakers, network, GPU, CUDA, or kokoro-onnx installation required.

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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from tom.schemas.voice import SynthesisRequest, SynthesisResult
from tom.voice.tts import (
    KokoroTTSProvider,
    MockTTSProvider,
    TTSConfig,
    TTSError,
    TTSProvider,
    TTSSynthesisError,
    TTSUnavailableError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


# ===========================================================================
# SynthesisRequest schema tests
# ===========================================================================


class TestSynthesisRequest:
    def test_basic_construction(self) -> None:
        req = SynthesisRequest(text="Hello world")
        assert req.text == "Hello world"
        assert req.voice is None
        assert req.speed == 1.0
        assert req.sample_rate == 24000
        assert req.language is None

    def test_custom_parameters(self) -> None:
        req = SynthesisRequest(
            text="Testing custom params",
            voice="af_sky",
            speed=1.2,
            sample_rate=16000,
            language="en-us",
        )
        assert req.text == "Testing custom params"
        assert req.voice == "af_sky"
        assert req.speed == 1.2
        assert req.sample_rate == 16000
        assert req.language == "en-us"

    def test_empty_text_invalid(self) -> None:
        with pytest.raises(ValidationError):
            SynthesisRequest(text="")

    def test_whitespace_only_text_invalid(self) -> None:
        with pytest.raises(ValidationError):
            SynthesisRequest(text="   \n\t  ")

    def test_speed_lower_bound(self) -> None:
        with pytest.raises(ValidationError):
            SynthesisRequest(text="valid", speed=0.0)

    def test_speed_negative_invalid(self) -> None:
        with pytest.raises(ValidationError):
            SynthesisRequest(text="valid", speed=-0.5)

    def test_speed_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            SynthesisRequest(text="valid", speed=5.1)

    def test_sample_rate_lower_bound(self) -> None:
        with pytest.raises(ValidationError):
            SynthesisRequest(text="valid", sample_rate=0)

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            SynthesisRequest(text="valid", extra_field="forbidden")  # type: ignore[call-arg]


# ===========================================================================
# SynthesisResult schema tests
# ===========================================================================


class TestSynthesisResult:
    def test_basic_construction(self) -> None:
        samples = [0.0, 0.1, -0.1]
        res = SynthesisResult(samples=samples)
        assert res.samples == samples
        assert res.sample_rate == 24000
        assert res.channels == 1
        assert res.duration_s == 0.0
        assert res.latency_ms == 0
        assert res.model_name == ""
        assert res.provider == ""
        assert res.metadata == {}

    def test_custom_parameters(self) -> None:
        samples = [0.0] * 2400
        res = SynthesisResult(
            samples=samples,
            sample_rate=24000,
            channels=1,
            duration_s=0.1,
            latency_ms=45,
            model_name="kokoro-v0_19.onnx",
            provider="KokoroTTSProvider",
            metadata={"voice": "af_heart"},
        )
        assert len(res.samples) == 2400
        assert res.sample_rate == 24000
        assert res.channels == 1
        assert res.duration_s == 0.1
        assert res.latency_ms == 45
        assert res.model_name == "kokoro-v0_19.onnx"
        assert res.provider == "KokoroTTSProvider"
        assert res.metadata == {"voice": "af_heart"}

    def test_negative_duration_invalid(self) -> None:
        with pytest.raises(ValidationError):
            SynthesisResult(samples=[0.0], duration_s=-0.1)

    def test_negative_latency_invalid(self) -> None:
        with pytest.raises(ValidationError):
            SynthesisResult(samples=[0.0], latency_ms=-1)

    def test_channels_bounds(self) -> None:
        with pytest.raises(ValidationError):
            SynthesisResult(samples=[0.0], channels=0)
        with pytest.raises(ValidationError):
            SynthesisResult(samples=[0.0], channels=3)

    def test_sample_rate_bounds(self) -> None:
        with pytest.raises(ValidationError):
            SynthesisResult(samples=[0.0], sample_rate=0)

    def test_extra_fields_ignored(self) -> None:
        res = SynthesisResult(samples=[0.0], unexpected_field="ignored")  # type: ignore[call-arg]
        assert not hasattr(res, "unexpected_field")


# ===========================================================================
# TTSProvider ABC tests
# ===========================================================================


class TestTTSProviderABC:
    def test_cannot_instantiate_abc(self) -> None:
        with pytest.raises(TypeError):
            TTSProvider()  # type: ignore[abstract]

    def test_incomplete_subclass_cannot_instantiate(self) -> None:
        class IncompleteTTSProvider(TTSProvider):
            async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
                return SynthesisResult(samples=[0.0])

        with pytest.raises(TypeError):
            IncompleteTTSProvider()  # type: ignore[abstract]


# ===========================================================================
# Error hierarchy tests
# ===========================================================================


class TestTTSErrorHierarchy:
    def test_base_error(self) -> None:
        err = TTSError("base error")
        assert isinstance(err, Exception)

    def test_unavailable_error(self) -> None:
        err = TTSUnavailableError("kokoro-onnx missing")
        assert isinstance(err, TTSError)

    def test_synthesis_error(self) -> None:
        err = TTSSynthesisError("inference failed")
        assert isinstance(err, TTSError)


# ===========================================================================
# MockTTSProvider tests
# ===========================================================================


class TestMockTTSProvider:
    def test_default_synthesize(self) -> None:
        provider = MockTTSProvider()
        req = SynthesisRequest(text="Hello TOM", sample_rate=24000)
        result = run_async(provider.synthesize(req))

        assert isinstance(result, SynthesisResult)
        assert len(result.samples) == 2400  # 0.1s at 24000Hz
        assert result.sample_rate == 24000
        assert result.channels == 1
        assert result.duration_s == pytest.approx(0.1)
        assert result.provider == "MockTTSProvider"
        assert result.model_name == "mock"

    def test_custom_samples(self) -> None:
        custom_samples = [0.1, 0.2, 0.3, 0.4]
        provider = MockTTSProvider(samples=custom_samples, sample_rate=16000)
        req = SynthesisRequest(text="Testing custom samples", sample_rate=16000)
        result = run_async(provider.synthesize(req))

        assert result.samples == custom_samples
        assert result.sample_rate == 16000
        assert result.duration_s == pytest.approx(4 / 16000)

    def test_custom_prebuilt_result(self) -> None:
        expected = SynthesisResult(
            samples=[0.5, -0.5],
            sample_rate=22050,
            duration_s=0.05,
            latency_ms=12,
            provider="CustomMock",
        )
        provider = MockTTSProvider(result=expected)
        req = SynthesisRequest(text="Hello")
        result = run_async(provider.synthesize(req))
        assert result is expected

    def test_raise_error_simulation(self) -> None:
        provider = MockTTSProvider(raise_error=TTSUnavailableError("model offline"))
        req = SynthesisRequest(text="Hello")
        with pytest.raises(TTSUnavailableError, match="model offline"):
            run_async(provider.synthesize(req))

    def test_check_health_healthy(self) -> None:
        provider = MockTTSProvider()
        assert run_async(provider.check_health()) is True

    def test_check_health_failing(self) -> None:
        provider = MockTTSProvider(raise_error=TTSUnavailableError("offline"))
        assert run_async(provider.check_health()) is False

    def test_get_model_info(self) -> None:
        provider = MockTTSProvider(sample_rate=24000)
        info = provider.get_model_info()
        assert info["provider"] == "MockTTSProvider"
        assert info["model"] == "mock"
        assert info["sample_rate"] == 24000

    def test_deterministic_repeated_calls(self) -> None:
        provider = MockTTSProvider()
        req = SynthesisRequest(text="Repeatable")
        res1 = run_async(provider.synthesize(req))
        res2 = run_async(provider.synthesize(req))
        assert res1.samples == res2.samples
        assert res1.sample_rate == res2.sample_rate
        assert res1.duration_s == res2.duration_s


# ===========================================================================
# TTSConfig tests
# ===========================================================================


class TestTTSConfig:
    def test_defaults_are_cpu_safe(self) -> None:
        cfg = TTSConfig()
        assert cfg.device == "cpu"
        assert cfg.sample_rate == 24000
        assert cfg.default_voice == "af_heart"
        assert cfg.speed == 1.0
        assert cfg.lang == "en-us"

    def test_custom_config(self) -> None:
        cfg = TTSConfig(
            model_path="custom.onnx",
            voices_path="custom_voices.bin",
            default_voice="af_bella",
            device="cpu",
            sample_rate=22050,
            speed=1.1,
            lang="en-gb",
        )
        assert cfg.model_path == "custom.onnx"
        assert cfg.voices_path == "custom_voices.bin"
        assert cfg.default_voice == "af_bella"
        assert cfg.sample_rate == 22050
        assert cfg.speed == 1.1
        assert cfg.lang == "en-gb"


# ===========================================================================
# KokoroTTSProvider tests
# ===========================================================================


class TestKokoroTTSProvider:
    def test_instantiation_does_not_import_kokoro(self) -> None:
        provider = KokoroTTSProvider()
        assert provider._model is None

    def test_missing_dependency_raises_unavailable(self) -> None:
        provider = KokoroTTSProvider()
        with patch.dict(sys.modules, {"kokoro_onnx": None}):
            with pytest.raises(TTSUnavailableError, match="kokoro-onnx is not installed"):
                run_async(provider.synthesize(SynthesisRequest(text="Hello")))

    def test_check_health_returns_false_on_missing_dependency(self) -> None:
        provider = KokoroTTSProvider()
        with patch.dict(sys.modules, {"kokoro_onnx": None}):
            assert run_async(provider.check_health()) is False

    def test_initialization_failure_raises_unavailable(self) -> None:
        mock_module = MagicMock()
        mock_module.Kokoro.side_effect = RuntimeError("Failed to load onnx weights")

        provider = KokoroTTSProvider()
        with patch.dict(sys.modules, {"kokoro_onnx": mock_module}):
            with pytest.raises(TTSUnavailableError, match="Failed to load Kokoro model"):
                run_async(provider.synthesize(SynthesisRequest(text="Hello")))

    def test_successful_synthesis_with_mocked_model(self) -> None:
        mock_kokoro_instance = MagicMock()
        synthetic_samples = [0.0] * 4800  # 0.2s at 24kHz
        mock_kokoro_instance.create.return_value = (synthetic_samples, 24000)

        mock_module = MagicMock()
        mock_module.Kokoro.return_value = mock_kokoro_instance

        provider = KokoroTTSProvider()
        with patch.dict(sys.modules, {"kokoro_onnx": mock_module}):
            req = SynthesisRequest(text="Hello TOM", voice="af_heart", speed=1.0)
            result = run_async(provider.synthesize(req))

            assert isinstance(result, SynthesisResult)
            assert result.samples == synthetic_samples
            assert result.sample_rate == 24000
            assert result.duration_s == pytest.approx(0.2)
            assert result.provider == "KokoroTTSProvider"
            assert result.metadata["voice"] == "af_heart"
            mock_kokoro_instance.create.assert_called_once_with(
                "Hello TOM",
                voice="af_heart",
                speed=1.0,
                lang="en-us",
            )

    def test_numpy_array_samples_conversion(self) -> None:
        # Test when create() returns a numpy array mock with .tolist()
        mock_array = MagicMock()
        mock_array.tolist.return_value = [0.1, -0.1, 0.2]
        mock_kokoro_instance = MagicMock()
        mock_kokoro_instance.create.return_value = (mock_array, 24000)

        mock_module = MagicMock()
        mock_module.Kokoro.return_value = mock_kokoro_instance

        provider = KokoroTTSProvider()
        with patch.dict(sys.modules, {"kokoro_onnx": mock_module}):
            req = SynthesisRequest(text="Testing numpy conversion")
            result = run_async(provider.synthesize(req))

            assert result.samples == [0.1, -0.1, 0.2]
            assert result.sample_rate == 24000

    def test_synthesis_runtime_failure_raises_synthesis_error(self) -> None:
        mock_kokoro_instance = MagicMock()
        mock_kokoro_instance.create.side_effect = RuntimeError("ONNX Runtime inference error")

        mock_module = MagicMock()
        mock_module.Kokoro.return_value = mock_kokoro_instance

        provider = KokoroTTSProvider()
        with patch.dict(sys.modules, {"kokoro_onnx": mock_module}):
            req = SynthesisRequest(text="Failing synthesis")
            with pytest.raises(TTSSynthesisError, match="TTS synthesis failed"):
                run_async(provider.synthesize(req))

    def test_cancelled_error_is_reraised(self) -> None:
        mock_kokoro_instance = MagicMock()
        mock_kokoro_instance.create.side_effect = asyncio.CancelledError()

        mock_module = MagicMock()
        mock_module.Kokoro.return_value = mock_kokoro_instance

        provider = KokoroTTSProvider()
        with patch.dict(sys.modules, {"kokoro_onnx": mock_module}):
            req = SynthesisRequest(text="Cancelled request")
            with pytest.raises(asyncio.CancelledError):
                run_async(provider.synthesize(req))

    def test_check_health_healthy_with_mocked_model(self) -> None:
        mock_module = MagicMock()
        mock_module.Kokoro.return_value = MagicMock()

        provider = KokoroTTSProvider()
        with patch.dict(sys.modules, {"kokoro_onnx": mock_module}):
            assert run_async(provider.check_health()) is True

    def test_get_model_info(self) -> None:
        cfg = TTSConfig(
            model_path="test_model.onnx",
            voices_path="test_voices.bin",
            default_voice="af_bella",
            device="cpu",
            sample_rate=24000,
        )
        provider = KokoroTTSProvider(config=cfg)
        info = provider.get_model_info()
        assert info["provider"] == "KokoroTTSProvider"
        assert info["model"] == "test_model.onnx"
        assert info["voices_path"] == "test_voices.bin"
        assert info["default_voice"] == "af_bella"
        assert info["device"] == "cpu"
        assert info["loaded"] is False


# ===========================================================================
# EngineClient Audio Output Seam integration test
# ===========================================================================


class TestAudioPlaybackSeam:
    def test_synthesis_result_feeds_play_audio_buffer(self) -> None:
        """Verify SynthesisResult output is directly compatible with EngineClient.play_audio_buffer."""
        from tom.core.engine import EngineClient

        mock_ipc = AsyncMock()
        mock_ipc.request.return_value = {
            "success": True,
            "message": "Enqueued 2400 samples",
        }
        client = EngineClient(mock_ipc)

        provider = MockTTSProvider()
        req = SynthesisRequest(text="Play this speech", sample_rate=24000)
        result = run_async(provider.synthesize(req))

        response = run_async(
            client.play_audio_buffer(
                samples=result.samples,
                sample_rate=result.sample_rate,
                channels=result.channels,
            )
        )

        assert response.success is True
        mock_ipc.request.assert_called_once_with(
            "audio.play_buffer",
            {
                "samples": result.samples,
                "sample_rate": 24000,
                "channels": 1,
            },
        )
