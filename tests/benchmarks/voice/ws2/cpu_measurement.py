#!/usr/bin/env python3
"""
CPU measurement for sherpa-large streaming decode.
Measures per-process CPU (normalized to cores) and RTF per chunk
for num_threads 1, 2, 4. Checks onnxruntime spin-waiting.
"""

import time
import numpy as np
import wave
import psutil
import os
from pathlib import Path
import sherpa_onnx

MODEL_DIR = Path("tests/benchmarks/voice/ws2/models/sherpa-onnx-streaming-zipformer-en-2023-06-26")
encoder = str(list(MODEL_DIR.glob("*encoder*chunk*.onnx"))[1])
decoder = str(list(MODEL_DIR.glob("*decoder*chunk*.onnx"))[1])
joiner = str(list(MODEL_DIR.glob("*joiner*chunk*.onnx"))[1])
tokens = str(MODEL_DIR / "tokens.txt")

# Load one test recording
wav_file = Path("tests/benchmarks/voice/ws2/recordings/001_open_the_settings_window.wav")
with wave.open(str(wav_file), "rb") as wf:
    frames = wf.readframes(wf.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

CHUNK_SIZE = 1600  # 100ms at 16kHz
NUM_CHUNKS = len(audio) // CHUNK_SIZE + 1
AUDIO_DURATION = len(audio) / 16000

print(f"Test audio: {wav_file.name}, duration={AUDIO_DURATION:.2f}s, chunks={NUM_CHUNKS}")
print(f"CPU cores (logical): {psutil.cpu_count()}")
print(f"CPU cores (physical): {psutil.cpu_count(logical=False)}")
print()

class CPUMonitor:
    def __init__(self):
        self.process = psutil.Process(os.getpid())
        self.samples = []
        self.monitoring = False
    
    def start(self):
        self.samples = []
        self.monitoring = True
        # Prime the cpu_percent call
        self.process.cpu_percent(interval=None)
    
    def stop(self):
        self.monitoring = False
    
    def sample(self):
        if self.monitoring:
            self.samples.append(self.process.cpu_percent(interval=None))
    
    def get_stats(self):
        if not self.samples:
            return {"avg_pct": 0, "peak_pct": 0, "avg_normalized": 0, "peak_normalized": 0}
        logical_cores = psutil.cpu_count()
        return {
            "avg_pct": np.mean(self.samples),
            "peak_pct": np.max(self.samples),
            "avg_normalized": np.mean(self.samples) / logical_cores * 100,
            "peak_normalized": np.max(self.samples) / logical_cores * 100,
        }

def test_threading(num_threads):
    print(f"\n=== num_threads={num_threads} ===")
    
    recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=tokens, encoder=encoder, decoder=decoder, joiner=joiner,
        num_threads=num_threads, decoding_method="greedy_search",
        enable_endpoint_detection=False,
        provider="cpu",
    )
    
    cpu_monitor = CPUMonitor()
    chunk_times = []
    
    # Warmup
    stream = recognizer.create_stream()
    stream.accept_waveform(16000, audio[:CHUNK_SIZE])
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    
    # Actual test
    stream = recognizer.create_stream()
    cpu_monitor.start()
    
    start_total = time.perf_counter()
    for i in range(0, len(audio), CHUNK_SIZE):
        chunk = audio[i:i+CHUNK_SIZE]
        if len(chunk) == 0:
            continue
        
        chunk_start = time.perf_counter()
        stream.accept_waveform(16000, chunk)
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        chunk_elapsed = time.perf_counter() - chunk_start
        chunk_times.append(chunk_elapsed)
        cpu_monitor.sample()
        
        # Real-time pacing
        sleep_time = 0.1 - chunk_elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)
    
    stream.input_finished()
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    
    total_elapsed = time.perf_counter() - start_total
    cpu_monitor.stop()
    
    cpu_stats = cpu_monitor.get_stats()
    rtf_per_chunk = np.array(chunk_times) / 0.1  # 100ms chunks
    
    print(f"  Total wall time: {total_elapsed:.2f}s (audio: {AUDIO_DURATION:.2f}s)")
    print(f"  Chunk times: avg={np.mean(chunk_times)*1000:.1f}ms, max={np.max(chunk_times)*1000:.1f}ms")
    print(f"  RTF per chunk: avg={np.mean(rtf_per_chunk):.4f}, max={np.max(rtf_per_chunk):.4f}")
    print(f"  Per-process CPU: avg={cpu_stats['avg_pct']:.1f}%, peak={cpu_stats['peak_pct']:.1f}%")
    print(f"  Per-process CPU (normalized to {psutil.cpu_count()} cores): avg={cpu_stats['avg_normalized']:.1f}%, peak={cpu_stats['peak_normalized']:.1f}%")
    print(f"  Overall RTF: {total_elapsed / AUDIO_DURATION:.4f}")
    
    return {
        "num_threads": num_threads,
        "chunk_times_ms": chunk_times,
        "rtf_per_chunk": rtf_per_chunk.tolist(),
        "cpu_stats": cpu_stats,
        "total_elapsed": total_elapsed,
        "overall_rtf": total_elapsed / AUDIO_DURATION,
    }

def main():
    print("=== CPU MEASUREMENT FOR SHERPA-LARGE STREAMING DECODE ===")
    print(f"Audio: 001_open_the_settings_window.wav ({AUDIO_DURATION:.2f}s)")
    print(f"Logical cores: {psutil.cpu_count()}, Physical cores: {psutil.cpu_count(logical=False)}")
    
    results = {}
    for nt in [1, 2, 4]:
        results[nt] = test_threading(nt)
    
    # Summary
    print("\n=== SUMMARY ===")
    print(f"{'Threads':>8} {'Avg Chunk(ms)':>14} {'Max Chunk(ms)':>14} {'Avg RTF':>10} {'Max RTF':>10} {'CPU avg%':>10} {'CPU peak%':>10} {'CPU avg(norm)':>14} {'CPU peak(norm)':>14}")
    for nt in [1, 2, 4]:
        r = results[nt]
        cs = r["cpu_stats"]
        print(f"{nt:>8} {np.mean(r['chunk_times_ms'])*1000:>14.1f} {np.max(r['chunk_times_ms'])*1000:>14.1f} {np.mean(r['rtf_per_chunk']):>10.4f} {np.max(r['rtf_per_chunk']):>10.4f} {cs['avg_pct']:>10.1f} {cs['peak_pct']:>10.1f} {cs['avg_normalized']:>14.1f} {cs['peak_normalized']:>14.1f}")
    
    # Check for spin-waiting
    print("\n=== ONNXRUNTIME SPIN-WAIT CHECK ===")
    print("If per-process CPU >> num_threads * 100%, spin-waiting is likely.")
    for nt in [1, 2, 4]:
        cs = results[nt]["cpu_stats"]
        expected_max = nt * 100
        actual_peak = cs["peak_pct"]
        ratio = actual_peak / expected_max if expected_max > 0 else 0
        print(f"  threads={nt}: expected_max={expected_max}%, actual_peak={actual_peak:.1f}%, ratio={ratio:.1f}x")
        if ratio > 1.5:
            print(f"    -> SPIN-WAITING DETECTED (ratio > 1.5x)")
        else:
            print(f"    -> No significant spin-waiting")

if __name__ == "__main__":
    main()