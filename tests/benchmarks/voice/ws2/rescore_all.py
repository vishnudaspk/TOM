#!/usr/bin/env python3
"""
Rescore all engines using the audited single-source normalizer.
Engines:
1. faster-whisper small.en (beam_size=1)
2. sherpa-large (Zipformer streaming fixed)
3. Moonshine-tiny int8 (offline)
"""

import json
from pathlib import Path
from normalizer import normalize_text, compute_wer

RESULTS_DIR = Path("tests/benchmarks/voice/ws2/results")
CORRECTIONS_FILE = RESULTS_DIR / "corrections.txt"

def load_corrections():
    corrections = {}
    if CORRECTIONS_FILE.exists():
        with open(CORRECTIONS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    parts = line.split(" ", 1)
                    if len(parts) == 2:
                        corrections[int(parts[0]) - 1] = parts[1]
    return corrections

CORRECTIONS = load_corrections()

# 1. Load task1c data
with open(RESULTS_DIR / "task1c_accuracy_fixed.json", "r", encoding="utf-8") as f:
    t1c_data = json.load(f)

# 2. Load task2 Moonshine data
with open(RESULTS_DIR / "task2_offline_candidates.json", "r", encoding="utf-8") as f:
    t2_data = json.load(f)

engines_data = {
    "sherpa-large-fixed": [],
    "faster-whisper-small.en": [],
    "moonshine-tiny-int8": [],
}

for item in t1c_data["results"]["sherpa-large-fixed"]:
    idx = item["idx"]
    ref_raw = CORRECTIONS.get(idx, item["reference"])
    engines_data["sherpa-large-fixed"].append({
        "file": item["file"],
        "idx": idx,
        "category": item["category"],
        "raw_ref": ref_raw,
        "raw_hyp": item["hypothesis"],
    })

for item in t1c_data["results"]["faster-whisper-small.en"]:
    idx = item["idx"]
    ref_raw = CORRECTIONS.get(idx, item["reference"])
    engines_data["faster-whisper-small.en"].append({
        "file": item["file"],
        "idx": idx,
        "category": item["category"],
        "raw_ref": ref_raw,
        "raw_hyp": item["hypothesis"],
    })

for item in t2_data["per_file_results"]:
    idx = item["idx"]
    ref_raw = CORRECTIONS.get(idx, item["reference"])
    engines_data["moonshine-tiny-int8"].append({
        "file": item["file"],
        "idx": idx,
        "category": item["category"],
        "raw_ref": ref_raw,
        "raw_hyp": item["hypothesis"],
    })

rescored_results = {}
summary = {}

for engine_name, items in engines_data.items():
    processed_items = []
    wers = []
    by_category = {}
    num_exact = 0
    num_total = 0
    name_errors = []
    num_errors = []

    for it in items:
        norm_ref = normalize_text(it["raw_ref"])
        norm_hyp = normalize_text(it["raw_hyp"])
        wer = compute_wer(norm_ref, norm_hyp)
        wers.append(wer)

        cat = it["category"]
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(wer)

        if cat == "numbers":
            num_total += 1
            if norm_ref == norm_hyp:
                num_exact += 1
            else:
                num_errors.append({
                    "file": it["file"],
                    "norm_ref": norm_ref,
                    "norm_hyp": norm_hyp,
                    "wer": wer,
                })
        elif cat == "names":
            if wer > 0:
                name_errors.append({
                    "file": it["file"],
                    "norm_ref": norm_ref,
                    "norm_hyp": norm_hyp,
                    "wer": wer,
                })

        processed_items.append({
            "file": it["file"],
            "idx": it["idx"],
            "category": cat,
            "norm_ref": norm_ref,
            "norm_hyp": norm_hyp,
            "wer": round(wer, 4),
        })

    cat_summary = {c: round(sum(v) / len(v), 4) for c, v in by_category.items()}
    summary[engine_name] = {
        "overall_wer": round(sum(wers) / len(wers), 4),
        "by_category": cat_summary,
        "numbers_exact_match": f"{num_exact}/{num_total}",
        "numbers_exact_rate": round(num_exact / num_total, 4) if num_total else 0.0,
        "name_errors_count": len(name_errors),
        "number_errors_count": len(num_errors),
    }
    rescored_results[engine_name] = {
        "summary": summary[engine_name],
        "items": processed_items,
        "name_errors": name_errors,
        "number_errors": num_errors,
    }

output_payload = {
    "date": "2026-10-07",
    "scorer": "tests/benchmarks/voice/ws2/normalizer.py",
    "summary": summary,
    "details": rescored_results,
}

out_file = RESULTS_DIR / "rescored_accuracy.json"
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(output_payload, f, indent=2)

print("=== RESCORED ACCURACY SUMMARY ===")
print(f"{'Engine':<26} | {'Overall WER':<12} | {'Commands':<10} | {'Questions':<10} | {'Names':<10} | {'Numbers':<10} | {'Num Exact':<10}")
print("-" * 105)
for eng, s in summary.items():
    cats = s["by_category"]
    print(f"{eng:<26} | {s['overall_wer']*100:>10.2f}% | {cats.get('commands', 0)*100:>8.2f}% | {cats.get('questions', 0)*100:>8.2f}% | {cats.get('names', 0)*100:>8.2f}% | {cats.get('numbers', 0)*100:>8.2f}% | {s['numbers_exact_match']:>10}")
