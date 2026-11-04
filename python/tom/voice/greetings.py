"""Time-aware greeting templates and wake-word acknowledgements for TOM.

No external dependencies — stdlib only.
"""

from __future__ import annotations

import random

from tom.identity import ASSISTANT_NAME

__all__ = ["ASSISTANT_NAME", "WAKE_ACK_PHRASES", "pick_greeting", "pick_wake_ack"]

# Greeting templates bucketed by time-of-day.
_GREETINGS: dict[str, list[str]] = {
    "morning": [
        "Morning! TOM's ready when you are.",
        "Good morning. How can I help?",
        "Morning. What do you need?",
        "Rise and shine — I'm here.",
    ],
    "afternoon": [
        "Afternoon. What do you need?",
        "Good afternoon. Ready to help.",
        "Hey, I'm listening.",
        "Afternoon — what's on your mind?",
    ],
    "evening": [
        "Good evening. I'm listening.",
        "Evening. What's up?",
        "Evening — how can I help?",
        "Hey there. Ready when you are.",
    ],
    "night": [
        "Hey, you're up late. I'm here.",
        "Night mode. What do you need?",
        "Late night session — I'm ready.",
        "Still here. What's on your mind?",
    ],
}

# Short acknowledgements spoken after wake-word detection.
WAKE_ACK_PHRASES: list[str] = [
    "Yeah?",
    "Listening.",
    "I'm here.",
    "Go ahead.",
    "Yes?",
]


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
