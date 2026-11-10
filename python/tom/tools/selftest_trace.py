#!/usr/bin/env python
"""Self-test for STEP A debug trace.

Generates a speech WAV using Kokoro (or synthetic if unavailable),
runs it through the full voice pipeline with debug_trace enabled,
then validates the trace with check_trace and replays with replay_endpointing.

Runs 3 utterances: one with a 1.5s internal pause.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

# Add python to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "python"))

from tom.core.config import load_config
from tom.voice.debug_trace import create_debug_trace_manager
from tom.voice.microphone import MicrophoneConfig, MicrophoneLoop
from tom.voice.vad import EnergyVADDetector


def generate_speech_wav(text: str, output_path: Path, duration_s: float = 2.0) -> Path:
    """Generate a speech WAV using Kokoro TTS or synthetic fallback."""
    try:
        from tom.schemas.voice import SynthesisRequest
        from tom.voice.tts import KokoroTTSProvider, TTSConfig

        async def _generate():
            provider = KokoroTTSProvider(TTSConfig())
            req = SynthesisRequest(text=text)
            result = await provider.synthesize(req)
            return result.samples, result.sample_rate

        samples, rate = asyncio.run(_generate())
    except Exception:
        # Fallback: synthetic speech-like signal
        print("Kokoro unavailable, using synthetic speech")
        import numpy as np
        rate = 16000
        n = int(rate * duration_s)
        t = np.arange(n) / rate
        # Speech-like: multiple formants
        freq1, freq2, freq3 = 800, 1200, 2400
        signal = (np.sin(2*np.pi*freq1*t) * 0.3 +
                  np.sin(2*np.pi*freq2*t) * 0.2 +
                  np.sin(2*np.pi*freq3*t) * 0.1)
        # Add envelope
        envelope = np.exp(-t * 1.5)
        signal *= envelope
        signal = signal / np.max(np.abs(signal)) * 0.5
        samples = (signal * 32767).astype(np.int16)
        rate = 16000
        return samples, rate

    # Write WAV
    output_path = Path(output_path)
    import numpy as np
    if isinstance(samples, np.ndarray) and samples.dtype != np.int16:
        samples = (samples * 32767).astype(np.int16)

    with wave.open(str(output_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(samples.tobytes() if hasattr(samples, 'tobytes') else samples.tobytes())
    return output_path


async def run_selftest():
    """Run the full self-test: 3 utterances through pipeline with debug_trace."""

    config = load_config()
    config.voice.debug_trace = True

    # Create debug trace manager
    debug_trace = create_debug_trace_manager(config.voice)
    if not debug_trace:
        print("FAIL: debug_trace manager not created")
        return 1

    session = debug_trace.start_session()
    if not session:
        print("FAIL: session not started")
        return 1

    print(f"Session started: {session.session_dir}")

    # Setup microphone loop with debug trace
    mic_config = MicrophoneConfig(
        sample_rate=16000,
        frame_duration_ms=20,
        silence_duration_ms=600,
        energy_threshold=0.003,
    )
    mic_loop = MicrophoneLoop(config=mic_config, vad=EnergyVADDetector())

    # Log audio env
    session.log_audio_env(
        input_device="selftest_synthetic",
        output_device="selftest_synthetic",
    )

    # Start microphone
    await mic_loop.start()

    # Generate test utterances
    utterances = [
        "Hello, this is a short test.",
        "This is a longer utterance with a significant pause in the middle. And then it continues after the pause.",
        "Final short utterance.",
    ]

    for i, text in enumerate(utterances):
        print(f"Utterance {i+1}: {text[:50]}...")
        # Generate WAV
        wav_path = Path(tempfile.mktemp(suffix=".wav"))
        generate_speech_wav(text, wav_path, duration_s=3.0)

        # Load and push through mic loop
        import numpy as np
        with wave.open(str(wav_path), "rb") as wf:
            nframes = wf.getnframes()
            raw = wf.readframes(nframes)
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        await mic_loop.push_samples(samples)

        # Wait for processing
        await asyncio.sleep(1.0)

    await mic_loop.stop()
    debug_trace.close()

    # Validate trace
    print("\nRunning check_trace...")
    from tom.tools.check_trace import check_trace
    result = check_trace(session.session_dir)
    if result != 0:
        print("FAIL: check_trace failed")
        return 1

    print("Running replay_endpointing...")
    result = subprocess.run([
        sys.executable, "-m", "tom.tools.replay_endpointing",
        str(session.session_dir / "mic.wav")
    ], capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0:
        print("FAIL: replay_endpointing failed")
        print(result.stdout)
        print(result.stderr)
        return 1
    print(result.stdout)

    print("\nSELFTEST PASSED")
    return 0


def main():
    return asyncio.run(run_selftest())


if __name__ == "__main__":
    sys.exit(main())
