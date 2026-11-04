"""Wake-word detection — zero new dependencies.

Robust matching on the STT transcript, accounting for Whisper's
punctuation (e.g. "Hey, Tom.", "Hey Tom!", "Tom,"), optional prefixes
("hey", "hi", "ok", "okay"), standalone name recognition, direct address,
and common Whisper acoustic homophones for Tom ("Dom", "Don", "Thom").
"""

from __future__ import annotations

import re


def _get_name_pattern(name: str) -> str:
    escaped = re.escape(name)
    if name.lower() == "tom":
        # Support common Whisper acoustic homophones for Tom: Dom, Don (excluding don't), Thom
        return r"(?:tom|dom|thom|don(?!['’]t))"
    return escaped


def is_wake_phrase(text: str, phrase: str = "hey tom") -> bool:
    """Return True when wake phrase or direct address is detected."""
    if not text:
        return False

    clean_phrase = re.sub(r"[^\w\s]", "", phrase.lower()).strip()
    words = clean_phrase.split()
    raw_name = words[-1] if words else "tom"
    name = _get_name_pattern(raw_name)

    # 1. Any configured greeting prefix + name, handling commas/punctuation
    #    e.g. "Hey, Tom.", "Hey Dom", "Hi Tom!", "Okay, Tom."
    p_prefix = rf"\b(?:hey|hi|hello|ok|okay)\b[\W_]*\b{name}\b"

    # 2. Standalone name with optional punctuation (e.g. "Tom", "Tom.", "TOM!", "Don?")
    p_standalone = rf"^\s*[\W_]*\b{name}\b[\W_]*$"

    # 3. Direct address at start (e.g. "Tom, what time is it?", "Hey Dom, can you help?")
    p_direct = rf"^\s*[\W_]*\b{name}\b\s*[,:]"

    # 4. Exact phrase match with whitespace/punctuation tolerance between words
    p_phrase = r"\s*[\W_]*\s*".join(re.escape(w) for w in words)

    pattern = re.compile(rf"{p_prefix}|{p_standalone}|{p_direct}|\b{p_phrase}\b", re.IGNORECASE)
    return bool(pattern.search(text))


def strip_wake_phrase(text: str, phrase: str = "hey tom") -> str:
    """Strip wake phrase or direct address from *text* and clean punctuation."""
    if not text:
        return ""

    clean_phrase = re.sub(r"[^\w\s]", "", phrase.lower()).strip()
    words = clean_phrase.split()
    raw_name = words[-1] if words else "tom"
    name = _get_name_pattern(raw_name)

    p_prefix = rf"(?:(?:hey|hi|hello|ok|okay)\b[\W_]*\s*)?\b{name}\b[\W_]*\s*"
    p_phrase = r"\s*[\W_]*\s*".join(re.escape(w) for w in words) + r"[\W_]*\s*"

    pattern = re.compile(rf"^\s*(?:{p_prefix}|{p_phrase})", re.IGNORECASE)
    stripped = pattern.sub("", text, count=1).strip(" ,.!?")
    return stripped
