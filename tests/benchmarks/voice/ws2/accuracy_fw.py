#!/usr/bin/env python3
"""Accuracy test for faster-whisper only."""

import json
import numpy as np
import wave
import re
from pathlib import Path
from faster_whisper import WhisperModel

RECORDINGS_DIR = Path("tests/benchmarks/voice/ws2/recordings")
UTTERANCES_FILE = Path("tests/benchmarks/voice/ws2/utterances.txt")
CORRECTIONS_FILE = Path("tests/benchmarks/voice/ws2/results/corrections.txt")

def load_utterances():
    with open(UTTERANCES_FILE, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]

def load_corrections():
    corrections = {}
    with open(CORRECTIONS_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                parts = line.split(' ', 1)
                if len(parts) == 2:
                    corrections[int(parts[0]) - 1] = parts[1].lower()
    return corrections

UTTERANCES = load_utterances()
CORRECTIONS = load_corrections()

def get_reference(idx):
    if idx in CORRECTIONS:
        return CORRECTIONS[idx]
    return UTTERANCES[idx].lower()

def normalize_text(text):
    text = text.lower().strip()
    text = re.sub(r"[^\w\s']", ' ', text)
    text = re.sub(r"\s+", ' ', text).strip()
    
    num_words = {
        'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5',
        'six': '6', 'seven': '7', 'eight': '8', 'nine': '9', 'ten': '10',
        'eleven': '11', 'twelve': '12', 'thirteen': '13', 'fourteen': '14', 'fifteen': '15',
        'sixteen': '16', 'seventeen': '17', 'eighteen': '18', 'nineteen': '19', 'twenty': '20',
        'thirty': '30', 'forty': '40', 'fifty': '50', 'sixty': '60', 'seventy': '70',
        'eighty': '80', 'ninety': '90', 'hundred': '100', 'thousand': '1000',
        'am': 'am', 'pm': 'pm'
    }
    
    words = text.split()
    normalized = []
    i = 0
    while i < len(words):
        w = words[i]
        if w in num_words:
            if w in ['twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety'] and i + 1 < len(words):
                next_w = words[i + 1]
                if next_w in ['one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine']:
                    tens = int(num_words[w])
                    ones = int(num_words[next_w])
                    normalized.append(str(tens + ones))
                    i += 2
                    continue
            normalized.append(num_words[w])
        else:
            normalized.append(w)
        i += 1
    
    return ' '.join(normalized)

def compute_wer(ref, hyp):
    ref_words = ref.split()
    hyp_words = hyp.split()
    if not ref_words and not hyp_words:
        return 0.0
    if not ref_words or not hyp_words:
        return 1.0
    dp = [[0] * (len(hyp_words) + 1) for _ in range(len(ref_words) + 1)]
    for i in range(len(ref_words) + 1):
        dp[i][0] = i
    for j in range(len(hyp_words) + 1):
        dp[0][j] = j
    for i in range(1, len(ref_words) + 1):
        for j in range(1, len(hyp_words) + 1):
            if ref_words[i-1] == hyp_words[j-1]:
                dp[i][j] = dp[i-1][j-1]
            else:
                dp[i][j] = min(dp[i-1][j], dp[i][j-1], dp[i-1][j-1]) + 1
    return dp[len(ref_words)][len(hyp_words)] / len(ref_words)

def load_wav(filepath):
    with wave.open(str(filepath), 'rb') as wf:
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        sr = wf.getframerate()
    return audio, sr

def categorize(idx):
    if idx < 15:
        return "commands"
    elif idx < 25:
        return "questions"
    elif idx < 33:
        return "names"
    else:
        return "numbers"

def main():
    print("=== Testing faster-whisper small.en beam_size=1 ===")
    model = WhisperModel("small.en", device="cpu", compute_type="int8", cpu_threads=4)
    
    results = []
    recordings = sorted(RECORDINGS_DIR.glob("*.wav"))
    
    for idx, wav_file in enumerate(recordings):
        segments, info = model.transcribe(str(wav_file), beam_size=1, without_timestamps=True, language="en")
        hyp = " ".join([s.text for s in segments]).strip()
        
        ref = get_reference(idx)
        ref_norm = normalize_text(ref)
        hyp_norm = normalize_text(hyp)
        wer = compute_wer(ref_norm, hyp_norm)
        
        results.append({
            "file": wav_file.name,
            "idx": idx,
            "reference": ref,
            "hypothesis": hyp,
            "ref_normalized": ref_norm,
            "hyp_normalized": hyp_norm,
            "wer": round(wer, 4),
        })
        print(f"  {wav_file.name}: WER={wer:.3f} | ref='{ref_norm}' | hyp='{hyp_norm}'")
    
    # Summary
    print(f"\n=== faster-whisper-small.en SUMMARY ===")
    all_wer = [r['wer'] for r in results]
    print(f"  Overall WER: {np.mean(all_wer):.3f} (median {np.median(all_wer):.3f})")
    
    for cat in ["commands", "questions", "names", "numbers"]:
        cat_results = [r for r in results if categorize(r['idx']) == cat]
        if cat_results:
            cat_wer = [r['wer'] for r in cat_results]
            print(f"  {cat}: WER={np.mean(cat_wer):.3f} (n={len(cat_wer)})")
    
    num_results = [r for r in results if categorize(r['idx']) == "numbers"]
    exact_match = sum(1 for r in num_results if r['wer'] == 0)
    print(f"  numbers exact-match: {exact_match}/{len(num_results)} = {exact_match/len(num_results)*100:.1f}%")
    
    print(f"\n  Errors in names/numbers:")
    for r in results:
        cat = categorize(r['idx'])
        if cat in ["names", "numbers"] and r['wer'] > 0:
            print(f"    {r['file']}: ref='{r['ref_normalized']}' hyp='{r['hyp_normalized']}'")
    
    # Load existing sherpa results and combine
    with open("tests/benchmarks/voice/ws2/results/accuracy_results.json", "w") as f:
        json.dump({"faster-whisper-small.en": results}, f, indent=2)
    print("\nResults saved")

if __name__ == "__main__":
    main()