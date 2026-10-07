#!/usr/bin/env python3
"""
Test loading and decoding with Parakeet TDT 0.6B int8 in sherpa-onnx.
"""
import time
import wave
import numpy as np
import sherpa_onnx
from pathlib import Path

model_dir = Path("tests/benchmarks/voice/ws2/models_offline/parakeet-tdt-0.6b-v2-int8")
encoder = str(model_dir / "encoder.int8.onnx")
decoder = str(model_dir / "decoder.int8.onnx")
joiner = str(model_dir / "joiner.int8.onnx")
tokens = str(model_dir / "tokens.txt")

print(f"Testing Parakeet TDT 0.6B int8 from {model_dir}:")
print(f"  Encoder exists: {Path(encoder).exists()} ({Path(encoder).stat().st_size / (1024*1024):.1f} MB)")
print(f"  Decoder exists: {Path(decoder).exists()} ({Path(decoder).stat().st_size / (1024*1024):.1f} MB)")
print(f"  Joiner exists:  {Path(joiner).exists()} ({Path(joiner).stat().st_size / (1024*1024):.1f} MB)")
print(f"  Tokens exists:  {Path(tokens).exists()} ({Path(tokens).stat().st_size} bytes)")

try:
    print("\nAttempting OfflineRecognizer.from_transducer...")
    recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=encoder,
        decoder=decoder,
        joiner=joiner,
        tokens=tokens,
        num_threads=4,
        decoding_method="greedy_search",
        model_type="transducer",
    )
    print("Recognizer created successfully!")
    
    # Try decode
    wav_path = "tests/benchmarks/voice/ws2/recordings/001_open_the_settings_window.wav"
    with wave.open(wav_path, "rb") as wf:
        audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0

    t0 = time.perf_counter()
    s = recognizer.create_stream()
    s.accept_waveform(16000, audio)
    recognizer.decode_stream(s)
    elapsed = time.perf_counter() - t0
    print(f"Decoded: '{s.result.text}' in {elapsed*1000:.1f}ms")

except Exception as e:
    import traceback
    print("\nFAILED TO LOAD OR DECODE:")
    traceback.print_exc()
