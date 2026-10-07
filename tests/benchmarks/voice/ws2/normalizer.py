#!/usr/bin/env python3
"""
Audited single-source normalizer and WER scorer for TOM voice benchmarks.
All future tables must use this scorer.
"""

import re
from typing import Tuple, List, Dict, Any

SMALL_NUMS = {
    'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
    'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
    'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15,
    'sixteen': 16, 'seventeen': 17, 'eighteen': 18, 'nineteen': 19,
    'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
    'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90
}

ORDINALS = {
    'first': '1st', 'second': '2nd', 'third': '3rd', 'fourth': '4th', 'fifth': '5th',
    'sixth': '6th', 'seventh': '7th', 'eighth': '8th', 'ninth': '9th', 'tenth': '10th',
    'fifteenth': '15th', 'twentieth': '20th', 'thirtieth': '30th',
}

def normalize_text(text: str) -> str:
    """
    Standardize spoken words, times, numbers, ordinals, and contractions for fair WER.
    1. Lowercase and strip whitespace.
    2. Convert percentage symbol: 15% -> 15 percent.
    3. Normalize times: 7:30 -> 7 30.
    4. Normalize am/pm: 'a m', 'a.m.', 'p m', 'p.m.' -> 'am', 'pm'.
    5. Remove punctuation except word characters and spaces.
    6. Convert ordinals: 'first' -> '1st'.
    7. Convert hundreds: 'one hundred' -> '100', 'two hundred' -> '200'.
    8. Convert compound tens/units: 'twenty five' -> '25'.
    9. Convert small numbers: 'five' -> '5'.
    """
    if not text:
        return ""
    text = text.lower().strip()
    # Normalize percent sign
    text = re.sub(r'(\d+)\s*%', r'\1 percent', text)
    # Normalize time colons: 7:30 -> 7 30
    text = re.sub(r'(\d+):(\d+)', r'\1 \2', text)
    # Normalize a m / p m variants
    text = re.sub(r'\ba\s*\.?\s*m\.?\b', 'am', text)
    text = re.sub(r'\bp\s*\.?\s*m\.?\b', 'pm', text)
    # Remove punctuation except alphanumeric and space
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    
    words = text.split()
    words = [ORDINALS.get(w, w) for w in words]
    
    result = []
    i = 0
    while i < len(words):
        w = words[i]
        # Check hundred: 'one hundred' -> 100, 'two hundred' -> 200
        if i + 1 < len(words) and words[i + 1] == 'hundred':
            if w in SMALL_NUMS and SMALL_NUMS[w] < 10:
                val = SMALL_NUMS[w] * 100
                i += 2
                # Check for trailing numbers: e.g. two hundred fifty -> 250
                if i < len(words) and words[i] in SMALL_NUMS:
                    subval = SMALL_NUMS[words[i]]
                    i += 1
                    if i < len(words) and words[i] in SMALL_NUMS and SMALL_NUMS[words[i]] < 10:
                        subval += SMALL_NUMS[words[i]]
                        i += 1
                    val += subval
                result.append(str(val))
                continue
            elif w in ['a', 'the']:
                result.append('100')
                i += 2
                continue
        elif w == 'hundred':
            result.append('100')
            i += 1
            continue
        
        # Compound tens + units: 'twenty five' -> 25
        if w in ['twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety']:
            val = SMALL_NUMS[w]
            if i + 1 < len(words) and words[i + 1] in SMALL_NUMS and SMALL_NUMS[words[i + 1]] < 10:
                val += SMALL_NUMS[words[i + 1]]
                i += 1
            result.append(str(val))
            i += 1
            continue
        
        if w in SMALL_NUMS:
            result.append(str(SMALL_NUMS[w]))
            i += 1
            continue
            
        result.append(w)
        i += 1
        
    return ' '.join(result)

def compute_wer(ref: str, hyp: str) -> float:
    """Standard Levenshtein Word Error Rate."""
    r = ref.strip().split()
    h = hyp.strip().split()
    if not r:
        return 0.0 if not h else 1.0
    d = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
    for i in range(len(r) + 1):
        d[i][0] = i
    for j in range(len(h) + 1):
        d[0][j] = j
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            if r[i - 1] == h[j - 1]:
                d[i][j] = d[i - 1][j - 1]
            else:
                d[i][j] = min(d[i - 1][j] + 1,      # deletion
                              d[i][j - 1] + 1,      # insertion
                              d[i - 1][j - 1] + 1)  # substitution
    return float(d[len(r)][len(h)]) / len(r)

UNIT_TESTS: List[Tuple[str, str, str]] = [
    ("one hundred", "100", "one hundred to 100"),
    ("two hundred", "200", "two hundred to 200"),
    ("fifteen percent", "15 percent", "fifteen percent words"),
    ("15%", "15 percent", "fifteen percent symbol"),
    ("7:30", "7 30", "colon time format 7:30"),
    ("7 30", "7 30", "digit space time 7 30"),
    ("seven thirty", "7 30", "spoken time seven thirty"),
    ("3:15 pm", "3 15 pm", "colon time with pm"),
    ("three fifteen p m", "3 15 pm", "spaced letters p m"),
    ("3:15 p.m.", "3 15 pm", "punctuated p.m."),
    ("twenty five", "25", "compound tens + units"),
    ("the first file", "the 1st file", "ordinal conversion"),
    ("calculate fifteen percent of two hundred", "calculate 15 percent of 200", "full math command"),
    ("set an alarm for seven thirty am", "set an alarm for 7 30 am", "full alarm command"),
    ("remind me at three fifteen pm", "remind me at 3 15 pm", "full reminder command"),
    ("delete files older than thirty days", "delete files older than 30 days", "full delete command"),
]

def run_tests() -> bool:
    all_ok = True
    print("=== Running Normalizer Unit Tests ===")
    for inp, exp, desc in UNIT_TESTS:
        got = normalize_text(inp)
        ok = (got == exp)
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {desc:<35} | In: {inp!r:<40} | Got: {got!r:<30} | Exp: {exp!r}")
        if not ok:
            all_ok = False
            
    # Explicit equality assertions requested by prompt
    eq1 = (normalize_text("7:30") == normalize_text("7 30") == normalize_text("seven thirty"))
    eq2 = (normalize_text("3:15 pm") == normalize_text("three fifteen p m"))
    print(f"['PASS' if eq1 else 'FAIL'] '7:30' == '7 30' == 'seven thirty' -> {eq1}")
    print(f"['PASS' if eq2 else 'FAIL'] '3:15 pm' == 'three fifteen p m' -> {eq2}")
    if not eq1 or not eq2:
        all_ok = False
        
    print(f"\nResult: {'ALL TESTS PASSED (16/16)' if all_ok else 'SOME TESTS FAILED'}")
    return all_ok

if __name__ == "__main__":
    import sys
    if not run_tests():
        sys.exit(1)
