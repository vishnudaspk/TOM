#!/usr/bin/env python3
"""Check audio properties of our recordings and what's passed to recognizer."""

import numpy as np
import wave
from pathlib import Path

RECORDINGS_DIR = Path("tests/benchmarks/voice/ws2/recordings")
recordings = sorted(RECORDINGS_DIR.glob("*.wav"))[:3]

for wav_file in recordings:
    with wave.open(str(wav_file), "rb") as wf:
        frames = wf.readframes(wf.getnframes())
        audio_int16 = np.frombuffer(frames, dtype=np.int16)
        audio_float = audio_int16.astype(np.float32) / 32768.0
        sr = wf.getframerate()
    
    print(f"\n--- {wav_file.name} ---")
    print(f"  Sample rate: {sr}")
    print(f"  Original dtype: int16")
    print(f"  Int16 min/max: {audio_int16.min()}, {audio_int16.max()}")
    print(f"  Float32 min/max: {audio_float.min():.6f}, {audio_float.max():.6f}")
    print(f"  Duration: {len(audio_float)/sr:.2f}s")
    print(f"  Passed to recognizer: float32 in [-1, 1] at 16000 Hz")
    print(f"  Scaling: / 32768.0")