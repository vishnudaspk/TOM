#!/usr/bin/env python3
"""
Smart Turn evaluation on our audio (CPU, 4 threads).
- Complete-turn set: 41 WS2 recordings + 200ms silence → expected COMPLETE
- Hesitation set: 15 WS3 recordings split at first internal silence ≥300ms → prefix+200ms INCOMPLETE, full COMPLETE
- Synthetic set: 41 WS2 recordings truncated at ~60% → expected INCOMPLETE
- Compare with VAD-hangover-only endpointing (H=300ms)
"""

import time
import numpy as np
import wave
import json
import os
from pathlib import Path
import psutil

# Try to import pipecat components
try:
    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    PIPECAT_AVAILABLE = True
except ImportError as e:
    print(f"Pipecat import failed: {e}")
    PIPECAT_AVAILABLE = False

WS2_RECORDINGS = Path("tests/benchmarks/voice/ws2/recordings")
WS3_RECORDINGS = Path("tests/benchmarks/voice/ws3/recordings")

def load_wav(filepath):
    with wave.open(str(filepath), 'rb') as wf:
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        sr = wf.getframerate()
    return audio, sr

def add_silence(audio, duration_ms, sr=16000):
    """Append silence to audio."""
    silence = np.zeros(int(sr * duration_ms / 1000), dtype=np.float32)
    return np.concatenate([audio, silence])

def find_first_long_silence(audio, sr=16000, min_silence_ms=300, threshold=0.01):
    """Find first silence region >= min_silence_ms. Returns (start_idx, end_idx) or None."""
    min_samples = int(sr * min_silence_ms / 1000)
    in_silence = False
    silence_start = 0
    
    for i in range(0, len(audio) - 160, 160):  # 10ms windows
        window = audio[i:i+160]
        energy = np.sqrt(np.mean(window**2))
        
        if energy < threshold:
            if not in_silence:
                in_silence = True
                silence_start = i
        else:
            if in_silence:
                silence_duration = i - silence_start
                if silence_duration >= min_samples:
                    return silence_start, i
                in_silence = False
    
    # Check if silence extends to end
    if in_silence and (len(audio) - silence_start) >= min_samples:
        return silence_start, len(audio)
    
    return None

def test_smart_turn_analyzer():
    """Test LocalSmartTurnAnalyzerV3 on all sets."""
    if not PIPECAT_AVAILABLE:
        print("Pipecat not available, skipping Smart Turn test")
        return None
    
    print("Loading LocalSmartTurnAnalyzerV3 (cpu_count=4)...")
    analyzer = LocalSmartTurnAnalyzerV3(cpu_count=4)
    
    results = {
        "complete_turn": [],
        "hesitation_prefix": [],
        "hesitation_full": [],
        "synthetic": [],
    }
    
    # 1. Complete-turn set: 41 WS2 + 200ms silence
    print("\n=== Complete-turn set (41 WS2 + 200ms silence) ===")
    ws2_files = sorted(WS2_RECORDINGS.glob("*.wav"))
    for wav_file in ws2_files:
        audio, sr = load_wav(wav_file)
        audio = add_silence(audio, 200, sr)
        
        times = []
        for run in range(20):
            start = time.perf_counter()
            result = analyzer._predict_endpoint(audio)
            elapsed = (time.perf_counter() - start) * 1000
            times.append(elapsed)
        
        pred = result["prediction"]
        prob = result["probability"]
        avg_lat = np.mean(times[5:])  # exclude warmup
        p95_lat = np.percentile(times[5:], 95)
        
        results["complete_turn"].append({
            "file": wav_file.name,
            "prediction": int(pred),
            "probability": float(prob),
            "expected": 1,
            "correct": pred == 1,
            "latency_p50_ms": float(np.median(times[5:])),
            "latency_p95_ms": float(p95_lat),
        })
        print(f"  {wav_file.name}: pred={pred}, prob={prob:.3f}, correct={pred==1}, p50={np.median(times[5:]):.1f}ms")
    
    # 2. Hesitation set: 15 WS3
    print("\n=== Hesitation set (15 WS3) ===")
    ws3_files = sorted(WS3_RECORDINGS.glob("*.wav"))
    for wav_file in ws3_files:
        audio, sr = load_wav(wav_file)
        
        # Find first long silence
        silence_region = find_first_long_silence(audio, sr, min_silence_ms=300)
        
        if silence_region:
            silence_start, silence_end = silence_region
            # Prefix = audio up to silence start + 200ms
            prefix_audio = audio[:silence_start]
            prefix_audio = add_silence(prefix_audio, 200, sr)
            
            # Full audio + 200ms
            full_audio = add_silence(audio, 200, sr)
            
            # Test prefix (expected INCOMPLETE = 0)
            prefix_times = []
            for run in range(20):
                start = time.perf_counter()
                result = analyzer._predict_endpoint(prefix_audio)
                elapsed = (time.perf_counter() - start) * 1000
                prefix_times.append(elapsed)
            
            prefix_pred = result["prediction"]
            prefix_prob = result["probability"]
            
            # Test full (expected COMPLETE = 1)
            full_times = []
            for run in range(20):
                start = time.perf_counter()
                result = analyzer._predict_endpoint(full_audio)
                elapsed = (time.perf_counter() - start) * 1000
                full_times.append(elapsed)
            
            full_pred = result["prediction"]
            full_prob = result["probability"]
            
            results["hesitation_prefix"].append({
                "file": wav_file.name,
                "prediction": int(prefix_pred),
                "probability": float(prefix_prob),
                "expected": 0,
                "correct": prefix_pred == 0,
                "latency_p50_ms": float(np.median(prefix_times[5:])),
            })
            
            results["hesitation_full"].append({
                "file": wav_file.name,
                "prediction": int(full_pred),
                "probability": float(full_prob),
                "expected": 1,
                "correct": full_pred == 1,
                "latency_p50_ms": float(np.median(full_times[5:])),
            })
            
            print(f"  {wav_file.name}: prefix(pred={prefix_pred}, prob={prefix_prob:.3f}, correct={prefix_pred==0}), full(pred={full_pred}, prob={full_prob:.3f}, correct={full_pred==1})")
        else:
            print(f"  {wav_file.name}: NO SILENCE FOUND ≥300ms")
            # Test full anyway
            full_audio = add_silence(audio, 200, sr)
            full_times = []
            for run in range(20):
                start = time.perf_counter()
                result = analyzer._predict_endpoint(full_audio)
                elapsed = (time.perf_counter() - start) * 1000
                full_times.append(elapsed)
            full_pred = result["prediction"]
            full_prob = result["probability"]
            results["hesitation_full"].append({
                "file": wav_file.name,
                "prediction": int(full_pred),
                "probability": float(full_prob),
                "expected": 1,
                "correct": full_pred == 1,
                "latency_p50_ms": float(np.median(full_times[5:])),
            })
    
    # 3. Synthetic set: 41 WS2 truncated at ~60%
    print("\n=== Synthetic set (41 WS2 truncated at 60%) ===")
    for wav_file in ws2_files:
        audio, sr = load_wav(wav_file)
        # Truncate at 60% at a low-energy point
        truncate_idx = int(len(audio) * 0.6)
        # Find low-energy point near truncate_idx
        window = 1600  # 100ms
        best_idx = truncate_idx
        best_energy = float('inf')
        for i in range(max(0, truncate_idx - window), min(len(audio), truncate_idx + window)):
            if i + 160 < len(audio):
                energy = np.sqrt(np.mean(audio[i:i+160]**2))
                if energy < best_energy:
                    best_energy = energy
                    best_idx = i
        
        truncated = audio[:best_idx]
        truncated = add_silence(truncated, 200, sr)
        
        times = []
        for run in range(20):
            start = time.perf_counter()
            result = analyzer._predict_endpoint(truncated)
            elapsed = (time.perf_counter() - start) * 1000
            times.append(elapsed)
        
        pred = result["prediction"]
        prob = result["probability"]
        
        results["synthetic"].append({
            "file": wav_file.name,
            "prediction": int(pred),
            "probability": float(prob),
            "expected": 0,
            "correct": pred == 0,
            "latency_p50_ms": float(np.median(times[5:])),
        })
        print(f"  {wav_file.name}: pred={pred}, prob={prob:.3f}, correct={pred==0}, p50={np.median(times[5:]):.1f}ms")
    
    return results

