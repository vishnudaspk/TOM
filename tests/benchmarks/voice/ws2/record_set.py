#!/usr/bin/env python3
"""
Recording script for WS2 benchmark utterance set.

Records 16 kHz mono WAV files, one per utterance line in utterances.txt.
Shows the next sentence, waits for key press to start, records until key press to stop.
Saves as <index>_<slug>.wav in the same directory.

Requirements:
- Python 3.11+
- sounddevice (pip install sounddevice)
- numpy

Usage:
    python record_set.py
"""

import sys
import os
import wave
import threading
import time
from pathlib import Path

try:
    import sounddevice as sd
    import numpy as np
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install with: pip install sounddevice numpy")
    sys.exit(1)

# Configuration
SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = 'int16'
UTTERANCES_FILE = Path(__file__).parent / "utterances.txt"
OUTPUT_DIR = Path(__file__).parent / "recordings"

# Global state for recording control
recording = False
recorded_frames = []
stop_event = threading.Event()


def slugify(text: str, max_len: int = 50) -> str:
    """Convert text to a filesystem-safe slug."""
    import re
    # Keep alphanumeric and spaces, replace others with underscore
    slug = re.sub(r'[^a-zA-Z0-9\s]', '', text)
    slug = re.sub(r'\s+', '_', slug.strip())
    return slug[:max_len].lower()


def audio_callback(indata, frames, time_info, status):
    """Callback for sounddevice InputStream."""
    if recording:
        recorded_frames.append(indata.copy())


def wait_for_key(prompt: str):
    """Wait for user to press Enter."""
    print(prompt, end='', flush=True)
    input()


def record_utterance(text: str, index: int) -> Path:
    """Record a single utterance."""
    global recording, recorded_frames
    
    slug = slugify(text)
    output_path = OUTPUT_DIR / f"{index:03d}_{slug}.wav"
    
    print(f"\n{'='*60}")
    print(f"Utterance {index}: {text}")
    print(f"Output: {output_path.name}")
    print(f"{'='*60}")
    
    # Wait for start
    wait_for_key("Press ENTER to START recording... ")
    
    recorded_frames = []
    recording = True
    stop_event.clear()
    
    # Start recording stream
    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        callback=audio_callback,
        blocksize=1600,  # 100ms at 16kHz
    )
    
    with stream:
        print("Recording... Press ENTER to STOP.")
        wait_for_key("")
        recording = False
    
    # Save to WAV
    if recorded_frames:
        audio_data = np.concatenate(recorded_frames, axis=0)
        with wave.open(str(output_path), 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # 16-bit = 2 bytes
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_data.tobytes())
        duration = len(audio_data) / SAMPLE_RATE
        print(f"Saved: {output_path.name} ({duration:.2f}s, {len(audio_data)} samples)")
    else:
        print("WARNING: No audio recorded!")
    
    return output_path


def main():
    # Read utterances
    if not UTTERANCES_FILE.exists():
        print(f"Error: {UTTERANCES_FILE} not found")
        sys.exit(1)
    
    with open(UTTERANCES_FILE, 'r', encoding='utf-8') as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith('#')]
    
    if not lines:
        print("No utterances found in file")
        sys.exit(1)
    
    print(f"Found {len(lines)} utterances to record")
    
    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # Check audio devices
    try:
        devices = sd.query_devices()
        print(f"\nAvailable input devices:")
        for i, dev in enumerate(devices):
            if dev['max_input_channels'] > 0:
                print(f"  [{i}] {dev['name']} ({dev['max_input_channels']} ch)")
    except Exception as e:
        print(f"Warning: Could not query audio devices: {e}")
    
    # Record each utterance
    for i, text in enumerate(lines, 1):
        try:
            record_utterance(text, i)
        except KeyboardInterrupt:
            print("\nInterrupted by user")
            break
        except Exception as e:
            print(f"Error recording utterance {i}: {e}")
            continue
    
    print(f"\nDone! Recordings saved to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()