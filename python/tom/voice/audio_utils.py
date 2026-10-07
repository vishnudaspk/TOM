"""Audio utility functions for PCM sample and byte conversions.

Target format:
- 16 kHz sample rate
- 16-bit signed integer little-endian PCM
- Mono (1 channel)
- Normalized float32 samples (-1.0 to 1.0)
"""

from __future__ import annotations

import struct
from collections.abc import Sequence


def samples_to_pcm16_bytes(samples: Sequence[float]) -> bytes:
    """Convert normalized float samples (-1.0 to 1.0) to 16-bit little-endian PCM bytes."""
    if not samples:
        return b""
    # Fast clamping and scaling to signed 16-bit integers (-32768 to 32767)
    int_samples = [max(-32768, min(32767, int(s * 32767.0))) for s in samples]
    return struct.pack(f"<{len(int_samples)}h", *int_samples)


def pcm16_bytes_to_samples(data: bytes) -> list[float]:
    """Convert 16-bit little-endian PCM bytes to normalized float samples (-1.0 to 1.0)."""
    count = len(data) // 2
    if count == 0:
        return []
    ints = struct.unpack(f"<{count}h", data[: count * 2])
    return [s / 32767.0 for s in ints]
