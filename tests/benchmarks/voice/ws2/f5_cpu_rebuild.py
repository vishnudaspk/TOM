#!/usr/bin/env python3
"""
F5: CPU measurement rebuild.
Per-process CPU via psutil, normalized to total logical cores (max 100%).
Sampled every 100 ms across the entire decode run, with process.num_threads() logged.
Reports:
  - Percent of one core (e.g. 100% = 1 core fully pegged, max = logical_cores * 100%)
  - Percent of machine (normalized to logical_cores, max = 100%)
Engines evaluated at threads 1, 2, 4:
  1. Moonshine-base int8 (best candidate from F4)
  2. Moonshine-tiny int8
  3. faster-whisper small.en (beam_size=1)
"""

import time
import json
import wave
import psutil
import os
import threading
from pathlib import Path
import numpy as np
import sherpa_onnx
from faster_whisper import WhisperModel

RECORDINGS_DIR = Path("tests/benchmarks/voice/ws2/recordings")
RESULTS_DIR = Path("tests/benchmarks/voice/ws2/results")

# Use a 2.7s standard recording: 001_open_the_settings_window.wav
WAV_PATH = RECORDINGS_DIR / "001_open_the_settings_window.wav"
with wave.open(str(WAV_PATH), "rb") as wf:
    frames = wf.readframes(wf.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    sr = wf.getframerate()
duration_s = len(audio) / sr

LOGICAL_CORES = psutil.cpu_count(logical=True)
PHYSICAL_CORES = psutil.cpu_count(logical=False)
print(f"System: {LOGICAL_CORES} logical cores, {PHYSICAL_CORES} physical cores")
print(f"Test audio: {WAV_PATH.name} ({duration_s:.2f}s)")

class ContinuousCPUSampler:
    """Samples CPU every 100ms in a background thread during workload execution."""
    def __init__(self, pid=None):
        self.process = psutil.Process(pid or os.getpid())
        self.samples = []
        self.thread_counts = []
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        self.samples = []
        self.thread_counts = []
        self._stop_event.clear()
        # Initialize psutil measurement baseline
        self.process.cpu_percent(interval=None)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop_event.is_set():
            time.sleep(0.1)  # 100 ms sample interval
            cpu_val = self.process.cpu_percent(interval=None)
            th_val = self.process.num_threads()
            self.samples.append(cpu_val)
            self.thread_counts.append(th_val)

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join()

    def get_stats(self):
        if not self.samples:
            return {
                "avg_one_core_pct": 0.0,
                "peak_one_core_pct": 0.0,
                "avg_machine_pct": 0.0,
                "peak_machine_pct": 0.0,
                "avg_os_threads": 0.0,
                "peak_os_threads": 0,
            }
        arr = np.array(self.samples)
        th_arr = np.array(self.thread_counts)
        avg_one_core = float(np.mean(arr))
        peak_one_core = float(np.max(arr))
        return {
            "avg_one_core_pct": round(avg_one_core, 2),
            "peak_one_core_pct": round(peak_one_core, 2),
            "avg_machine_pct": round(avg_one_core / LOGICAL_CORES, 2),
            "peak_machine_pct": round(peak_one_core / LOGICAL_CORES, 2),
            "avg_os_threads": round(float(np.mean(th_arr)), 1),
            "peak_os_threads": int(np.max(th_arr)),
            "n_samples": len(self.samples),
        }

def test_engine_cpu(engine_name: str, num_threads: int):
    sampler = ContinuousCPUSampler()
    
    # Run workload 5 times in a loop to get stable continuous sampling over ~1-3 seconds
    if engine_name == "moonshine-base":
        base_dir = "tests/benchmarks/voice/ws2/models_offline/moonshine-base-en-int8"
        recognizer = sherpa_onnx.OfflineRecognizer.from_moonshine(
            preprocessor=f"{base_dir}/preprocess.onnx",
            encoder=f"{base_dir}/encode.int8.onnx",
            uncached_decoder=f"{base_dir}/uncached_decode.int8.onnx",
            cached_decoder=f"{base_dir}/cached_decode.int8.onnx",
            tokens=f"{base_dir}/tokens.txt",
            num_threads=num_threads,
        )
        # Warmup
        s = recognizer.create_stream()
        s.accept_waveform(sr, audio)
        recognizer.decode_stream(s)

        sampler.start()
        t0 = time.perf_counter()
        for _ in range(15):
            s = recognizer.create_stream()
            s.accept_waveform(sr, audio)
            recognizer.decode_stream(s)
        wall_time = time.perf_counter() - t0
        sampler.stop()

    elif engine_name == "moonshine-tiny":
        tiny_dir = "tests/benchmarks/voice/ws2/models_offline/moonshine-tiny-en-int8"
        recognizer = sherpa_onnx.OfflineRecognizer.from_moonshine(
            preprocessor=f"{tiny_dir}/preprocess.onnx",
            encoder=f"{tiny_dir}/encode.int8.onnx",
            uncached_decoder=f"{tiny_dir}/uncached_decode.int8.onnx",
            cached_decoder=f"{tiny_dir}/cached_decode.int8.onnx",
            tokens=f"{tiny_dir}/tokens.txt",
            num_threads=num_threads,
        )
        # Warmup
        s = recognizer.create_stream()
        s.accept_waveform(sr, audio)
        recognizer.decode_stream(s)

        sampler.start()
        t0 = time.perf_counter()
        for _ in range(30):
            s = recognizer.create_stream()
            s.accept_waveform(sr, audio)
            recognizer.decode_stream(s)
        wall_time = time.perf_counter() - t0
        sampler.stop()

    elif engine_name == "faster-whisper":
        model = WhisperModel("small.en", device="cpu", compute_type="int8", cpu_threads=num_threads)
        # Warmup
        _ = list(model.transcribe(audio, beam_size=1, without_timestamps=True, language="en")[0])

        sampler.start()
        t0 = time.perf_counter()
        for _ in range(3):
            _ = list(model.transcribe(audio, beam_size=1, without_timestamps=True, language="en")[0])
        wall_time = time.perf_counter() - t0
        sampler.stop()

    stats = sampler.get_stats()
    stats["wall_time_s"] = round(wall_time, 2)
    stats["configured_threads"] = num_threads
    stats["engine"] = engine_name
    return stats

def main():
    engines = ["moonshine-base", "moonshine-tiny", "faster-whisper"]
    threads = [1, 2, 4]
    results = {}

    print("\n=== STARTING CPU MEASUREMENT REBUILD ===")
    for eng in engines:
        results[eng] = {}
        for t in threads:
            print(f"Testing {eng} with num_threads={t}...")
            stats = test_engine_cpu(eng, t)
            results[eng][t] = stats
            print(f"  -> Wall time: {stats['wall_time_s']}s | OS Threads: {stats['avg_os_threads']} | CPU (1 core): avg={stats['avg_one_core_pct']}%, peak={stats['peak_one_core_pct']}% | CPU (machine): avg={stats['avg_machine_pct']}%, peak={stats['peak_machine_pct']}%")

    out_file = RESULTS_DIR / "f5_cpu_rebuild.json"
    payload = {
        "date": "2026-10-07",
        "logical_cores": LOGICAL_CORES,
        "physical_cores": PHYSICAL_CORES,
        "sample_interval_ms": 100,
        "results": results,
    }
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"\nSaved CPU rebuild results to {out_file}")

if __name__ == "__main__":
    main()
