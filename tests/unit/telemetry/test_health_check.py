"""Unit tests for health check CLI."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tom.ipc.errors import ConnectionLostError
from tom.schemas.config import TOMConfig

from scripts.health_check import (
    CheckResult,
    CheckStatus,
    check_engine_ipc,
    check_gpu_telemetry,
    check_inference_endpoint,
    check_model_states,
    check_system_ram,
    compute_exit_code,
    run_health_check,
)


def make_config() -> TOMConfig:
    """Create a test configuration."""
    config = TOMConfig()
    config.telemetry.health_check_timeout_ms = 500
    return config


class TestExitCodeComputation:
    """Test exit code precedence logic."""

    def test_all_healthy_returns_zero(self) -> None:
        results = [
            CheckResult("Engine IPC", CheckStatus.HEALTHY, "ok"),
            CheckResult("Inference Endpoint", CheckStatus.HEALTHY, "ok"),
            CheckResult("GPU Telemetry", CheckStatus.HEALTHY, "ok"),
            CheckResult("System RAM", CheckStatus.HEALTHY, "ok"),
            CheckResult("Model States", CheckStatus.HEALTHY, "ok"),
        ]
        assert compute_exit_code(results) == 0

    def test_one_degraded_returns_one(self) -> None:
        results = [
            CheckResult("Engine IPC", CheckStatus.HEALTHY, "ok"),
            CheckResult("Inference Endpoint", CheckStatus.DEGRADED, "degraded"),
            CheckResult("GPU Telemetry", CheckStatus.HEALTHY, "ok"),
            CheckResult("System RAM", CheckStatus.HEALTHY, "ok"),
        ]
        assert compute_exit_code(results) == 1

    def test_one_unavailable_returns_two(self) -> None:
        results = [
            CheckResult("Engine IPC", CheckStatus.HEALTHY, "ok"),
            CheckResult("Inference Endpoint", CheckStatus.UNAVAILABLE, "unavail"),
            CheckResult("GPU Telemetry", CheckStatus.HEALTHY, "ok"),
            CheckResult("System RAM", CheckStatus.HEALTHY, "ok"),
        ]
        assert compute_exit_code(results) == 2

    def test_failed_returns_two(self) -> None:
        results = [
            CheckResult("Engine IPC", CheckStatus.FAILED, "error"),
            CheckResult("Inference Endpoint", CheckStatus.HEALTHY, "ok"),
        ]
        assert compute_exit_code(results) == 2

    def test_degraded_plus_unavailable_returns_two(self) -> None:
        results = [
            CheckResult("Engine IPC", CheckStatus.DEGRADED, "degraded"),
            CheckResult("Inference Endpoint", CheckStatus.UNAVAILABLE, "unavail"),
        ]
        assert compute_exit_code(results) == 2

    def test_model_states_unavailable_does_not_affect_exit_code(self) -> None:
        results = [
            CheckResult("Engine IPC", CheckStatus.HEALTHY, "ok"),
            CheckResult("Inference Endpoint", CheckStatus.HEALTHY, "ok"),
            CheckResult("GPU Telemetry", CheckStatus.HEALTHY, "ok"),
            CheckResult("System RAM", CheckStatus.HEALTHY, "ok"),
            CheckResult("Model States", CheckStatus.UNAVAILABLE, "not available"),
        ]
        assert compute_exit_code(results) == 0

    def test_model_states_degraded_does_not_affect_exit_code(self) -> None:
        results = [
            CheckResult("Engine IPC", CheckStatus.HEALTHY, "ok"),
            CheckResult("Inference Endpoint", CheckStatus.HEALTHY, "ok"),
            CheckResult("GPU Telemetry", CheckStatus.HEALTHY, "ok"),
            CheckResult("System RAM", CheckStatus.HEALTHY, "ok"),
            CheckResult("Model States", CheckStatus.DEGRADED, "some issue"),
        ]
        assert compute_exit_code(results) == 0


class TestEngineIPCCheck:
    """Test engine IPC health check."""

    @pytest.mark.asyncio
    async def test_healthy_ping(self) -> None:
        config = make_config()
        mock_ipc = AsyncMock()
        mock_engine = AsyncMock()
        mock_engine.ping = AsyncMock(return_value=MagicMock(pong=True, version="1.0.0"))

        with patch("scripts.health_check.EngineClient", return_value=mock_engine):
            result = await check_engine_ipc(
                mock_ipc, config.telemetry.health_check_timeout_ms / 1000
            )

        assert result.status == CheckStatus.HEALTHY
        assert "1.0.0" in result.message

    @pytest.mark.asyncio
    async def test_ping_false(self) -> None:
        config = make_config()
        mock_ipc = AsyncMock()
        mock_engine = AsyncMock()
        mock_engine.ping = AsyncMock(return_value=MagicMock(pong=False, version=""))

        with patch("scripts.health_check.EngineClient", return_value=mock_engine):
            result = await check_engine_ipc(
                mock_ipc, config.telemetry.health_check_timeout_ms / 1000
            )

        assert result.status == CheckStatus.UNAVAILABLE
        assert "pong=false" in result.message.lower()

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        config = make_config()
        mock_ipc = AsyncMock()
        mock_engine = AsyncMock()
        mock_engine.ping = AsyncMock(side_effect=TimeoutError())

        with patch("scripts.health_check.EngineClient", return_value=mock_engine):
            result = await check_engine_ipc(
                mock_ipc, config.telemetry.health_check_timeout_ms / 1000
            )

        assert result.status == CheckStatus.UNAVAILABLE
        assert "timeout" in result.message.lower()

    @pytest.mark.asyncio
    async def test_not_connected(self) -> None:
        from tom.ipc.errors import NotConnectedError

        config = make_config()
        mock_ipc = AsyncMock()
        mock_engine = AsyncMock()
        mock_engine.ping = AsyncMock(side_effect=NotConnectedError("not connected"))

        with patch("scripts.health_check.EngineClient", return_value=mock_engine):
            result = await check_engine_ipc(
                mock_ipc, config.telemetry.health_check_timeout_ms / 1000
            )

        assert result.status == CheckStatus.UNAVAILABLE
        assert "not connected" in result.message.lower()

    @pytest.mark.asyncio
    async def test_connection_lost(self) -> None:
        from tom.ipc.errors import ConnectionLostError

        config = make_config()
        mock_ipc = AsyncMock()
        mock_engine = AsyncMock()
        mock_engine.ping = AsyncMock(side_effect=ConnectionLostError("lost"))

        with patch("scripts.health_check.EngineClient", return_value=mock_engine):
            result = await check_engine_ipc(
                mock_ipc, config.telemetry.health_check_timeout_ms / 1000
            )

        assert result.status == CheckStatus.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_unexpected_error(self) -> None:
        config = make_config()
        mock_ipc = AsyncMock()
        mock_engine = AsyncMock()
        mock_engine.ping = AsyncMock(side_effect=RuntimeError("unexpected"))

        with patch("scripts.health_check.EngineClient", return_value=mock_engine):
            result = await check_engine_ipc(
                mock_ipc, config.telemetry.health_check_timeout_ms / 1000
            )

        assert result.status == CheckStatus.FAILED
        assert "unexpected" in result.message.lower()


class TestInferenceEndpointCheck:
    """Test inference endpoint health check."""

    @pytest.mark.asyncio
    async def test_healthy(self) -> None:
        config = make_config()
        mock_provider = AsyncMock()
        mock_provider.check_health = AsyncMock(return_value=True)

        result = await check_inference_endpoint(
            mock_provider, config.telemetry.health_check_timeout_ms / 1000
        )

        assert result.status == CheckStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_unavailable(self) -> None:
        config = make_config()
        mock_provider = AsyncMock()
        mock_provider.check_health = AsyncMock(return_value=False)

        result = await check_inference_endpoint(
            mock_provider, config.telemetry.health_check_timeout_ms / 1000
        )

        assert result.status == CheckStatus.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        config = make_config()
        mock_provider = AsyncMock()
        mock_provider.check_health = AsyncMock(side_effect=TimeoutError())

        result = await check_inference_endpoint(
            mock_provider, config.telemetry.health_check_timeout_ms / 1000
        )

        assert result.status == CheckStatus.UNAVAILABLE
        assert "timeout" in result.message.lower()

    @pytest.mark.asyncio
    async def test_unexpected_error(self) -> None:
        config = make_config()
        mock_provider = AsyncMock()
        mock_provider.check_health = AsyncMock(side_effect=RuntimeError("unexpected"))

        result = await check_inference_endpoint(
            mock_provider, config.telemetry.health_check_timeout_ms / 1000
        )

        assert result.status == CheckStatus.FAILED


class TestGPUCheck:
    """Test GPU telemetry check."""

    @pytest.mark.asyncio
    async def test_engine_unhealthy_skipped(self) -> None:
        config = make_config()
        mock_ipc = AsyncMock()

        result = await check_gpu_telemetry(
            mock_ipc, False, config.telemetry.health_check_timeout_ms / 1000
        )

        assert result.status == CheckStatus.UNAVAILABLE
        assert "skipped" in result.message.lower()

    @pytest.mark.asyncio
    async def test_gpu_available_with_headroom(self) -> None:
        config = make_config()
        mock_ipc = AsyncMock()
        mock_engine = AsyncMock()
        mock_engine.get_gpu = AsyncMock(
            return_value=MagicMock(
                available=True,
                memory_total_bytes=8_000_000_000,
                memory_used_bytes=5_000_000_000,
            )
        )

        with patch("scripts.health_check.EngineClient", return_value=mock_engine):
            result = await check_gpu_telemetry(
                mock_ipc, True, config.telemetry.health_check_timeout_ms / 1000
            )

        assert result.status == CheckStatus.HEALTHY
        assert "headroom" in result.message

    @pytest.mark.asyncio
    async def test_gpu_low_headroom(self) -> None:
        config = make_config()
        mock_ipc = AsyncMock()
        mock_engine = AsyncMock()
        # 8GB total, 7.6GB used = 400MB headroom
        mock_engine.get_gpu = AsyncMock(
            return_value=MagicMock(
                available=True,
                memory_total_bytes=8_000_000_000,
                memory_used_bytes=7_600_000_000,
            )
        )

        with patch("scripts.health_check.EngineClient", return_value=mock_engine):
            result = await check_gpu_telemetry(
                mock_ipc, True, config.telemetry.health_check_timeout_ms / 1000
            )

        assert result.status == CheckStatus.DEGRADED
        assert "low vram" in result.message.lower()

    @pytest.mark.asyncio
    async def test_gpu_unavailable(self) -> None:
        config = make_config()
        mock_ipc = AsyncMock()
        mock_engine = AsyncMock()
        mock_engine.get_gpu = AsyncMock(
            return_value=MagicMock(available=False, reason="no GPU detected")
        )

        with patch("scripts.health_check.EngineClient", return_value=mock_engine):
            result = await check_gpu_telemetry(
                mock_ipc, True, config.telemetry.health_check_timeout_ms / 1000
            )

        assert result.status == CheckStatus.UNAVAILABLE
        assert "unavailable" in result.message.lower()

    @pytest.mark.asyncio
    async def test_gpu_ipc_error(self) -> None:
        from tom.ipc.errors import ConnectionLostError

        config = make_config()
        mock_ipc = AsyncMock()
        mock_engine = AsyncMock()
        mock_engine.get_gpu = AsyncMock(side_effect=ConnectionLostError("lost"))

        with patch("scripts.health_check.EngineClient", return_value=mock_engine):
            result = await check_gpu_telemetry(
                mock_ipc, True, config.telemetry.health_check_timeout_ms / 1000
            )

        assert result.status == CheckStatus.UNAVAILABLE


class TestRAMCheck:
    """Test system RAM check."""

    @pytest.mark.asyncio
    async def test_engine_unhealthy_skipped(self) -> None:
        config = make_config()
        mock_ipc = AsyncMock()

        result = await check_system_ram(
            mock_ipc, False, config.telemetry.health_check_timeout_ms / 1000
        )

        assert result.status == CheckStatus.UNAVAILABLE
        assert "skipped" in result.message.lower()

    @pytest.mark.asyncio
    async def test_ram_healthy(self) -> None:
        config = make_config()
        mock_ipc = AsyncMock()
        mock_engine = AsyncMock()
        mock_engine.get_memory = AsyncMock(return_value=MagicMock(available_bytes=4_000_000_000))

        with patch("scripts.health_check.EngineClient", return_value=mock_engine):
            result = await check_system_ram(
                mock_ipc, True, config.telemetry.health_check_timeout_ms / 1000
            )

        assert result.status == CheckStatus.HEALTHY
        assert "4.00" in result.message or "available" in result.message.lower()

    @pytest.mark.asyncio
    async def test_ram_degraded(self) -> None:
        config = make_config()
        mock_ipc = AsyncMock()
        mock_engine = AsyncMock()
        mock_engine.get_memory = AsyncMock(return_value=MagicMock(available_bytes=1_500_000_000))

        with patch("scripts.health_check.EngineClient", return_value=mock_engine):
            result = await check_system_ram(
                mock_ipc, True, config.telemetry.health_check_timeout_ms / 1000
            )

        assert result.status == CheckStatus.DEGRADED
        assert "low available" in result.message.lower()

    @pytest.mark.asyncio
    async def test_ram_ipc_error(self) -> None:
        from tom.ipc.errors import ConnectionLostError

        config = make_config()
        mock_ipc = AsyncMock()
        mock_engine = AsyncMock()
        mock_engine.get_memory = AsyncMock(side_effect=ConnectionLostError("lost"))

        with patch("scripts.health_check.EngineClient", return_value=mock_engine):
            result = await check_system_ram(
                mock_ipc, True, config.telemetry.health_check_timeout_ms / 1000
            )

        assert result.status == CheckStatus.UNAVAILABLE


class TestModelStatesCheck:
    """Test model lifecycle states check (informational)."""

    @pytest.mark.asyncio
    async def test_coordinator_none(self) -> None:
        config = make_config()

        result = await check_model_states(None, config.telemetry.health_check_timeout_ms / 1000)

        assert result.status == CheckStatus.UNAVAILABLE
        assert "not available" in result.message.lower()

    @pytest.mark.asyncio
    async def test_no_models(self) -> None:
        config = make_config()
        mock_coord = AsyncMock()
        mock_coord.list_models = AsyncMock(return_value=[])

        result = await check_model_states(
            mock_coord, config.telemetry.health_check_timeout_ms / 1000
        )

        assert result.status == CheckStatus.HEALTHY
        assert "no models" in result.message.lower()

    @pytest.mark.asyncio
    async def test_with_models(self) -> None:
        config = make_config()
        mock_coord = AsyncMock()

        # Mock registrations
        mock_reg1 = MagicMock()
        mock_reg1.model_name = "model-a"
        mock_reg2 = MagicMock()
        mock_reg2.model_name = "model-b"
        mock_coord.list_models = AsyncMock(return_value=[mock_reg1, mock_reg2])

        # Mock records
        mock_record1 = MagicMock()
        mock_record1.state.value = "ready"
        mock_record1.active_acquisitions = 0
        mock_record2 = MagicMock()
        mock_record2.state.value = "busy"
        mock_record2.active_acquisitions = 1

        async def get_record(name):
            if name == "model-a":
                return mock_record1
            return mock_record2

        mock_coord.get_record = get_record

        result = await check_model_states(
            mock_coord, config.telemetry.health_check_timeout_ms / 1000
        )

        assert result.status == CheckStatus.HEALTHY
        assert "ready" in result.message
        assert "busy" in result.message
        assert "active acquisitions: 1" in result.message

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        config = make_config()
        mock_coord = AsyncMock()
        mock_coord.list_models = AsyncMock(side_effect=TimeoutError())

        result = await check_model_states(
            mock_coord, config.telemetry.health_check_timeout_ms / 1000
        )

        assert result.status == CheckStatus.UNAVAILABLE
        assert "timeout" in result.message.lower()

    @pytest.mark.asyncio
    async def test_error_does_not_fail_required_checks(self) -> None:
        """Model states error returns UNAVAILABLE but doesn't affect required checks."""
        config = make_config()
        mock_coord = AsyncMock()
        mock_coord.list_models = AsyncMock(side_effect=RuntimeError("db error"))

        result = await check_model_states(
            mock_coord, config.telemetry.health_check_timeout_ms / 1000
        )

        assert result.status == CheckStatus.UNAVAILABLE


