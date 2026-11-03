"""Wake-word detection — zero new dependencies.

Uses case-insensitive substring match on the STT transcript.
Accurate enough for "hey tom"; avoids adding pvporcupine/openwakeword.
"""

from __future__ import annotations

import re


def is_wake_phrase(text: str, phrase: str = "hey tom") -> bool:
    """Return True when *phrase* appears anywhere in *text* (case-insensitive)."""
    return phrase.lower() in text.lower()


def strip_wake_phrase(text: str, phrase: str = "hey tom") -> str:
    """Strip *phrase* from *text* and clean leading/trailing punctuation and whitespace."""
    pattern = re.compile(re.escape(phrase), re.IGNORECASE)
    return pattern.sub("", text, count=1).strip(" ,.!?")
