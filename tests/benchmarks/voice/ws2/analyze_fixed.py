#!/usr/bin/env python3
"""Compute summary statistics from sherpa test results with corrected metrics."""

import json
import numpy as np
from pathlib import Path

RESULTS_FILE = Path("tests/benchmarks/voice/ws2/results/sherpa_test_results.json")

with open(RESULTS_FILE) as f:
    data = json.load(f)

def compute_stats(results, model_name):
    print(f"\n{'='*60}")
    print(f"{model_name} SUMMARY (CORRECTED METRICS)")
    print(f"{'='*60}")
    
    # PRIMARY METRIC: final_from_endpoint (time from VAD endpoint to final text)
    ffe = [r["final_from_endpoint_ms"] for r in results if r["final_from_endpoint_ms"] is not None]
    if ffe:
        arr = np.array(ffe)
        print(f"  final_from_endpoint (ms) - PRIMARY METRIC:")
        print(f"    Count: {len(arr)}/41")
        print(f"    Mean: {np.mean(arr):.1f}")
        print(f"    Median (p50): {np.median(arr):.1f}")
        print(f"    p95: {np.percentile(arr, 95):.1f}")
        print(f"    Min: {np.min(arr):.1f}")
        print(f"    Max: {np.max(arr):.1f}")
        print(f"    Stdev: {np.std(arr):.1f}")
        cv = np.std(arr) / np.mean(arr) if np.mean(arr) > 0 else 0
        print(f"    CV (stdev/mean): {cv:.2%}")
        if cv > 0.5:  # Fixed: was incorrectly flagging 2.6% as high variance
            print(f"    HIGH-VARIANCE: CV = {cv:.2%} > 50%")
    
    # First partial - but we need to check if it's from speech onset or feed start
    fp = [r["first_partial_ms"] for r in results if r["first_partial_ms"] is not None]
    if fp:
        arr = np.array(fp)
        print(f"\n  first_partial_from_feed_start (ms) - NEEDS CORRECTION:")
        print(f"    Count: {len(arr)}/41")
        print(f"    Mean: {np.mean(arr):.1f}")
        print(f"    Median (p50): {np.median(arr):.1f}")
        print(f"    p95: {np.percentile(arr, 95):.1f}")
        print(f"    Min: {np.min(arr):.1f}")
        print(f"    Max: {np.max(arr):.1f}")
        print(f"    Stdev: {np.std(arr):.1f}")
        cv = np.std(arr) / np.mean(arr) if np.mean(arr) > 0 else 0
        print(f"    CV (stdev/mean): {cv:.2%}")
        if cv > 0.5:
            print(f"    HIGH-VARIANCE: CV = {cv:.2%} > 50%")
    
    # Duration breakdown
    print(f"\n  By utterance length:")
    for length_cat, max_dur in [("short (<2s)", 2.0), ("medium (2-3s)", 3.0), ("long (>3s)", float('inf'))]:
        subset = [r for r in results if r["duration_s"] < max_dur]
        if subset:
            ffe_vals = [r["final_from_endpoint_ms"] for r in subset if r["final_from_endpoint_ms"] is not None]
            fp_vals = [r["first_partial_ms"] for r in subset if r["first_partial_ms"] is not None]
            if ffe_vals:
                print(f"    {length_cat} (n={len(subset)}): ffe_p50={np.median(ffe_vals):.1f}ms, fp_p50={np.median(fp_vals):.1f}ms" if fp_vals else f"    {length_cat} (n={len(subset)}): ffe_p50={np.median(ffe_vals):.1f}ms")
            else:
                print(f"    {length_cat} (n={len(subset)}): no data")

if __name__ == "__main__":
    for model_name, results in data.items():
        compute_stats(results, model_name)