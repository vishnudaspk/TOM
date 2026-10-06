#!/usr/bin/env python3
"""Test sherpa-20M with corrected metrics."""

import time
import numpy as np
import wave
import json
from pathlib import Path
import sherpa_onnx

MODEL_DIR = Path("tests/benchmarks/voice/ws2/models/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17")
RECORDINGS_DIR = Path("tests/benchmarks/voice/ws2/recordings")

# Check model files
encoder_files = list(MODEL_DIR.glob("*encoder*chunk*.int8.onnx"))
if not encoder_files:
    encoder_files = list(MODEL_DIR.glob("*encoder*.int8.onnx"))
encoder = str(encoder_files[0])
decoder = str(list(MODEL_DIR.glob("*decoder*.int8.onnx"))[0])
joiner = str(list(MODEL_DIR.glob("*joiner*.int8.onnx"))[0])
tokens = str(MODEL_DIR / "tokens.txt")

print(f"Encoder: {Path(encoder).name}")
print(f"Decoder: {Path(decoder).name}")
print(f"Joiner: {Path(joiner).name}")

recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
    tokens=tokens, encoder=encoder, decoder=decoder, joiner=joiner,
    num_threads=4, decoding_method="greedy_search",
    enable_endpoint_detection=True,
    rule1_min_trailing_silence=2.4, rule2_min_trailing_silence=1.2, rule3_min_utterance_length=20.0,
    provider="cpu",
)

results = []
for wav_file in sorted(RECORDINGS_DIR.glob("*.wav")):
    with wave.open(str(wav_file), "rb") as wf:
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        sr = wf.getframerate()

    threshold = 0.01
    onset_sample = 0
    for i in range(0, len(audio) - 160, 160):
        if np.sqrt(np.mean(audio[i:i+160]**2)) > threshold:
            onset_sample = i
            break
    onset_time = onset_sample / sr

    stream = recognizer.create_stream()
    start = time.perf_counter()
    speech_onset = start + onset_time

    first_partial_time = None
    first_partial_text = ""
    CHUNK_SIZE = 1600

    for i in range(0, len(audio), CHUNK_SIZE):
        chunk = audio[i:i+CHUNK_SIZE]
        if len(chunk) == 0:
            continue
        chunk_start = time.perf_counter()
        stream.accept_waveform(16000, chunk)
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
            result = recognizer.get_result(stream)
            if result and result.strip() and first_partial_time is None:
                first_partial_time = time.perf_counter() - speech_onset
                first_partial_text = result.strip()
        elapsed = time.perf_counter() - chunk_start
        sleep_time = 0.1 - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)

    speech_end = time.perf_counter()
    stream.accept_waveform(16000, np.zeros(int(0.3 * 16000), dtype=np.float32))
    stream.input_finished()
    endpoint = time.perf_counter()

    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    ffe = time.perf_counter() - endpoint

    final = recognizer.get_result_all(stream)
    final_text = final.text.strip() if hasattr(final, "text") else str(final).strip()

    fp_onset = first_partial_time * 1000 if first_partial_time else None
    results.append({
        "file": wav_file.name,
        "leading_silence_ms": round(onset_time * 1000, 1),
        "first_partial_from_onset_ms": round(fp_onset, 1) if fp_onset else None,
        "first_partial_text": first_partial_text,
        "final_from_endpoint_ms": round(ffe * 1000, 1),
        "final_text": final_text,
    })
    print(f"{wav_file.name}: lead={onset_time*1000:.0f}ms, fp_onset={fp_onset}, ffe={ffe*1000:.1f}ms, text=\"{final_text[:50]}\"")

with open("tests/benchmarks/voice/ws2/results/sherpa_20M_corrected.json", "w") as f:
    json.dump(results, f, indent=2)
print("Saved")