#!/usr/bin/env python3
"""
F6: Recompute final_from_endpoint with real H = 600 ms for sherpa-large.
15 stratified recordings, 10 runs each, paced 100 ms chunks, tail padding 600 ms.
"""

import time
import json
import wave
from pathlib import Path
import numpy as np
import sherpa_onnx

# Load stratified indices
with open("tests/benchmarks/voice/ws2/results/stratified_indices.json", "r") as f:
    stratified_indices = json.load(f)

RECORDINGS_DIR = Path("tests/benchmarks/voice/ws2/recordings")
recordings = sorted(RECORDINGS_DIR.glob("*.wav"))
selected_files = [recordings[i] for i in stratified_indices]

MODEL_DIR = Path("tests/benchmarks/voice/ws2/models/sherpa-onnx-streaming-zipformer-en-2023-06-26")
encoder = str(list(MODEL_DIR.glob("*encoder*chunk*.int8.onnx"))[0])
decoder = str(list(MODEL_DIR.glob("*decoder*chunk*.int8.onnx"))[0])
joiner = str(list(MODEL_DIR.glob("*joiner*chunk*.int8.onnx"))[0])
tokens = str(MODEL_DIR / "tokens.txt")

recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
    tokens=tokens, encoder=encoder, decoder=decoder, joiner=joiner,
    num_threads=4, decoding_method="greedy_search",
    enable_endpoint_detection=True,
    rule1_min_trailing_silence=2.4, rule2_min_trailing_silence=1.2, rule3_min_utterance_length=20.0,
    provider="cpu",
)

CHUNK_SIZE = 1600
H_MS = 600
TAIL_PADDING = np.zeros(int((H_MS / 1000.0) * 16000), dtype=np.float32)

all_ffe = []
for wav_file in selected_files:
    with wave.open(str(wav_file), "rb") as wf:
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

    for run in range(10):
        stream = recognizer.create_stream()
        for i in range(0, len(audio), CHUNK_SIZE):
            chunk = audio[i:i + CHUNK_SIZE]
            if len(chunk) == 0:
                continue
            stream.accept_waveform(16000, chunk)
            while recognizer.is_ready(stream):
                recognizer.decode_stream(stream)
            time.sleep(0.001)

        # Feed real H=600ms tail padding
        stream.accept_waveform(16000, TAIL_PADDING)
        stream.input_finished()
        t_endpoint = time.perf_counter()
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        ffe = (time.perf_counter() - t_endpoint) * 1000.0
        all_ffe.append(ffe)

arr = np.array(all_ffe)
mean_val = float(np.mean(arr))
std_val = float(np.std(arr))
ratio = std_val / mean_val if mean_val > 0 else 0

result = {
    "hangover_H_ms": H_MS,
    "n_runs": len(all_ffe),
    "mean_ms": round(mean_val, 2),
    "p50_ms": round(float(np.percentile(arr, 50)), 2),
    "p95_ms": round(float(np.percentile(arr, 95)), 2),
    "stdev_ms": round(std_val, 2),
    "stdev_over_mean_pct": round(ratio * 100, 2),
    "high_variance": (ratio > 0.25),
}

print(f"sherpa-large with H={H_MS}ms:")
print(f"  mean: {result['mean_ms']} ms | p50: {result['p50_ms']} ms | p95: {result['p95_ms']} ms | stdev: {result['stdev_ms']} ms")
print(f"  stdev / mean: {result['stdev_over_mean_pct']}% -> HIGH_VARIANCE = {result['high_variance']}")

out_file = Path("tests/benchmarks/voice/ws2/results/f6_h600_latency.json")
with open(out_file, "w") as f:
    json.dump(result, f, indent=2)
