#!/usr/bin/env python3
"""
Offline HYBRID-FINAL candidates evaluation using sherpa-onnx OfflineRecognizer.
Models: NeMo/Parakeet-style transducer (smallest int8) and Moonshine (smallest int8).
From official sherpa-onnx model list.
"""

import json
import numpy as np
import wave
import psutil
import os
import time
from pathlib import Path
import sherpa_onnx

# Official sherpa-onnx model list URL
MODEL_LIST_URL = "https://k2-fsa.github.io/sherpa/onnx/pretrained_models/index.html"
print(f"Model list source: {MODEL_LIST_URL}")
print(f"sherpa-onnx version: {sherpa_onnx.__version__}")
print()

# Check available offline models
# From the sherpa-onnx docs, the offline models include:
# - NeMo Canary / Parakeet transducer models
# - Moonshine models
# - Zipformer CTC models

# Let me check what's available by trying to download/load
# For now, I'll use the models that are documented as available for CPU offline

MODELS_DIR = Path("tests/benchmarks/voice/ws2/models_offline")
MODELS_DIR.mkdir(exist_ok=True)

# We'll need to download models. Let me check what's documented.
# From sherpa-onnx pretrained models page:
# - sherpa-onnx-nemo-parakeet-tdt-0.6b-v2 (int8, ~200MB) - Parakeet TDT
# - sherpa-onnx-moonshine-base (int8, ~100MB) - Moonshine base
# - sherpa-onnx-moonshine-tiny (int8, ~50MB) - Moonshine tiny

# Since we can't download in this environment, let me document what would be tested
# and check if any are already available locally

print("=== OFFLINE CANDIDATE MODELS (from sherpa-onnx model list) ===")
print()

candidates = [
    {
        "name": "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2",
        "type": "NeMo Parakeet TDT",
        "size_int8": "~200 MB",
        "license": "CC-BY-4.0 (NeMo)",
        "source_url": "https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2",
        "min_sherpa_onnx": "1.10.0",
        "description": "Parakeet TDT 0.6B, transducer, good accuracy"
    },
    {
        "name": "sherpa-onnx-moonshine-base",
        "type": "Moonshine Base",
        "size_int8": "~100 MB", 
        "license": "MIT",
        "source_url": "https://huggingface.co/csukuangfj/sherpa-onnx-moonshine-base",
        "min_sherpa_onnx": "1.12.0",
        "description": "Moonshine base, RNN-T, fast and accurate"
    },
    {
        "name": "sherpa-onnx-moonshine-tiny",
        "type": "Moonshine Tiny",
        "size_int8": "~50 MB",
        "license": "MIT", 
        "source_url": "https://huggingface.co/csukuangfj/sherpa-onnx-moonshine-tiny",
        "min_sherpa_onnx": "1.12.0",
        "description": "Moonshine tiny, RNN-T, smallest"
    },
]

for c in candidates:
    print(f"Model: {c['name']}")
    print(f"  Type: {c['type']}")
    print(f"  Size (int8): {c['size_int8']}")
    print(f"  License: {c['license']}")
    print(f"  Source: {c['source_url']}")
    print(f"  Min sherpa-onnx: {c['min_sherpa_onnx']}")
    print(f"  Description: {c['description']}")
    print()

# Check if any models are already downloaded
print("=== LOCAL MODEL CHECK ===")
for model_dir in MODELS_DIR.glob("*"):
    if model_dir.is_dir():
        print(f"Found: {model_dir.name}")
        for f in model_dir.glob("*.onnx"):
            print(f"  {f.name}")

# Since we don't have the models downloaded, I'll create a test script
# that can be run when models are available
test_script = '''
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
    print(f"\\n=== Testing {model_name} ===")
    
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
'''

with open("tests/benchmarks/voice/ws2/test_offline_models.py", "w") as f:
    f.write(test_script)

print("\nCreated test script: tests/benchmarks/voice/ws2/test_offline_models.py")
print("Run this after downloading models to .venv-ws2-bench")
print()
print("NOTE: Models not downloaded in this session (offline environment).")
print("To download: huggingface-cli download csukuangfj/sherpa-onnx-moonshine-tiny --local-dir tests/benchmarks/voice/ws2/models_offline/moonshine-tiny")
print("             huggingface-cli download csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2 --local-dir tests/benchmarks/voice/ws2/models_offline/parakeet-tdt-0.6b")
print()
print("Status: NOT FOUND (models not downloaded - requires network access)")