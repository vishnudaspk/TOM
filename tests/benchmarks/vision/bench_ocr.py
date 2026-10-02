"""Vision Latency Benchmark — OCR.

Measures MockOCRProvider.extract_text() overhead (scheduling, privacy filtering,
result construction) without real winocr inference.
If winocr is installed, also measures WindowsMediaOCRProvider on a synthetic image.

Run:
    python tests/benchmarks/vision/bench_ocr.py

Targets:
    Median < 100 ms
"""

from __future__ import annotations

import importlib
import importlib.util
import statistics
import time

from PIL import Image
from tom.schemas.vision import BoundingBox, OCRResult, TextLocation
from tom.vision.ocr import MockOCRProvider


def _make_pil(w: int = 800, h: int = 600) -> Image.Image:
    return Image.new("RGB", (w, h), "white")


def _make_result(text: str = "Benchmark screen text for OCR latency test") -> OCRResult:
    return OCRResult(
        text=text,
        confidence=0.92,
        words=[
            TextLocation(
                text=w,
                bounding_box=BoundingBox(
                    left=i * 60.0, top=10.0, right=i * 60.0 + 50.0, bottom=30.0
                ),
                confidence=0.92,
            )
            for i, w in enumerate(text.split())
        ],
    )


def bench_mock_ocr(n: int = 200) -> dict:
    """Benchmark MockOCRProvider (always available, includes privacy shield filtering)."""
    provider = MockOCRProvider(responses=[_make_result()] * n)
    img = _make_pil()
    timings = []
    for _ in range(n):
        t0 = time.perf_counter()
        provider.extract_text(img)
        timings.append((time.perf_counter() - t0) * 1000)
    return {
        "provider": "MockOCRProvider",
        "n": n,
        "min_ms": round(min(timings), 3),
        "median_ms": round(statistics.median(timings), 3),
        "p95_ms": round(sorted(timings)[int(0.95 * n)], 3),
        "max_ms": round(max(timings), 3),
        "target_ms": 100,
        "passed": statistics.median(timings) < 100,
    }


def bench_windows_media_ocr(n: int = 10) -> dict | None:
    """Benchmark WindowsMediaOCRProvider if winocr is installed."""
    if importlib.util.find_spec("winocr") is None:
        return None
    try:
        from tom.vision.ocr import WindowsMediaOCRProvider

        provider = WindowsMediaOCRProvider()
        img = _make_pil(1920, 1080)
        # Warm up
        provider.extract_text(img)
        timings = []
        for _ in range(n):
            t0 = time.perf_counter()
            provider.extract_text(img)
            timings.append((time.perf_counter() - t0) * 1000)
        return {
            "provider": "WindowsMediaOCRProvider (real winocr)",
            "n": n,
            "min_ms": round(min(timings), 3),
            "median_ms": round(statistics.median(timings), 3),
            "p95_ms": round(sorted(timings)[int(0.95 * n)], 3),
            "max_ms": round(max(timings), 3),
            "target_ms": 100,
            "passed": statistics.median(timings) < 100,
        }
    except Exception as e:
        return {"provider": "WindowsMediaOCRProvider", "status": f"SKIPPED: {e}"}


def main() -> None:
    print("=" * 60)
    print("bench_ocr.py — OCR Latency Benchmark")
    print("=" * 60)

    _print_result(bench_mock_ocr())

    real = bench_windows_media_ocr()
    if real is None:
        print("\n[WindowsMediaOCRProvider] SKIPPED / NOT MEASURED — winocr not installed")
    else:
        _print_result(real)


def _print_result(r: dict) -> None:
    print(f"\nProvider: {r.get('provider')}")
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
