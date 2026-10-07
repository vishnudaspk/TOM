#!/usr/bin/env python3
"""
Task 1b/1c: Fixed harness - full 41-recording accuracy test with correct protocol.
Correct protocol:
  - Feed audio in 100 ms chunks
  - After EACH accept_waveform: while is_ready: decode_stream
  - Feed tail padding (silence, unpaced, not counted in latency)
  - Call input_finished()
  - Final decode loop
  - Read result

Also includes normalizer unit test (8+ hand-written cases).
Produces:
  tests/benchmarks/voice/ws2/results/task1c_accuracy_fixed.json
  tests/benchmarks/voice/ws2/results/task1c_normalizer_unit_test.json
"""

import json
import time
import wave
import re
import numpy as np
from pathlib import Path

import sherpa_onnx
from faster_whisper import WhisperModel

# ---- Paths ----
RECORDINGS_DIR = Path("tests/benchmarks/voice/ws2/recordings")
UTTERANCES_FILE = Path("tests/benchmarks/voice/ws2/utterances.txt")
CORRECTIONS_FILE = Path("tests/benchmarks/voice/ws2/results/corrections.txt")
MODEL_DIR = Path("tests/benchmarks/voice/ws2/models/sherpa-onnx-streaming-zipformer-en-2023-06-26")
RESULTS_DIR = Path("tests/benchmarks/voice/ws2/results")

CHUNK_SAMPLES = 1600   # 100 ms at 16000 Hz
TAIL_PADDING_SAMPLES = int(16000 * 0.5)  # 500 ms tail silence

# ---- References ----
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
                        corrections[int(parts[0]) - 1] = parts[1].lower()
    return corrections

UTTERANCES = load_utterances()
CORRECTIONS = load_corrections()
print(f"Loaded {len(UTTERANCES)} utterances, {len(CORRECTIONS)} corrections")

def get_reference(idx):
    if idx in CORRECTIONS:
        return CORRECTIONS[idx]
    return UTTERANCES[idx].lower()

# ---- Normalizer ----
NUM_WORDS = {
    'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5',
    'six': '6', 'seven': '7', 'eight': '8', 'nine': '9', 'ten': '10',
    'eleven': '11', 'twelve': '12', 'thirteen': '13', 'fourteen': '14', 'fifteen': '15',
    'sixteen': '16', 'seventeen': '17', 'eighteen': '18', 'nineteen': '19',
    'twenty': '20', 'thirty': '30', 'forty': '40', 'fifty': '50', 'sixty': '60',
    'seventy': '70', 'eighty': '80', 'ninety': '90', 'hundred': '100', 'thousand': '1000',
}
ORDINALS = {
    'first': '1st', 'second': '2nd', 'third': '3rd', 'fourth': '4th', 'fifth': '5th',
    'sixth': '6th', 'seventh': '7th', 'eighth': '8th', 'ninth': '9th', 'tenth': '10th',
    'fifteenth': '15th', 'twentieth': '20th', 'thirtieth': '30th',
}
AM_PM = {'am', 'pm'}

def normalize_text(text: str) -> str:
    """
    Normalize for WER:
    1. Lowercase
    2. Remove punctuation except apostrophes in contractions
    3. Convert spoken numbers to digits (compound: twenty five -> 25)
    4. Ordinals: first -> 1st, etc.
    5. Time patterns: 'seven thirty am' -> '7:30 am'
    """
    text = text.lower().strip()
    # Remove punctuation except apostrophes
    text = re.sub(r"[^\w\s']", ' ', text)
    text = re.sub(r"\s+", ' ', text).strip()

    words = text.split()
    # Ordinal pass
    words = [ORDINALS.get(w, w) for w in words]

    # Number + time reconstruction
    result = []
    i = 0
    while i < len(words):
        w = words[i]
        if w in NUM_WORDS:
            num = int(NUM_WORDS[w])
            # Compound number: twenty five -> 25
            if w in ['twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety'] and i + 1 < len(words):
                nw = words[i + 1]
                if nw in ['one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine']:
                    num = int(NUM_WORDS[w]) + int(NUM_WORDS[nw])
                    i += 1
            # Time pattern: <hour> <minutes> am/pm -> h:mm am/pm
            if i + 1 < len(words):
                next1 = words[i + 1]
                if next1 in NUM_WORDS and int(NUM_WORDS[next1]) >= 0 and int(NUM_WORDS[next1]) <= 59:
                    mins = int(NUM_WORDS[next1])
                    if i + 2 < len(words) and words[i + 2] in AM_PM:
                        result.append(f"{num}:{mins:02d}")
                        result.append(words[i + 2])
                        i += 3
                        continue
            result.append(str(num))
        else:
            result.append(w)
        i += 1

    return ' '.join(result)

