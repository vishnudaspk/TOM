"""Vision Latency Benchmark — Screen Capture.

Measures ScreenCaptureService.capture_screen() round-trip latency.
Uses MockCaptureBackend (in-memory) to avoid real display I/O.
Real mss backend results are noted separately if available.

Run:
    python tests/benchmarks/vision/bench_capture.py

Targets:
    Median < 30 ms
"""

from __future__ import annotations

import importlib
import importlib.util
import statistics
import time

from tom.vision.capture import MockCaptureBackend, ScreenCaptureService


def bench_mock_capture(n: int = 100) -> dict:
    """Benchmark MockCaptureBackend (always available, zero real display I/O)."""
    svc = ScreenCaptureService(backend=MockCaptureBackend())
    timings = []
    for _ in range(n):
        t0 = time.perf_counter()
        frame = svc.capture_screen()
        timings.append((time.perf_counter() - t0) * 1000)
    assert frame is not None  # sanity
    return {
        "backend": "MockCaptureBackend",
        "n": n,
        "min_ms": round(min(timings), 3),
        "median_ms": round(statistics.median(timings), 3),
        "p95_ms": round(sorted(timings)[int(0.95 * n)], 3),
        "max_ms": round(max(timings), 3),
        "target_ms": 30,
        "passed": statistics.median(timings) < 30,
    }


def bench_real_capture(n: int = 20) -> dict | None:
    """Benchmark real mss backend if available. Returns None if unavailable."""
    if importlib.util.find_spec("mss") is None:
        return None
    try:
        from tom.vision.capture import MSSCaptureBackend

        svc = ScreenCaptureService(backend=MSSCaptureBackend())
        timings = []
        for _ in range(n):
            t0 = time.perf_counter()
            svc.capture_screen()
            timings.append((time.perf_counter() - t0) * 1000)
        return {
            "backend": "MssBackend (real display)",
            "n": n,
            "min_ms": round(min(timings), 3),
            "median_ms": round(statistics.median(timings), 3),
            "p95_ms": round(sorted(timings)[int(0.95 * n)], 3),
            "max_ms": round(max(timings), 3),
            "target_ms": 30,
            "passed": statistics.median(timings) < 30,
        }
    except Exception as e:
        return {"backend": "MssBackend", "status": f"SKIPPED: {e}"}


def main() -> None:
    print("=" * 60)
    print("bench_capture.py — Screen Capture Latency Benchmark")
    print("=" * 60)

    mock_result = bench_mock_capture()
    _print_result(mock_result)

    real_result = bench_real_capture()
    if real_result is None:
        print("\n[Real mss backend] SKIPPED / NOT MEASURED — mss not installed")
    else:
        _print_result(real_result)


def _print_result(r: dict) -> None:
    print(f"\nBackend : {r.get('backend')}")
    if "status" in r:
        print(f"Status  : {r['status']}")
        return
    print(f"N       : {r['n']} iterations")
    print(f"Min     : {r['min_ms']} ms")
    print(f"Median  : {r['median_ms']} ms   (target < {r['target_ms']} ms)")
    print(f"P95     : {r['p95_ms']} ms")
    print(f"Max     : {r['max_ms']} ms")
    print(f"Result  : {'PASSED' if r.get('passed') else 'FAILED'}")


if __name__ == "__main__":
    main()
