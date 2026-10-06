#!/usr/bin/env python3
"""Compute sherpa accuracy summary."""

import json
import numpy as np
from pathlib import Path

with open("tests/benchmarks/voice/ws2/results/sherpa_accuracy.json") as f:
    results = json.load(f)

def categorize(idx):
    if idx < 15: return "commands"
    elif idx < 25: return "questions"
    elif idx < 33: return "names"
    else: return "numbers"

all_wer = [r["wer"] for r in results]
print(f"Overall WER: {np.mean(all_wer):.3f} (median {np.median(all_wer):.3f})")

for cat in ["commands", "questions", "names", "numbers"]:
    cat_results = [r for r in results if categorize(r["idx"]) == cat]
    if cat_results:
        cat_wer = [r["wer"] for r in cat_results]
        print(f"  {cat}: WER={np.mean(cat_wer):.3f} (n={len(cat_wer)})")

num_results = [r for r in results if categorize(r["idx"]) == "numbers"]
exact_match = sum(1 for r in num_results if r["wer"] == 0)
print(f"  numbers exact-match: {exact_match}/{len(num_results)} = {exact_match/len(num_results)*100:.1f}%")

print("\nErrors in names/numbers:")
for r in results:
    cat = categorize(r["idx"])
    if cat in ["names", "numbers"] and r["wer"] > 0:
        print(f"  {r['file']}: ref='{r['ref_normalized']}' hyp='{r['hyp_normalized']}'")