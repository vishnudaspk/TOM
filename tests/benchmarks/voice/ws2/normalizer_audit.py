#!/usr/bin/env python3
"""
Normalizer audit for WS2 accuracy evaluation.
Prints 15 pairs per engine, unit-tests normalizer, recomputes WER.
"""

import json
import numpy as np
import re
from pathlib import Path

# Load data
with open("tests/benchmarks/voice/ws2/results/accuracy_combined.json") as f:
    accuracy_data = json.load(f)

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

# ============================================================
# AUDITED NORMALIZER - Canonical forms for numbers/times
# ============================================================
def normalize_text(text):
    """
    Normalize text for WER calculation.
    Canonical forms:
    - Lowercase
    - Remove punctuation except apostrophes in contractions
    - Spoken numbers -> digits: 'five' -> '5', 'twenty five' -> '25'
    - Times: 'seven thirty am' -> '7:30 am', 'three fifteen pm' -> '3:15 pm'
    - Ordinals: 'thirty first' -> '31' (not handled separately, treated as compound)
    - Dates: 'next friday' -> 'next friday' (kept as words)
    - 'percent' -> '%'
    - Contractions preserved: "don't" -> "don't"
    """
    text = text.lower().strip()
    
    # Remove punctuation except apostrophes and colons (for times)
    text = re.sub(r"[^\w\s':]", ' ', text)
    text = re.sub(r"\s+", ' ', text).strip()
    
    # Number word to digit mapping
    num_words = {
        'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5',
        'six': '6', 'seven': '7', 'eight': '8', 'nine': '9', 'ten': '10',
        'eleven': '11', 'twelve': '12', 'thirteen': '13', 'fourteen': '14', 'fifteen': '15',
        'sixteen': '16', 'seventeen': '17', 'eighteen': '18', 'nineteen': '19', 'twenty': '20',
        'thirty': '30', 'forty': '40', 'fifty': '50', 'sixty': '60', 'seventy': '70',
        'eighty': '80', 'ninety': '90', 'hundred': '100', 'thousand': '1000',
    }
    
    # Tens that can combine with ones
    tens_words = {'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety'}
    ones_words = {'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine'}
    
    words = text.split()
    normalized = []
    i = 0
    while i < len(words):
        w = words[i]
        
        # Handle time format: "seven thirty am" -> "7:30 am"
        if w in num_words and i + 2 < len(words):
            next1 = words[i + 1]
            next2 = words[i + 2]
            # Check for "X thirty am/pm" or "X fifteen am/pm" etc.
            if next1 in num_words and next2 in ['am', 'pm']:
                hour = num_words[w]
                minute = num_words[next1]
                normalized.append(f"{hour}:{minute} {next2}")
                i += 3
                continue
        
        # Handle compound numbers: "twenty five" -> "25"
        if w in tens_words and i + 1 < len(words):
            next_w = words[i + 1]
            if next_w in ones_words:
                tens_val = int(num_words[w])
                ones_val = int(num_words[next_w])
                normalized.append(str(tens_val + ones_val))
                i += 2
                continue
        
        # Handle "hundred" compounds: "two hundred" -> "200"
        if w in num_words and w not in tens_words and w not in ['hundred', 'thousand'] and i + 1 < len(words):
            next_w = words[i + 1]
            if next_w == 'hundred':
                val = int(num_words[w]) * 100
                normalized.append(str(val))
                i += 2
                continue
            elif next_w == 'thousand':
                val = int(num_words[w]) * 1000
                normalized.append(str(val))
                i += 2
                continue
        
        # Simple number word
        if w in num_words:
            normalized.append(num_words[w])
        elif w == 'percent':
            normalized.append('%')
        else:
            normalized.append(w)
        i += 1
    
    return ' '.join(normalized)

# WER calculation
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

def categorize(idx):
    if idx < 15: return "commands"
    elif idx < 25: return "questions"
    elif idx < 33: return "names"
    else: return "numbers"

