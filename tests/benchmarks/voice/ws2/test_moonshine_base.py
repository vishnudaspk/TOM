#!/usr/bin/env python3
import time
import wave
import numpy as np
import sherpa_onnx

base_dir = "tests/benchmarks/voice/ws2/models_offline/moonshine-base-en-int8"
r = sherpa_onnx.OfflineRecognizer.from_moonshine(
    preprocessor=f"{base_dir}/preprocess.onnx",
    encoder=f"{base_dir}/encode.int8.onnx",
    uncached_decoder=f"{base_dir}/uncached_decode.int8.onnx",
    cached_decoder=f"{base_dir}/cached_decode.int8.onnx",
    tokens=f"{base_dir}/tokens.txt",
    num_threads=4,
)

wav_path = "tests/benchmarks/voice/ws2/recordings/001_open_the_settings_window.wav"
with wave.open(wav_path, "rb") as wf:
    audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0

t0 = time.perf_counter()
s = r.create_stream()
s.accept_waveform(16000, audio)
r.decode_stream(s)
elapsed = time.perf_counter() - t0

print(f"Decoded: '{s.result.text}' in {elapsed*1000:.1f}ms")
