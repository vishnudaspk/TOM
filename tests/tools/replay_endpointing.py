#!/usr/bin/env python
"""Offline replay of VAD + endpointing + Smart Turn on recorded mic.wav.

Usage: python -m tom.tools.replay_endpointing debug/<session_dir>/mic.wav
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path
from typing import Any

import numpy as np


def load_wav(path: Path) -> tuple[np.ndarray, int]:
    """Load WAV file as float32 mono array, return (samples, sample_rate)."""
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        nchannels = wf.getnchannels()
        frames = wf.getnframes()
        raw = wf.readframes(frames)

    if wf.getsampwidth() != 2:
        raise ValueError(f"Expected 16-bit WAV, got {wf.getsampwidth()*8}-bit")

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if nchannels > 1:
        samples = samples.reshape(-1, nchannels).mean(axis=1)
    return samples, rate


class EnergyVAD:
    """Deterministic energy-based VAD matching the runtime implementation."""

    def __init__(self, threshold: float = 0.003, frame_duration_ms: int = 20, sample_rate: int = 16000):
        self.threshold = threshold
        self.frame_size = int(sample_rate * frame_duration_ms / 1000)
        self.sample_rate = sample_rate

    def is_speech(self, frame: np.ndarray) -> bool:
        if len(frame) == 0:
            return False
        rms = np.sqrt(np.mean(frame**2))
        return rms > self.threshold

    def process_stream(self, samples: np.ndarray) -> list[dict[str, Any]]:
        """Process audio stream and return speech segments with VAD decisions."""
        n_frames = len(samples) // self.frame_size
        segments = []
        in_speech = False
        speech_start = 0
        pre_roll = []
        onset_count = 0
        trailing_silence = 0
        silence_duration_ms = 600  # Config default
        min_speech_frames = 2
        max_utterance_seconds = 30.0

        for i in range(n_frames):
            frame = samples[i*self.frame_size:(i+1)*self.frame_size]
            is_speech = self.is_speech(frame)

            if not in_speech:
                if is_speech:
                    onset_count += 1
                    pre_roll.append(frame)
                    if onset_count >= min_speech_frames:
                        in_speech = True
                        speech_start = i * self.frame_size / self.sample_rate
                        onset_count = 0
                        pre_roll = []
                else:
                    onset_count = 0
                    if len(pre_roll) >= 5:  # 100ms pre-roll
                        pre_roll.pop(0)
                    pre_roll.append(frame)
            else:
                if is_speech:
                    trailing_silence = 0
                else:
                    trailing_silence += 20  # frame_duration_ms

                # Check endpoints
                silence_endpoint = trailing_silence >= silence_duration_ms
                duration_s = (i * self.frame_size - speech_start) / self.sample_rate
                max_duration_endpoint = duration_s >= max_utterance_seconds

                if silence_endpoint or max_duration_endpoint:
                    end_time = i * self.frame_size / self.sample_rate
                    segments.append({
                        "start_s": speech_start,
                        "end_s": end_time,
                        "duration_s": end_time - speech_start,
                        "reason": "silence" if silence_endpoint else "max_duration",
                    })
                    in_speech = False
                    trailing_silence = 0

        return segments


class MockSmartTurn:
    """Mock Smart Turn that returns INCOMPLETE for first half, COMPLETE for second."""

    def __init__(self):
        self.call_count = 0

    def analyze(self, audio: bytes) -> str:
        """Return COMPLETE or INCOMPLETE."""
        self.call_count += 1
        # Simple heuristic: if audio > 1 second, COMPLETE
        duration = len(audio) / 2 / 16000  # 16-bit mono
        if duration > 1.0:
            return "COMPLETE"
        return "INCOMPLETE"


def main():
    parser = argparse.ArgumentParser(description="Replay endpointing on recorded mic.wav")
    parser.add_argument("mic_wav", type=Path, help="Path to mic.wav (16 kHz mono)")
    parser.add_argument("--threshold", type=float, default=0.003, help="VAD energy threshold")
    parser.add_argument("--frame-ms", type=int, default=20, help="Frame duration in ms")
    parser.add_argument("--silence-ms", type=int, default=600, help="Silence hangover in ms")
    args = parser.parse_args()

    mic_path = Path(args.mic_wav)
    if not mic_path.exists():
        print(f"File not found: {mic_path}", file=sys.stderr)
        return 1

    samples, rate = load_wav(mic_path)
    print(f"Loaded {mic_path}: {len(samples)} samples, {rate} Hz, {len(samples)/rate:.2f}s")

    # Process with VAD
    vad = EnergyVAD(threshold=args.threshold, frame_duration_ms=args.frame_ms, sample_rate=rate)
    segments = vad.process_stream(samples)

    print(f"\nVAD Segments ({len(segments)}):")
    for i, seg in enumerate(segments):
        print(f"  {i+1}: {seg['start_s']:.3f}s - {seg['end_s']:.3f}s "
              f"(dur={seg['duration_s']:.3f}s, reason={seg['reason']})")

    # Simulate Smart Turn
    print("\nSmart Turn simulation:")
    for i, seg in enumerate(segments):
        # Convert segment samples to bytes for analysis
        start_idx = int(seg['start_s'] * rate)
        end_idx = int(seg['end_s'] * rate)
        seg_samples = samples[start_idx:end_idx]
        seg_bytes = (seg_samples * 32767).astype(np.int16).tobytes()
        decision = "COMPLETE" if seg['duration_s'] > 1.0 else "INCOMPLETE"
        print(f"  Segment {i+1}: {decision} (dur={seg['duration_s']:.2f}s)")

    return 0


if __name__ == "__main__":
    sys.exit(main())