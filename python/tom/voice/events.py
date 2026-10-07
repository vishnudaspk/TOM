"""Pydantic schemas for Phase 9 Voice Events.

Defines the continuous audio frontend event contracts:
SpeechStartEvent -> AudioFrameEvent* -> SpeechEndEvent
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SpeechStartEvent(BaseModel):
    """Emitted when user voice activity onset is detected."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    timestamp: float = Field(
        default_factory=time.time,
        description="Unix timestamp of speech onset",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Diagnostic metadata (e.g. initial RMS energy)",
    )


class AudioFrameEvent(BaseModel):
    """Emitted for each audio frame processed during an active speech turn."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    samples: list[float] = Field(
        description="Normalized float32 PCM samples (-1.0 to 1.0)",
    )
    pcm_bytes: bytes = Field(
        description="Raw 16-bit mono PCM bytes",
    )
    timestamp: float = Field(
        default_factory=time.time,
        description="Unix timestamp of the frame",
    )
    frame_index: int = Field(
        default=0,
        ge=0,
        description="Sequential frame index within the active utterance",
    )
    is_speech: bool = Field(
        default=True,
        description="VAD classification for this frame",
    )


class SpeechEndEvent(BaseModel):
    """Emitted when user speech concludes via silence endpoint or maximum duration."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    samples: list[float] = Field(
        description="Full concatenated utterance samples (-1.0 to 1.0)",
    )
    pcm_bytes: bytes = Field(
        description="Full concatenated 16-bit PCM bytes",
    )
    duration_s: float = Field(
        ge=0.0,
        description="Total speech duration in seconds",
    )
    frame_count: int = Field(
        ge=0,
        description="Total frames in the utterance",
    )
    timestamp: float = Field(
        default_factory=time.time,
        description="Unix timestamp of endpoint",
    )


VoiceEvent = SpeechStartEvent | AudioFrameEvent | SpeechEndEvent

__all__ = [
    "AudioFrameEvent",
    "SpeechEndEvent",
    "SpeechStartEvent",
    "VoiceEvent",
]