# ---- Normalizer unit test (8 hand-written cases) ----
NORMALIZER_TEST_CASES = [
    # (input, expected_output, description)
    ("five",                      "5",           "single digit spoken"),
    ("thirty days",               "30 days",     "tens spoken"),
    ("twenty five",               "25",          "compound spoken"),
    ("set a timer for five minutes", "set a timer for 5 minutes", "spoken in sentence"),
    ("seven thirty am",           "7:30 am",     "time 7:30 AM"),
    ("three fifteen pm",          "3:15 pm",     "time 3:15 PM"),
    ("delete files older than thirty days", "delete files older than 30 days", "thirty in sentence"),
    ("calculate fifteen percent", "calculate 15 percent", "fifteen"),
    ("the first file",            "the 1st file", "ordinal first"),
    ("one hundred",               "100",         "hundred"),
    ("SEVEN THIRTY AM",           "7:30 am",     "uppercase input"),
    ("midnight",                  "midnight",    "non-number pass-through"),
]

def run_normalizer_unit_tests():
    print("\n=== Normalizer Unit Tests ===")
    results = []
    all_pass = True
    for inp, expected, desc in NORMALIZER_TEST_CASES:
        got = normalize_text(inp)
        ok = (got == expected)
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"  {status}: [{desc}] '{inp}' -> '{got}' (expected '{expected}')")
        results.append({"input": inp, "expected": expected, "got": got, "pass": ok, "desc": desc})
    print(f"\n  {'ALL PASS' if all_pass else 'FAILURES FOUND'} ({sum(r['pass'] for r in results)}/{len(results)})")
    return results, all_pass

# ---- WER ----
def compute_wer(ref: str, hyp: str) -> float:
    r, h = ref.split(), hyp.split()
    if not r and not h:
        return 0.0
    if not r or not h:
        return 1.0
    dp = [[0]*(len(h)+1) for _ in range(len(r)+1)]
    for i in range(len(r)+1): dp[i][0] = i
    for j in range(len(h)+1): dp[0][j] = j
    for i in range(1, len(r)+1):
        for j in range(1, len(h)+1):
            dp[i][j] = dp[i-1][j-1] if r[i-1]==h[j-1] else min(dp[i-1][j], dp[i][j-1], dp[i-1][j-1]) + 1
    return dp[len(r)][len(h)] / len(r)

# ---- Audio ----
def load_wav_float32(path: Path):
    with wave.open(str(path), 'rb') as wf:
        sr = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    return audio, sr

# ---- Sherpa correct protocol ----
def make_sherpa_recognizer():
    encoder = str(list(MODEL_DIR.glob("*encoder*chunk*.int8.onnx"))[0])
    decoder = str(list(MODEL_DIR.glob("*decoder*chunk*.int8.onnx"))[0])
    joiner  = str(list(MODEL_DIR.glob("*joiner*chunk*.int8.onnx"))[0])
    tokens  = str(MODEL_DIR / "tokens.txt")
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=tokens, encoder=encoder, decoder=decoder, joiner=joiner,
        num_threads=4, decoding_method="greedy_search",
        enable_endpoint_detection=False,   # off: we rely on VAD (file end)
        provider="cpu",
    )

def sherpa_decode(recognizer, audio, sr=16000):
    stream = recognizer.create_stream()
    # Feed in 100 ms chunks; decode after each chunk
    for start in range(0, len(audio), CHUNK_SAMPLES):
        chunk = audio[start:start + CHUNK_SAMPLES]
        stream.accept_waveform(sr, chunk)
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
    # Tail padding (flush encoder)
    tail = np.zeros(TAIL_PADDING_SAMPLES, dtype=np.float32)
    stream.accept_waveform(sr, tail)
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    # Finalize
    stream.input_finished()
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    return recognizer.get_result(stream)

# ---- Category ----
def categorize(idx):
    if idx < 15:  return "commands"
    elif idx < 25: return "questions"
    elif idx < 33: return "names"
    else:          return "numbers"

