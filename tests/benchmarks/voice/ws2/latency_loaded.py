#!/usr/bin/env python3
"""
Reduced latency runs - LOADED condition (LM Studio models loaded).
"""

import time
import json
import numpy as np
import wave
import psutil
import os
from pathlib import Path
import sherpa_onnx
from faster_whisper import WhisperModel
import random

with open("tests/benchmarks/voice/ws2/results/stratified_indices.json") as f:
    stratified_indices = json.load(f)

RECORDINGS_DIR = Path("tests/benchmarks/voice/ws2/recordings")
recordings = sorted(RECORDINGS_DIR.glob("*.wav"))
selected_files = [recordings[i] for i in stratified_indices]

def load_wav(filepath):
    with wave.open(str(filepath), "rb") as wf:
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        sr = wf.getframerate()
    return audio, sr

audio_cache = {}
for wav_file in selected_files:
    audio_cache[wav_file.name] = load_wav(wav_file)

def detect_onset(audio, sr, threshold=0.01):
    for i in range(0, len(audio) - 160, 160):
        if np.sqrt(np.mean(audio[i:i+160]**2)) > threshold:
            return i / sr
    return 0.0

class CPUMonitor:
    def __init__(self):
        self.process = psutil.Process(os.getpid())
        self.samples = []
        self.monitoring = False
    
    def start(self):
        self.samples = []
        self.monitoring = True
    
    def stop(self):
        self.monitoring = False
    
    def sample(self):
        if self.monitoring:
            self.samples.append(self.process.cpu_percent(interval=None))
    
    def get_stats(self):
        if not self.samples:
            return {"avg": 0, "peak": 0}
        return {"avg": np.mean(self.samples), "peak": np.max(self.samples)}

def test_sherpa_latency():
    print("\n=== Testing sherpa-large latency (10 runs each) - LOADED ===")
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
    TAIL_PADDING = np.zeros(int(0.3 * 16000), dtype=np.float32)
    cpu_monitor = CPUMonitor()

    all_results = []

    for wav_file in selected_files:
        audio, sr = audio_cache[wav_file.name]
        onset_time = detect_onset(audio, sr)
        
        file_results = []
        for run in range(10):
            stream = recognizer.create_stream()
            
            start = time.perf_counter()
            speech_onset = start + onset_time
            
            first_partial_time = None
            chunk_times = []
            
            cpu_monitor.start()
            
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
                elapsed = time.perf_counter() - chunk_start
                chunk_times.append(elapsed)
                cpu_monitor.sample()
                sleep_time = 0.1 - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)
            
            cpu_monitor.stop()
            
            speech_end = time.perf_counter()
            stream.accept_waveform(16000, TAIL_PADDING)
            stream.input_finished()
            endpoint = time.perf_counter()
            
            while recognizer.is_ready(stream):
                recognizer.decode_stream(stream)
            ffe = time.perf_counter() - endpoint
            
            cpu_stats = cpu_monitor.get_stats()
            
            fp_onset = first_partial_time * 1000 if first_partial_time else None
            
            file_results.append({
                "run": run,
                "final_from_endpoint_ms": round(ffe * 1000, 1),
                "first_partial_from_onset_ms": round(fp_onset, 1) if fp_onset else None,
                "avg_chunk_decode_ms": round(np.mean(chunk_times) * 1000, 2) if chunk_times else 0,
                "rtf": round(np.mean(chunk_times) / 0.1, 4) if chunk_times else 0,
                "cpu_avg_pct": round(cpu_stats["avg"], 1),
                "cpu_peak_pct": round(cpu_stats["peak"], 1),
            })
            
            print(f"  {wav_file.name} run {run+1}: ffe={ffe*1000:.1f}ms, fp={fp_onset:.1f}ms, cpu_avg={cpu_stats['avg']:.1f}%, cpu_peak={cpu_stats['peak']:.1f}%")
        
        all_results.append({
            "file": wav_file.name,
            "runs": file_results,
        })
    
    return all_results

def test_fw_latency():
    print("\n=== Testing faster-whisper latency (5 runs each) - LOADED ===")
    model = WhisperModel("small.en", device="cpu", compute_type="int8", cpu_threads=4)
    
    all_results = []
    cpu_monitor = CPUMonitor()
    
    for wav_file in selected_files:
        file_results = []
        for run in range(5):
            cpu_monitor.start()
            start = time.perf_counter()
            
            segments, info = model.transcribe(str(wav_file), beam_size=1, without_timestamps=True, language="en")
            hyp = " ".join([s.text for s in segments])
            
            elapsed = time.perf_counter() - start
            cpu_monitor.stop()
            cpu_stats = cpu_monitor.get_stats()
            
            file_results.append({
                "run": run,
                "total_latency_ms": round(elapsed * 1000, 1),
                "cpu_avg_pct": round(cpu_stats["avg"], 1),
                "cpu_peak_pct": round(cpu_stats["peak"], 1),
            })
            
            print(f"  {wav_file.name} run {run+1}: latency={elapsed*1000:.1f}ms, cpu_avg={cpu_stats['avg']:.1f}%, cpu_peak={cpu_stats['peak']:.1f}%")
        
        all_results.append({
            "file": wav_file.name,
            "runs": file_results,
        })
    
    return all_results

def main():
    random.seed(42)
    random.shuffle(selected_files)
    
    print("=== Warmup runs ===")
    MODEL_DIR = Path("tests/benchmarks/voice/ws2/models/sherpa-onnx-streaming-zipformer-en-2023-06-26")
    encoder = str(list(MODEL_DIR.glob("*encoder*chunk*.int8.onnx"))[0])
    decoder = str(list(MODEL_DIR.glob("*decoder*chunk*.int8.onnx"))[0])
    joiner = str(list(MODEL_DIR.glob("*joiner*chunk*.int8.onnx"))[0])
    tokens = str(MODEL_DIR / "tokens.txt")
    recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=tokens, encoder=encoder, decoder=decoder, joiner=joiner,
        num_threads=4, decoding_method="greedy_search",
        enable_endpoint_detection=True, provider="cpu",
    )
    for wav_file in selected_files[:3]:
        audio, sr = audio_cache[wav_file.name]
        stream = recognizer.create_stream()
        stream.accept_waveform(16000, audio)
        stream.input_finished()
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
    print("Warmup done")
    
    sherpa_results = test_sherpa_latency()
    fw_results = test_fw_latency()
    
    output = {
        "condition": "loaded",
        "sherpa-large": sherpa_results,
        "faster-whisper-small.en": fw_results,
        "environment": {
            "lm_studio_loaded": True,
            "lm_studio_models": ["Qwen3-1.7B", "Qwen3-8B"],
            "cpu_threads": 4,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    }
    with open("tests/benchmarks/voice/ws2/results/latency_loaded.json", "w") as f:
        json.dump(output, f, indent=2)
    print("\nLOADED results saved")

if __name__ == "__main__":
    main()