# ============================================================
# UNIT TESTS FOR NORMALIZER (8 hand-written cases)
# ============================================================
def test_normalizer():
    print("=== NORMALIZER UNIT TESTS (8 cases) ===")
    test_cases = [
        # (input, expected_output)
        ("open the settings window", "open the settings window"),
        ("set a timer for five minutes", "set a timer for 5 minutes"),
        ("seven thirty am", "7:30 am"),
        ("three fifteen pm", "3:15 pm"),
        ("twenty five", "25"),
        ("two hundred", "200"),
        ("calculate fifteen percent of two hundred", "calculate 15 % of 200"),
        ("don't open the settings", "don't open the settings"),
    ]
    
    all_pass = True
    for inp, expected in test_cases:
        out = normalize_text(inp)
        status = "PASS" if out == expected else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"  {status}: '{inp}' -> '{out}' (expected '{expected}')")
    
    print(f"\nAll tests passed: {all_pass}")
    return all_pass

# ============================================================
# PRINT 15 PAIRS PER ENGINE
# ============================================================
def print_pairs(engine_name, results, n=15):
    print(f"\n=== {engine_name}: First {n} (normalized_ref, normalized_hyp) pairs ===")
    for i, r in enumerate(results[:n]):
        ref_norm = normalize_text(r['reference'])
        hyp_norm = normalize_text(r['hypothesis'])
        wer = compute_wer(ref_norm, hyp_norm)
        print(f"  {i+1:2d}. ref: '{ref_norm}'")
        print(f"      hyp: '{hyp_norm}' (WER={wer:.3f})")

# ============================================================
# RECOMPUTE WER WITH AUDITED NORMALIZER
# ============================================================
def compute_wer_stats(engine_name, results):
    print(f"\n=== {engine_name} WER STATS (audited normalizer) ===")
    
    all_wer = []
    cat_wer = {"commands": [], "questions": [], "names": [], "numbers": []}
    num_exact = 0
    num_total = 0
    
    for r in results:
        ref_norm = normalize_text(r['reference'])
        hyp_norm = normalize_text(r['hypothesis'])
        wer = compute_wer(ref_norm, hyp_norm)
        all_wer.append(wer)
        
        cat = categorize(r['idx'])
        cat_wer[cat].append(wer)
        
        if cat == "numbers":
            num_total += 1
            if wer == 0:
                num_exact += 1
    
    print(f"  Overall WER: {np.mean(all_wer):.3f} (median {np.median(all_wer):.3f})")
    for cat in ["commands", "questions", "names", "numbers"]:
        if cat_wer[cat]:
            print(f"  {cat:10s}: WER={np.mean(cat_wer[cat]):.3f} (n={len(cat_wer[cat])})")
    if num_total > 0:
        print(f"  numbers exact-match: {num_exact}/{num_total} = {num_exact/num_total*100:.1f}%")
    
    # List name/number errors
    print(f"\n  Name/Number errors:")
    for r in results:
        cat = categorize(r['idx'])
        if cat in ["names", "numbers"]:
            ref_norm = normalize_text(r['reference'])
            hyp_norm = normalize_text(r['hypothesis'])
            wer = compute_wer(ref_norm, hyp_norm)
            if wer > 0:
                print(f"    {r['file']}: ref='{ref_norm}' hyp='{hyp_norm}'")
    
    return {
        "overall_wer": np.mean(all_wer),
        "median_wer": np.median(all_wer),
        "by_category": {k: np.mean(v) if v else 0 for k, v in cat_wer.items()},
        "numbers_exact_match": f"{num_exact}/{num_total}"
    }

def main():
    # Run unit tests
    test_normalizer()
    
    # Print pairs for each engine
    for engine_name in ["sherpa-large", "faster-whisper-small.en"]:
        results = accuracy_data[engine_name]
        print_pairs(engine_name, results, 15)
    
    # Compute stats
    print("\n" + "="*60)
    print("RECOMPUTED WER WITH AUDITED NORMALIZER")
    print("="*60)
    
    stats = {}
    for engine_name in ["sherpa-large", "faster-whisper-small.en"]:
        results = accuracy_data[engine_name]
        stats[engine_name] = compute_wer_stats(engine_name, results)
    
    # Summary table
    print("\n=== SUMMARY TABLE ===")
    print(f"{'Engine':<25} {'Overall':>8} {'Commands':>8} {'Questions':>8} {'Names':>8} {'Numbers':>8} {'Num Exact':>12}")
    for engine_name, s in stats.items():
        print(f"{engine_name:<25} {s['overall_wer']:>7.1%} {s['by_category']['commands']:>7.1%} {s['by_category']['questions']:>7.1%} {s['by_category']['names']:>7.1%} {s['by_category']['numbers']:>7.1%} {s['numbers_exact_match']:>12}")

if __name__ == "__main__":
    main()