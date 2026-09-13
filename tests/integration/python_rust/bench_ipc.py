"""Benchmark script for Python <-> Rust IPC roundtrip latency.

Measures 100 sequential engine.ping() calls across the Windows Named Pipe,
computes P50, P95, and P99 latencies, and verifies compliance with the
Phase 2 performance target (P95 < 10ms).
"""

import asyncio
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

from tom.core.engine import EngineClient
from tom.ipc.client import NamedPipeIpcClient
from tom.ipc.protocol import PIPE_NAME
from tom.ipc.transport import NamedPipeTransport
from tom.schemas.config import IpcConfig

BENCHMARK_ITERATIONS: int = 100
TARGET_P95_MS: float = 10.0


async def _can_connect() -> bool:
    transport = NamedPipeTransport(config=IpcConfig(pipe_name=PIPE_NAME, connection_timeout_ms=500))
    try:
        await transport.connect()
        await transport.close()
        return True
    except Exception:
        return False


async def run_benchmark(pipe_name: str) -> bool:
    print(f"Connecting to tom-engine at '{pipe_name}'...")
    client = NamedPipeIpcClient(config=IpcConfig(pipe_name=pipe_name, request_timeout_ms=5000))
    engine = EngineClient(client)

    await client.connect()
    print("Connected. Running warm-up request...")
    warmup = await engine.ping()
    assert warmup.pong is True, "Warm-up ping failed!"

    print(f"Executing {BENCHMARK_ITERATIONS} sequential engine.ping() requests...")
    latencies_ms: list[float] = []

    for _ in range(BENCHMARK_ITERATIONS):
        t0 = time.perf_counter()
        resp = await engine.ping()
        t1 = time.perf_counter()
        assert resp.pong is True
        latencies_ms.append((t1 - t0) * 1000.0)

    await client.close()

    latencies_ms.sort()
    p50 = statistics.median(latencies_ms)
    p95 = latencies_ms[int(len(latencies_ms) * 0.95)]
    p99 = latencies_ms[int(len(latencies_ms) * 0.99)]
    avg = statistics.mean(latencies_ms)
    min_lat = min(latencies_ms)
    max_lat = max(latencies_ms)

    print("\n" + "=" * 50)
    print("      TOM IPC Roundtrip Latency Benchmark      ")
    print("=" * 50)
    print(f"Iterations:     {BENCHMARK_ITERATIONS}")
    print(f"Min:            {min_lat:.3f} ms")
    print(f"Mean:           {avg:.3f} ms")
    print(f"P50 (Median):   {p50:.3f} ms")
    print(f"P95:            {p95:.3f} ms (Target: < {TARGET_P95_MS:.1f} ms)")
    print(f"P99:            {p99:.3f} ms")
    print(f"Max:            {max_lat:.3f} ms")
    print("=" * 50)

    if p95 < TARGET_P95_MS:
        print(f"RESULT: PASS (P95 {p95:.3f} ms < {TARGET_P95_MS:.1f} ms target)")
        return True
    else:
        print(f"RESULT: FAIL (P95 {p95:.3f} ms >= {TARGET_P95_MS:.1f} ms target)")
        return False


def main() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    exe_path = repo_root / "rust" / "tom-engine" / "target" / "debug" / "tom-engine.exe"
    spawned_proc = None

    if not asyncio.run(_can_connect()):
        if exe_path.is_file():
            print(f"Launching tom-engine from {exe_path}...")
            spawned_proc = subprocess.Popen(
                [str(exe_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=os.environ.copy(),
            )
            time.sleep(1.0)
        else:
            print(f"Error: tom-engine pipe '{PIPE_NAME}' not found and binary not built.")
            print("Run 'cargo build' in rust/tom-engine first.")
            sys.exit(1)

    try:
        success = asyncio.run(run_benchmark(PIPE_NAME))
        sys.exit(0 if success else 1)
    finally:
        if spawned_proc is not None:
            print("Stopping background tom-engine...")
            spawned_proc.terminate()
            try:
                spawned_proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                spawned_proc.kill()


if __name__ == "__main__":
    main()
