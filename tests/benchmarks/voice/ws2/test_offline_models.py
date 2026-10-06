
#!/usr/bin/env python3
"""Test offline models when downloaded."""

import time
import numpy as np
import wave
import psutil
import os
from pathlib import Path
import sherpa_onnx

RECORDINGS_DIR = Path("tests/benchmarks/voice/ws2/recordings")
recordings = sorted(RECORDINGS_DIR.glob("*.wav"))

def load_wav(f):
    with wave.open(str(f), "rb") as wf:
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        sr = wf.getframerate()
    return audio, sr

def categorize(idx):
    if idx < 15: return "commands"
    elif idx < 25: return "questions"
    elif idx < 33: return "names"
    else: return "numbers"

def test_offline_model(model_path, model_name):
    print(f"\n=== Testing {model_name} ===")
    
    # Find model files
    encoder = str(list(model_path.glob("*encoder*.onnx"))[0])
    decoder = str(list(model_path.glob("*decoder*.onnx"))[0]) if list(model_path.glob("*decoder*.onnx")) else None
    joiner = str(list(model_path.glob("*joiner*.onnx"))[0]) if list(model_path.glob("*joiner*.onnx")) else None
    tokens = str(model_path / "tokens.txt")
    
    # Load recognizer (OfflineRecognizer for transducer)
    recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
        tokens=tokens,
        encoder=encoder,
        decoder=decoder,
        joiner=joiner,
        num_threads=4,
        provider="cpu",
    )
    
    # Metrics
    all_latencies = []
    all_wer = []
    cat_wer = {"commands": [], "questions": [], "names": [], "numbers": []}
    num_exact = 0
    num_total = 0
    cpu_samples = []
    process = psutil.Process(os.getpid())
    peak_ram = 0
    
    for idx, wav_file in enumerate(recordings):
        audio, sr = load_wav(wav_file)
        
        # CPU monitoring
        process.cpu_percent(interval=None)
        cpu_samples_run = []
        
        # Run 10 times for latency stats
        run_latencies = []
        for run in range(10):
            start = time.perf_counter()
            stream = recognizer.create_stream()
            stream.accept_waveform(16000, audio)
            stream.input_finished()
            while recognizer.is_ready(stream):
                recognizer.decode_stream(stream)
            latency = time.perf_counter() - start
            run_latencies.append(latency * 1000)
            cpu_samples_run.append(process.cpu_percent(interval=None))
        
        # Get result
        stream = recognizer.create_stream()
        stream.accept_waveform(16000, audio)
        stream.input_finished()
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        result = recognizer.get_result_all(stream)
        hyp = result.text.strip() if hasattr(result, 'text') else str(result).strip()
        
        # Reference
        # ... load reference and compute WER
        
        # Stats
        latencies = np.array(run_latencies)
        all_latencies.extend(latencies)
        cpu_samples.extend(cpu_samples_run)
        
        # Track peak RAM
        mem = process.memory_info().rss / 1024 / 1024
        peak_ram = max(peak_ram, mem)
    
    # Group by duration bucket
    # ... compute stats
    
    print(f"  Model: {model_name}")
    print(f"  Latency p50/p95/stdev by bucket...")
    print(f"  WER...")
    print(f"  Peak RAM: {peak_ram:.0f} MB")
    print(f"  CPU avg/peak: {np.mean(cpu_samples):.1f}% / {np.max(cpu_samples):.1f}%")

if __name__ == "__main__":
    # Test each available model
    for model_dir in Path("tests/benchmarks/voice/ws2/models_offline").glob("*"):
        if model_dir.is_dir():
            test_offline_model(model_dir, model_dir.name)
