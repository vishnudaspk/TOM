#!/usr/bin/env python3
"""
Sanity check: 5 utterances × 3 runs × each engine
"""

import time
import numpy as np
import wave
import json
from pathlib import Path
import sherpa_onnx
import asyncio
from faster_whisper import WhisperModel

MODEL_DIR = Path("models/sherpa-onnx-streaming-zipformer-en-2023-06-26")
RECORDINGS_DIR = Path("recordings")

# Select 5 utterances (stratified: 1 short, 1 medium, 1 long, 1 TOM, 1 number)
UTTERANCE_IDS = [1, 16, 8, 28, 40]

CHUNK_SIZE = int(0.1 * 16000)  # 100ms at 16kHz
TAIL_PADDING_S = 0.3  # 300ms tail padding


def load_wav(filepath):
    with wave.open(str(filepath), 'rb') as wf:
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        sample_rate = wf.getframerate()
    return audio, sample_rate


def test_sherpa_large():
    """Test sherpa-large model."""
    print("Testing sherpa-large...")

    encoder = str(Path("models/sherpa-onnx-streaming-zipformer-en-2023-06-26/encoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx"))
    decoder = str(Path("models/sherpa-onnx-streaming-zipformer-en-2023-06-26/decoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx"))
    joiner = str(Path("models/sherpa-onnx-streaming-zipformer-en-2023-06-26/joiner-epoch-99-avg-1-chunk-16-left-128.int8.onnx"))
    tokens = str(Path("models/sherpa-onnx-streaming-zipformer-en-2023-06-26/tokens.txt"))

    recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=tokens,
        encoder=encoder,
        decoder=decoder,
        joiner=joiner,
        num_threads=4,
        decoding_method="greedy_search",
        enable_endpoint_detection=True,
        rule1_min_trailing_silence=2.4,
        rule2_min_trailing_silence=1.2,
        rule3_min_utterance_length=20.0,
        provider="cpu",
    )

    recordings = sorted(Path("recordings").glob("*.wav"))
    selected = [recordings[i-1] for i in [1, 16, 8, 28, 40]]

    all_results = []
    for run in range(3):
        print(f"  Run {run+1}/3")
        for wav_file in selected:
            audio, sr = load_wav(wav_file)
            if sr != 16000:
                continue

            stream = recognizer.create_stream()
            first_partial_time = None
            speech_onset_time = None
            start_time = time.perf_counter()
            speech_onset_time = start_time

            for i in range(0, len(audio), CHUNK_SIZE):
                chunk = audio[i:i+CHUNK_SIZE]
                if len(chunk) == 0:
                    continue
                chunk_start = time.perf_counter()
                stream.accept_waveform(16000, chunk)

                while recognizer.is_ready(stream):
                    recognizer.decode_stream(stream)
                    result = recognizer.get_result(stream)
                    if result and result.strip() and first_partial_time is None:
                        first_partial_time = time.perf_counter() - speech_onset_time

                elapsed = time.perf_counter() - chunk_start
                sleep_time = 0.1 - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

            speech_end_time = time.perf_counter()
            tail_padding = np.zeros(int(TAIL_PADDING_S * 16000), dtype=np.float32)
            stream.accept_waveform(16000, tail_padding)
            stream.input_finished()

            endpoint_time = time.perf_counter()

            final_start = time.perf_counter()
            while recognizer.is_ready(stream):
                recognizer.decode_stream(stream)
            final_time = time.perf_counter() - speech_onset_time
            final_from_endpoint = time.perf_counter() - endpoint_time

            result = recognizer.get_result_all(stream)
            final_text = result.text.strip() if hasattr(result, 'text') else str(result).strip()

            all_results.append({
                "first_partial_ms": round(first_partial_time * 1000, 1) if first_partial_time else None,
                "finalization_ms": round(final_time * 1000, 1),
                "final_from_endpoint_ms": round(final_from_endpoint * 1000, 1),
                "final_text": final_text,
            })

    return all_results


def load_wav(filepath):
    with wave.open(str(filepath), 'rb') as wf:
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        sample_rate = wf.getframerate()
    return audio, sample_rate


async def test_faster_whisper():
    """Test faster-whisper baseline."""
    print("Testing faster-whisper baseline...")
    model = WhisperModel("C:/Users/vishnuu/Projects/TOM/models/faster-whisper-small.en", device="cpu", compute_type="int8", cpu_threads=4)

    recordings = sorted(Path("recordings").glob("*.wav"))
    selected = [recordings[i-1] for i in [1, 16, 8, 28, 40]]

    all_results = []
    for run in range(3):
        print(f"  Run {run+1}/3")
        for wav_file in selected:
            audio, sr = load_wav(wav_file)
            if sr != 16000:
                continue

            start = time.perf_counter()
            segments, info = model.transcribe(audio, beam_size=1, vad_filter=False)
            # Consume the generator to actually run transcription
            text_parts = []
            for seg in segments:
                text_parts.append(seg.text)
            text = "".join(text_parts).strip()
            final_time = (time.perf_counter() - start) * 1000

            all_results.append({
                "file": wav_file.name,
                "run": run+1,
                "finalization_ms": round(final_time, 1),
                "final_text": text,
            })
    return all_results


def load_wav(filepath):
    with wave.open(str(filepath), 'rb') as wf:
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        sample_rate = wf.getframerate()
    return audio, sample_rate


async def main():
    print("=" * 60)
    print("SANITY CHECK: 5 utterances x 3 runs x each engine")
    print("=" * 60)

    # Test sherpa-large
    sherpa_results = test_sherpa_large()

    # Test faster-whisper
    fw_results = await test_faster_whisper()

    # Analyze results
    print("\n" + "="*60)
    print("SANITY CHECK RESULTS")
    print("="*60)

    # Analyze sherpa
    sherpa_fp = [r["first_partial_ms"] for r in sherpa_results if r["first_partial_ms"]]
    sherpa_fin = [r["final_from_endpoint_ms"] for r in sherpa_results]
    print(f"\nsherpa-large:")
    if sherpa_fp:
        print(f"  First partial: count={len(sherpa_fp)}/15, p50={np.median(sherpa_fp):.1f}ms")
    else:
        print("  First partial: 0/15")
    print(f"  Final from endpoint: p50={np.median(sherpa_fin):.1f}ms, p95={np.percentile(sherpa_fin, 95):.1f}ms, stdev={np.std(sherpa_fin):.1f}ms")

    # Analyze faster-whisper
    fw_fin = [r["finalization_ms"] for r in fw_results]
    print(f"\nfaster-whisper (beam=1):")
    print(f"  Finalization: p50={np.median(fw_fin):.1f}ms, p95={np.percentile(fw_fin, 95):.1f}ms, stdev={np.std(fw_fin):.1f}ms")

    # Check sanity
    issues = []
    if len(sherpa_fp) < 15 * 0.8:
        issues.append(f"sherpa: only {len(sherpa_fp)}/15 partials")
    if np.std(sherpa_fin) / np.mean(sherpa_fin) > 0.25:
        issues.append(f"sherpa: HIGH-VARIANCE (stdev/mean={np.std(sherpa_fin)/np.mean(sherpa_fin):.1%})")
    if np.std(fw_fin) / np.mean(fw_fin) > 0.25:
        issues.append(f"faster-whisper: HIGH-VARIANCE")

    if issues:
        print(f"\nISSUES: {issues}")
    else:
        print("\nSanity check PASSED")


if __name__ == "__main__":
    asyncio.run(main())