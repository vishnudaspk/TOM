"""Voice Activity Detection (VAD) detectors.

Provider-agnostic interface and lightweight, deterministic energy-based VAD.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Sequence


class VADDetector(ABC):
    """Abstract interface for Voice Activity Detection."""

    @abstractmethod
    def is_speech(self, frame_samples: Sequence[float], sample_rate: int = 16000) -> bool:
        """Classify whether an individual audio frame contains speech."""
        ...

    def reset(self) -> None:
        """Reset internal detector state (e.g. noise floor tracking)."""
        return None


class EnergyVADDetector(VADDetector):
    """Deterministic, energy-based VAD using RMS energy and adaptive noise floor."""

    def __init__(
        self,
        threshold: float = 0.003,
        adaptive: bool = True,
    ) -> None:
        """Initialize the energy VAD detector.

        Args:
            threshold: RMS energy threshold for speech (0.003 ≈ -50 dBFS).
            adaptive: Whether to track the noise floor dynamically during quiet periods.
        """
        self._threshold = threshold
        self._adaptive = adaptive
        self._noise_floor = min(0.001, threshold * 0.5)

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def noise_floor(self) -> float:
        return self._noise_floor

    def calculate_rms(self, frame_samples: Sequence[float]) -> float:
        """Calculate Root Mean Square (RMS) energy of sample values."""
        if not frame_samples:
            return 0.0
        sum_sq = sum(s * s for s in frame_samples)
        return math.sqrt(sum_sq / len(frame_samples))

    def is_speech(self, frame_samples: Sequence[float], sample_rate: int = 16000) -> bool:
        """Determine whether the audio frame exceeds the speech energy threshold."""
        rms = self.calculate_rms(frame_samples)
        limit = max(self._threshold, self._noise_floor * 2.0) if self._adaptive else self._threshold
        is_active = rms > limit

        if not is_active and self._adaptive:
            # Smoothly adapt noise floor during non-speech periods,
            # but cap noise floor so that limit (noise_floor * 2.0) can never exceed threshold
            max_floor = max(0.0005, self._threshold * 0.5)
            self._noise_floor = max(0.0001, min(max_floor, 0.95 * self._noise_floor + 0.05 * rms))

        return is_active

    def reset(self) -> None:
        self._noise_floor = min(0.001, self._threshold * 0.5)


class MockVADDetector(VADDetector):
    """Deterministic mock VAD detector for testing with predefined boolean sequences."""

    def __init__(
        self,
        sequence: list[bool] | None = None,
        default_result: bool = False,
    ) -> None:
        self._sequence = list(sequence) if sequence is not None else []
        self._default_result = default_result
        self._index = 0

    def is_speech(self, frame_samples: Sequence[float], sample_rate: int = 16000) -> bool:
        if self._index < len(self._sequence):
            res = self._sequence[self._index]
            self._index += 1
            return res
        return self._default_result

    def reset(self) -> None:
        self._index = 0


__all__ = [
    "EnergyVADDetector",
    "MockVADDetector",
    "VADDetector",
]
