#!/usr/bin/env python3
"""
Summarize Checkpoint 1 results from measured JSON files.
"""

import json
from pathlib import Path

# 1. Load accuracy fixed
acc_path = Path("tests/benchmarks/voice/ws2/results/task1c_accuracy_fixed.json")
with open(acc_path, "r", encoding="utf-8") as f:
    acc_data = json.load(f)

# 2. Load offline candidates
off_path = Path("tests/benchmarks/voice/ws2/results/task2_offline_candidates.json")
with open(off_path, "r", encoding="utf-8") as f:
    off_data = json.load(f)

# 3. Load latency unloaded / loaded
lat_u_path = Path("tests/benchmarks/voice/ws2/results/latency_unloaded.json")
with open(lat_u_path, "r", encoding="utf-8") as f:
    lat_u_data = json.load(f)

lat_l_path = Path("tests/benchmarks/voice/ws2/results/latency_loaded.json")
with open(lat_l_path, "r", encoding="utf-8") as f:
    lat_l_data = json.load(f)

# Print 15 sample pairs
print("=" * 80)
print("15 SAMPLE (REF, HYP) PAIRS PER ENGINE")
print("=" * 80)
sherpa_items = acc_data["results"]["sherpa-large-fixed"]
fw_items = acc_data["results"]["faster-whisper-small.en"]

for i in range(15):
    s = sherpa_items[i]
    w = fw_items[i]
    print(f"[{i+1:02d}] {s['file']} ({s['category']})")
    print(f"     REF:    {s['ref_normalized']}")
    print(f"     SHERPA: {s['hyp_normalized']} (WER={s['wer']:.2f})")
    print(f"     FW:     {w['hyp_normalized']} (WER={w['wer']:.2f})")

print("\n" + "=" * 80)
print("NAME AND NUMBER ERRORS LIST")
print("=" * 80)
print("\n--- NAME ERRORS (sherpa-large-fixed vs faster-whisper) ---")
for s, w in zip(sherpa_items, fw_items):
    if s["category"] == "names":
        print(f"File {s['file']}:")
        print(f"  REF:    {s['ref_normalized']}")
        print(f"  SHERPA: {s['hyp_normalized']} (WER={s['wer']:.2f})")
        print(f"  FW:     {w['hyp_normalized']} (WER={w['wer']:.2f})")

print("\n--- NUMBER ERRORS (sherpa-large-fixed vs faster-whisper) ---")
for s, w in zip(sherpa_items, fw_items):
    if s["category"] == "numbers":
        exact_s = (s['ref_normalized'].strip() == s['hyp_normalized'].strip())
        exact_w = (w['ref_normalized'].strip() == w['hyp_normalized'].strip())
        print(f"File {s['file']}:")
        print(f"  REF:    {s['ref_normalized']}")
        print(f"  SHERPA: {s['hyp_normalized']} [exact={exact_s}] (WER={s['wer']:.2f})")
        print(f"  FW:     {w['hyp_normalized']} [exact={exact_w}] (WER={w['wer']:.2f})")

print("\n" + "=" * 80)
print("NORMALIZER UNIT TESTS")
print("=" * 80)
for r in acc_data["normalizer_unit_tests"]["results"]:
    status = "PASS" if r["pass"] else "FAIL"
    print(f"  [{status}] {r['desc']}: '{r['input']}' -> '{r['got']}' (expected '{r['expected']}')")
