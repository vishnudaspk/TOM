#!/usr/bin/env python3
"""
Task 2: Offline HYBRID-FINAL candidate evaluation.
Model: sherpa-onnx-moonshine-tiny-en-int8
API: sherpa_onnx.OfflineRecognizer.from_moonshine
Metrics: offline_final_from_endpoint p50/p95/stdev over 10 runs per recording
         grouped by duration bucket (<2s, 2-3s, >3s); WER by category.
Threads: 4 (num_threads=4)
Protocol: unpaced (whole audio fed at once), 10 runs per recording.
Produces: tests/benchmarks/voice/ws2/results/task2_offline_candidates.json
"""

import json
import time
import wave
import numpy as np
import os
import psutil
from pathlib import Path
import sherpa_onnx

RECORDINGS_DIR = Path("tests/benchmarks/voice/ws2/recordings")
UTTERANCES_FILE = Path("tests/benchmarks/voice/ws2/utterances.txt")
CORRECTIONS_FILE = Path("tests/benchmarks/voice/ws2/results/corrections.txt")
MOONSHINE_DIR = Path("tests/benchmarks/voice/ws2/models_offline/moonshine-tiny-en-int8")
RESULTS_DIR = Path("tests/benchmarks/voice/ws2/results")

N_RUNS = 10
NUM_THREADS = 4


# ---- References ----
def load_utterances():
    with open(UTTERANCES_FILE, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]

