#!/usr/bin/env python3
"""
Recording validation script for WS2 benchmark utterances.

Validates:
- File count matches utterance count
- WAV properties (sample rate, channels, duration, peak amplitude, RMS, clipping, silence)
- Flags problematic recordings
"""

import json
import wave
import numpy as np
from pathlib import Path
from typing import Dict, List, Any

RECORDINGS_DIR = Path(__file__).parent / "recordings"
UTTERANCES_FILE = Path(__file__).parent / "utterances.txt"
OUTPUT_FILE = Path(__file__).parent / "results" / "recording_validation.json"

# Thresholds
MIN_DURATION = 0.5  # seconds
MAX_DURATION = 8.0  # seconds
CLIPPING_THRESHOLD = 0.001  # 0.1% of samples
QUIET_THRESHOLD_DB = -30  # dBFS
LEADING_SILENCE_MAX = 1.5  # seconds
CLIPPING_THRESHOLD_LINEAR = 0.99  # 99% of full scale for int16


def load_utterances() -> List[str]:
    """Load utterances from file."""
    with open(UTTERANCES_FILE, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]


def slugify(text: str, max_len: int = 50) -> str:
    import re
    slug = re.sub(r'[^a-zA-Z0-9\s]', '', text)
    slug = re.sub(r'\s+', '_', slug.strip())
    return slug[:max_len].lower()


def analyze_wav(filepath: Path) -> Dict[str, Any]:
    """Analyze a single WAV file."""
    with wave.open(str(filepath), 'rb') as wf:
        sample_rate = wf.getframerate()
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        n_frames = wf.getnframes()
        frames = wf.readframes(n_frames)
        
        # Convert to float32 normalized
        if sampwidth == 2:
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        else:
            audio = np.frombuffer(frames, dtype=np.float32)
        
        if channels > 1:
            audio = audio.reshape(-1, channels)
            audio = audio.mean(axis=1)  # Convert to mono for analysis
        
        duration = len(audio) / sample_rate
        
        # Peak amplitude
        peak = np.max(np.abs(audio))
        peak_db = 20 * np.log10(peak) if peak > 0 else -np.inf
        
        # RMS
        rms = np.sqrt(np.mean(audio**2))
        rms_db = 20 * np.log10(rms) if rms > 0 else -np.inf
        
        # Clipping percentage (samples at or near full scale)
        clipped = np.sum(np.abs(audio) >= CLIPPING_THRESHOLD_LINEAR)
        clipping_pct = (clipped / len(audio)) * 100
        
        # Leading silence (samples at start below threshold)
        silence_threshold = 0.01  # ~-40 dB
        leading_silence_samples = 0
        for sample in audio:
            if abs(sample) < silence_threshold:
                leading_silence_samples += 1
            else:
                break
        leading_silence = leading_silence_samples / sample_rate
        
        # Trailing silence
        trailing_silence_samples = 0
        for sample in reversed(audio):
            if abs(sample) < silence_threshold:
                trailing_silence_samples += 1
            else:
                break
        trailing_silence = trailing_silence_samples / sample_rate
        
        return {
            "file": filepath.name,
            "sample_rate": sample_rate,
            "channels": channels,
            "sampwidth": sampwidth,
            "n_frames": n_frames,
            "duration_s": round(duration, 3),
            "peak_amplitude": round(float(peak), 6),
            "peak_db": round(float(peak_db), 2) if peak_db > -np.inf else -np.inf,
            "rms": round(float(rms), 6),
            "rms_db": round(float(rms_db), 2) if rms_db > -np.inf else -np.inf,
            "clipping_pct": round(clipping_pct, 4),
            "leading_silence_s": round(leading_silence, 3),
            "trailing_silence_s": round(trailing_silence, 3),
        }


def validate_recordings() -> Dict[str, Any]:
    """Run validation on all recordings."""
    utterances = load_utterances()
    expected_files = [f"{i+1:03d}_{slugify(u)}.wav" for i, u in enumerate(utterances)]
    
    # Get actual files
    actual_files = sorted([f.name for f in RECORDINGS_DIR.glob("*.wav")])
    
    missing = [f for f in expected_files if f not in actual_files]
    extra = [f for f in actual_files if f not in expected_files]
    
    # Analyze each WAV
    results = []
    flagged = []
    
    for expected in expected_files:
        filepath = RECORDINGS_DIR / expected
        if not filepath.exists():
            results.append({"file": expected, "error": "File not found"})
            flagged.append({"file": expected, "reason": "missing"})
            continue
        
        try:
            analysis = analyze_wav(filepath)
            results.append(analysis)
            
            # Check flags
            flags = []
            if analysis["duration_s"] < 0.5:
                flags.append("too_short")
            if analysis["duration_s"] > 8.0:
                flags.append("too_long")
            if analysis["clipping_pct"] > 0.1:
                flags.append("clipped")
            if analysis["peak_db"] < -30:
                flags.append("very_quiet")
            if analysis["leading_silence_s"] > 1.5:
                flags.append("excessive_leading_silence")
            
            if flags:
                flagged.append({
                    "file": expected,
                    "flags": flags,
                    "duration_s": analysis["duration_s"],
                    "clipping_pct": analysis["clipping_pct"],
                    "peak_db": analysis["peak_db"],
                    "leading_silence_s": analysis["leading_silence_s"]
                })
                
        except Exception as e:
            results.append({"file": expected, "error": str(e)})
            flagged.append({"file": expected, "reason": f"analysis_error: {e}"})
    
    # Summary statistics by category
    categories = {
        "commands": list(range(0, 15)),      # 0-14
        "questions": list(range(15, 25)),     # 15-24
        "tom_names": list(range(25, 33)),     # 25-32
        "numbers": list(range(33, 41)),       # 33-40
    }
    
    category_stats = {}
    for cat_name, indices in categories.items():
        durations = []
        for i in indices:
            if i < len(results) and "duration_s" in results[i]:
                durations.append(results[i]["duration_s"])
        if durations:
            category_stats[cat_name] = {
                "count": len(durations),
                "min_s": round(min(durations), 3),
                "median_s": round(np.median(durations), 3),
                "max_s": round(max(durations), 3),
                "mean_s": round(np.mean(durations), 3),
            }
    
    return {
        "expected_count": len(expected_files),
        "actual_count": len(actual_files),
        "missing_files": missing,
        "extra_files": extra,
        "file_analyses": results,
        "flagged_files": flagged,
        "category_stats": category_stats,
    }


def main():
    output = validate_recordings()
    
    # Ensure output directory exists
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, indent=2)
    
    # Print summary
    print(f"Expected: {output['expected_count']}")
    print(f"Actual: {output['actual_count']}")
    print(f"Missing: {len(output['missing_files'])}")
    print(f"Extra: {len(output['extra_files'])}")
    print(f"Flagged: {len(output['flagged_files'])}")
    print(f"\nCategory stats:")
    for cat, stats in output['category_stats'].items():
        print(f"  {cat}: {stats}")
    print(f"\nResults saved to: {Path(__file__).parent / 'results' / 'recording_validation.json'}")


if __name__ == "__main__":
    main()