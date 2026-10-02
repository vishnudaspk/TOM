"""Model Inference & Hardware Residency Benchmark.

Adheres to:
- Phase 8 Architecture (Model Benchmarking & Hardware Allocation)
- Measures:
  - TTFT (Time to First Token in ms)
  - Throughput (tokens/sec)
  - Strict JSON schema fidelity
  - Dedicated GPU VRAM footprint and net residency
- Target hardware profile: RTX 4060 Laptop GPU (8 GB VRAM, 16 GB RAM)
- Safe, offline deterministic scaffolding by default (using MockModelProvider).
- Live evaluation against local OpenAI-compatible endpoints (e.g., LM Studio at
  http://127.0.0.1:1234/v1) is strictly opt-in via TOM_BENCHMARK_LIVE=1.

Run offline verification:
    pytest tests/benchmarks/models/bench_llm.py -v

Run standalone CLI:
    python tests/benchmarks/models/bench_llm.py
    python tests/benchmarks/models/bench_llm.py --live --model qwen2.5-7b-instruct
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import anyio
import pytest
from pydantic import BaseModel, Field
from tom.models.benchmark import BenchmarkResult, ModelBenchmarkSuite
from tom.models.providers.http import HttpModelProvider
from tom.models.providers.mock import MockModelProvider
from tom.resources.manager import (
    MockTelemetryProvider,
    SystemTelemetryProvider,
)


class BenchmarkTaskSchema(BaseModel):
    """Structured test schema for JSON fidelity benchmarking."""

    plan_title: str
    target_app: str
    action_count: int = Field(ge=1)
    safe_to_execute: bool


def _build_mock_suite() -> ModelBenchmarkSuite:
    """Build deterministic offline benchmark suite."""
    sample_json = (
        "```json\n"
        "{\n"
        '  "plan_title": "Clean temporary directory",\n'
        '  "target_app": "System",\n'
        '  "action_count": 3,\n'
        '  "safe_to_execute": true\n'
        "}\n"
        "```"
    )
    mock_provider = MockModelProvider(
        responses=[sample_json],
        loop=True,
    )
    mock_telemetry = MockTelemetryProvider(
        vram_total_mb=8192.0,
        vram_used_mb=2100.0,
        vram_free_mb=6092.0,
        gpu_available=True,
    )
    return ModelBenchmarkSuite(provider=mock_provider, telemetry=mock_telemetry)


async def run_offline_benchmark(iterations: int = 5) -> BenchmarkResult:
    """Run offline benchmark with deterministic double."""
    suite = _build_mock_suite()
    return await suite.run_benchmark(
        model_id="mock-qwen-reasoner",
        prompt="Generate a structured plan to clean temporary files.",
        schema=BenchmarkTaskSchema,
        iterations=iterations,
        warmup_iterations=1,
    )


async def run_live_benchmark(
    base_url: str = "http://127.0.0.1:1234/v1",
    model_id: str = "default",
    iterations: int = 3,
) -> BenchmarkResult:
    """Run live benchmark against local endpoint (LM Studio / Bionic)."""
    provider = HttpModelProvider(base_url=base_url)
    telemetry = SystemTelemetryProvider()
    suite = ModelBenchmarkSuite(provider=provider, telemetry=telemetry)
    prompt = (
        "Generate a structured plan for finding and archiving system logs. "
        "Return ONLY a JSON object matching this schema:\n"
        "```json\n"
        "{\n"
        '  "plan_title": "Archive system logs",\n'
        '  "target_app": "System",\n'
        '  "action_count": 2,\n'
        '  "safe_to_execute": true\n'
        "}\n"
        "```"
    )
    return await suite.run_benchmark(
        model_id=model_id,
        prompt=prompt,
        schema=BenchmarkTaskSchema,
        iterations=iterations,
        warmup_iterations=1,
        max_tokens=1024,
    )


def print_benchmark_report(res: BenchmarkResult) -> None:
    """Pretty-print benchmark results to stdout."""
    print("=" * 65)
    print(f"TOM MODEL BENCHMARK REPORT: {res.model_id}")
    print("=" * 65)
    print(f"Timestamp       : {res.timestamp.isoformat()}")
    print(f"Iterations      : {res.iterations}")
    print("-" * 65)
    print("LATENCY (TTFT):")
    print(f"  Mean          : {res.ttft_ms.mean:.2f} ms")
    print(f"  Min / Max     : {res.ttft_ms.min:.2f} ms / {res.ttft_ms.max:.2f} ms")
    print(f"  P50 / P95     : {res.ttft_ms.p50:.2f} ms / {res.ttft_ms.p95:.2f} ms")
    print("-" * 65)
    print("THROUGHPUT:")
    print(f"  Mean Tokens/s : {res.tokens_per_sec.mean:.2f} tokens/sec")
    print(f"  Min / Max     : {res.tokens_per_sec.min:.2f} / {res.tokens_per_sec.max:.2f}")
    print("-" * 65)
    print("RELIABILITY & FIDELITY:")
    print(f"  JSON Fidelity : {res.schema_fidelity * 100:.1f}% passing")
    print("-" * 65)
    print("HARDWARE / VRAM:")
    peak_str = f"{res.peak_vram_mb:.1f} MB" if res.peak_vram_mb is not None else "N/A"
    res_str = f"{res.vram_residency_mb:.1f} MB" if res.vram_residency_mb is not None else "N/A"
    print(f"  Peak VRAM     : {peak_str}")
    print(f"  Net Residency : {res_str}")
    print("=" * 65)


# ---------------------------------------------------------------------------
# Offline Pytest Test Cases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_offline_benchmark_scaffolding() -> None:
    """Verify that benchmark suite executes deterministically in pytest."""
    res = await run_offline_benchmark(iterations=3)
    assert res.iterations == 3
    assert res.model_id == "mock-qwen-reasoner"
    assert res.ttft_ms.mean >= 0.0
    assert res.tokens_per_sec.mean > 0.0
    assert res.schema_fidelity == 1.0
    assert res.peak_vram_mb == 2100.0


@pytest.mark.anyio
@pytest.mark.skipif(
    not os.environ.get("TOM_BENCHMARK_LIVE"),
    reason="Live model benchmark requires TOM_BENCHMARK_LIVE=1 and a running model server",
)
async def test_live_benchmark_opt_in() -> None:
    """Opt-in live benchmark test against local inference endpoint."""
    model_id = os.environ.get("TOM_BENCHMARK_MODEL", "qwen2.5-7b-instruct")
    res = await run_live_benchmark(model_id=model_id, iterations=2)
    assert res.iterations == 2
    assert res.tokens_per_sec.mean > 0.0


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="TOM Model Benchmark Harness")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run live benchmark against local model endpoint",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:1234/v1",
        help="Inference endpoint base URL",
    )
    parser.add_argument(
        "--model",
        default="qwen2.5-7b-instruct",
        help="Model identifier",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=5,
        help="Number of measured benchmark iterations",
    )

    args = parser.parse_args()

    async def _async_main() -> None:
        if args.live:
            print(f"Running live benchmark against {args.base_url} (model={args.model})...")
            try:
                res = await run_live_benchmark(
                    base_url=args.base_url,
                    model_id=args.model,
                    iterations=args.iterations,
                )
                print_benchmark_report(res)
            except Exception as e:
                print(f"Live benchmark failed: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            print("Running deterministic offline benchmark...")
            res = await run_offline_benchmark(iterations=args.iterations)
            print_benchmark_report(res)

    anyio.run(_async_main)


if __name__ == "__main__":
    _repo_root = Path(__file__).resolve().parent.parent.parent.parent
    _python_dir = _repo_root / "python"
    if str(_python_dir) not in sys.path:
        sys.path.insert(0, str(_python_dir))
    main()