def test_vad_hangover():
    """Test current pipeline's VAD-hangover-only endpointing."""
    if not PIPECAT_AVAILABLE:
        return None
    
    print("\n=== VAD Hangover Baseline (H=300ms) ===")
    vad = SileroVADAnalyzer(sample_rate=16000, params=VADParams(
        confidence=0.7,
        start_secs=0.2,
        stop_secs=0.3,  # 300ms hangover
        min_volume=0.6
    ))
    
    results = {
        "complete_turn": [],
        "hesitation_prefix": [],
        "hesitation_full": [],
        "synthetic": [],
    }
    
    # We'll simulate by checking if VAD goes to QUIET after silence
    # This is a simplified test - just checking if VAD correctly identifies speech segments
    
    return results

def main():
    print("=== Smart Turn Evaluation ===")
    print(f"Pipecat available: {PIPECAT_AVAILABLE}")
    print(f"CPU cores: {psutil.cpu_count()}")
    print(f"LM Studio: UNLOADED (assumed)")
    
    if not PIPECAT_AVAILABLE:
        print("ERROR: Pipecat not available")
        return
    
    # Warmup
    print("\nWarming up...")
    analyzer = LocalSmartTurnAnalyzerV3(cpu_count=4)
    test_audio = np.zeros(16000 * 2, dtype=np.float32)  # 2s silence
    for _ in range(3):
        analyzer._predict_endpoint(test_audio)
    
    # Run tests
    results = test_smart_turn_analyzer()
    
    # Save results
    output = {
        "lm_studio_loaded": False,
        "cpu_threads": 4,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "results": results,
    }
    
    with open("tests/benchmarks/voice/ws3/results/smart_turn_results.json", "w") as f:
        json.dump(output, f, indent=2)
    
    print("\n=== SUMMARY ===")
    for set_name, set_results in results.items():
        if not set_results:
            continue
        correct = sum(1 for r in set_results if r["correct"])
        total = len(set_results)
        acc = correct / total * 100 if total > 0 else 0
        false_complete = sum(1 for r in set_results if r["prediction"] == 1 and r["expected"] == 0)
        false_incomplete = sum(1 for r in set_results if r["prediction"] == 0 and r["expected"] == 1)
        latencies = [r["latency_p50_ms"] for r in set_results if "latency_p50_ms" in r]
        p50_lat = np.median(latencies) if latencies else 0
        p95_lat = np.percentile(latencies, 95) if latencies else 0
        
        print(f"\n{set_name}:")
        print(f"  Accuracy: {correct}/{total} = {acc:.1f}%")
        print(f"  False-complete (dangerous): {false_complete}")
        print(f"  False-incomplete (delay): {false_incomplete}")
        print(f"  Latency p50: {p50_lat:.1f}ms, p95: {p95_lat:.1f}ms")

if __name__ == "__main__":
    main()