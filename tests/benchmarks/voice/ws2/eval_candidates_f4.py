#!/usr/bin/env python3
"""
F4: Candidate STT Models (HYBRID-FINAL) Evaluation.
Models:
1. Moonshine-base int8 (csukuangfj/sherpa-onnx-moonshine-base-en-int8)
2. NeMo Parakeet TDT 0.6B int8 (csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8)
3. NeMo Parakeet 110M int8 (csukuangfj/sherpa-onnx-nemo-parakeet_tdt_transducer_110m-en-36000-int8)

Metrics:
- 4 threads, unpaced, endpoint-to-text
- offline_final_from_endpoint p50/p95/stdev over 10 runs per recording
- Grouped by duration bucket (<2s, 2-3s, >3s)
- WER and numbers exact-match using F2 scorer (normalizer.py) per category
- Peak RAM RSS (via psutil)
"""

import time
import json
import wave
import psutil
import os
from pathlib import Path
import numpy as np
import sherpa_onnx
from normalizer import normalize_text, compute_wer

RECORDINGS_DIR = Path("tests/benchmarks/voice/ws2/recordings")
UTTERANCES_FILE = Path("tests/benchmarks/voice/ws2/utterances.txt")
CORRECTIONS_FILE = Path("tests/benchmarks/voice/ws2/results/corrections.txt")
RESULTS_DIR = Path("tests/benchmarks/voice/ws2/results")

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
                        corrections[int(parts[0]) - 1] = parts[1]
    return corrections

UTTERANCES = load_utterances()
CORRECTIONS = load_corrections()
recordings = sorted(RECORDINGS_DIR.glob("*.wav"))

def load_wav(filepath):
    with wave.open(str(filepath), 'rb') as wf:
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        sr = wf.getframerate()
    return audio, sr

def get_bucket(dur: float) -> str:
    if dur < 2.0:
        return "<2s"
    elif dur <= 3.0:
        return "2-3s"
    else:
        return ">3s"

# Pre-load audio
audio_cache = {}
for i, wav_path in enumerate(recordings):
    audio, sr = load_wav(wav_path)
    dur = len(audio) / sr
    audio_cache[wav_path.name] = {
        "idx": i,
        "audio": audio,
        "sr": sr,
        "duration_s": dur,
        "bucket": get_bucket(dur),
        "category": (
            "commands" if i < 15 else
            "questions" if i < 25 else
            "names" if i < 33 else
            "numbers"
        ),
        "raw_ref": CORRECTIONS.get(i, UTTERANCES[i]),
    }

