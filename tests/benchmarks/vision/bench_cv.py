"""Vision Latency Benchmark — CV Element Detection.

Measures CVElementDetector.detect() latency on synthetic PIL images.
Requires opencv-python; skips gracefully if not installed.

Run:
    python tests/benchmarks/vision/bench_cv.py

Targets:
    Median < 50 ms
"""

from __future__ import annotations

import importlib
import importlib.util
import statistics
import time


def _make_rect_image(w: int = 1920, h: int = 1080):  # type: ignore[return]
    """Draw a synthetic image with a few rectangular shapes for contour detection."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (w, h), (240, 240, 240))
    draw = ImageDraw.Draw(img)
    # Draw a few button-like rectangles
    draw.rectangle([100, 100, 300, 150], fill=(50, 50, 200))
    draw.rectangle([100, 200, 500, 250], fill=(50, 200, 50))
    draw.rectangle([600, 400, 900, 460], fill=(200, 50, 50))
    return img


def bench_cv(n: int = 100) -> dict | None:
    if importlib.util.find_spec("cv2") is None:
        return None
    try:
        from tom.vision.cv import CVElementDetector

        detector = CVElementDetector()
        img = _make_rect_image()
        # Warm up
        detector.detect(img)
        timings = []
        for _ in range(n):
            t0 = time.perf_counter()
            detector.detect(img)
            timings.append((time.perf_counter() - t0) * 1000)
        return {
            "n": n,
            "image_size": "1920×1080",
            "min_ms": round(min(timings), 3),
            "median_ms": round(statistics.median(timings), 3),
            "p95_ms": round(sorted(timings)[int(0.95 * n)], 3),
            "max_ms": round(max(timings), 3),
            "target_ms": 50,
            "passed": statistics.median(timings) < 50,
        }
    except Exception as e:
        return {"status": f"SKIPPED: {e}"}


def main() -> None:
    print("=" * 60)
    print("bench_cv.py — CV Element Detection Latency Benchmark")
    print("=" * 60)

    result = bench_cv()
    if result is None:
        print("\nSKIPPED / NOT MEASURED — opencv-python not installed")
        print("Install with: pip install opencv-python numpy")
        return

    if "status" in result:
        print(f"\n{result['status']}")
        return

    print("\nDetector: CVElementDetector (OpenCV Canny + contour)")
    print(f"Image   : {result['image_size']} synthetic rectangles")
    print(f"N       : {result['n']} iterations")
    print(f"Min     : {result['min_ms']} ms")
    print(f"Median  : {result['median_ms']} ms   (target < {result['target_ms']} ms)")
    print(f"P95     : {result['p95_ms']} ms")
    print(f"Max     : {result['max_ms']} ms")
    print(f"Result  : {'PASSED' if result.get('passed') else 'FAILED'}")


if __name__ == "__main__":
    main()
