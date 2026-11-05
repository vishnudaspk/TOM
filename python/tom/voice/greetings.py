"""Time-aware greeting templates and wake-word acknowledgements for TOM.

No external dependencies — stdlib only.
"""

from __future__ import annotations

import random
from typing import Any

from tom.identity import ASSISTANT_NAME

__all__ = [
    "ASSISTANT_NAME",
    "WAKE_ACK_PHRASES",
    "clear_pre_synthesized",
    "get_pre_synthesized",
    "pick_greeting",
    "pick_wake_ack",
    "pre_synthesize_greeting_pool",
    "set_pre_synthesized",
]

# Greeting templates bucketed by time-of-day.
_GREETINGS: dict[str, list[str]] = {
    "morning": [
        "Morning! TOM's ready when you are.",
        "Good morning. How can I help?",
        "Morning. What do you need?",
        "Rise and shine — I'm here.",
        "Good morning. Standing by.",
    ],
    "afternoon": [
        "Afternoon. What do you need?",
        "Good afternoon. Ready to help.",
        "Hey, I'm listening.",
        "Afternoon — what's on your mind?",
        "Good afternoon. Standing by.",
    ],
    "evening": [
        "Good evening. I'm listening.",
        "Evening. What's up?",
        "Evening — how can I help?",
        "Hey there. Ready when you are.",
        "Good evening. Standing by.",
    ],
    "night": [
        "Hey, you're up late. I'm here.",
        "Night mode. What do you need?",
        "Late night session — I'm ready.",
        "Still here. What's on your mind?",
        "Working late tonight. Standing by.",
    ],
}

# Short acknowledgements spoken after wake-word detection.
WAKE_ACK_PHRASES: list[str] = [
    "Yeah?",
    "Listening.",
    "I'm here.",
    "Go ahead.",
    "Yes?",
    "Standing by.",
]

# Pre-synthesized audio cache for zero-latency instant playback (Track C)
_PRE_SYNTHESIZED_CACHE: dict[str, Any] = {}


def get_pre_synthesized(text: str) -> Any | None:
    """Retrieve pre-synthesized audio result for text, or None if not cached."""
    return _PRE_SYNTHESIZED_CACHE.get(text)


def set_pre_synthesized(text: str, result: Any) -> None:
    """Cache pre-synthesized audio result."""
    _PRE_SYNTHESIZED_CACHE[text] = result


def clear_pre_synthesized() -> None:
    """Clear the pre-synthesized audio cache."""
    _PRE_SYNTHESIZED_CACHE.clear()


def pre_synthesize_greeting_pool(tts_provider: Any, voice: str = "bm_george") -> int:
    """Pre-synthesize all greeting and wake-ack phrases into memory for instant turnaround."""
    count = 0
    all_phrases: list[str] = []
    for bucket in _GREETINGS.values():
        all_phrases.extend(bucket)
    all_phrases.extend(WAKE_ACK_PHRASES)

    for phrase in all_phrases:
        if phrase in _PRE_SYNTHESIZED_CACHE:
            continue
        try:
            from tom.schemas.voice import SynthesisRequest

            req = SynthesisRequest(text=phrase, voice=voice)
            if hasattr(tts_provider, "_synthesize_sync"):
                samples, sr, _ = tts_provider._synthesize_sync(req)
                from tom.voice.audio_utils import samples_to_pcm16_bytes

                pcm_bytes = samples_to_pcm16_bytes(samples)
                from tom.schemas.voice import SynthesisResult

                res = SynthesisResult(
                    samples=samples,
                    sample_rate=sr,
                    channels=1,
                    audio_data=pcm_bytes,
                )
                _PRE_SYNTHESIZED_CACHE[phrase] = res
                count += 1
        except Exception:
            pass
    return count


def _period(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 23:
        return "evening"
    return "night"


def pick_greeting(hour: int, last: str | None = None) -> str:
    """Return a time-appropriate greeting that differs from *last* if possible."""
    pool = _GREETINGS[_period(hour)]
    candidates = [g for g in pool if g != last]
    return random.choice(candidates if candidates else pool)


def pick_wake_ack(last: str | None = None) -> str:
    """Return a short wake-word acknowledgement different from *last* if possible."""
    candidates = [p for p in WAKE_ACK_PHRASES if p != last]
    return random.choice(candidates if candidates else WAKE_ACK_PHRASES)
