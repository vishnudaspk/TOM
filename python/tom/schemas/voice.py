"""Pydantic v2 schemas for TOM Voice subsystem.

Adheres to:
- Phase 6, Iteration 1: STT Provider Abstraction & FasterWhisper Provider
- Phase 6, Iteration 2: TTS Provider Abstraction & Kokoro Provider
- Phase 6, Iteration 3: VoicePipelineManager State Machine & Barge-In
- Decision 041: Discrete utterance-buffered audio over Named Pipe IPC
- skills/coding/validation: Strict bounds, Pydantic v2, offline-safe.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TranscriptionSegment(BaseModel):
    """Timing-annotated segment from an STT transcription.

    Populated by providers that expose word/segment-level timestamps
    (e.g. FasterWhisper). Optional — consumers must tolerate an empty list.
    """

    model_config = ConfigDict(extra="ignore")

    start: float = Field(ge=0.0, description="Segment start time in seconds")
    end: float = Field(ge=0.0, description="Segment end time in seconds")
    text: str = Field(description="Segment transcript text")
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Per-segment confidence score, if available",
    )


class TranscriptionRequest(BaseModel):
    """Audio payload submitted to an STTProvider for transcription.

    ``samples`` carries normalised float32 PCM as returned by
    ``EngineClient.get_captured_speech()``.  Pass ``bytes`` only when raw
    WAV/PCM bytes are pre-encoded by the caller.
    """

    model_config = ConfigDict(extra="forbid")

    samples: list[float] | bytes = Field(
        description="PCM audio: list[float] normalised -1..1 or raw bytes"
    )
    sample_rate: int = Field(default=16000, gt=0, description="Sample rate in Hz")
    channels: int = Field(default=1, ge=1, le=2, description="Channel count")
    language: str | None = Field(
        default=None,
        description="BCP-47 language hint (e.g. 'en'). None = auto-detect.",
    )


class TranscriptionResult(BaseModel):
    """Typed transcription result returned by any STTProvider."""

    model_config = ConfigDict(extra="ignore")

    text: str = Field(description="Full transcript text")
    language: str | None = Field(default=None, description="Detected or requested language")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Overall confidence score")
    duration_s: float = Field(default=0.0, ge=0.0, description="Audio duration in seconds")
    segments: list[TranscriptionSegment] = Field(
        default_factory=list,
        description="Segment-level timing annotations (empty when unavailable)",
    )
    latency_ms: int = Field(
        default=0, ge=0, description="Provider wall-clock latency in milliseconds"
    )
    model_name: str = Field(default="", description="Model identifier used")
    provider: str = Field(default="", description="Provider class name")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific extra metadata",
    )


class SynthesisRequest(BaseModel):
    """Text and voice parameters submitted to a TTSProvider for synthesis."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, description="Text to synthesize into speech")
    voice: str | None = Field(
        default=None,
        description="Voice identifier or preset name (e.g. 'af_heart'). None = provider default.",
    )
    speed: float = Field(
        default=1.0,
        gt=0.0,
        le=5.0,
        description="Speech rate multiplier (1.0 = normal)",
    )
    sample_rate: int = Field(
        default=24000,
        gt=0,
        description="Target sample rate in Hz (default 24000 to match Kokoro and engine playback)",
    )
    language: str | None = Field(
        default=None,
        description="Language or accent code (e.g. 'en-us', 'en-gb'). None = default.",
    )

    @field_validator("text")
    @classmethod
    def validate_text_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must not be empty or whitespace-only")
        return v


class SynthesisResult(BaseModel):
    """Typed synthesis result returned by any TTSProvider."""

    model_config = ConfigDict(extra="ignore")

    samples: list[float] = Field(description="Normalised float32 PCM audio samples (-1.0 to 1.0)")
    sample_rate: int = Field(
        default=24000,
        gt=0,
        description="Sample rate in Hz",
    )
    channels: int = Field(
        default=1,
        ge=1,
        le=2,
        description="Channel count (1=mono, 2=stereo)",
    )
    duration_s: float = Field(
        default=0.0,
        ge=0.0,
        description="Synthesized audio duration in seconds",
    )
    latency_ms: int = Field(
        default=0,
        ge=0,
        description="Provider wall-clock latency in milliseconds",
    )
    model_name: str = Field(
        default="",
        description="Model identifier used",
    )
    provider: str = Field(
        default="",
        description="Provider class name",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific extra metadata",
    )


class VoicePipelineState(StrEnum):
    """Lifecycle states for VoicePipelineManager (Decision 044)."""

    IDLE = "IDLE"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    ERROR = "ERROR"


class VoiceTurnResult(BaseModel):
    """Result of a single voice pipeline interaction turn."""

    model_config = ConfigDict(extra="ignore")

    transcript: str = Field(default="", description="Recognized user transcript")
    response_text: str = Field(default="", description="Raw assistant text response")
    formatted_text: str = Field(default="", description="Speech-formatted text")
    interrupted: bool = Field(default=False, description="Whether turn was interrupted (barge-in)")
    error: str | None = Field(default=None, description="Error message if turn failed")
    state: VoicePipelineState = Field(
        default=VoicePipelineState.IDLE, description="Final pipeline state"
    )


__all__ = [
    "SynthesisRequest",
    "SynthesisResult",
    "TranscriptionRequest",
    "TranscriptionResult",
    "TranscriptionSegment",
    "VoicePipelineState",
    "VoiceTurnResult",
]
