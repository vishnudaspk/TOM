#!/usr/bin/env python3
"""
F3: First-word / clipped-start diagnostic.
Cheap A/B on faster-whisper small.en (beam_size=1) and Moonshine-tiny int8:
  (a) original audio
  (b) 300 ms silence prepended
  (c) 600 ms silence prepended
Reports overall WER and FIRST WORD WER for each condition.
Also reports per-file onset and pre-speech audio duration.
"""

import time
import json
import wave
from pathlib import Path
import numpy as np
from faster_whisper import WhisperModel
import sherpa_onnx
from normalizer import normalize_text, compute_wer

RECORDINGS_DIR = Path("tests/benchmarks/voice/ws2/recordings")
UTTERANCES_FILE = Path("tests/benchmarks/voice/ws2/utterances.txt")
CORRECTIONS_FILE = Path("tests/benchmarks/voice/ws2/results/corrections.txt")
RESULTS_DIR = Path("tests/benchmarks/voice/ws2/results")

def load_utterances():
    with open(UTTERANCES_FILE, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]

def load_corrections():
    corrections = {}
    if CORRECTIONS_FILE.exists():
        with open(CORRECTIONS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    parts = line.split(' ', 1)
                    if len(parts) == 2:
                        corrections[int(parts[0]) - 1] = parts[1]
    return corrections

UTTERANCES = load_utterances()
CORRECTIONS = load_corrections()
recordings = sorted(RECORDINGS_DIR.glob("*.wav"))

def load_wav(filepath):
    with wave.open(str(filepath), 'rb') as wf:
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        sr = wf.getframerate()
    return audio, sr

def detect_onset(audio, sr=16000, threshold=0.01, frame_ms=10):
    frame_samples = int(sr * (frame_ms / 1000.0))
    for i in range(0, len(audio) - frame_samples, frame_samples):
        frame = audio[i:i + frame_samples]
        rms = np.sqrt(np.mean(frame ** 2))
        if rms > threshold:
            return i / sr
    return 0.0

def get_first_word(text: str) -> str:
    tokens = text.strip().split()
    return tokens[0] if tokens else ""

print(f"Loaded {len(recordings)} recordings.")

# 1. Measure per-file onset
onset_data = {}
for i, wav_path in enumerate(recordings):
    audio, sr = load_wav(wav_path)
    total_dur = len(audio) / sr
    onset_s = detect_onset(audio, sr, threshold=0.01)
    onset_data[wav_path.name] = {
        "idx": i,
        "total_duration_s": round(total_dur, 3),
        "pre_speech_silence_s": round(onset_s, 3),
        "pre_speech_silence_ms": round(onset_s * 1000, 1),
    }

print("\n--- SAMPLE ONSET TIMES (first 5 files) ---")
for k in list(onset_data.keys())[:5]:
    v = onset_data[k]
    print(f"  {k}: pre-speech silence = {v['pre_speech_silence_ms']} ms (total {v['total_duration_s']} s)")

# 2. Setup engines
print("\nLoading models for diagnostic...")
fw_model = WhisperModel("small.en", device="cpu", compute_type="int8", cpu_threads=4)

moonshine_dir = Path("tests/benchmarks/voice/ws2/models_offline/moonshine-tiny-en-int8")
ms_recognizer = sherpa_onnx.OfflineRecognizer.from_moonshine(
    preprocessor=str(moonshine_dir / "preprocess.onnx"),
    encoder=str(moonshine_dir / "encode.int8.onnx"),
    uncached_decoder=str(moonshine_dir / "uncached_decode.int8.onnx"),
    cached_decoder=str(moonshine_dir / "cached_decode.int8.onnx"),
    tokens=str(moonshine_dir / "tokens.txt"),
    num_threads=4,
)

conditions = {
    "original": 0.0,
    "prepend_300ms": 0.3,
    "prepend_600ms": 0.6,
}

diagnostic_results = {
    "onsets": onset_data,
    "faster_whisper": {},
    "moonshine_tiny": {},
}

for cond_name, pad_sec in conditions.items():
    print(f"\n=== Running condition: {cond_name} (pad={pad_sec*1000:.0f}ms) ===")
    pad_samples = int(16000 * pad_sec)
    silence = np.zeros(pad_samples, dtype=np.float32)

    fw_wers = []
    fw_fw_errors = 0
    fw_items = []

    ms_wers = []
    ms_fw_errors = 0
    ms_items = []

    for i, wav_path in enumerate(recordings):
        audio, sr = load_wav(wav_path)
        if pad_samples > 0:
            test_audio = np.concatenate([silence, audio])
        else:
            test_audio = audio

        raw_ref = CORRECTIONS.get(i, UTTERANCES[i])
        norm_ref = normalize_text(raw_ref)
        ref_fw = get_first_word(norm_ref)

        # A. faster-whisper
        segments, _ = fw_model.transcribe(test_audio, beam_size=1, without_timestamps=True, language="en")
        fw_hyp = " ".join(s.text for s in segments)
        norm_fw_hyp = normalize_text(fw_hyp)
        fw_wer = compute_wer(norm_ref, norm_fw_hyp)
        fw_hyp_fw = get_first_word(norm_fw_hyp)
        fw_fw_match = (ref_fw == fw_hyp_fw)
        if not fw_fw_match:
            fw_fw_errors += 1
        fw_wers.append(fw_wer)
        fw_items.append({
            "file": wav_path.name,
            "norm_ref": norm_ref,
            "norm_hyp": norm_fw_hyp,
            "wer": round(fw_wer, 4),
            "ref_first_word": ref_fw,
            "hyp_first_word": fw_hyp_fw,
            "first_word_match": fw_fw_match,
        })

        # B. Moonshine-tiny
        stream = ms_recognizer.create_stream()
        stream.accept_waveform(sr, test_audio)
        ms_recognizer.decode_stream(stream)
        ms_res = stream.result
        ms_hyp = ms_res.text.strip() if ms_res else ""
        norm_ms_hyp = normalize_text(ms_hyp)
        ms_wer = compute_wer(norm_ref, norm_ms_hyp)
        ms_hyp_fw = get_first_word(norm_ms_hyp)
        ms_fw_match = (ref_fw == ms_hyp_fw)
        if not ms_fw_match:
            ms_fw_errors += 1
        ms_wers.append(ms_wer)
        ms_items.append({
            "file": wav_path.name,
            "norm_ref": norm_ref,
            "norm_hyp": norm_ms_hyp,
            "wer": round(ms_wer, 4),
            "ref_first_word": ref_fw,
            "hyp_first_word": ms_hyp_fw,
            "first_word_match": ms_fw_match,
        })

    fw_mean_wer = sum(fw_wers) / len(fw_wers)
    fw_fw_wer = fw_fw_errors / len(recordings)
    diagnostic_results["faster_whisper"][cond_name] = {
        "overall_wer": round(fw_mean_wer, 4),
        "first_word_error_rate": round(fw_fw_wer, 4),
        "first_word_errors_count": fw_fw_errors,
        "items": fw_items,
    }

    ms_mean_wer = sum(ms_wers) / len(ms_wers)
    ms_fw_wer = ms_fw_errors / len(recordings)
    diagnostic_results["moonshine_tiny"][cond_name] = {
        "overall_wer": round(ms_mean_wer, 4),
        "first_word_error_rate": round(ms_fw_wer, 4),
        "first_word_errors_count": ms_fw_errors,
        "items": ms_items,
    }

    print(f"  faster-whisper : Overall WER = {fw_mean_wer*100:.2f}%, First-Word Error Rate = {fw_fw_wer*100:.2f}% ({fw_fw_errors}/{len(recordings)})")
    print(f"  Moonshine-tiny : Overall WER = {ms_mean_wer*100:.2f}%, First-Word Error Rate = {ms_fw_wer*100:.2f}% ({ms_fw_errors}/{len(recordings)})")

out_file = RESULTS_DIR / "f3_clipped_start_diagnostic.json"
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(diagnostic_results, f, indent=2)

print(f"\nResults saved to {out_file}")
