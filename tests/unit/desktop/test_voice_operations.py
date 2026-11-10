from __future__ import annotations

from types import SimpleNamespace

import pytest
from tom.main import TomApp


class FakeProvider:
    def __init__(self, device: str = "cpu") -> None:
        self.device = device
        self.loaded = False
        self.loads = 0
        self.unloads = 0

    def get_model_info(self) -> dict[str, object]:
        return {"loaded": self.loaded, "model": "fixture-model", "device": self.device}

    async def load_model(self) -> dict[str, object]:
        self.loads += 1
        self.loaded = True
        return self.get_model_info()

    async def unload_model(self) -> dict[str, object]:
        self.unloads += 1
        self.loaded = False
        return self.get_model_info()


@pytest.mark.asyncio
async def test_voice_load_and_unload_reuse_configured_provider() -> None:
    app = TomApp()
    app._model_coordinator = None
    app._stt_provider = FakeProvider()

    loaded = await app._management_voice_operation("load_stt", {})
    unloaded = await app._management_voice_operation("unload_stt", {})

    assert loaded["success"] is True
    assert unloaded["success"] is True
    assert app._stt_provider.loads == 1
    assert app._stt_provider.unloads == 1


@pytest.mark.asyncio
async def test_unload_is_rejected_while_voice_pipeline_exists() -> None:
    app = TomApp()
    app._model_coordinator = None
    app._interaction_core = object()
    app._stt_provider = FakeProvider()

    with pytest.raises(RuntimeError, match="Stop the active Voice pipeline"):
        await app._management_voice_operation("unload_stt", {})
    assert app._stt_provider.unloads == 0


@pytest.mark.asyncio
async def test_cuda_stt_load_is_rejected_when_resource_budget_denies_it() -> None:
    class Telemetry:
        def get_telemetry(self):
            return type("Snapshot", (), {"gpu_available": True})()

    class Resources:
        telemetry = Telemetry()

        @staticmethod
        def check_budget(**_kwargs: object) -> bool:
            return False

    app = TomApp()
    app._model_coordinator = None
    app._resource_manager = Resources()
    app._stt_provider = FakeProvider(device="cuda")
    app._config = SimpleNamespace(
        models=SimpleNamespace(stt=SimpleNamespace(vram_required_mb=640))
    )

    with pytest.raises(RuntimeError, match="VRAM safety budget"):
        await app._management_voice_operation("load_stt", {})
    assert app._stt_provider.loads == 0


@pytest.mark.asyncio
async def test_cuda_stt_load_requires_configured_vram_estimate() -> None:
    app = TomApp()
    app._model_coordinator = None
    app._resource_manager = object()
    app._stt_provider = FakeProvider(device="cuda")
    app._config = SimpleNamespace(
        models=SimpleNamespace(stt=SimpleNamespace(vram_required_mb=0))
    )

    with pytest.raises(RuntimeError, match="vram_required_mb is not configured"):
        await app._management_voice_operation("load_stt", {})
    assert app._stt_provider.loads == 0


def test_voice_lifecycle_reports_starting_ready_degraded_and_stopped() -> None:
    app = TomApp()
    app._mode = "voice"
    app._interaction_core = None
    assert app._voice_lifecycle() == ("starting", None)

    app._voice_last_error = "RuntimeError: microphone unavailable"
    assert app._voice_lifecycle() == (
        "failed",
        "RuntimeError: microphone unavailable",
    )

    app._voice_last_error = None
    app._interaction_core = SimpleNamespace(
        state=SimpleNamespace(value="listening"),
        is_running=True,
        is_stopped=False,
    )
    app._microphone_loop = SimpleNamespace(is_running=True)
    assert app._voice_lifecycle() == ("ready", None)

    app._microphone_loop = SimpleNamespace(is_running=False)
    assert app._voice_lifecycle() == (
        "degraded",
        "Voice pipeline is running but the microphone is not capturing",
    )

    app._mode = "service"
    assert app._voice_lifecycle() == (
        "degraded",
        "Voice pipeline is running but the microphone is not capturing",
    )

    app._interaction_core = None
    app._microphone_loop = None
    assert app._voice_lifecycle() == ("stopped", None)


@pytest.mark.asyncio
async def test_stop_pipeline_releases_runtime_without_unloading_providers() -> None:
    from unittest.mock import AsyncMock

    class Core:
        is_stopped = False
        state = SimpleNamespace(value="listening")

        def __init__(self) -> None:
            self.stop = AsyncMock(side_effect=self._stop)

        async def _stop(self) -> None:
            self.is_stopped = True

    app = TomApp()
    app._mode = "service"
    app._stt_provider = FakeProvider()
    app._tts_provider = FakeProvider()
    app._interaction_core = Core()
    app._microphone_loop = SimpleNamespace(is_running=True)

    result = await app._management_voice_operation("stop_pipeline", {})

    assert result["success"] is True
    assert result["result"]["pipeline_active"] is False
    assert app._interaction_core is None
    assert app._microphone_loop is None
    assert app._stt_provider.unloads == 0
    assert app._tts_provider.unloads == 0


def test_voice_lifecycle_is_ready_inside_a_service_python_runtime() -> None:
    app = TomApp()
    app._mode = "service"
    app._interaction_core = SimpleNamespace(
        state=SimpleNamespace(value="listening"),
        is_running=True,
        is_stopped=False,
    )
    app._microphone_loop = SimpleNamespace(is_running=True)

    assert app._voice_lifecycle() == ("ready", None)
    assert app._voice_pipeline_active() is True

    app._interaction_core = None
    app._microphone_loop = None
    assert app._voice_pipeline_active() is False


@pytest.mark.asyncio
async def test_management_status_marks_stopped_voice_without_provider_errors() -> None:
    app = TomApp()
    app._mode = "service"
    app._stt_provider = FakeProvider()
    app._tts_provider = FakeProvider()

    status = await app._management_status()

    assert status["python"]["state"] == "starting"
    assert status["engine"]["state"] == "not_configured"
    assert status["model"]["state"] == "unavailable"
    assert status["voice"]["lifecycle_state"] == "stopped"
    assert status["voice"]["last_error"] is None
    assert status["voice"]["pipeline_active"] is False
    assert status["voice"]["captured_samples_scope"] == "current_capture_session"
    assert status["voice"]["stt"]["state"] == "unloaded"
    assert status["voice"]["tts"]["state"] == "unloaded"
