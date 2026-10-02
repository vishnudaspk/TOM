"""Standardized Model Benchmark Suite.

Adheres to:
- Phase 8 Architecture (Model Benchmark Suite & Metrics)
- Hardware targets: RTX 4060 (8 GB VRAM), 16 GB system RAM
- Measures:
  1. TTFT (Time to First Token in ms)
  2. Tokens/sec (Generation throughput)
  3. JSON schema fidelity (Pydantic validation pass rate)
  4. Dedicated VRAM residency and peak memory footprint

Injectable, provider-agnostic, and fully decoupled from hardware telemetry.
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tom.models.base import LLMProvider
from tom.resources.manager import ResourceTelemetryProvider
from tom.schemas.agent import Message, Role
from tom.schemas.models import ModelRequest
from tom.telemetry.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "BenchmarkMetric",
    "BenchmarkResult",
    "IterationResult",
    "ModelBenchmarkSuite",
    "compute_metric",
]


class BenchmarkMetric(BaseModel):
    """Statistical summary of a benchmark measurement."""

    model_config = ConfigDict(extra="ignore")

    mean: float
    min: float
    max: float
    p50: float = 0.0
    p95: float = 0.0
    samples: list[float] = Field(default_factory=list)


def compute_metric(samples: list[float]) -> BenchmarkMetric:
    """Calculate mean, min, max, p50, and p95 from a list of samples."""
    if not samples:
        return BenchmarkMetric(mean=0.0, min=0.0, max=0.0, p50=0.0, p95=0.0, samples=[])
    sorted_s = sorted(samples)
    n = len(sorted_s)
    mean_val = sum(sorted_s) / n
    min_val = sorted_s[0]
    max_val = sorted_s[-1]
    p50_val = sorted_s[n // 2]
    p95_idx = min(n - 1, int(n * 0.95))
    p95_val = sorted_s[p95_idx]
    return BenchmarkMetric(
        mean=round(mean_val, 2),
        min=round(min_val, 2),
        max=round(max_val, 2),
        p50=round(p50_val, 2),
        p95=round(p95_val, 2),
        samples=[round(s, 2) for s in samples],
    )


class IterationResult(BaseModel):
    """Metrics recorded for a single benchmark evaluation run."""

    model_config = ConfigDict(extra="ignore")

    iteration: int
    ttft_ms: float
    tokens_per_sec: float
    total_tokens: int
    duration_ms: float
    json_valid: bool
    vram_used_mb: float | None = None


class BenchmarkResult(BaseModel):
    """Machine-readable aggregation of model benchmark metrics."""

    model_config = ConfigDict(extra="ignore")

    model_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    iterations: int
    ttft_ms: BenchmarkMetric
    tokens_per_sec: BenchmarkMetric
    schema_fidelity: float = Field(
        ge=0.0, le=1.0, description="Fraction of runs passing strict JSON schema validation"
    )
    vram_residency_mb: float | None = Field(
        default=None, description="Net VRAM residency increase during inference in MB"
    )
    peak_vram_mb: float | None = Field(
        default=None, description="Peak VRAM footprint recorded in MB"
    )
    raw_runs: list[IterationResult] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelBenchmarkSuite:
    """Orchestrates empirical benchmarking against any LLMProvider."""

    def __init__(
        self,
        provider: LLMProvider,
        telemetry: ResourceTelemetryProvider | None = None,
    ) -> None:
        self.provider = provider
        self.telemetry = telemetry

    def _extract_and_validate_json(self, text: str, schema: type[BaseModel]) -> bool:
        """Attempt to extract and validate JSON against the provided Pydantic model."""
        clean_text = text.strip()
        # Strip markdown fences if present
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", clean_text)
        if fence_match:
            candidate = fence_match.group(1).strip()
        else:
            candidate = clean_text

        # First check raw json syntax
        try:
            parsed = json.loads(candidate)
        except Exception:
            # Fallback: scan for first { to last }
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    parsed = json.loads(candidate[start : end + 1])
                except Exception:
                    return False
            else:
                return False

        try:
            if isinstance(parsed, dict):
                schema.model_validate(parsed)
                return True
            return False
        except Exception:
            return False

    async def run_benchmark(
        self,
        model_id: str,
        prompt: str = "Explain the difference between a process and a thread in two sentences.",
        schema: type[BaseModel] | None = None,
        iterations: int = 5,
        warmup_iterations: int = 1,
        max_tokens: int = 256,
        temperature: float = 0.0,
    ) -> BenchmarkResult:
        """Run benchmark suite and collect TTFT, throughput, fidelity, and VRAM."""
        logger.info(
            "model_benchmark_started",
            model_id=model_id,
            iterations=iterations,
            has_schema=schema is not None,
        )

        initial_vram: float | None = None
        peak_vram: float | None = None

        if self.telemetry is not None and self.telemetry.is_available():
            try:
                telem = self.telemetry.get_telemetry()
                if telem.gpu_available:
                    initial_vram = telem.vram_used_mb
                    peak_vram = initial_vram
            except Exception as e:
                logger.warning("benchmark_telemetry_query_failed", error=str(e))

        request = ModelRequest(
            model=model_id,
            messages=[Message(role=Role.USER, content=prompt)],
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
        )

        # 1. Warmup runs
        for _ in range(warmup_iterations):
            try:
                async for _ in self.provider.stream(request):
                    pass
            except Exception as e:
                logger.warning("benchmark_warmup_failed", error=str(e))

        # 2. Measured iterations
        ttft_samples: list[float] = []
        tps_samples: list[float] = []
        valid_schema_count = 0
        runs: list[IterationResult] = []

        for i in range(iterations):
            start_time = time.perf_counter()
            first_token_time: float | None = None
            content_chunks: list[str] = []
            completion_tokens = 0

            chunk_tokens_count = 0
            async for chunk in self.provider.stream(request):
                now = time.perf_counter()
                has_token = bool(chunk.content or chunk.reasoning_content)
                if has_token:
                    chunk_tokens_count += 1
                    if first_token_time is None:
                        first_token_time = now
                if chunk.content:
                    content_chunks.append(chunk.content)

                if chunk.usage and chunk.usage.completion_tokens > 0:
                    completion_tokens = chunk.usage.completion_tokens

            end_time = time.perf_counter()
            total_duration_s = max(0.0001, end_time - start_time)
            duration_ms = total_duration_s * 1000.0

            if first_token_time is not None:
                ttft_ms = max(0.0, (first_token_time - start_time) * 1000.0)
            else:
                ttft_ms = duration_ms

            full_text = "".join(content_chunks)

            # If provider didn't emit completion_tokens in usage, estimate from output
            if completion_tokens == 0:
                completion_tokens = max(chunk_tokens_count, len(full_text.split()), 1)

            # Tokens per second
            tps = completion_tokens / total_duration_s

            # JSON fidelity validation
            if schema is not None:
                is_valid = self._extract_and_validate_json(full_text, schema)
            else:
                is_valid = True

            if is_valid:
                valid_schema_count += 1

            # Telemetry snapshot
            current_vram: float | None = None
            if self.telemetry is not None and self.telemetry.is_available():
                try:
                    t = self.telemetry.get_telemetry()
                    if t.gpu_available:
                        current_vram = t.vram_used_mb
                        if peak_vram is None or current_vram > peak_vram:
                            peak_vram = current_vram
                except Exception:
                    pass

            ttft_samples.append(ttft_ms)
            tps_samples.append(tps)

            runs.append(
                IterationResult(
                    iteration=i + 1,
                    ttft_ms=round(ttft_ms, 2),
                    tokens_per_sec=round(tps, 2),
                    total_tokens=completion_tokens,
                    duration_ms=round(duration_ms, 2),
                    json_valid=is_valid,
                    vram_used_mb=round(current_vram, 2) if current_vram is not None else None,
                )
            )

        schema_fidelity = valid_schema_count / iterations if iterations > 0 else 0.0

        vram_residency: float | None = None
        if peak_vram is not None and initial_vram is not None:
            vram_residency = max(0.0, peak_vram - initial_vram)

        result = BenchmarkResult(
            model_id=model_id,
            iterations=iterations,
            ttft_ms=compute_metric(ttft_samples),
            tokens_per_sec=compute_metric(tps_samples),
            schema_fidelity=round(schema_fidelity, 4),
            vram_residency_mb=round(vram_residency, 2) if vram_residency is not None else None,
            peak_vram_mb=round(peak_vram, 2) if peak_vram is not None else None,
            raw_runs=runs,
            metadata={
                "warmup_iterations": warmup_iterations,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
        )

        logger.info(
            "model_benchmark_completed",
            model_id=model_id,
            ttft_mean_ms=result.ttft_ms.mean,
            tps_mean=result.tokens_per_sec.mean,
            schema_fidelity=result.schema_fidelity,
            vram_residency_mb=result.vram_residency_mb,
        )

        return result
