"""Unit tests for ModelBenchmarkSuite (Phase 8 Iteration 3).

Adheres to:
- Offline, deterministic testing with MockModelProvider and MockTelemetryProvider.
- Verifies TTFT, tokens/sec, JSON schema fidelity, and VRAM residency tracking.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field
from tom.models.benchmark import ModelBenchmarkSuite, compute_metric
from tom.models.providers.mock import MockModelProvider
from tom.resources.manager import MockTelemetryProvider
from tom.schemas.models import FinishReason, StreamChunk, TokenUsage


class SampleStructuredOutput(BaseModel):
    task_name: str
    step_count: int = Field(ge=1)
    status: str


class CustomStreamingMockProvider(MockModelProvider):
    """Mock provider with explicit StreamChunk yielding control for benchmark tests."""

    def __init__(
        self,
        stream_chunks_per_call: list[list[StreamChunk]],
    ) -> None:
        super().__init__()
        self._chunks_queue = stream_chunks_per_call
        self._idx = 0

    async def stream(self, request):  # type: ignore[override]
        if self._idx < len(self._chunks_queue):
            chunks = self._chunks_queue[self._idx]
            self._idx += 1
        else:
            chunks = [
                StreamChunk(content="default", is_final=False),
                StreamChunk(
                    content="",
                    finish_reason=FinishReason.STOP,
                    is_final=True,
                    usage=TokenUsage(completion_tokens=10),
                ),
            ]
        for c in chunks:
            yield c


class TestModelBenchmarkSuite:
    def test_compute_metric_empty_and_valid(self) -> None:
        empty = compute_metric([])
        assert empty.mean == 0.0
        assert empty.samples == []

        metric = compute_metric([10.0, 20.0, 30.0, 40.0, 50.0])
        assert metric.mean == 30.0
        assert metric.min == 10.0
        assert metric.max == 50.0
        assert metric.p50 == 30.0
        assert metric.p95 == 50.0
        assert len(metric.samples) == 5

    @pytest.mark.anyio
    async def test_run_benchmark_basic_metrics(self) -> None:
        mock_provider = MockModelProvider(
            responses=[
                "First token generated and then second token.",
                "Another response with tokens.",
                "Third response iteration.",
                "Fourth response warmup.",
            ],
            loop=True,
        )

        suite = ModelBenchmarkSuite(provider=mock_provider)
        result = await suite.run_benchmark(
            model_id="test-router-model",
            iterations=3,
            warmup_iterations=1,
        )

        assert result.model_id == "test-router-model"
        assert result.iterations == 3
        assert len(result.raw_runs) == 3
        assert result.ttft_ms.mean >= 0.0
        assert result.tokens_per_sec.mean > 0.0
        assert result.schema_fidelity == 1.0  # No schema requested
        assert result.vram_residency_mb is None

    @pytest.mark.anyio
    async def test_run_benchmark_json_schema_fidelity_success(self) -> None:
        valid_json_response = (
            '```json\n{"task_name": "disk_cleanup", "step_count": 3, "status": "READY"}\n```'
        )
        mock_provider = MockModelProvider(
            responses=[valid_json_response],
            loop=True,
        )

        suite = ModelBenchmarkSuite(provider=mock_provider)
        result = await suite.run_benchmark(
            model_id="qwen-reasoner",
            schema=SampleStructuredOutput,
            iterations=2,
            warmup_iterations=0,
        )

        assert result.schema_fidelity == 1.0
        for run in result.raw_runs:
            assert run.json_valid is True

    @pytest.mark.anyio
    async def test_run_benchmark_json_schema_fidelity_failure(self) -> None:
        invalid_response = "I cannot generate valid JSON for this task."
        mock_provider = MockModelProvider(
            responses=[invalid_response],
            loop=True,
        )

        suite = ModelBenchmarkSuite(provider=mock_provider)
        result = await suite.run_benchmark(
            model_id="bad-model",
            schema=SampleStructuredOutput,
            iterations=2,
            warmup_iterations=0,
        )

        assert result.schema_fidelity == 0.0
        for run in result.raw_runs:
            assert run.json_valid is False

    @pytest.mark.anyio
    async def test_run_benchmark_with_vram_telemetry(self) -> None:
        mock_provider = MockModelProvider(
            responses=["Short text for testing."],
            loop=True,
        )
        mock_telemetry = MockTelemetryProvider(
            vram_total_mb=8192.0,
            vram_used_mb=2000.0,
            vram_free_mb=6192.0,
            gpu_available=True,
        )

        suite = ModelBenchmarkSuite(provider=mock_provider, telemetry=mock_telemetry)
        result = await suite.run_benchmark(
            model_id="vram-monitored-model",
            iterations=2,
            warmup_iterations=0,
        )

        assert result.peak_vram_mb == 2000.0
        assert result.vram_residency_mb == 0.0

    @pytest.mark.anyio
    async def test_run_benchmark_telemetry_unavailable(self) -> None:
        mock_provider = MockModelProvider(
            responses=["Output content."],
            loop=True,
        )
        mock_telemetry = MockTelemetryProvider(
            gpu_available=False,
            should_fail=True,
        )

        suite = ModelBenchmarkSuite(provider=mock_provider, telemetry=mock_telemetry)
        result = await suite.run_benchmark(
            model_id="model-without-telemetry",
            iterations=1,
            warmup_iterations=0,
        )

        # Should complete gracefully without crash
        assert result.model_id == "model-without-telemetry"
        assert result.vram_residency_mb is None
        assert result.peak_vram_mb is None