class TestRunHealthCheck:
    """Integration test for the full health check run."""

    @pytest.mark.asyncio
    async def test_full_run_with_mocks(self) -> None:
        config = make_config()

        # Mock IPC client
        mock_ipc = AsyncMock()
        mock_ipc.connect = AsyncMock()
        mock_ipc.close = AsyncMock()

        # Mock EngineClient for all engine calls
        mock_engine = AsyncMock()
        mock_engine.ping = AsyncMock(return_value=MagicMock(pong=True, version="1.0.0"))
        mock_engine.get_gpu = AsyncMock(
            return_value=MagicMock(
                available=True,
                memory_total_bytes=8_000_000_000,
                memory_used_bytes=5_000_000_000,
            )
        )
        mock_engine.get_memory = AsyncMock(return_value=MagicMock(available_bytes=4_000_000_000))

        # Mock provider
        mock_provider = AsyncMock()
        mock_provider.check_health = AsyncMock(return_value=True)

        # Mock coordinator
        mock_coord = AsyncMock()
        mock_coord.list_models = AsyncMock(return_value=[])
        mock_coord.get_record = AsyncMock(side_effect=RuntimeError("not found"))

        with patch("scripts.health_check.NamedPipeIpcClient", return_value=mock_ipc):
            with patch("scripts.health_check.EngineClient", return_value=mock_engine):
                with patch("scripts.health_check.HttpModelProvider", return_value=mock_provider):
                    with patch(
                        "tom.resources.model_manager.ModelLifecycleCoordinator",
                        return_value=mock_coord,
                    ):
                        exit_code, results = await run_health_check(config)

        assert exit_code == 0
        assert len(results) == 5
        names = [r.name for r in results]
        assert names == [
            "Engine IPC",
            "Inference Endpoint",
            "GPU Telemetry",
            "System RAM",
            "Model States",
        ]
        assert all(r.status == CheckStatus.HEALTHY for r in results if r.name != "Model States")

    @pytest.mark.asyncio
    async def test_ipc_cleanup_on_exception(self) -> None:
        """Verify IPC client is closed even when checks raise exceptions."""
        config = make_config()

        mock_ipc = AsyncMock()
        mock_ipc.connect = AsyncMock()
        mock_ipc.close = AsyncMock()

        mock_engine = AsyncMock()
        mock_engine.ping = AsyncMock(side_effect=RuntimeError("unexpected"))

        mock_provider = AsyncMock()
        mock_provider.check_health = AsyncMock(return_value=True)

        with patch("scripts.health_check.NamedPipeIpcClient", return_value=mock_ipc):
            with patch("scripts.health_check.EngineClient", return_value=mock_engine):
                with patch("scripts.health_check.HttpModelProvider", return_value=mock_provider):
                    exit_code, _ = await run_health_check(config)

        # close should be called even on exception
        mock_ipc.close.assert_called_once()
        assert exit_code == 2  # FAILED

    @pytest.mark.asyncio
    async def test_ipc_cleanup_on_timeout(self) -> None:
        """Verify IPC client is closed on timeout."""
        config = make_config()

        mock_ipc = AsyncMock()
        mock_ipc.connect = AsyncMock()
        mock_ipc.close = AsyncMock()

        mock_engine = AsyncMock()
        mock_engine.ping = AsyncMock(side_effect=TimeoutError())

        mock_provider = AsyncMock()
        mock_provider.check_health = AsyncMock(return_value=True)

        with patch("scripts.health_check.NamedPipeIpcClient", return_value=mock_ipc):
            with patch("scripts.health_check.EngineClient", return_value=mock_engine):
                with patch("scripts.health_check.HttpModelProvider", return_value=mock_provider):
                    exit_code, _ = await run_health_check(config)

        mock_ipc.close.assert_called_once()
        assert exit_code == 2  # UNAVAILABLE

    @pytest.mark.asyncio
    async def test_inference_runs_when_ipc_fails(self) -> None:
        """Inference check runs independently when IPC fails."""
        config = make_config()

        mock_ipc = AsyncMock()
        mock_ipc.connect = AsyncMock(side_effect=ConnectionLostError("connect failed"))
        mock_ipc.close = AsyncMock()

        mock_provider = AsyncMock()
        mock_provider.check_health = AsyncMock(return_value=True)

        with patch("scripts.health_check.NamedPipeIpcClient", return_value=mock_ipc):
            with patch("scripts.health_check.HttpModelProvider", return_value=mock_provider):
                exit_code, results = await run_health_check(config)

        # Inference should still run
        mock_provider.check_health.assert_called_once()
        # IPC checks should be UNAVAILABLE
        engine_result = next(r for r in results if r.name == "Engine IPC")
        assert engine_result.status == CheckStatus.UNAVAILABLE
        # Inference should be HEALTHY
        inf_result = next(r for r in results if r.name == "Inference Endpoint")
        assert inf_result.status == CheckStatus.HEALTHY
        # GPU/RAM should be skipped (UNAVAILABLE)
        gpu_result = next(r for r in results if r.name == "GPU Telemetry")
        assert gpu_result.status == CheckStatus.UNAVAILABLE
        ram_result = next(r for r in results if r.name == "System RAM")
        assert ram_result.status == CheckStatus.UNAVAILABLE


class TestCLIInvocation:
    """Test CLI can be imported and has main function."""

    def test_module_has_main(self) -> None:
        import scripts.health_check as hc

        assert hasattr(hc, "main")
        assert callable(hc.main)

    def test_module_has_run_health_check(self) -> None:
        import scripts.health_check as hc

        assert hasattr(hc, "run_health_check")
        assert callable(hc.run_health_check)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