def load_corrections():
    corrections = {}
    if CORRECTIONS_FILE.exists():
        with open(CORRECTIONS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    parts = line.split(' ', 1)
                    if len(parts) == 2:
                        corrections[int(parts[0]) - 1] = parts[1].lower()
    return corrections

UTTERANCES = load_utterances()
CORRECTIONS = load_corrections()

def get_reference(idx):
    if idx in CORRECTIONS:
        return CORRECTIONS[idx]
    return UTTERANCES[idx].lower()

# ---- Normalizer (same as task1c) ----
import re

NUM_WORDS = {
    'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5',
    'six': '6', 'seven': '7', 'eight': '8', 'nine': '9', 'ten': '10',
    'eleven': '11', 'twelve': '12', 'thirteen': '13', 'fourteen': '14', 'fifteen': '15',
    'sixteen': '16', 'seventeen': '17', 'eighteen': '18', 'nineteen': '19',
    'twenty': '20', 'thirty': '30', 'forty': '40', 'fifty': '50', 'sixty': '60',
    'seventy': '70', 'eighty': '80', 'ninety': '90', 'hundred': '100', 'thousand': '1000',
}
ORDINALS = {
    'first': '1st', 'second': '2nd', 'third': '3rd', 'fourth': '4th', 'fifth': '5th',
}
AM_PM = {'am', 'pm'}

def normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s']", ' ', text)
    text = re.sub(r"\s+", ' ', text).strip()
    words = text.split()
    words = [ORDINALS.get(w, w) for w in words]
    result = []
    i = 0
    while i < len(words):
        w = words[i]
        if w in NUM_WORDS:
            num = int(NUM_WORDS[w])
            if w in ['twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety'] and i + 1 < len(words):
                nw = words[i + 1]
                if nw in ['one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine']:
                    num = int(NUM_WORDS[w]) + int(NUM_WORDS[nw])
                    i += 1
            if i + 1 < len(words):
                next1 = words[i + 1]
                if next1 in NUM_WORDS and 0 <= int(NUM_WORDS[next1]) <= 59:
                    mins = int(NUM_WORDS[next1])
                    if i + 2 < len(words) and words[i + 2] in AM_PM:
                        result.append(f"{num}:{mins:02d}")
                        result.append(words[i + 2])
                        i += 3
                        continue
            result.append(str(num))
        else:
            result.append(w)
        i += 1
    return ' '.join(result)

def compute_wer(ref: str, hyp: str) -> float:
    r, h = ref.split(), hyp.split()
    if not r and not h: return 0.0
    if not r or not h: return 1.0
    dp = [[0]*(len(h)+1) for _ in range(len(r)+1)]
    for i in range(len(r)+1): dp[i][0] = i
    for j in range(len(h)+1): dp[0][j] = j
    for i in range(1, len(r)+1):
        for j in range(1, len(h)+1):
            dp[i][j] = dp[i-1][j-1] if r[i-1]==h[j-1] else min(dp[i-1][j], dp[i][j-1], dp[i-1][j-1]) + 1
    return dp[len(r)][len(h)] / len(r)

def categorize(idx):
    if idx < 15:  return "commands"
    elif idx < 25: return "questions"
    elif idx < 33: return "names"
    else:          return "numbers"

def load_wav_float32(path: Path):
    with wave.open(str(path), 'rb') as wf:
        sr = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    return audio, sr


def duration_bucket(dur_s: float) -> str:
    if dur_s < 2.0: return "<2s"
    elif dur_s <= 3.0: return "2-3s"
    else: return ">3s"


def make_moonshine_recognizer():
    preprocessor = str(MOONSHINE_DIR / "preprocess.onnx")
    encoder = str(MOONSHINE_DIR / "encode.int8.onnx")
    uncached_decoder = str(MOONSHINE_DIR / "uncached_decode.int8.onnx")
    cached_decoder = str(MOONSHINE_DIR / "cached_decode.int8.onnx")
    tokens = str(MOONSHINE_DIR / "tokens.txt")
    return sherpa_onnx.OfflineRecognizer.from_moonshine(
        preprocessor=preprocessor,
        encoder=encoder,
        uncached_decoder=uncached_decoder,
        cached_decoder=cached_decoder,
        tokens=tokens,
        num_threads=NUM_THREADS,
        decoding_method="greedy_search",
        provider="cpu",
    )


def decode_offline(recognizer, audio, sr=16000) -> str:
    stream = recognizer.create_stream()
    stream.accept_waveform(sr, audio)
    recognizer.decode_stream(stream)
    return stream.result.text.strip()


def main():
    print("=" * 70)
    print("Task 2: Offline HYBRID-FINAL candidate evaluation")
    print(f"sherpa_onnx version: {sherpa_onnx.__version__}")
    print(f"Model: {MOONSHINE_DIR.name}")
    print(f"Threads: {NUM_THREADS}, Runs: {N_RUNS} per recording")
    print("=" * 70)

    # Verify model files
    required_files = ["preprocess.onnx", "encode.int8.onnx", "uncached_decode.int8.onnx", "cached_decode.int8.onnx", "tokens.txt"]
    for f in required_files:
        p = MOONSHINE_DIR / f
        if not p.exists():
            print(f"MISSING: {p}")
            return
        sz = p.stat().st_size / 1024 / 1024
        print(f"  {f}: {sz:.1f} MB")

    recognizer = make_moonshine_recognizer()
    print("\nRecognizer loaded. Starting evaluation...")

    process = psutil.Process(os.getpid())
    recordings = sorted(RECORDINGS_DIR.glob("*.wav"))

    all_results = []
    all_latencies_by_bucket = {"<2s": [], "2-3s": [], ">3s": []}
    all_wer = []
    cat_wer = {"commands": [], "questions": [], "names": [], "numbers": []}
    num_exact = 0
    num_total = 0
    cpu_samples = []
    peak_ram_mb = 0

    for idx, wav_file in enumerate(recordings):
        audio, sr = load_wav_float32(wav_file)
        dur_s = len(audio) / sr
        bucket = duration_bucket(dur_s)
        ref = get_reference(idx)
        ref_n = normalize_text(ref)
        cat = categorize(idx)

        # Warmup decode (not counted)
        _ = decode_offline(recognizer, audio, sr)

        # Timed runs
        run_latencies = []
        hyp = ""
        for run in range(N_RUNS):
            process.cpu_percent(interval=None)  # reset
            t0 = time.perf_counter()
            stream = recognizer.create_stream()
            stream.accept_waveform(sr, audio)
            recognizer.decode_stream(stream)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            cpu_pct = process.cpu_percent(interval=None)
            cpu_samples.append(cpu_pct)
            run_latencies.append(elapsed_ms)
            if run == 0:
                hyp = stream.result.text.strip()

        mem_mb = process.memory_info().rss / 1024 / 1024
        peak_ram_mb = max(peak_ram_mb, mem_mb)

        lats = np.array(run_latencies)
        p50 = float(np.percentile(lats, 50))
        p95 = float(np.percentile(lats, 95))
        stdev = float(np.std(lats))

        hyp_n = normalize_text(hyp)
        wer = compute_wer(ref_n, hyp_n)
        all_wer.append(wer)
        cat_wer[cat].append(wer)
        all_latencies_by_bucket[bucket].append(lats.tolist())

        if cat == "numbers":
            num_total += 1
            if wer == 0.0:
                num_exact += 1

        print(f"  [{idx+1:02d}] {wav_file.name} dur={dur_s:.2f}s bucket={bucket}")
        print(f"       p50={p50:.1f}ms p95={p95:.1f}ms stdev={stdev:.1f}ms")
        print(f"       WER={wer:.3f} | ref='{ref_n}' | hyp='{hyp_n}'")

        all_results.append({
            "file": wav_file.name, "idx": idx, "category": cat,
            "duration_s": round(dur_s, 3), "bucket": bucket,
            "reference": ref, "hypothesis": hyp,
            "ref_normalized": ref_n, "hyp_normalized": hyp_n,
            "wer": round(wer, 4),
            "latency_ms": {"p50": round(p50, 2), "p95": round(p95, 2), "stdev": round(stdev, 2), "runs": run_latencies},
        })

    # Summary
    print("\n=== MOONSHINE-TINY SUMMARY ===")
    print(f"  Overall WER (mean): {np.mean(all_wer):.3f}  n={len(all_wer)}")
    for cat in ["commands", "questions", "names", "numbers"]:
        w = cat_wer[cat]
        if w: print(f"  {cat}: mean WER={np.mean(w):.3f}  n={len(w)}")
    print(f"  numbers exact-match: {num_exact}/{num_total}")

    print("\n  Latency by bucket (10 runs per recording, unpaced):")
    bucket_summary = {}
    for b, lats_list in all_latencies_by_bucket.items():
        if lats_list:
            flat = [v for sublist in lats_list for v in sublist]
            bs = {
                "n_recordings": len(lats_list),
                "n_total_runs": len(flat),
                "p50": round(float(np.percentile(flat, 50)), 2),
                "p95": round(float(np.percentile(flat, 95)), 2),
                "stdev": round(float(np.std(flat)), 2),
                "mean": round(float(np.mean(flat)), 2),
            }
            bucket_summary[b] = bs
            high_var = bs["stdev"] > 0.25 * bs["mean"]
            print(f"  {b}: p50={bs['p50']}ms p95={bs['p95']}ms stdev={bs['stdev']}ms n_recs={bs['n_recordings']}"
                  + (" HIGH-VARIANCE" if high_var else ""))
        else:
            print(f"  {b}: no recordings")

    cpu_avg = float(np.mean(cpu_samples)) if cpu_samples else 0
    cpu_peak = float(np.max(cpu_samples)) if cpu_samples else 0
    print(f"\n  CPU: avg={cpu_avg:.1f}%  peak={cpu_peak:.1f}%  (psutil per-process, {NUM_THREADS} threads)")
    print(f"  Peak RAM: {peak_ram_mb:.0f} MB")

    # Meets targets?
    print("\n  Target check (p50<=350ms, p95<=500ms):")
    for b, bs in bucket_summary.items():
        p50_ok = bs["p50"] <= 350
        p95_ok = bs["p95"] <= 500
        print(f"    {b}: p50={'PASS' if p50_ok else 'FAIL'} ({bs['p50']}ms)  p95={'PASS' if p95_ok else 'FAIL'} ({bs['p95']}ms)")

    # Save
    output = {
        "date": "2026-10-07",
        "model": "sherpa-onnx-moonshine-tiny-en-int8",
        "source_repo": "csukuangfj/sherpa-onnx-moonshine-tiny-en-int8",
        "license": "MIT",
        "sherpa_onnx_version": sherpa_onnx.__version__,
        "num_threads": NUM_THREADS,
        "n_runs": N_RUNS,
        "protocol": "unpaced (whole audio at once), endpoint-to-text, 10 runs per recording, warmup excluded",
        "accuracy": {
            "overall_wer_mean": round(float(np.mean(all_wer)), 4),
            "by_category": {cat: round(float(np.mean(w)), 4) if w else None for cat, w in cat_wer.items()},
            "numbers_exact_match": f"{num_exact}/{num_total}",
        },
        "latency_by_bucket": bucket_summary,
        "cpu": {"avg_pct": round(cpu_avg, 1), "peak_pct": round(cpu_peak, 1), "num_threads": NUM_THREADS},
        "peak_ram_mb": round(peak_ram_mb, 0),
        "per_file_results": all_results,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / "task2_offline_candidates.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
