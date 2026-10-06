#!/usr/bin/env python3
"""Test sherpa-large on bundled test_wavs."""

import numpy as np
import wave
from pathlib import Path
import sherpa_onnx

MODEL_DIR = Path("tests/benchmarks/voice/ws2/models/sherpa-onnx-streaming-zipformer-en-2023-06-26")
encoder = str(list(MODEL_DIR.glob("*encoder*chunk*.int8.onnx"))[0])
decoder = str(list(MODEL_DIR.glob("*decoder*chunk*.int8.onnx"))[0])
joiner = str(list(MODEL_DIR.glob("*joiner*chunk*.int8.onnx"))[0])
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

test_wavs = list((MODEL_DIR / "test_wavs").glob("*.wav"))
for wav_file in test_wavs:
    with wave.open(str(wav_file), "rb") as wf:
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        sr = wf.getframerate()
    
    print(f"\n--- {wav_file.name} (sr={sr}, len={len(audio)/sr:.2f}s) ---")
    
    stream = recognizer.create_stream()
    stream.accept_waveform(16000, audio)
    stream.input_finished()
    
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    
    result = recognizer.get_result_all(stream)
    text = result.text if hasattr(result, "text") else str(result)
    print(f"Hypothesis: {text.strip()}")