#!/usr/bin/env python3
"""Compute summary statistics from sherpa test results."""

import json
import numpy as np
from pathlib import Path

RESULTS_FILE = Path("results/sherpa_test_results.json")

with open(RESULTS_FILE) as f:
    data = json.load(f)

def compute_stats(results, model_name):
    print(f"\n{'='*60}")
    print(f"{model_name} SUMMARY")
    print(f"{'='*60}")
    
    first_partials = [r["first_partial_ms"] for r in results if r["first_partial_ms"] is not None]
    finalizations = [r["finalization_ms"] for r in results]
    
    # First partial stats
    if first_partials:
        fp_arr = np.array(first_partials)
        print(f"  First Partial (ms):")
        print(f"    Count: {len(first_partials)}/41")
        print(f"    Mean: {np.mean(fp_arr):.1f}")
        print(f"    Median (p50): {np.median(fp_arr):.1f}")
        print(f"    p95: {np.percentile(fp_arr, 95):.1f}")
        print(f"    Min: {np.min(fp_arr):.1f}")
        print(f"    Max: {np.max(fp_arr):.1f}")
        print(f"    Stdev: {np.std(fp_arr):.1f}")
        if np.std(fp_arr) / np.mean(fp_arr) > 0.25:
            print(f"    HIGH-VARIANCE: stdev/mean = {np.std(fp_arr)/np.mean(fp_arr):.2%}")
    else:
        print("  First Partial: No partials emitted")
    
    # Finalization stats
    fin_arr = np.array(finalizations)
    print(f"  Finalization (ms):")
    print(f"    Mean: {np.mean(fin_arr):.1f}")
    print(f"    Median (p50): {np.median(fin_arr):.1f}")
    print(f"    p95: {np.percentile(fin_arr, 95):.1f}")
    print(f"    Min: {np.min(fin_arr):.1f}")
    print(f"    Max: {np.max(fin_arr):.1f}")
    print(f"    Stdev: {np.std(fin_arr):.1f}")
    if np.std(fin_arr) / np.mean(fin_arr) > 0.25:
        print(f"    HIGH-VARIANCE: stdev/mean = {np.std(fin_arr)/np.mean(fin_arr):.2%}")
    
    # Duration breakdown by length
    print(f"\n  By utterance length:")
    for length_cat, max_dur in [("short (<2s)", 2.0), ("medium (2-3s)", 3.0), ("long (>3s)", float('inf'))]:
        subset = [r for r in results if r["duration_s"] < max_dur]
        if subset:
            fp_vals = [r["first_partial_ms"] for r in subset if r["first_partial_ms"] is not None]
            fin_vals = [r["finalization_ms"] for r in subset]
            if fp_vals:
                print(f"    {length_cat} (n={len(subset)}): fp_p50={np.median(fp_vals):.0f}ms, fin_p50={np.median(fin_vals):.0f}ms")
            else:
                print(f"    {length_cat} (n={len(subset)}): no partials, fin_p50={np.median(fin_vals):.0f}ms")

    # Accuracy check (compare with reference)
    # For now, just count empty transcriptions
    empty_count = sum(1 for r in results if not r["final_text"].strip())
    print(f"\n  Empty transcriptions: {empty_count}/41")

if __name__ == "__main__":
    with open("results/sherpa_test_results.json") as f:
        data = json.load(f)
    
    for model_name, results in data.items():
        compute_stats(results, model_name)