# ---- Main ----
def run_engine(name, decode_fn):
    recordings = sorted(RECORDINGS_DIR.glob("*.wav"))
    results = []
    for idx, wav_file in enumerate(recordings):
        audio, sr = load_wav_float32(wav_file)
        hyp = decode_fn(audio, sr)
        ref = get_reference(idx)
        ref_n = normalize_text(ref)
        hyp_n = normalize_text(hyp)
        wer = compute_wer(ref_n, hyp_n)
        results.append({
            "file": wav_file.name,
            "idx": idx,
            "category": categorize(idx),
            "reference": ref,
            "hypothesis": hyp,
            "ref_normalized": ref_n,
            "hyp_normalized": hyp_n,
            "wer": round(wer, 4),
        })
        print(f"  [{name}] {wav_file.name}: WER={wer:.3f}")
        print(f"    REF: {ref_n}")
        print(f"    HYP: {hyp_n}")
    return results

def summarize(name, results):
    print(f"\n=== {name} SUMMARY ===")
    all_wer = [r['wer'] for r in results]
    print(f"  Overall WER (mean): {np.mean(all_wer):.3f}  median: {np.median(all_wer):.3f}  n={len(all_wer)}")
    for cat in ["commands", "questions", "names", "numbers"]:
        cat_r = [r for r in results if r['category'] == cat]
        if cat_r:
            wers = [r['wer'] for r in cat_r]
            print(f"  {cat}: mean WER={np.mean(wers):.3f}  n={len(wers)}")
    num_r = [r for r in results if r['category'] == 'numbers']
    exact = sum(1 for r in num_r if r['wer'] == 0.0)
    print(f"  numbers exact-match: {exact}/{len(num_r)}")
    print(f"\n  15 sample pairs (ref_norm, hyp_norm):")
    sample = results[:15]
    for r in sample:
        print(f"    [{r['file']}] ref='{r['ref_normalized']}' | hyp='{r['hyp_normalized']}'")
    print(f"\n  Name/number errors:")
    for r in results:
        if r['category'] in ('names','numbers') and r['wer'] > 0:
            print(f"    {r['file']}: ref='{r['ref_normalized']}' hyp='{r['hyp_normalized']}'")

def main():
    print("=" * 70)
    print("Task 1b/1c: Fixed sherpa harness - full 41-recording accuracy test")
    print("=" * 70)
    print(f"sherpa_onnx version: {sherpa_onnx.__version__}")

    # Normalizer unit tests
    norm_results, norm_ok = run_normalizer_unit_tests()

    # Sherpa
    print("\n=== sherpa-large (FIXED PROTOCOL) ===")
    recognizer = make_sherpa_recognizer()
    sherpa_results = run_engine("sherpa-large", lambda a, sr: sherpa_decode(recognizer, a, sr))
    summarize("sherpa-large (fixed)", sherpa_results)

    # Faster-whisper
    print("\n=== faster-whisper small.en beam_size=1 ===")
    fw_model = WhisperModel("small.en", device="cpu", compute_type="int8", cpu_threads=4)
    def fw_decode(audio, sr):
        import tempfile, soundfile as sf
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            sf.write(tmp.name, audio, sr)
            segs, _ = fw_model.transcribe(tmp.name, beam_size=1, language="en")
            return " ".join(s.text for s in segs).strip()
    # Actually use wave directly
    def fw_decode_direct(audio, sr):
        import tempfile
        import wave as wav_mod
        import os
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            fname = tmp.name
        with wav_mod.open(fname, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes((audio * 32768).astype(np.int16).tobytes())
        segs, _ = fw_model.transcribe(fname, beam_size=1, language="en")
        text = " ".join(s.text for s in segs).strip()
        os.unlink(fname)
        return text

    fw_results = run_engine("faster-whisper", fw_decode_direct)
    summarize("faster-whisper small.en beam_size=1", fw_results)

    # Save
    output = {
        "date": "2026-10-07",
        "protocol": "FIXED: chunk feed + decode loop after each chunk + tail padding + input_finished + final decode",
        "sherpa_onnx_version": sherpa_onnx.__version__,
        "bug_fixed": "Old harness: accept_waveform then immediately input_finished with NO decode loop -> gibberish output",
        "normalizer_unit_tests": {"results": norm_results, "all_pass": norm_ok},
        "results": {
            "sherpa-large-fixed": sherpa_results,
            "faster-whisper-small.en": fw_results,
        }
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / "task1c_accuracy_fixed.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {out_path}")

if __name__ == "__main__":
    main()
