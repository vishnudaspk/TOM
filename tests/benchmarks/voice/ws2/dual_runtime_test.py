#!/usr/bin/env python3
"""
Dual-runtime smoke test: import onnxruntime and sherpa_onnx in same process,
run Kokoro synthesis and sherpa decode concurrently.
"""

import time
import numpy as np
import wave
from pathlib import Path
import sherpa_onnx
import kokoro_onnx
import soundfile as sf

print("=== Dual-runtime smoke test ===")

# Load sherpa model
MODEL_DIR = Path("tests/benchmarks/voice/ws2/models/sherpa-onnx-streaming-zipformer-en-2023-06-26")
encoder = str(list(MODEL_DIR.glob("*encoder*chunk*.int8.onnx"))[0])
decoder = str(list(MODEL_DIR.glob("*decoder*chunk*.int8.onnx"))[0])
joiner = str(list(MODEL_DIR.glob("*joiner*chunk*.int8.onnx"))[0])
tokens = str(MODEL_DIR / "tokens.txt")

print("Loading sherpa-onnx recognizer...")
recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
    tokens=tokens, encoder=encoder, decoder=decoder, joiner=joiner,
    num_threads=4, decoding_method="greedy_search",
    enable_endpoint_detection=True, provider="cpu",
)
print("sherpa-onnx loaded")

# Load Kokoro model
print("Loading kokoro-onnx...")
# Try to find kokoro model files
import kokoro_onnx
print("kokoro_onnx module loaded")

# Check if model files exist
kokoro_model = Path("tests/benchmarks/voice/ws2/models/kokoro-v1.0.onnx")
kokoro_voices = Path("tests/benchmarks/voice/ws2/models/voices-v1.0.bin")

if not kokoro_model.exists():
    print("Kokoro model not found, skipping synthesis test")
    print("Expected: tests/benchmarks/voice/ws2/models/kokoro-v1.0.onnx and voices-v1.0.bin")
else:
    kokoro = kokoro_onnx.Kokoro(str(kokoro_model), str(kokoro_voices))
    print("kokoro-onnx loaded")

# Test 1: Run sherpa decode
print("\n--- Test 1: sherpa decode ---")
wav_file = "tests/benchmarks/voice/ws2/recordings/001_open_the_settings_window.wav"
with wave.open(wav_file, "rb") as wf:
    frames = wf.readframes(wf.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

start = time.perf_counter()
stream = recognizer.create_stream()
stream.accept_waveform(16000, audio)
stream.input_finished()
while recognizer.is_ready(stream):
    recognizer.decode_stream(stream)
result = recognizer.get_result_all(stream)
text = result.text if hasattr(result, "text") else str(result)
sherpa_time = time.perf_counter() - start
print(f"Sherpa decode: {sherpa_time*1000:.1f}ms, text='{text.strip()}'")

# Test 2: Run Kokoro synthesis (if model available)
if kokoro_model.exists():
    print("\n--- Test 2: Kokoro synthesis ---")
    start = time.perf_counter()
    samples, sample_rate = kokoro.create("Hello world, this is a test.", voice="af_heart", speed=1.0)
    kokoro_time = time.perf_counter() - start
    print(f"Kokoro synthesis: {kokoro_time*1000:.1f}ms, {len(samples)} samples at {sample_rate}Hz")
else:
    print("\n--- Test 2: SKIPPED (no model) ---")

# Test 3: Concurrent execution
print("\n--- Test 3: Concurrent execution ---")
import threading

sherpa_result = None
kokoro_result = None
sherpa_error = None
kokoro_error = None

def run_sherpa():
    global sherpa_result, sherpa_error
    try:
        start = time.perf_counter()
        stream = recognizer.create_stream()
        stream.accept_waveform(16000, audio)
        stream.input_finished()
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        result = recognizer.get_result_all(stream)
        sherpa_result = result.text if hasattr(result, "text") else str(result)
        sherpa_time = time.perf_counter() - start
    except Exception as e:
        sherpa_error = str(e)

def run_kokoro():
    global kokoro_result, kokoro_error
    if not kokoro_model.exists():
        kokoro_error = "Model not found"
        return
    try:
        start = time.perf_counter()
        samples, sample_rate = kokoro.create("Testing concurrent execution.", voice="af_heart", speed=1.0)
        kokoro_result = f"{len(samples)} samples at {sample_rate}Hz"
        kokoro_time = time.perf_counter() - start
    except Exception as e:
        kokoro_error = str(e)

t1 = threading.Thread(target=run_sherpa)
t2 = threading.Thread(target=run_kokoro)

start = time.perf_counter()
t1.start()
t2.start()
t1.join()
t2.join()
total_time = time.perf_counter() - start

print(f"Concurrent execution: {total_time*1000:.1f}ms total")
print(f"  Sherpa: {sherpa_result[:50] if sherpa_result else 'ERROR: ' + str(sherpa_error)}")
print(f"  Kokoro: {kokoro_result if kokoro_result else 'ERROR: ' + str(kokoro_error)}")

if sherpa_error or kokoro_error:
    print("\n⚠️  ERRORS DETECTED - Dual-runtime conflict possible")
else:
    print("\n✅ No crashes, DLL errors, or timing issues detected")

print("\n=== Dual-runtime smoke test complete ===")