def eval_recognizer(name: str, recognizer, model_info: dict):
    print(f"\n=======================================================")
    print(f"Evaluating candidate: {name}")
    print(f"=======================================================")
    process = psutil.Process(os.getpid())
    ram_initial = process.memory_info().rss / (1024 * 1024)

    # 1. Accuracy pass (1 run per file)
    per_file_accuracy = []
    wers = []
    by_category = {}
    num_exact = 0
    num_total = 0
    name_errors = []
    num_errors = []

    for wav_name, item in audio_cache.items():
        stream = recognizer.create_stream()
        stream.accept_waveform(item["sr"], item["audio"])
        recognizer.decode_stream(stream)
        hyp_raw = stream.result.text.strip() if stream.result else ""

        norm_ref = normalize_text(item["raw_ref"])
        norm_hyp = normalize_text(hyp_raw)
        wer = compute_wer(norm_ref, norm_hyp)
        wers.append(wer)

        cat = item["category"]
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(wer)

        if cat == "numbers":
            num_total += 1
            if norm_ref == norm_hyp:
                num_exact += 1
            else:
                num_errors.append({"file": wav_name, "ref": norm_ref, "hyp": norm_hyp, "wer": wer})
        elif cat == "names":
            if wer > 0:
                name_errors.append({"file": wav_name, "ref": norm_ref, "hyp": norm_hyp, "wer": wer})

        per_file_accuracy.append({
            "file": wav_name,
            "idx": item["idx"],
            "category": cat,
            "bucket": item["bucket"],
            "raw_ref": item["raw_ref"],
            "raw_hyp": hyp_raw,
            "norm_ref": norm_ref,
            "norm_hyp": norm_hyp,
            "wer": round(wer, 4),
        })

    overall_wer = sum(wers) / len(wers)
    cat_summary = {c: round(sum(v) / len(v), 4) for c, v in by_category.items()}

    print(f"Accuracy pass complete:")
    print(f"  Overall WER: {overall_wer*100:.2f}%")
    print(f"  Commands:    {cat_summary.get('commands', 0)*100:.2f}%")
    print(f"  Questions:   {cat_summary.get('questions', 0)*100:.2f}%")
    print(f"  Names:       {cat_summary.get('names', 0)*100:.2f}%")
    print(f"  Numbers:     {cat_summary.get('numbers', 0)*100:.2f}%")
    print(f"  Numbers exact: {num_exact}/{num_total}")

    # 2. Latency pass (10 unpaced runs per recording)
    print("\nRunning latency pass (10 runs per recording)...")
    latency_by_bucket = {"<2s": [], "2-3s": [], ">3s": []}
    per_file_latency = {}

    # Warmup 3 runs
    warm_audio = list(audio_cache.values())[0]["audio"]
    for _ in range(3):
        s = recognizer.create_stream()
        s.accept_waveform(16000, warm_audio)
        recognizer.decode_stream(s)

    ram_peak = process.memory_info().rss / (1024 * 1024)

    for wav_name, item in audio_cache.items():
        file_latencies = []
        for _ in range(10):
            t0 = time.perf_counter()
            s = recognizer.create_stream()
            s.accept_waveform(item["sr"], item["audio"])
            recognizer.decode_stream(s)
            lat_ms = (time.perf_counter() - t0) * 1000.0
            file_latencies.append(lat_ms)

        current_rss = process.memory_info().rss / (1024 * 1024)
        if current_rss > ram_peak:
            ram_peak = current_rss

        per_file_latency[wav_name] = {
            "mean_ms": round(float(np.mean(file_latencies)), 2),
            "p50_ms": round(float(np.percentile(file_latencies, 50)), 2),
            "p95_ms": round(float(np.percentile(file_latencies, 95)), 2),
            "stdev_ms": round(float(np.std(file_latencies)), 2),
            "runs": [round(x, 2) for x in file_latencies],
        }
        latency_by_bucket[item["bucket"]].extend(file_latencies)

    bucket_stats = {}
    for b_name, lats in latency_by_bucket.items():
        if lats:
            arr = np.array(lats)
            b_mean = float(np.mean(arr))
            b_std = float(np.std(arr))
            bucket_stats[b_name] = {
                "n_runs": len(lats),
                "mean_ms": round(b_mean, 2),
                "p50_ms": round(float(np.percentile(arr, 50)), 2),
                "p95_ms": round(float(np.percentile(arr, 95)), 2),
                "stdev_ms": round(b_std, 2),
                "high_variance": (b_std > 0.25 * b_mean),
            }

    print("Latency stats by bucket:")
    for b, bs in bucket_stats.items():
        print(f"  Bucket {b:<4}: p50={bs['p50_ms']}ms, p95={bs['p95_ms']}ms, stdev={bs['stdev_ms']}ms, high_variance={bs['high_variance']}")

    print(f"Peak RAM RSS: {ram_peak:.1f} MB (initial {ram_initial:.1f} MB)")

    return {
        "model_name": name,
        "model_info": model_info,
        "peak_ram_mb": round(ram_peak, 1),
        "accuracy": {
            "overall_wer": round(overall_wer, 4),
            "by_category": cat_summary,
            "numbers_exact_match": f"{num_exact}/{num_total}",
            "numbers_exact_rate": round(num_exact / num_total, 4) if num_total else 0.0,
            "name_errors": name_errors,
            "number_errors": num_errors,
        },
        "latency_by_bucket": bucket_stats,
        "per_file_accuracy": per_file_accuracy,
        "per_file_latency": per_file_latency,
    }

if __name__ == "__main__":
    results = {}

    # 1. Moonshine-base int8
    base_dir = Path("tests/benchmarks/voice/ws2/models_offline/moonshine-base-en-int8")
    if base_dir.exists():
        r_base = sherpa_onnx.OfflineRecognizer.from_moonshine(
            preprocessor=str(base_dir / "preprocess.onnx"),
            encoder=str(base_dir / "encode.int8.onnx"),
            uncached_decoder=str(base_dir / "uncached_decode.int8.onnx"),
            cached_decoder=str(base_dir / "cached_decode.int8.onnx"),
            tokens=str(base_dir / "tokens.txt"),
            num_threads=4,
        )
        base_info = {
            "source_repo": "csukuangfj/sherpa-onnx-moonshine-base-en-int8",
            "license": "MIT",
            "size_disk_mb": 273.6,
            "num_threads": 4,
        }
        results["moonshine-base-en-int8"] = eval_recognizer("moonshine-base-en-int8", r_base, base_info)

    out_file = RESULTS_DIR / "f4_candidate_models.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved candidates results to {out_file}")
