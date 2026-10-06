#!/usr/bin/env python3
"""Select 15 stratified utterances for reduced latency runs."""

import json

with open("tests/benchmarks/voice/ws2/results/sherpa_large_corrected.json") as f:
    results = json.load(f)

categories = {
    "commands": list(range(0, 15)),
    "questions": list(range(15, 25)),
    "names": list(range(25, 33)),
    "numbers": list(range(33, 41)),
}

selected = []
for cat, indices in categories.items():
    step = max(1, len(indices) // 3)
    for i in indices[::step][:4]:
        if i < len(results):
            selected.append(results[i])

print(f"Selected {len(selected)} utterances:")
for r in selected:
    print(f"  {r['file']}: ffe={r['final_from_endpoint_ms']}ms, fp_onset={r['first_partial_from_onset_ms']}ms")

# Save selected indices
selected_indices = [results.index(r) for r in selected]
with open("tests/benchmarks/voice/ws2/results/stratified_indices.json", "w") as f:
    json.dump(selected_indices, f)