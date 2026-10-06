#!/usr/bin/env python3
"""
VAD Hangover Baseline evaluation on the same test sets.
Current pipeline uses EnergyVAD with hangover H=300ms.
"""

import time
import numpy as np
import wave
import json
from pathlib import Path

WS2_RECORDINGS = Path("tests/benchmarks/voice/ws2/recordings")
WS3_RECORDINGS = Path("tests/benchmarks/voice/ws3/recordings")

def load_wav(filepath):
    with wave.open(str(filepath), 'rb') as wf:
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        sr = wf.getframerate()
    return audio, sr

def add_silence(audio, duration_ms, sr=16000):
    silence = np.zeros(int(sr * duration_ms / 1000), dtype=np.float32)
    return np.concatenate([audio, silence])

def find_first_long_silence(audio, sr=16000, min_silence_ms=300, threshold=0.01):
    min_samples = int(sr * min_silence_ms / 1000)
    in_silence = False
    silence_start = 0
    
    for i in range(0, len(audio) - 160, 160):
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
    
    if in_silence and (len(audio) - silence_start) >= min_samples:
        return silence_start, len(audio)
    
    return None

def vad_hangover_endpoint(audio, sr=16000, hangover_ms=300, threshold=0.01, frame_ms=10):
    """
    Simulate EnergyVAD with hangover.
    Returns True if speech detected (not in hangover), False if endpoint reached.
    """
    frame_samples = int(sr * frame_ms / 1000)
    hangover_frames = int(hangover_ms / frame_ms)
    
    speech_frames = 0
    in_speech = False
    hangover_counter = 0
    
    for i in range(0, len(audio) - frame_samples, frame_samples):
        frame = audio[i:i+frame_samples]
        energy = np.sqrt(np.mean(frame**2))
        is_speech = energy > threshold
        
        if is_speech:
            in_speech = True
            speech_frames += 1
            hangover_counter = 0
        elif in_speech:
            hangover_counter += 1
            if hangover_counter >= hangover_frames:
                # Endpoint reached
                return True  # Speech ended
    
    return False  # Still in speech or never started

def test_vad_baseline():
    print("=== VAD Hangover Baseline (H=300ms) ===")
    
    # 1. Complete-turn: 41 WS2 + 200ms silence
    print("\n--- Complete-turn set (41 WS2 + 200ms) ---")
    ws2_files = sorted(WS2_RECORDINGS.glob("*.wav"))
    complete_results = []
    for wav_file in ws2_files:
        audio, sr = load_wav(wav_file)
        audio = add_silence(audio, 200, sr)
        
        # With 200ms silence and 300ms hangover, endpoint should NOT be reached yet
        # (need 300ms silence, only have 200ms)
        endpoint_reached = vad_hangover_endpoint(audio, sr, hangover_ms=300)
        
        # Expected: False (not enough silence for hangover)
        correct = not endpoint_reached
        complete_results.append({"correct": correct, "endpoint": endpoint_reached})
        print(f"  {wav_file.name}: endpoint={endpoint_reached}, correct={correct}")
    
    # 2. Hesitation prefix: find silence >=300ms, take prefix + 200ms
    print("\n--- Hesitation prefix (15 WS3) ---")
    ws3_files = sorted(WS3_RECORDINGS.glob("*.wav"))
    hesitation_prefix_results = []
    for wav_file in ws3_files:
        audio, sr = load_wav(wav_file)
        silence_region = find_first_long_silence(audio, sr, min_silence_ms=300)
        
        if silence_region:
            silence_start, _ = silence_region
            prefix = audio[:silence_start]
            prefix = add_silence(prefix, 200, sr)
            
            # With 200ms silence and 300ms hangover, should NOT reach endpoint
            endpoint_reached = vad_hangover_endpoint(prefix, sr, hangover_ms=300)
            correct = not endpoint_reached  # Expected INCOMPLETE
            hesitation_prefix_results.append({"correct": correct, "endpoint": endpoint_reached})
            print(f"  {wav_file.name}: endpoint={endpoint_reached}, correct={correct}")
        else:
            print(f"  {wav_file.name}: NO SILENCE FOUND")
            hesitation_prefix_results.append({"correct": False, "endpoint": None})
    
    # 3. Hesitation full
    print("\n--- Hesitation full (15 WS3) ---")
    hesitation_full_results = []
    for wav_file in ws3_files:
        audio, sr = load_wav(wav_file)
        audio = add_silence(audio, 200, sr)
        
        endpoint_reached = vad_hangover_endpoint(audio, sr, hangover_ms=300)
        correct = endpoint_reached  # Expected COMPLETE
        hesitation_full_results.append({"correct": correct, "endpoint": endpoint_reached})
        print(f"  {wav_file.name}: endpoint={endpoint_reached}, correct={correct}")
    
    # 4. Synthetic: 60% truncation + 200ms
    print("\n--- Synthetic (41 WS2 truncated 60%) ---")
    synthetic_results = []
    for wav_file in ws2_files:
        audio, sr = load_wav(wav_file)
        truncate_idx = int(len(audio) * 0.6)
        window = 1600
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
        
        endpoint_reached = vad_hangover_endpoint(truncated, sr, hangover_ms=300)
        correct = not endpoint_reached  # Expected INCOMPLETE
        synthetic_results.append({"correct": correct, "endpoint": endpoint_reached})
        print(f"  {wav_file.name}: endpoint={endpoint_reached}, correct={correct}")
    
    return {
        "complete_turn": complete_results,
        "hesitation_prefix": hesitation_prefix_results,
        "hesitation_full": hesitation_full_results,
        "synthetic": synthetic_results,
    }

def main():
    results = test_vad_baseline()
    
    print("\n=== VAD BASELINE SUMMARY ===")
    for set_name, set_results in results.items():
        if not set_results:
            continue
        correct = sum(1 for r in set_results if r["correct"])
        total = len(set_results)
        acc = correct / total * 100 if total > 0 else 0
        false_complete = sum(1 for r in set_results if r.get("endpoint") == True and set_name in ["hesitation_prefix", "synthetic"])
        false_incomplete = sum(1 for r in set_results if r.get("endpoint") == False and set_name in ["complete_turn", "hesitation_full"])
        print(f"{set_name}: Acc={correct}/{total}={acc:.1f}%, False-complete={false_complete}, False-incomplete={false_incomplete}")
    
    with open("tests/benchmarks/voice/ws3/results/vad_baseline_results.json", "w") as f:
        json.dump({
            "lm_studio_loaded": False,
            "hangover_ms": 300,
            "results": {k: [{"correct": r["correct"], "endpoint": r["endpoint"]} for r in v] for k, v in results.items()}
        }, f, indent=2)
    print("\nResults saved")

if __name__ == "__main__":
    main()